from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from routes import cibil_routes, lender_routes

app = FastAPI()

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    with open("openapi.yaml", "r") as f:
        import yaml
        schema = yaml.safe_load(f)
    app.openapi_schema = schema
    return app.openapi_schema

app.openapi = custom_openapi
app.include_router(cibil_routes.router, prefix="/cibil")
app.include_router(lender_routes.router)
