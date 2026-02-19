"""Configuration via pydantic-settings. Loaded at runtime, never at import time."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class AuthoritySettings(BaseSettings):
    """All env vars for the Tollbooth Authority service."""

    # Ed25519 signing key — base64-encoded PEM private key
    authority_signing_key: str = ""

    # Authority's own BTCPay (for collecting operator tax)
    btcpay_host: str = ""
    btcpay_store_id: str = ""
    btcpay_api_key: str = ""

    # TheBrain vault for operator ledger persistence
    thebrain_api_key: str = ""
    thebrain_vault_brain_id: str = ""
    thebrain_vault_home_id: str = ""

    # Tax parameters
    tax_rate_percent: float = 2.0
    tax_min_sats: int = 10

    # Certificate TTL
    certificate_ttl_seconds: int = 600

    model_config = {"env_file": ".env", "extra": "ignore"}
