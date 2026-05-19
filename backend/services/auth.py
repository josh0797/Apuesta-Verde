"""Simple email+password authentication (free login).

For MVP we provide:
  - POST /api/auth/register {email, password, name?} -> JWT token
  - POST /api/auth/login    {email, password} -> JWT token
  - GET  /api/auth/me       -> current user
  - POST /api/auth/logout   -> noop (client clears token)

A convenience seeded demo user is created on startup: demo@valuebet.app / demo1234
so that the testing agent (and manual users) can sign in without registration.
"""
from __future__ import annotations

import os
import time
import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel, EmailStr, Field

log = logging.getLogger("auth")

JWT_SECRET = os.environ.get("JWT_SECRET", "value-bet-intelligence-dev-secret-change-me")
JWT_ALG = "HS256"
JWT_TTL_HOURS = 24 * 14  # 14 days


class UserPublic(BaseModel):
    id: str
    email: str
    name: Optional[str] = None
    created_at: str
    language: str = "es"


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    name: Optional[str] = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class AuthOut(BaseModel):
    token: str
    user: UserPublic


def _hash_pw(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_pw(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def _make_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_TTL_HOURS * 3600,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def _decode_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])


async def seed_demo_user(db) -> None:
    """Idempotent: ensure demo@valuebet.app exists."""
    existing = await db.users.find_one({"email": "demo@valuebet.app"})
    if existing:
        return
    uid = str(uuid.uuid4())
    await db.users.insert_one({
        "id": uid,
        "email": "demo@valuebet.app",
        "name": "Demo Analyst",
        "password_hash": _hash_pw("demo1234"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "language": "es",
    })
    log.info("Seeded demo user demo@valuebet.app / demo1234")


def build_router(db) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])

    async def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
        if not authorization:
            raise HTTPException(status_code=401, detail="missing authorization")
        if not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="invalid scheme")
        token = authorization.split(" ", 1)[1].strip()
        try:
            payload = _decode_token(token)
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="token expired")
        except Exception:
            raise HTTPException(status_code=401, detail="invalid token")
        user = await db.users.find_one({"id": payload["sub"]})
        if not user:
            raise HTTPException(status_code=401, detail="user not found")
        return user

    @router.post("/register", response_model=AuthOut)
    async def register(payload: RegisterIn):
        existing = await db.users.find_one({"email": payload.email.lower()})
        if existing:
            raise HTTPException(status_code=409, detail="email already registered")
        uid = str(uuid.uuid4())
        doc = {
            "id": uid,
            "email": payload.email.lower(),
            "name": payload.name,
            "password_hash": _hash_pw(payload.password),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "language": "es",
        }
        await db.users.insert_one(doc)
        token = _make_token(uid, doc["email"])
        return AuthOut(token=token, user=UserPublic(
            id=uid, email=doc["email"], name=doc["name"], created_at=doc["created_at"], language="es",
        ))

    @router.post("/login", response_model=AuthOut)
    async def login(payload: LoginIn):
        user = await db.users.find_one({"email": payload.email.lower()})
        if not user or not _verify_pw(payload.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="invalid credentials")
        token = _make_token(user["id"], user["email"])
        return AuthOut(token=token, user=UserPublic(
            id=user["id"], email=user["email"], name=user.get("name"),
            created_at=user["created_at"], language=user.get("language", "es"),
        ))

    @router.get("/me", response_model=UserPublic)
    async def me(user: dict = Depends(get_current_user)):
        return UserPublic(
            id=user["id"], email=user["email"], name=user.get("name"),
            created_at=user["created_at"], language=user.get("language", "es"),
        )

    @router.post("/logout")
    async def logout(user: dict = Depends(get_current_user)):
        return {"ok": True}

    @router.patch("/me/language")
    async def update_language(lang: dict, user: dict = Depends(get_current_user)):
        new_lang = (lang or {}).get("language", "es")
        if new_lang not in ("es", "en"):
            raise HTTPException(status_code=400, detail="invalid language")
        await db.users.update_one({"id": user["id"]}, {"$set": {"language": new_lang}})
        return {"ok": True, "language": new_lang}

    return router, get_current_user
