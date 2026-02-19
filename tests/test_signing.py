"""Tests for JWT EdDSA signing and verification."""

from __future__ import annotations

import base64
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from tollbooth_authority.signing import AuthoritySigner, verify_certificate


def _make_key_b64() -> str:
    """Generate a fresh Ed25519 key and return its base64-encoded PEM."""
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return base64.b64encode(pem).decode()


@pytest.fixture
def signer() -> AuthoritySigner:
    return AuthoritySigner(_make_key_b64())


def test_sign_and_verify_round_trip(signer: AuthoritySigner):
    claims = {"sub": "op-1", "amount_sats": 1000, "exp": int(time.time()) + 300}
    token = signer.sign_certificate(claims)
    decoded = verify_certificate(token, signer.public_key_pem)
    assert decoded["sub"] == "op-1"
    assert decoded["amount_sats"] == 1000


def test_wrong_key_rejects():
    signer_a = AuthoritySigner(_make_key_b64())
    signer_b = AuthoritySigner(_make_key_b64())
    claims = {"sub": "op-1", "exp": int(time.time()) + 300}
    token = signer_a.sign_certificate(claims)
    with pytest.raises(jwt.InvalidSignatureError):
        verify_certificate(token, signer_b.public_key_pem)


def test_expired_jwt_rejects(signer: AuthoritySigner):
    claims = {"sub": "op-1", "exp": int(time.time()) - 10}
    token = signer.sign_certificate(claims)
    with pytest.raises(jwt.ExpiredSignatureError):
        verify_certificate(token, signer.public_key_pem)


def test_corrupt_token_rejects(signer: AuthoritySigner):
    with pytest.raises(jwt.DecodeError):
        verify_certificate("not.a.jwt", signer.public_key_pem)


def test_public_key_pem_format(signer: AuthoritySigner):
    pem = signer.public_key_pem
    assert pem.startswith("-----BEGIN PUBLIC KEY-----")
    assert pem.strip().endswith("-----END PUBLIC KEY-----")
