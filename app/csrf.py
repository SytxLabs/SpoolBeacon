import hmac
import os

from quart import session


def generate_csrf_token() -> str:
    if "_csrf_token" not in session:
        session["_csrf_token"] = os.urandom(32).hex()
    return session["_csrf_token"]


def validate_csrf(token: str | None) -> bool:
    expected = session.get("_csrf_token")
    if not expected or not token:
        return False
    return hmac.compare_digest(expected, token)
