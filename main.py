from fastapi import FastAPI
from routes import Credit_routes, lender_routes
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    with open("openapi.yaml", "r") as f:
        import yaml
        schema = yaml.safe_load(f)
    app.openapi_schema = schema
    return app.openapi_schema

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allow all HTTP methods
    allow_headers=["*"],  # Allow all headers
)

@app.get("/")
async def read_root():
    return {"message": "CORS setup working!"}

app.openapi = custom_openapi
app.include_router(Credit_routes.router, prefix="/Credit")
app.include_router(lender_routes.router)
