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
INCLUDE_TAGS = {t.strip().lower() for t in ["ongrid", "transbnk", "credits"]}
INCLUDE_PATHS = {"/cibil/intell-report", "/cibil/fetchlenders_apf"}  # de-duped
FILTER_BASE_URL   = os.getenv("FILTER_BASE_URL", "").strip()
# Don’t accidentally probe our own infra/docs
PATH_EXCLUDE_PREFIXES = {"/health", "/openapi", "/docs", "/redoc"}

SAFE_METHODS = {"get", "head", "options"}
INCLUDE_405_AS_UP = os.getenv("ROUTE_INCLUDE_405_AS_UP", "1") == "1"
ROUTE_TIMEOUT_SECS = float(os.getenv("ROUTE_TIMEOUT_SECS", "3.5"))
ROUTE_PARALLEL_LIMIT = int(os.getenv("ROUTE_PARALLEL_LIMIT", "20"))


PROBE_DEFAULT_HEADERS: Dict[str, str] = {
    "x-api-key": os.getenv("api-key"),
    # "x-trusted-auth": "route-health"
}

############################################################################ health code ################################################################################################################################ 

# ---- PROBES (YOU FILL THESE) ----
# Structure: { base_url: { path: [ {method, params?, query?, json?, headers?, expect?}, ... ] } }
# - base_url MUST match resolved base (usually spec.servers[0].url)
# - expect = list of status codes that count as UP (e.g., [200,400,401,403])
PROBES: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
    # Example (uncomment & edit):
    "https://api.orbit.basichomeloan.com": {
        "/cibil/fetchlenders_apf": [
            {
        "method": "POST",
        # send it as QUERY, not JSON:
        "query": {"propertyName": "dlf"},
        # auth header (from env). You can also hardcode for a quick test.
        "headers": {"x-api-key": os.getenv("ROUTE_HEALTH_API_KEY", "")},
        # treat these as UP (loose mode). Tighten to [200] if you want strict success.
        "expect": [200, 400, 401, 403]
      }
        ],
    #     "/cibil/intell-report": [
    #         {"method": "POST", "json": {"pan": "ABCDE1234F", "dob": "1990-01-01"}, "expect": [200,400,401,403]}
    #     ],
    }
}

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

# ========== HELPERS ==========
def _path_vars(path: str) -> List[str]:
    return re.findall(r"\{([^}]+)\}", path or "")

def _fill_path(path: str, params: Dict[str, Any]) -> Optional[str]:
    try:
        out = path
        for k in _path_vars(path):
            if k not in params:
                return None
            out = out.replace("{"+k+"}", str(params[k]))
        return out
    except Exception:
        return None

def _clean_headers(h: Dict[str, str]) -> Dict[str, str]:
    return {k: v for k, v in (h or {}).items() if v not in (None, "", [])}

def _join(base: str, path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return base.rstrip("/") + path

def _norm_tags(op: dict) -> List[str]:
    tags = op.get("tags") or []
    out: List[str] = []
    for t in tags:
        if isinstance(t, dict):
            name = str(t.get("name", "")).strip().lower()
        else:
            name = str(t).strip().lower()
        if name:
            out.append(name)
    return out

def _op_is_included(path: str, op: dict) -> bool:
    if any(path.startswith(pref) for pref in PATH_EXCLUDE_PREFIXES):
        return False
    if path in INCLUDE_PATHS:
        return True
    tags = _norm_tags(op or {})
    return any(tag in INCLUDE_TAGS for tag in tags)

async def _fetch_source_openapi(url: str) -> dict:
    candidates = [
        url,
        url.rstrip("/") + "/openapi.json",
        url.rstrip("/") + "/docs/openapi.json",
        url.rstrip("/") + "/openapi/aggregate.json",
    ]
    async with httpx.AsyncClient(follow_redirects=True, timeout=FILTER_TIMEOUT) as client:
        last_err = None
        for u in candidates:
            try:
                r = await client.get(u); r.raise_for_status()
                j = r.json()
                if isinstance(j, dict) and ("paths" in j or "swagger" in j or "openapi" in j):
                    return j
            except Exception as e:
                last_err = e
                continue
    raise RuntimeError(f"Upstream OpenAPI fetch failed; last_error={last_err}")

def _resolve_base_url(source: Dict[str, Any]) -> str:
    servers = source.get("servers", [])
    if servers and servers[0].get("url"):
        return str(servers[0]["url"]).rstrip("/")
    if FILTER_BASE_URL:
        return FILTER_BASE_URL.rstrip("/")
    m = re.match(r"^(https?://[^/]+)", FILTER_SOURCE_URL.strip())
    return m.group(1) if m else "http://3.6.21.243:8000"

def _has_required_params(op: Dict[str, Any]) -> bool:
    params = op.get("parameters", [])
    for p in params:
        if p.get("required"):
            schema = p.get("schema", {})
            loc = p.get("in")
            if loc == "path":
                return True
            if loc == "query" and "example" not in p and "default" not in schema:
                return True
    return False

# ========== PROBING ==========
async def _probe_once(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    probe: Optional[Dict[str, Any]] = None
) -> Tuple[bool, Optional[int], str]:
    try:
        headers = _clean_headers({**PROBE_DEFAULT_HEADERS, **(probe.get("headers", {}) if probe else {})})
        resp = await client.request(
            probe.get("method", method) if probe else method,
            url,
            headers=headers,
            params=probe.get("query") if probe else None,
            json=probe.get("json") if probe else None,
            timeout=ROUTE_TIMEOUT_SECS,
            follow_redirects=True,
        )
        if probe and probe.get("expect"):
            ok = resp.status_code in set(probe["expect"])
            return ok, resp.status_code, f"expect={probe['expect']}, got={resp.status_code}"
        if INCLUDE_405_AS_UP and resp.status_code == 405:
            return True, resp.status_code, "405 treated as UP (route exists)"
        ok = 200 <= resp.status_code < 400
        return ok, resp.status_code, f"got={resp.status_code}"
    except Exception as e:
        return False, None, f"error={type(e).__name__}: {e}"

async def _probe_route(
    client: httpx.AsyncClient,
    base: str,
    method: str,
    path: str,
    op: Dict[str, Any]
) -> RouteProbe:
    upper = method.upper()
    url = _join(base, path)

    # 1) If explicit probes exist for this route, run those (POST/secured/parametrized)
    base_key = base.rstrip("/")
    base_probes = PROBES.get(base_key) or PROBES.get(base_key + "/") or {}
    custom_list = base_probes.get(path, [])
    if custom_list:
        any_ok, last_status, last_reason = False, None, None
        t0 = time.perf_counter()
        for pr in custom_list:
            target_path = path
            if _path_vars(path):
                filled = _fill_path(path, pr.get("params", {}))
                if not filled:
                    last_status, last_reason = None, "missing_path_params_in_probe"
                    continue
                target_path = filled
            target_url = _join(base, target_path)
            ok, status, reason = await _probe_once(client, pr.get("method", upper), target_url, probe=pr)
            any_ok = any_ok or ok
            last_status, last_reason = status, reason
        return RouteProbe(
            method=upper, path=path, url=url,
            up=any_ok, status_code=last_status,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            reason=last_reason, skipped=False
        )

    # 2) No explicit probe → safe mode (only simple GET/HEAD/OPTIONS without required params)
    if method.lower() not in SAFE_METHODS:
        return RouteProbe(upper, path, url, up=False, status_code=None, latency_ms=None, skipped=True, skipped_reason="unsafe_method")
    if _path_vars(path) or _has_required_params(op):
        return RouteProbe(upper, path, url, up=False, status_code=None, latency_ms=None, skipped=True, skipped_reason="required_params")

    t0 = time.perf_counter()
    ok, code, reason = await _probe_once(client, upper, url, probe=None)
    return RouteProbe(upper, path, url, up=ok, status_code=code, latency_ms=int((time.perf_counter()-t0)*1000), reason=reason)

# ========== MAIN RUNNER ==========
async def _run_filtered_health() -> Dict[str, Any]:
    source = await _fetch_source_openapi(FILTER_SOURCE_URL)
    base = _resolve_base_url(source)

    # Build the **filtered** list of operations first (tags + explicit paths only)
    paths: dict = source.get("paths", {}) or {}
    to_probe: List[Tuple[str, str, Dict[str, Any]]] = []
    for pth, item in paths.items():
        if any(pth.startswith(pref) for pref in PATH_EXCLUDE_PREFIXES):
            continue
        for method, op in (item or {}).items():
            if method.lower() not in {"get","post","put","patch","delete","head","options"}:
                continue
            if _op_is_included(pth, op or {}):
                to_probe.append((method, pth, op or {}))

    # Concurrency throttle + fire
    sem = asyncio.Semaphore(ROUTE_PARALLEL_LIMIT)
    results: List[RouteProbe] = []
    async with httpx.AsyncClient() as client:
        async def one(m, p, o):
            async with sem:
                results.append(await _probe_route(client, base, m, p, o))
        await asyncio.gather(*(one(m, p, o) for (m, p, o) in to_probe))

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
        "env": os.getenv("api-key"),
    }


# ========== ROUTE ==========
@app.get("/health/filtered")
async def health_filtered():
    """
    Route-level HEALTH (filtered):
      • Includes only ops whose tags are in INCLUDE_TAGS, plus exact INCLUDE_PATHS
      • Runs explicit PROBES first (auth/payload/expected codes)
      • Falls back to safe GET/HEAD/OPTIONS without required params
      • Returns per-route: up, status_code, latency_ms, reason, skipped_reason
    """
    try:
        payload = await _run_filtered_health()
        return payload
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": "health_filtered_failed", "detail": str(e)})
# --- Filtered Route Health (tags + two explicit paths) END ---

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
