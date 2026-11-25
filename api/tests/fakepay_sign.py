import hashlib
import hmac
import time


def sign(secret: str, body: bytes, timestamp: int | None = None) -> str:
    t = int(timestamp or time.time())
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"
