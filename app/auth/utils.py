import datetime
from functools import wraps

import jwt
from flask import current_app, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

ROLE_STUDENT = "student"
ROLE_INDUSTRY = "industry"
ALL_ROLES = {ROLE_STUDENT, ROLE_INDUSTRY}


def hash_password(password):
    return generate_password_hash(password)


def verify_password(password, password_hash):
    return check_password_hash(password_hash, password)


def _encode(payload, expires_delta):
    now = datetime.datetime.utcnow()
    payload = {**payload, "iat": now, "exp": now + expires_delta}
    return jwt.encode(payload, current_app.config["JWT_SECRET"], algorithm="HS256")


def generate_access_token(user_id, role):
    minutes = current_app.config["JWT_ACCESS_TOKEN_EXPIRES_MIN"]
    return _encode({"sub": user_id, "role": role, "type": "access"}, datetime.timedelta(minutes=minutes))


def generate_refresh_token(user_id):
    days = current_app.config["JWT_REFRESH_TOKEN_EXPIRES_DAYS"]
    return _encode({"sub": user_id, "type": "refresh"}, datetime.timedelta(days=days))


def decode_token(token):
    return jwt.decode(token, current_app.config["JWT_SECRET"], algorithms=["HS256"])


def _extract_token_from_header():
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return None


def token_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _extract_token_from_header()
        if not token:
            return jsonify({"error": "missing bearer token"}), 401
        try:
            payload = decode_token(token)
            if payload.get("type") != "access":
                raise jwt.InvalidTokenError("wrong token type")
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "invalid token"}), 401

        request.user = {"id": payload["sub"], "role": payload["role"]}
        return fn(*args, **kwargs)

    return wrapper


def roles_required(*allowed_roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if request.user["role"] not in allowed_roles:
                return jsonify({"error": "forbidden: insufficient role"}), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator
