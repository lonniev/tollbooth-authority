#!/usr/bin/env python3
"""Generate an Ed25519 keypair for Tollbooth Authority JWT signing.

Outputs:
  1. Base64-encoded PEM private key (for AUTHORITY_SIGNING_KEY env var)
  2. PEM public key (for hardcoding in tollbooth-dpyc)
  3. Verification round-trip test
"""

import base64
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


def main() -> None:
    # Generate keypair
    private_key = Ed25519PrivateKey.generate()

    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )

    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    private_b64 = base64.b64encode(private_pem).decode()

    print("=" * 60)
    print("AUTHORITY_SIGNING_KEY (base64-encoded PEM private key):")
    print("=" * 60)
    print(private_b64)
    print()
    print("=" * 60)
    print("Public Key PEM (for hardcoding in tollbooth-dpyc):")
    print("=" * 60)
    print(public_pem.decode())

    # Verification round-trip
    import jwt
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    claims = {"sub": "test", "exp": int(time.time()) + 300}
    token = jwt.encode(claims, private_key, algorithm="EdDSA")
    pub = load_pem_public_key(public_pem)
    decoded = jwt.decode(token, pub, algorithms=["EdDSA"])
    assert decoded["sub"] == "test"
    print("Round-trip verification: PASSED")


if __name__ == "__main__":
    main()
