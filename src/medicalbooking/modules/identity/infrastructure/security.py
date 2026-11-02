"""Credential hashing and access tokens (infrastructure adapters)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from medicalbooking.shared.domain.errors import ServiceUnavailable, Unauthenticated


class Argon2PasswordHasher:
    """Argon2id with library defaults (memory-hard, tuned by the argon2-cffi project)."""

    def __init__(self) -> None:
        self._hasher = _Argon2()

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False


class JwtTokenIssuer:
    """Short-lived HS256 access tokens. The algorithm is fixed, never read from the token."""

    ALGORITHM = "HS256"
    ISSUER = "medicalbooking"
    AUDIENCE = "medicalbooking-api"

    def __init__(
        self,
        signing_key: str | None,
        ttl_minutes: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._key = signing_key
        self._ttl = timedelta(minutes=ttl_minutes)
        self._clock = clock

    def issue(self, user_id: str) -> tuple[str, int]:
        key = self._require_key()
        now = self._clock()
        payload = {
            "sub": user_id,
            "iss": self.ISSUER,
            "aud": self.AUDIENCE,
            "iat": int(now.timestamp()),
            "exp": int((now + self._ttl).timestamp()),
            "jti": str(uuid.uuid4()),
        }
        token = jwt.encode(payload, key, algorithm=self.ALGORITHM)
        return token, int(self._ttl.total_seconds())

    def subject_of(self, token: str) -> str:
        key = self._require_key()
        try:
            payload = jwt.decode(
                token,
                key,
                algorithms=[self.ALGORITHM],
                audience=self.AUDIENCE,
                issuer=self.ISSUER,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
            subject = str(uuid.UUID(str(payload["sub"])))
        except (jwt.PyJWTError, KeyError, ValueError, TypeError):
            raise Unauthenticated("Authentication required") from None
        return subject

    def _require_key(self) -> str:
        if not self._key:
            raise ServiceUnavailable("Authentication is not configured")
        return self._key
