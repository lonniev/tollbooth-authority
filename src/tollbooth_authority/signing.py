"""JWT signing with EdDSA (Ed25519) and key loading."""

from __future__ import annotations

import base64

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class AuthoritySigner:
    """Signs JWT certificates using an Ed25519 private key."""

    def __init__(self, private_key_b64: str) -> None:
        """Load Ed25519 private key from base64-encoded PEM."""
        pem_bytes = base64.b64decode(private_key_b64)
        self._private_key: Ed25519PrivateKey = serialization.load_pem_private_key(  # type: ignore[assignment]
            pem_bytes, password=None
        )

    @property
    def public_key_pem(self) -> str:
        """PEM-encoded public key (for hardcoding in tollbooth-dpyc)."""
        return self._private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    def sign_certificate(self, claims: dict) -> str:
        """Sign claims as a JWT with EdDSA. Returns compact JWT string."""
        return jwt.encode(claims, self._private_key, algorithm="EdDSA")


def verify_certificate(token: str, public_key_pem: str) -> dict:
    """Verify and decode a JWT signed with EdDSA.

    Raises jwt.InvalidTokenError on failure.
    Checks ``exp`` automatically. Returns decoded claims dict.
    """
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    public_key = load_pem_public_key(public_key_pem.encode())
    return jwt.decode(token, public_key, algorithms=["EdDSA"])
