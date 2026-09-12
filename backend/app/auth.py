from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import AuthAttempt

PERSONAS = {"Dachi", "Lui", "Saba"}
ph = PasswordHasher()


def _serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    return URLSafeTimedSerializer(settings.session_secret, salt="instatrack-session-v1")


def verify_password(password: str) -> bool:
    password_hash = get_settings().team_password_hash
    if not password_hash:
        return False
    try:
        return ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_token(persona: str | None = None) -> str:
    return _serializer().dumps({"authenticated": True, "persona": persona})


def read_token(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=get_settings().session_ttl_seconds)
        return data if data.get("authenticated") is True else None
    except (BadSignature, SignatureExpired):
        return None


def require_session(request: Request) -> dict:
    session = read_token(request.cookies.get(get_settings().session_cookie_name))
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return session


def require_persona(session: dict = Depends(require_session)) -> str:
    persona = session.get("persona")
    if persona not in PERSONAS:
        raise HTTPException(status_code=status.HTTP_428_PRECONDITION_REQUIRED, detail="Choose a team profile")
    return persona


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    raw = forwarded or (request.client.host if request.client else "unknown")
    return hashlib.sha256(raw.encode()).hexdigest()


def enforce_login_limit(db: Session, key: str) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    failed = db.scalar(select(func.count(AuthAttempt.id)).where(AuthAttempt.client_key == key, AuthAttempt.succeeded.is_(False), AuthAttempt.attempted_at >= cutoff)) or 0
    if failed >= 8:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many login attempts. Try again later.")


def record_login_attempt(db: Session, key: str, succeeded: bool) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=2)
    db.execute(delete(AuthAttempt).where(AuthAttempt.attempted_at < cutoff))
    db.add(AuthAttempt(client_key=key, succeeded=succeeded))
    db.commit()
