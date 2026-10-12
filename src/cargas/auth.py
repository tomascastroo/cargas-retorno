"""Contraseñas (scrypt, biblioteca estándar) y tokens firmados con HMAC-SHA256.

Token: base64url(json{uid, cid, exp}).firma. Sin dependencias externas; reemplazable por
OAuth/OIDC sin tocar la lógica de negocio (sólo `current_user` en api/deps.py).
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass

from cargas.config import get_settings

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    return hmac.compare_digest(digest.hex(), digest_hex)


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    company_id: uuid.UUID


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(payload: str) -> str:
    key = get_settings().secret_key.get_secret_value().encode()
    return _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())


def create_token(user_id: uuid.UUID, company_id: uuid.UUID) -> str:
    exp = int(time.time()) + get_settings().token_ttl_hours * 3600
    payload = _b64(json.dumps({"uid": str(user_id), "cid": str(company_id), "exp": exp}).encode())
    return f"{payload}.{_sign(payload)}"


def decode_token(token: str) -> TokenClaims | None:
    try:
        payload, signature = token.split(".")
        if not hmac.compare_digest(signature, _sign(payload)):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if data["exp"] < time.time():
            return None
        return TokenClaims(uuid.UUID(data["uid"]), uuid.UUID(data["cid"]))
    except (ValueError, KeyError, TypeError):
        return None
