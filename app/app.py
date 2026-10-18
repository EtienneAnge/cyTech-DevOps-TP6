"""MiniShop : application FastAPI VOLONTAIREMENT VULNÉRABLE (support du TP Cours 7).

Ne jamais déployer ce code tel quel. Votre mission : le rendre conforme.
Documentation interactive : http://localhost:5000/docs
"""

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

ph = PasswordHasher() 


import base64
import hashlib
import logging
import os
import pickle
import random
import sqlite3
import subprocess

import requests
import uvicorn
import yaml
from fastapi import Body, FastAPI, Request, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from starlette.middleware.sessions import SessionMiddleware

SECRET_KEY = os.environ["SECRET_KEY"]
DB_PATH = os.environ.get("DB_PATH", "shop.db")


import secrets

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


DEV = os.environ.get("APP_ENV") == "dev"

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield

app = FastAPI(
    title="MiniShop",
    debug=False,
    lifespan=lifespan,
    docs_url="/docs" if DEV else None,
    redoc_url=None,
    openapi_url="/openapi.json" if DEV else None,
)


limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("minishop")






def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def md5(value):
    return hashlib.md5(value.encode()).hexdigest()


def init_db():
    conn = db()
    conn.executescript(
        """
        DROP TABLE IF EXISTS users;
        DROP TABLE IF EXISTS products;
        DROP TABLE IF EXISTS invoices;
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE,
                            password TEXT, is_admin INTEGER DEFAULT 0);
        CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price REAL);
        CREATE TABLE invoices (id INTEGER PRIMARY KEY, owner_id INTEGER,
                               label TEXT, amount REAL);
        """
    )
    conn.execute("INSERT INTO users (username, password, is_admin) VALUES (?, ?, 1)",
                 ("admin", ph.hash("admin123")))
    conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", ("alice", ph.hash("alice123")))
    conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", ("bob", ph.hash("bob123")))
    conn.executemany("INSERT INTO products (name, price) VALUES (?, ?)",
                     [("apple", 1.2), ("banana", 0.8), ("cherry", 3.5)])
    conn.executemany("INSERT INTO invoices (owner_id, label, amount) VALUES (?, ?, ?)",
                     [(2, "Facture Alice", 42.0), (3, "Facture Bob", 99.0)])
    conn.commit()
    conn.close()


@app.exception_handler(Exception)
async def handle_error(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"error": "internal server error"}, status_code=500)

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/login")
@limiter.limit("5/minute")
def login(request: Request, data: dict = Body(...)):
    username, password = data["username"], data["password"]
    log.info(f"login attempt user={username}")
    row = db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    try:
        if not row:
            raise VerifyMismatchError
        ph.verify(row["password"], password)
    except (VerifyMismatchError, InvalidHashError):
        return JSONResponse({"error": "bad credentials"}, status_code=401)
    request.session["user_id"] = row["id"]
    request.session["token"] = secrets.token_urlsafe(32)
    return {"status": "logged in", "user": row["username"]}


@app.get("/search")
def search(q: str = ""):
    sql = f"SELECT id, name, price FROM products WHERE name LIKE ?"
    return [dict(r) for r in db().execute(sql, (f"%{q}%",)).fetchall()]

import ipaddress
@app.get("/ping")
def ping(host: str = Query("127.0.0.1", max_length=45)):
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid IP")
    
    try:
        out = subprocess.run(["ping", "-c", "1", f"{host}"],timeout=5, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="timeout")
    
    return PlainTextResponse(out.stdout + out.stderr)

import html

@app.get("/hello")
def hello(name: str = ""):
    return HTMLResponse(f"<h1>Bonjour {html.escape(name)}</h1>")


from pathlib import Path

UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"

@app.get("/files/{name:path}")
def get_file(name: str):
    path = (UPLOAD_DIR / name).resolve()
    if not path.is_relative_to(UPLOAD_DIR) or not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path)

import ast
@app.post("/calc")
def calc(data: dict = Body(...)):
    try:
        result = ast.literal_eval(data["expr"])
    except Exception:
        raise HTTPException(status_code=400, detail="invalid")
    
    return {
        "result": str(result)
        }


@app.post("/import-yaml")
async def import_yaml(request: Request):
    cfg = yaml.safe_load(await request.body(), Loader=yaml.Loader)
    return {"keys": list(cfg.keys()) if isinstance(cfg, dict) else str(type(cfg))}

from pydantic import BaseModel, Field, ConfigDict
class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")   # refuse les champs inconnus
    username: str = Field(min_length=1, max_length=64)
    email: str | None = Field(default=None, max_length=254)
    bio: str | None = Field(default=None, max_length=500)

@app.post("/import-profile")
def import_profile(profile: Profile):
    return {"profile": profile.model_dump()}


from urllib.parse import urlparse

ALLOWED_HOSTS = {"example.com"}

@app.get("/fetch")
def fetch(url: str):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise HTTPException(status_code=400, detail="URL not alllowed")
    try:
        r = requests.get(url, timeout=5, verify=True, allow_redirects=False)
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="resquest failed")
    return PlainTextResponse(r.text[:500])

@app.get("/invoices/{invoice_id}")
def invoice(invoice_id: int, request: Request):
    if "user_id" not in request.session:
        return JSONResponse({"error": "login required"}, status_code=401)
    row = db().execute("SELECT * FROM invoices WHERE id = ? AND owner_id = ?", (invoice_id,request.session.get("user_id"))).fetchone()
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    return dict(row)

ROLE_PERMISSIONS = {
    "admin": {"user:list", "user:delete"},
    "user": set(),
}

def current_user(request: Request):
    user_id = request.session.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=401, detail="login required")
    row = db().execute("SELECT id, username, is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="login required")
    return row

def require(permission: str):
    def checker(user=Depends(current_user)):
        role = "admin" if user["is_admin"] else "user"
        if permission not in ROLE_PERMISSIONS.get(role, set()):
            raise HTTPException(status_code=403, detail="forbidden")
        return user
    return checker

@app.get("/admin/users")
def admin_users(user=Depends(require("user:list"))):
    rows = db().execute("SELECT id, username, is_admin FROM users").fetchall()
    return [dict(r) for r in rows]

@app.delete("/users/{user_id}")
def delete_user(user_id: int, user=Depends(require("user:delete"))):
    conn = db()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    return {"deleted": user_id}

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = "default-src 'self'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Frame-Options"] = "DENY"
    return response

@app.get("/boom")
def boom():
    return 1 / 0


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=5000)
