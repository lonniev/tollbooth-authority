"""JWT claims builder for purchase order certificates."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


def create_certificate_claims(
    operator_id: str,
    amount_sats: int,
    tax_sats: int,
    ttl_seconds: int = 600,
    authority_npub: str = "",
) -> dict:
    """Build JWT claims for a certified purchase order.

    Returns a dict suitable for passing to ``AuthoritySigner.sign_certificate``.
    When *authority_npub* is non-empty it is included as a claim.
    """
    now = datetime.now(timezone.utc)
    claims = {
        "jti": str(uuid.uuid4()),
        "sub": operator_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "amount_sats": amount_sats,
        "tax_paid_sats": tax_sats,
        "net_sats": amount_sats - tax_sats,
        "dpyc_protocol": "dpyp-01-base-certificate",
    }
    if authority_npub:
        claims["authority_npub"] = authority_npub
    return claims
