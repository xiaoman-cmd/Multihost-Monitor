import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text
from sqlalchemy.exc import SQLAlchemyError

from .config import Settings
from .db import make_engine, session_factory
from .models import AdminSession, AdminUser, Host, now
from .security import COOKIE_NAME, DUMMY_HASH, LoginLimiter, digest, hasher, verify

REVISION = "0003_phase03"
DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


def create_app(settings: Settings | None = None, *, static_dir: Path = DIST) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings.database_url)
    sessions = session_factory(engine)
    limiter = LoginLimiter()

    @asynccontextmanager
    async def lifespan(app):
        stop = asyncio.Event()
        maintenance = None
        try:
            with engine.connect() as connection:
                if connection.scalar(text("SELECT version_num FROM alembic_version")) != REVISION:
                    raise RuntimeError("Database schema is outdated; run alembic upgrade head")
            with sessions.begin() as db:
                admin = db.scalar(select(AdminUser).where(AdminUser.username == settings.admin_username))
                if admin is None:
                    if db.scalar(select(AdminUser.id).limit(1)) is not None:
                        raise RuntimeError("Configured administrator differs from existing administrator")
                    db.add(AdminUser(username=settings.admin_username,
                                     password_hash=hasher.hash(settings.admin_password)))
                elif not verify(settings.admin_password, admin.password_hash):
                    raise RuntimeError("Configured administrator password differs from database")
                db.execute(delete(AdminSession).where(AdminSession.expires_at <= now()))
            if settings.retention_enabled:
                from .retention import retention_loop
                maintenance = asyncio.create_task(retention_loop(sessions, stop, app.state))
            yield
        finally:
            stop.set()
            if maintenance is not None:
                await maintenance
            engine.dispose()

    app = FastAPI(title="MultiHost Monitor", version="1.0.0-phase03", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = engine
    app.state.sessions = sessions
    app.state.settings = settings

    from .agent_api import BodyLimit
    app.add_middleware(BodyLimit)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def same_origin(request: Request):
        if request.headers.get("origin") != settings.origin:
            raise HTTPException(403, "Origin rejected")

    def authenticated(request: Request):
        token = request.cookies.get(COOKIE_NAME, "")
        if not token or len(token) > 128:
            raise HTTPException(401, "Authentication required")
        with sessions() as db:
            session = db.get(AdminSession, digest(token))
            if session is None or session.expires_at <= now():
                raise HTTPException(401, "Session expired or invalid")
            admin = db.get(AdminUser, session.admin_id)
            if admin is None:
                raise HTTPException(401, "Authentication required")
            return session, admin

    @app.get("/api/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/api/health/ready")
    def ready():
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
                wal = connection.scalar(text("PRAGMA journal_mode"))
                foreign_keys = connection.scalar(text("PRAGMA foreign_keys"))
                timeout = connection.scalar(text("PRAGMA busy_timeout"))
                if revision != REVISION or wal != "wal" or foreign_keys != 1 or timeout != 5000:
                    raise RuntimeError("Database not ready")
        except (SQLAlchemyError, RuntimeError):
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {"status": "ok", "database": "ok"}

    @app.post("/api/auth/login", dependencies=[Depends(same_origin)])
    def login(payload: LoginInput, request: Request, response: Response):
        if not limiter.allow(request.client.host if request.client else "unknown"):
            raise HTTPException(429, "Too many login attempts", headers={"Retry-After": "60"})
        with sessions.begin() as db:
            admin = db.scalar(select(AdminUser).where(AdminUser.username == payload.username))
            valid = verify(payload.password, admin.password_hash if admin else DUMMY_HASH)
            if not valid or admin is None:
                raise HTTPException(401, "Invalid username or password")
            old_token = request.cookies.get(COOKIE_NAME)
            if old_token:
                db.execute(delete(AdminSession).where(AdminSession.token_hash == digest(old_token)))
            db.execute(delete(AdminSession).where(AdminSession.expires_at <= now()))
            token = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(32)
            # One active session per administrator bounds storage and invalidates old logins.
            db.execute(delete(AdminSession).where(AdminSession.admin_id == admin.id))
            db.add(AdminSession(token_hash=digest(token), admin_id=admin.id, csrf_token=csrf,
                                expires_at=now() + settings.session_hours * 3600))
            response.set_cookie(COOKIE_NAME, token, httponly=True, secure=settings.cookie_secure,
                                samesite="strict", max_age=settings.session_hours * 3600, path="/")
            return {"username": admin.username, "csrf_token": csrf}

    @app.get("/api/auth/me")
    def me(auth=Depends(authenticated)):
        session, admin = auth
        return {"username": admin.username, "csrf_token": session.csrf_token}

    @app.post("/api/auth/logout", status_code=204, dependencies=[Depends(same_origin)])
    def logout(request: Request, response: Response, auth=Depends(authenticated)):
        session, _ = auth
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf_token):
            raise HTTPException(403, "CSRF token rejected")
        with sessions.begin() as db:
            db.execute(delete(AdminSession).where(AdminSession.token_hash == session.token_hash))
        response.delete_cookie(COOKIE_NAME, path="/", httponly=True,
                               secure=settings.cookie_secure, samesite="strict")

    @app.get("/api/hosts")
    def hosts(auth=Depends(authenticated)):
        with sessions() as db:
            rows = db.scalars(select(Host).order_by(Host.id).limit(100)).all()
            return {"items": [{"id": h.id, "name": h.name, "hostname": h.hostname,
                               "status": h.status, "last_seen_at": h.last_seen_at} for h in rows]}

    from .agent_api import install_routes
    install_routes(app, sessions, authenticated, same_origin)

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "API route not found")
        root = static_dir.resolve()
        candidate = (root / path).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
        if path and "." in Path(path).name:
            raise HTTPException(404, "Asset not found")
        if not (root / "index.html").is_file():
            raise HTTPException(503, "Frontend not built; run npm --prefix web run build")
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})

    return app
