from fastapi import FastAPI,  Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from routes import cibil_routes, lender_routes, trans_routes
import httpx
import asyncio
import yaml
from datetime import datetime
import logging
from dataclasses import dataclass, asdict
import os, asyncio, re, time
from datetime import datetime, timezone
from fastapi.responses import JSONResponse
from typing import Dict, Any, List, Optional, Tuple

app = FastAPI()
SERVICE_NAME = os.getenv("SERVICE_NAME", "pem-main")
SERVICE_VERSION = os.getenv("SERVICE_VERSION", "local-dev")

logging.basicConfig(level=logging.INFO)
access_logger = logging.getLogger("app.access")
health_logger = logging.getLogger("app.health")

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )


@app.get("/")
async def read_root():
    return {"message": "CORS setup working!"}

# --- Include your existing routers ---
app.include_router(cibil_routes.router, prefix="/cibil")
app.include_router(lender_routes.router)
app.include_router(trans_routes.router, prefix="/cibil")

# --- Swagger Aggregation Setup ---
ALLOWED_PATHS = { "/health", "/health/ready", "/health/deps",
    "/openapi.json", "/openapi/aggregate.json",
    "/openapi/filtered.json", "/openapi/filtered/list",
    "/docs/filtered", "/redoc/filtered","/health/filtered"}
ALLOWED_PREFIXES = ("/docs", "/redoc", "/docs/aggregate", "/redoc/aggregate", "/combined-docs", "/combined-swagger")
# List the container Swagger URLs
SERVICE_URLS = [
    "http://127.0.0.1:8000/openapi.json",
    "http://3.6.21.243:8001/openapi.json",
    "http://3.6.21.243:9000/openapi.json",
    "http://3.6.21.243:5000/openapi.json"
]

# Upstream source (one file only)
FILTER_SOURCE_URL = os.getenv("FILTER_SOURCE_URL", "https://api.orbit.basichomeloan.com/openapi.json")
FILTER_TIMEOUT = float(os.getenv("FILTER_TIMEOUT", "4.0"))

# Which ops to include
FILTER_INCLUDE_TAGS = {t.strip().lower() for t in ["ongrid", "transbank", "credits"]}
FILTER_INCLUDE_PATHS = {"/cibil/fetch-lenders", "/cibil/intell-report", "/cibil/fetchlenders_apf"}  # de-duped

SAFE_METHODS = {"get", "head", "options"}
INCLUDE_405_AS_UP = os.getenv("ROUTE_INCLUDE_405_AS_UP", "1") == "1"
ROUTE_TIMEOUT_SECS = float(os.getenv("ROUTE_TIMEOUT_SECS", "3.5"))
ROUTE_PARALLEL_LIMIT = int(os.getenv("ROUTE_PARALLEL_LIMIT", "20"))


PROBE_DEFAULT_HEADERS: Dict[str, str] = {
    "x-api-key": os.getenv("api-key"),
    # "x-trusted-auth": "route-health"
}

############################################################################ health code ################################################################################################################################ 

@dataclass
class RouteProbe:
    method: str
    path: str
    url: str
    up: bool
    status_code: Optional[int]
    latency_ms: Optional[int]
    reason: Optional[str] = None
    skipped: bool = False
    skipped_reason: Optional[str] = None

def _op_has_included_tag(op: dict) -> bool:
    for t in (op.get("tags") or []):
        if str(t).strip().lower() in FILTER_INCLUDE_TAGS:
            return True
    return False

def _filter_paths(full_paths: dict) -> dict:
    kept: dict = {}
    for pth, item in (full_paths or {}).items():
        keep_item = {}
        for method, op in (item or {}).items():
            if method.lower() not in {"get","post","put","patch","delete","head","options"}:
                continue
            if pth in FILTER_INCLUDE_PATHS or _op_has_included_tag(op or {}):
                keep_item[method] = op
        if keep_item:
            kept[pth] = keep_item
    return kept

def _path_vars(path: str) -> List[str]:
    return re.findall(r"\{([^}]+)\}", path or "")

def _has_required_params(op: Dict[str, Any]) -> bool:
    params = op.get("parameters", [])
    for p in params:
        if p.get("required"):
            schema = p.get("schema", {})
            if p.get("in") == "path":
                return True
            if p.get("in") == "query" and "example" not in p and "default" not in schema:
                return True
    return False

def _resolve_base_url(source: Dict[str, Any]) -> str:
    # 1) First server in spec, else env override, else infer from FILTER_SOURCE_URL
    servers = source.get("servers", [])
    if servers and servers[0].get("url"):
        base = servers[0]["url"]
    else:
        base = os.getenv("FILTER_BASE_URL")
        if not base:
            # crude fallback: take origin from FILTER_SOURCE_URL
            m = re.match(r"^(https?://[^/]+)/", FILTER_SOURCE_URL.strip()+"/")
            base = m.group(1) if m else "http://3.6.21.243:8000"
    return base.rstrip("/")

def _join(base: str, path: str) -> str:
    if not path.startswith("/"):
        path = "/"+path
    return base + path

async def _fetch_source_openapi(url: str) -> dict:
    async with httpx.AsyncClient(follow_redirects=True, timeout=FILTER_TIMEOUT) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()

async def _probe_once(client: httpx.AsyncClient, method: str, url: str) -> Tuple[bool,int,str]:
    try:
        r = await client.request(
            method, url,
            headers=PROBE_DEFAULT_HEADERS,
            timeout=ROUTE_TIMEOUT_SECS,
            follow_redirects=True,
        )
        if INCLUDE_405_AS_UP and r.status_code == 405:
            return True, r.status_code, "405 treated as UP (route exists)"
        ok = 200 <= r.status_code < 400
        return ok, r.status_code, f"got={r.status_code}"
    except Exception as e:
        return False, None, f"error={type(e).__name__}: {e}"

async def _probe_route(client: httpx.AsyncClient, base: str, method: str, path: str, op: Dict[str, Any]) -> RouteProbe:
    upper = method.upper()
    url = _join(base, path)
    # Skip unsafe methods or required params (no per-route samples requested)
    if method.lower() not in SAFE_METHODS:
        return RouteProbe(upper, path, url, up=False, status_code=None, latency_ms=None, skipped=True, skipped_reason="unsafe_method")
    if _path_vars(path) or _has_required_params(op):
        return RouteProbe(upper, path, url, up=False, status_code=None, latency_ms=None, skipped=True, skipped_reason="required_params")

    t0 = time.perf_counter()
    ok, code, reason = await _probe_once(client, upper, url)
    return RouteProbe(upper, path, url, up=ok, status_code=code, latency_ms=int((time.perf_counter()-t0)*1000), reason=reason)

async def _run_filtered_health() -> Dict[str, Any]:
    source = await _fetch_source_openapi(FILTER_SOURCE_URL)
    base = _resolve_base_url(source)
    kept_paths = _filter_paths(source.get("paths", {}))

    # Collect probes
    to_probe: List[Tuple[str,str,Dict[str,Any]]] = []
    for pth, item in kept_paths.items():
        for method, op in item.items():
            to_probe.append((method, pth, op or {}))

    # Concurrency throttle
    sem = asyncio.Semaphore(ROUTE_PARALLEL_LIMIT)
    results: List[RouteProbe] = []
    async with httpx.AsyncClient() as client:
        async def one(m,p,o):
            async with sem:
                results.append(await _probe_route(client, base, m, p, o))
        await asyncio.gather(*(one(m,p,o) for (m,p,o) in to_probe))

    # Summarize
    up = sum(1 for r in results if r.up and not r.skipped)
    down = sum(1 for r in results if (not r.up) and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    status = "pass" if down == 0 else ("degraded" if up > 0 else "fail")
    return {
        "status": status,
        "source_openapi": FILTER_SOURCE_URL,
        "base_url_used": base,
        "summary": {"total": len(results), "up": up, "down": down, "skipped": skipped},
        "results": [asdict(r) for r in results],
    }

@app.get("/health/filtered")
async def health_filtered():
    """
    Route-level HEALTH for a FILTERED subset of one upstream OpenAPI:
    - Includes tags: ongrid, transbank, credits
    - Includes paths: /cibil/fetch-lenders, /cibil/intell-report
    - Probes only safe routes (GET/HEAD/OPTIONS) without required params
    - Returns up/down, status code, latency, and skip reason
    """
    try:
        payload = await _run_filtered_health()
        return payload
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": "health_filtered_failed", "detail": str(e)})
# --- Filtered Route Health (single source) END ---

########################################################################### health end ##################################################################################################################################

def _inject_security_for_cibil(spec: dict, base_url: str) -> dict:
    """
    - sets servers[] so Swagger "Try it out" hits your real domain
    - defines components.securitySchemes.ApiKeyAuth (x-api-key in header)
    - adds per-operation security ONLY for /cibil/** paths
    """
    spec = dict(spec)  # shallow copy
    spec["servers"] = [{"url": base_url.rstrip("/")}]

    components = spec.setdefault("components", {})
    sec_schemes = components.setdefault("securitySchemes", {})
    sec_schemes["ApiKeyAuth"] = {
        "type": "apiKey",
        "in": "header",
        "name": "x-api-key"
    }

    paths = spec.get("paths", {}) or {}
    for path, item in paths.items():
        if not path.startswith("/cibil/"):
            continue
        # add security to every HTTP operation under this path
        for method, op in list(item.items()):
            if method.lower() not in ("get","post","put","patch","delete","options","head"):
                continue
            if "security" not in op:
                op["security"] = [{"ApiKeyAuth": []}]  # don't override if already present

    return spec

async def fetch_openapi_spec(url):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"❌ Failed to fetch or parse {url}: {e}")
            return {"paths": {}, "components": {"schemas": {}}}  # Return empty structure to prevent crash

async def get_combined_openapi():
    specs = await asyncio.gather(*(fetch_openapi_spec(url) for url in SERVICE_URLS))

    combined_paths = {}
    combined_components = {"schemas": {}}

    for spec in specs:
        combined_paths.update(spec.get("paths", {}) or {})
        components = (spec.get("components", {}) or {}).get("schemas", {}) or {}
        combined_components["schemas"].update(components)

    return {
        "openapi": "3.0.0",
        "info": {"title": "Combined API", "version": "1.0.0"},
        "paths": combined_paths,
        "components": combined_components
    }

# Belt & suspenders: block direct calls that bypass NGINX

@app.middleware("http")
async def require_trusted_auth(request: Request, call_next):
    path = request.url.path
    if path in ALLOWED_PATHS or any(path.startswith(p) for p in ALLOWED_PREFIXES):
        return await call_next(request)
    if request.headers.get("x-trusted-auth") != "yes":
        raise HTTPException(status_code=403, detail="Direct access forbidden")
    return await call_next(request)

@app.get("/cibil/health")
async def health(request: Request):
    return {
        "ok": True,
        "team": request.headers.get("x-team-id")  # set by NGINX if Auth-Gateway returns it
    }



@app.get("/openapi/aggregate.json")
async def openapi_aggregate(request: Request):
    spec = await get_combined_openapi()

    # Build public base URL from NGINX
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host  = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    base_url = f"{proto}://{host}"
    spec["servers"] = [{"url": base_url}]  # ensures Swagger uses https://dev-api...

    # Add ApiKey scheme (x-api-key)
    components = spec.setdefault("components", {})
    sec_schemes = components.setdefault("securitySchemes", {})
    sec_schemes["ApiKeyAuth"] = {"type": "apiKey", "in": "header", "name": "x-api-key"}

    # Require the key ONLY for /cibil/**
    for path, item in (spec.get("paths") or {}).items():
        if not path.startswith("/cibil/"):
            continue
        for method, op in list(item.items()):
            if method.lower() in {"get","post","put","patch","delete","options","head"}:
                op.setdefault("security", [{"ApiKeyAuth": []}])

    return spec

@app.get("/docs/aggregate", include_in_schema=False)
async def aggregated_swagger_ui():
    # unchanged, hits /openapi/aggregate.json which now includes security + servers
    timestamp = int(datetime.now().timestamp())
    return get_swagger_ui_html(
        openapi_url=f"/openapi/aggregate.json?t={timestamp}",
        title="Combined API Docs"
    )

@app.get("/redoc/aggregate", include_in_schema=False)
async def aggregated_redoc():
    return get_redoc_html(openapi_url="/openapi/aggregate.json", title="Combined API Docs")
