"""Password hashing only; plaintext is never persisted in application records."""

import base64
import hashlib
import hmac
import secrets

ROUNDS = 260_000


def password_hash(password: str) -> str:
    if not 1 <= len(password) <= 128:
        raise ValueError("调用密码长度须为 1 至 128 个字符。")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ROUNDS)
    return f"pbkdf2_sha256${ROUNDS}${base64.urlsafe_b64encode(salt).decode()}${digest.hex()}"


def parse_hash(encoded: str):
    try:
        algorithm, rounds, salt, digest = encoded.split("$")
        iterations = int(rounds)
        raw_salt = base64.b64decode(salt, altchars=b"-_", validate=True)
        raw_digest = bytes.fromhex(digest)
        if (
            algorithm != "pbkdf2_sha256"
            or not 200_000 <= iterations <= 1_000_000
            or len(raw_salt) != 16
            or len(raw_digest) != 32
        ):
            return None
        return iterations, raw_salt, raw_digest
    except (ValueError, TypeError):
        return None


def verify_password(password: str, encoded: str) -> bool:
    parsed = parse_hash(encoded)
    if parsed is None or not 1 <= len(password) <= 128:
        return False
    iterations, salt, expected = parsed
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return hmac.compare_digest(actual, expected)
