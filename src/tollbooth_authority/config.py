"""Configuration via pydantic-settings. Loaded at runtime, never at import time."""

from __future__ import annotations

from pydantic_settings import BaseSettings

from tollbooth import ToolPricing


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

    # Tier config (VIP multipliers for operator tax balances)
    btcpay_tier_config: str | None = None
    btcpay_user_tiers: str | None = None

    # Tax parameters
    tax_rate_percent: float = 2.0
    tax_min_sats: int = 10

    # Upstream Authority chain — revenue backflow
    # When an operator buys cert_sats from this Authority, fire a % payout
    # to the upstream Authority's Lightning Address. Empty = Prime Authority.
    upstream_authority_address: str = ""
    upstream_tax_percent: float = 2.0
    upstream_tax_min_sats: int = 10

    # Certificate TTL
    certificate_ttl_seconds: int = 600

    # DPYC Nostr Identity
    dpyc_authority_npub: str = ""
    dpyc_upstream_authority_npub: str = ""

    # NeonVault (replaces TheBrainVault for ledger persistence)
    neon_database_url: str = ""

    # Nostr audit (optional — enabled when all 3 are set)
    tollbooth_nostr_audit_enabled: str = ""
    tollbooth_nostr_operator_nsec: str = ""
    tollbooth_nostr_relays: str = ""

    # DPYC Registry enforcement
    dpyc_registry_url: str = "https://raw.githubusercontent.com/lonniev/dpyc-community/main/members.json"
    dpyc_registry_cache_ttl_seconds: int = 300
    dpyc_enforce_membership: bool = False  # opt-in; safe default

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def certify_pricing(self) -> ToolPricing:
        """ToolPricing instance built from existing tax env vars."""
        return ToolPricing(
            rate_percent=self.tax_rate_percent,
            rate_param="amount_sats",
            min_cost=self.tax_min_sats,
        )
