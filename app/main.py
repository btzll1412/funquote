from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import config
from app.auth import AuthRequired
from app.database import Base, engine
from app.routes import (
    auth_routes,
    catalog_routes,
    customer_routes,
    main_routes,
    profile_routes,
    quote_routes,
    settings_routes,
)

Base.metadata.create_all(bind=engine)
config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="FunQuote — Universal AI-Assisted Quoting")
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY)

static_dir = __file__.rsplit("/", 1)[0] + "/static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.mount("/uploads", StaticFiles(directory=str(config.UPLOAD_DIR)), name="uploads")


@app.exception_handler(AuthRequired)
async def auth_required_handler(request: Request, exc: AuthRequired):
    return RedirectResponse("/login", status_code=303)


app.include_router(auth_routes.router)
app.include_router(main_routes.router)
app.include_router(profile_routes.router)
app.include_router(catalog_routes.router)
app.include_router(customer_routes.router)
app.include_router(quote_routes.router)
app.include_router(settings_routes.router)
