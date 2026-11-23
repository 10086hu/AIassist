"""Versioned scrypt password hashes using Python's OpenSSL-backed implementation."""
import hashlib
import hmac
import secrets


# OWASP's 32 MiB configuration. The extra p cost trades CPU for lower memory.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 32768, 8, 3


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        maxmem=64 * 1024 * 1024, dklen=32,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        # Only the supported version is accepted; malformed DB values cannot
        # request unbounded CPU/memory from OpenSSL.
        if (algorithm, int(n), int(r), int(p)) != ("scrypt", SCRYPT_N, SCRYPT_R, SCRYPT_P):
            return False
        if len(salt) != 32 or len(expected) != 64:
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt),
            n=int(n), r=int(r), p=int(p), maxmem=64 * 1024 * 1024, dklen=32,
        )
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


# Unknown accounts still perform a real password verification.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))
