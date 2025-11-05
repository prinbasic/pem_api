from fastapi import FastAPI,  Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from routes import cibil_routes, lender_routes, trans_routes
import httpx
import asyncio
import yaml
from datetime import datetime

app = FastAPI()


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
ALLOWED_PATHS = {"/health", "/openapi.json", "/openapi/aggregate.json"}
ALLOWED_PREFIXES = ("/docs", "/redoc", "/docs/aggregate", "/redoc/aggregate", "/combined-docs", "/combined-swagger")
# List the container Swagger URLs
SERVICE_URLS = [
    "http://127.0.0.1:8000/openapi.json",
    "http://3.6.21.243:8001/openapi.json",
    "http://3.6.21.243:9000/ai/openapi.json",
    "http://3.6.21.243:5000/openapi.json"
]

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
