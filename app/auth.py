import os
import bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
APP_PASSWORD_HASH = os.getenv("APP_PASSWORD_HASH", "")
COOKIE_NAME = "session_token"
REMEMBER_ME_DAYS = 30

serializer = URLSafeTimedSerializer(SECRET_KEY)


def verify_password(plain_password: str) -> bool:
    if not APP_PASSWORD_HASH:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), APP_PASSWORD_HASH.encode("utf-8"))
    except Exception:
        return False


def create_session_token() -> str:
    return serializer.dumps({"authenticated": True})


def verify_session_token(token: str, max_age: int = 30 * 24 * 3600) -> bool:
    try:
        data = serializer.loads(token, max_age=max_age)
        return data.get("authenticated") is True
    except (BadSignature, SignatureExpired):
        return False


def get_current_user(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    return verify_session_token(token)


def require_auth(request: Request):
    if not get_current_user(request):
        raise HTTPException(status_code=302, headers={"Location": "/login"})
