"""Minimal CSRF protection via signed cookie + form field."""
import hmac
import hashlib
import os
import secrets
from fastapi import Request, HTTPException

_SECRET = os.getenv("SECRET_KEY", "dev-only-brightloop-change-me")


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def sign_token(token: str) -> str:
    sig = hmac.new(_SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()
    return f"{token}.{sig}"


def verify_token(signed: str) -> bool:
    if not signed or "." not in signed:
        return False
    token, sig = signed.rsplit(".", 1)
    expected = hmac.new(_SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def get_csrf_from_request(request: Request) -> str:
    return request.cookies.get("csrf_token", "")


def require_csrf(request: Request, form_token: str = None):
    cookie = get_csrf_from_request(request)
    if not cookie or not verify_token(cookie):
        raise HTTPException(403, "Invalid CSRF token")
    if form_token is not None and not hmac.compare_digest(cookie, form_token):
        raise HTTPException(403, "CSRF token mismatch")
