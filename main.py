from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from routes import cibil_routes, lender_routes, trans_routes
import httpx
import asyncio
import yaml
from datetime import datetime

app = FastAPI()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def read_root():
    return {"message": "CORS setup working!"}

# --- Include your existing routers ---
app.include_router(cibil_routes.router, prefix="/cibil")
app.include_router(lender_routes.router)
app.include_router(trans_routes.router, prefix="/cibil")

# --- Swagger Aggregation Setup ---

# List the container Swagger URLs
SERVICE_URLS = [
    "http://127.0.0.1:8000/openapi.json",
    "http://127.0.0.1:8001/openapi.json",
    "http://127.0.0.1:9000/ai/openapi.json",
    "http://127.0.0.1:5000/openapi.json"
]

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
        combined_paths.update(spec.get("paths", {}))
        components = spec.get("components", {}).get("schemas", {})
        combined_components["schemas"].update(components)

    return {
        "openapi": "3.0.0",
        "info": {"title": "Combined API", "version": "1.0.0"},
        "paths": combined_paths,
        "components": combined_components
    }


@app.get("/openapi/aggregate.json")
async def openapi_aggregate():
    return await get_combined_openapi()


@app.get("/docs/aggregate", include_in_schema=False)
async def aggregated_swagger_ui():
    timestamp = int(datetime.now().timestamp())  # generate a unique query param
    return get_swagger_ui_html(
        openapi_url=f"/openapi/aggregate.json?t={timestamp}",
        title="Combined API Docs"
    )


@app.get("/redoc/aggregate", include_in_schema=False)
async def aggregated_redoc():
    return get_redoc_html(openapi_url="/openapi/aggregate.json", title="Combined API Docs")
