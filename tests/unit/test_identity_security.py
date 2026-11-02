from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from medicalbooking.modules.identity.infrastructure.security import (
    Argon2PasswordHasher,
    JwtTokenIssuer,
)
from medicalbooking.shared.domain.errors import ServiceUnavailable, Unauthenticated

KEY = "k" * 32
USER = str(uuid.UUID("33333333-3333-3333-3333-333333333333"))


def test_argon2id_hash_verifies_and_rejects_wrong_password() -> None:
    hasher = Argon2PasswordHasher()

    encoded = hasher.hash("correct-horse-battery")

    assert encoded.startswith("$argon2id$")
    assert hasher.verify(encoded, "correct-horse-battery")
    assert not hasher.verify(encoded, "wrong-password-value")


def test_hasher_treats_malformed_hashes_as_mismatch() -> None:
    assert Argon2PasswordHasher().verify("not-a-hash", "anything") is False


def test_token_round_trip_returns_subject() -> None:
    issuer = JwtTokenIssuer(KEY, ttl_minutes=15)

    token, expires_in = issuer.issue(USER)

    assert expires_in == 900
    assert issuer.subject_of(token) == USER


def test_expired_token_is_rejected() -> None:
    past = datetime(2026, 1, 1, tzinfo=UTC)
    issuer = JwtTokenIssuer(KEY, ttl_minutes=1, clock=lambda: past)
    token, _ = issuer.issue(USER)

    with pytest.raises(Unauthenticated):
        JwtTokenIssuer(KEY, ttl_minutes=1).subject_of(token)


def test_token_for_another_audience_is_rejected() -> None:
    forged = jwt.encode(
        {
            "sub": USER,
            "iss": JwtTokenIssuer.ISSUER,
            "aud": "some-other-service",
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        KEY,
        algorithm="HS256",
    )

    with pytest.raises(Unauthenticated):
        JwtTokenIssuer(KEY, ttl_minutes=15).subject_of(forged)


def test_unsigned_alg_none_token_is_rejected() -> None:
    unsigned = jwt.encode(
        {
            "sub": USER,
            "iss": JwtTokenIssuer.ISSUER,
            "aud": JwtTokenIssuer.AUDIENCE,
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        key="",
        algorithm="none",
    )

    with pytest.raises(Unauthenticated):
        JwtTokenIssuer(KEY, ttl_minutes=15).subject_of(unsigned)


def test_token_signed_with_a_different_key_is_rejected() -> None:
    token, _ = JwtTokenIssuer("z" * 32, ttl_minutes=15).issue(USER)

    with pytest.raises(Unauthenticated):
        JwtTokenIssuer(KEY, ttl_minutes=15).subject_of(token)


def test_non_uuid_subject_is_rejected() -> None:
    token = jwt.encode(
        {
            "sub": "not-a-uuid",
            "iss": JwtTokenIssuer.ISSUER,
            "aud": JwtTokenIssuer.AUDIENCE,
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        KEY,
        algorithm="HS256",
    )

    with pytest.raises(Unauthenticated):
        JwtTokenIssuer(KEY, ttl_minutes=15).subject_of(token)


def test_issuing_without_a_configured_key_is_unavailable_not_insecure() -> None:
    with pytest.raises(ServiceUnavailable):
        JwtTokenIssuer(None, ttl_minutes=15).issue(USER)
