"""FastMCP app — Tollbooth Authority service with 7 MCP tools."""

from __future__ import annotations

import asyncio
import importlib.metadata
import logging
import platform
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import Field

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

from tollbooth import BTCPayClient, LedgerCache
from tollbooth.slug_tools import make_slug_tool
from tollbooth.tools.credits import (
    check_balance_tool,
    check_payment_tool,
    direct_purchase_tool,
    reconcile_pending_invoices,
)
from tollbooth.vaults import TheBrainVault

from tollbooth_authority import __version__
from tollbooth_authority.config import AuthoritySettings
from tollbooth_authority.nostr_signing import AuthorityNostrSigner
from tollbooth_authority.onboarding import (
    OnboardingState,
    ONBOARDING_TEMPLATES,
)
from tollbooth_authority.registry import DEFAULT_REGISTRY_URL, DPYCRegistry, RegistryError
from tollbooth_authority.replay import ReplayTracker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastMCP app
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "tollbooth-authority",
    instructions=(
        "Tollbooth Authority — Certified Purchase Order Service.\n\n"
        "The Authority is the institutional backbone of the Tollbooth ecosystem. "
        "It registers MCP operators, collects a small certification fee on every "
        "purchase order via Bitcoin Lightning, and issues Schnorr-signed Nostr event "
        "certificates that prove an operator has paid before collecting a fare from "
        "a user.\n\n"
        "## First-Time Bootstrap (follow these steps in order)\n\n"
        "1. Call `register_operator(npub=...)` with your Nostr npub — creates your "
        "ledger entry. Get your npub from the dpyc-oracle's how_to_join() tool. "
        "Returns your operator_id (npub) and a zero balance.\n"
        "2. Call `purchase_credits` with the number of sats to pre-fund "
        "(e.g., 1000). Returns a Lightning invoice with a checkoutLink.\n"
        "3. Pay the invoice using any Lightning wallet.\n"
        "4. Call `check_payment` with the invoice_id from step 2. "
        "On settlement, your credit balance is funded.\n"
        "5. Call `check_balance` or `operator_status` to confirm your funded balance "
        "and retrieve the Authority's Nostr npub for certificate verification.\n\n"
        "## Fee Computation\n\n"
        "Fee per certification = max(TAX_MIN_SATS, ceil(amount_sats * TAX_RATE_PERCENT / 100)). "
        "Defaults: 2% rate, 10 sat minimum. The fee is deducted from the operator's "
        "pre-funded balance when `certify_credits` is called.\n\n"
        "## Tool Overview\n\n"
        "- `register_operator` — First step. Idempotent; safe to call again.\n"
        "- `purchase_credits` — Creates a Lightning invoice. Call whenever balance is low.\n"
        "- `check_payment` — Polls an invoice. Call after payment; safe to call multiple times.\n"
        "- `check_balance` — Read-only balance check. No side effects.\n"
        "- `operator_status` — Registration info + Authority npub for certificate verification.\n"
        "- `certify_credits` — Core machine-to-machine tool. Deducts fee, returns Schnorr-signed "
        "Nostr event certificate.\n"
        "## Low-Balance Recovery\n\n"
        "If `certify_credits` returns 'Insufficient credit balance', the operator must "
        "fund more credits: call `purchase_credits`, pay, then `check_payment`. "
        "The operator's MCP server should surface this to the admin, not the end user.\n\n"
        "## Key Generation\n\n"
        "The Authority signs certificates with a Nostr nsec/npub keypair. Generate one "
        "using any Nostr key generator (e.g., `nak key generate`). The nsec goes in "
        "`TOLLBOOTH_NOSTR_OPERATOR_NSEC`; the npub is surfaced via `operator_status` "
        "for tollbooth-dpyc verification.\n\n"
        "## Deployment Configuration — Persistence & Tiers\n\n"
        "The Authority's operator credit balances are stored in a persistent vault so "
        "they survive process restarts and redeployments. **The vault is pluggable** — "
        "this reference deployment uses TheBrain as its vault store, but other "
        "implementations could use Redis, SQLite, S3, or any key-value store.\n\n"
        "The env vars below are **specific to the TheBrainVault implementation**. "
        "An Authority using a different vault backend would need different env vars "
        "matching its store's connection requirements. When advising a human operator "
        "on deployment, always check which vault implementation the Authority uses "
        "(see `vault.py`) and ask for the corresponding credentials.\n\n"
        "### TheBrainVault env vars (this implementation)\n\n"
        "- `THEBRAIN_API_KEY` — API key for TheBrain cloud (https://api.bra.in)\n"
        "- `THEBRAIN_VAULT_BRAIN_ID` — Brain ID containing the vault home thought\n"
        "- `THEBRAIN_VAULT_HOME_ID` — Thought ID serving as the vault index root\n\n"
        "Without these three vars, the vault is disabled and balances reset on every "
        "restart. All three must be set together.\n\n"
        "### VIP tier env vars (optional)\n\n"
        "- `BTCPAY_TIER_CONFIG` — JSON mapping tier names to multipliers, "
        'e.g., `{"vip": {"multiplier": 100000}}`\n'
        "- `BTCPAY_USER_TIERS` — JSON mapping operator npubs to tier names, "
        'e.g., `{"npub1abc...": "vip"}`\n\n'
        "These grant trusted operators a credit multiplier on purchases. "
        "If unset, all operators receive the default 1x multiplier.\n"
    ),
)
tool = make_slug_tool(mcp, "authority")

# ---------------------------------------------------------------------------
# Settings (deferred — never at import time)
# ---------------------------------------------------------------------------

_settings: AuthoritySettings | None = None
_settings_loaded = False


def _ensure_settings_loaded() -> None:
    global _settings, _settings_loaded
    if not _settings_loaded:
        try:
            _settings = AuthoritySettings()
            _settings_loaded = True
        except Exception as e:
            print(f"Error: Failed to load settings: {e}", file=sys.stderr)
            sys.exit(1)


def _get_settings() -> AuthoritySettings:
    _ensure_settings_loaded()
    assert _settings is not None
    return _settings


# ---------------------------------------------------------------------------
# Singletons (lazy)
# ---------------------------------------------------------------------------

_btcpay_client: BTCPayClient | None = None
_vault: Any = None
_ledger_cache: LedgerCache | None = None
_replay_tracker: ReplayTracker | None = None


_nostr_signer: AuthorityNostrSigner | None = None

_DEFAULT_RELAY = "wss://nostr.wine"
_FALLBACK_POOL = [
    "wss://relay.primal.net",
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.nostr.band",
]


def _resolve_relays(configured: str | None) -> list[str]:
    """Resolve relay list: env var -> default -> probe fallback pool."""
    from tollbooth.nostr_diagnostics import probe_relay_liveness

    if configured:
        relays = [r.strip() for r in configured.split(",") if r.strip()]
    else:
        relays = [_DEFAULT_RELAY]

    results = probe_relay_liveness(relays, timeout=5)
    live = [r["relay"] for r in results if r["connected"]]

    if live:
        logger.info("Relay probe: %d/%d configured relays live", len(live), len(relays))
        return live

    # All configured relays down — probe fallback pool
    logger.warning("All configured relays down (%s), probing fallback pool...", ", ".join(relays))
    fallback_results = probe_relay_liveness(_FALLBACK_POOL, timeout=5)
    fallback_live = [r["relay"] for r in fallback_results if r["connected"]]

    if fallback_live:
        logger.info("Fallback relays live: %s", ", ".join(fallback_live))
        return fallback_live

    # Nothing alive — return configured + fallback and hope for recovery
    logger.warning("No relays responded — using full list, hoping for recovery")
    return relays + _FALLBACK_POOL


def _get_nostr_signer() -> AuthorityNostrSigner:
    """Return the Nostr signer. Raises ValueError if nsec is not configured."""
    global _nostr_signer
    if _nostr_signer is not None:
        return _nostr_signer
    s = _get_settings()
    if not s.tollbooth_nostr_operator_nsec:
        raise ValueError(
            "TOLLBOOTH_NOSTR_OPERATOR_NSEC is required. "
            "Generate a Nostr keypair (e.g., `nak key generate`) and set the nsec."
        )
    _nostr_signer = AuthorityNostrSigner(s.tollbooth_nostr_operator_nsec)
    logger.info("Authority Nostr signer initialized (npub=%s).", _nostr_signer.npub)
    return _nostr_signer


def _get_operator_npub() -> str:
    """Return the Authority's own npub (derived from its nsec)."""
    return _get_nostr_signer().npub


def _get_btcpay() -> BTCPayClient:
    global _btcpay_client
    if _btcpay_client is not None:
        return _btcpay_client
    s = _get_settings()
    if not s.btcpay_host or not s.btcpay_store_id or not s.btcpay_api_key:
        raise ValueError(
            "BTCPay not configured. Set BTCPAY_HOST, BTCPAY_STORE_ID, BTCPAY_API_KEY."
        )
    _btcpay_client = BTCPayClient(s.btcpay_host, s.btcpay_api_key, s.btcpay_store_id)
    logger.info("BTCPay client initialized for tax collection.")
    return _btcpay_client


def _get_vault() -> Any:
    global _vault
    if _vault is not None:
        return _vault
    s = _get_settings()

    # Primary: NeonVault (if configured)
    if s.neon_database_url:
        from tollbooth.vaults import NeonVault

        vault: Any = NeonVault(database_url=s.neon_database_url)
        # ensure_schema is idempotent — safe on every cold start
        try:
            asyncio.ensure_future(vault.ensure_schema())
        except RuntimeError:
            pass  # No running event loop yet (e.g. during test setup)
        logger.info("NeonVault initialized for operator ledger persistence.")
    else:
        # Fallback: TheBrainVault (legacy)
        if not s.thebrain_api_key or not s.thebrain_vault_brain_id or not s.thebrain_vault_home_id:
            raise ValueError(
                "Vault not configured. Set NEON_DATABASE_URL (preferred) "
                "or THEBRAIN_API_KEY + THEBRAIN_VAULT_BRAIN_ID + THEBRAIN_VAULT_HOME_ID (legacy)."
            )
        vault = TheBrainVault(
            api_key=s.thebrain_api_key,
            brain_id=s.thebrain_vault_brain_id,
            home_thought_id=s.thebrain_vault_home_id,
        )
        logger.info("TheBrainVault initialized for operator ledger persistence (legacy fallback).")

    # Optional: Nostr audit decorator
    if s.tollbooth_nostr_audit_enabled == "true":
        from tollbooth.nostr_audit import AuditedVault, NostrAuditPublisher

        audit_relays = _resolve_relays(s.tollbooth_nostr_relays or None)
        publisher = NostrAuditPublisher(
            operator_nsec=s.tollbooth_nostr_operator_nsec,
            relays=audit_relays,
        )
        vault = AuditedVault(vault, publisher)
        logger.info("Nostr audit enabled — publishing to %s", ", ".join(audit_relays))

    _vault = vault
    return _vault


def _get_ledger_cache() -> LedgerCache:
    global _ledger_cache
    if _ledger_cache is not None:
        return _ledger_cache
    vault = _get_vault()
    _ledger_cache = LedgerCache(vault)
    try:
        asyncio.ensure_future(_ledger_cache.start_background_flush())
    except RuntimeError:
        pass
    _register_shutdown_handlers()
    logger.info("Ledger cache initialized.")
    return _ledger_cache


def _get_replay_tracker() -> ReplayTracker:
    global _replay_tracker
    if _replay_tracker is not None:
        return _replay_tracker
    s = _get_settings()
    _replay_tracker = ReplayTracker(ttl_seconds=s.certificate_ttl_seconds)
    return _replay_tracker


# ---------------------------------------------------------------------------
# Authority onboarding (curator npub + Nostr DM challenge-response)
# ---------------------------------------------------------------------------

_onboarding = OnboardingState()
_config_vault: Any = None
_cached_authority_npub: str | None = None


def _get_config_vault() -> Any:
    """Return a NeonVault for authority_config reads/writes, or None."""
    global _config_vault
    if _config_vault is not None:
        return _config_vault
    s = _get_settings()
    if not s.neon_database_url:
        return None
    from tollbooth.vaults import NeonVault
    _config_vault = NeonVault(database_url=s.neon_database_url)
    return _config_vault


# ---------------------------------------------------------------------------
# Pricing model store singleton
# ---------------------------------------------------------------------------

_pricing_store: Any = None


def _get_pricing_store() -> Any:
    global _pricing_store
    if _pricing_store is not None:
        return _pricing_store
    vault = _get_config_vault()
    if vault is None:
        raise RuntimeError("Pricing model store requires NEON_DATABASE_URL")
    from tollbooth.pricing_store import PricingModelStore

    _pricing_store = PricingModelStore(neon_vault=vault)
    try:
        asyncio.ensure_future(_pricing_store.ensure_schema())
    except RuntimeError:
        pass
    return _pricing_store


# ---------------------------------------------------------------------------
# Pricing resolver singleton
# ---------------------------------------------------------------------------

_pricing_resolver: Any = None


async def _get_pricing_resolver() -> Any:
    global _pricing_resolver
    if _pricing_resolver is not None:
        return _pricing_resolver
    from tollbooth.pricing_resolver import PricingResolver
    from tollbooth_authority.default_pricing import build_default_model

    try:
        store = _get_pricing_store()
        nostr_signer = _get_nostr_signer()
        _pricing_resolver = PricingResolver(
            store=store,
            operator=nostr_signer.npub,
        )
    except RuntimeError:
        # No Neon — use default pricing model directly
        default = build_default_model()
        _pricing_resolver = _DefaultPricingResolver(default)
    return _pricing_resolver


class _DefaultPricingResolver:
    """Minimal resolver backed by the default pricing model (no Neon)."""

    def __init__(self, model: Any) -> None:
        self._model = model

    async def get_tool_pricing(self, tool_name: str) -> Any:
        from tollbooth.pricing import ToolPricing

        for tp in self._model.tools:
            if tp.tool_name == tool_name:
                return tp.to_tool_pricing()
        return ToolPricing()


async def _get_authority_npub() -> str | None:
    """Read the curator npub: NeonVault → env var → None."""
    global _cached_authority_npub
    if _cached_authority_npub is not None:
        return _cached_authority_npub
    vault = _get_config_vault()
    if vault is not None:
        try:
            npub = await vault.get_config("authority_npub")
            if npub:
                _cached_authority_npub = npub
                return npub
        except Exception:
            pass
    import os
    npub = os.environ.get("DPYC_AUTHORITY_NPUB")
    if npub:
        _cached_authority_npub = npub
    return npub


async def _set_authority_npub(npub: str) -> None:
    """Persist the curator npub to NeonVault and update cache."""
    global _cached_authority_npub
    vault = _get_config_vault()
    if vault is not None:
        await vault.set_config("authority_npub", npub)
    _cached_authority_npub = npub


def _get_nostr_exchange() -> Any:
    """Create a NostrCredentialExchange for onboarding DMs."""
    from tollbooth.nostr_credentials import NostrCredentialExchange

    s = _get_settings()
    if not s.tollbooth_nostr_operator_nsec:
        raise ValueError(
            "TOLLBOOTH_NOSTR_OPERATOR_NSEC is required for Authority onboarding."
        )
    relays = _resolve_relays(s.tollbooth_nostr_relays or None)
    return NostrCredentialExchange(
        nsec=s.tollbooth_nostr_operator_nsec,
        relays=relays,
        templates=ONBOARDING_TEMPLATES,
        credential_vault=None,  # no caching for onboarding DMs
    )


async def _resolve_prime_npub() -> str:
    """Find the Prime Authority's npub from the DPYC registry."""
    s = _get_settings()
    registry = DPYCRegistry(
        url=DEFAULT_REGISTRY_URL,
        cache_ttl_seconds=s.dpyc_registry_cache_ttl_seconds,
    )
    try:
        members = await registry._fetch()
        for m in members:
            if m.get("role") == "prime_authority" and m.get("status") == "active":
                return m["npub"]
        raise ValueError("No active Prime Authority found in registry.")
    finally:
        await registry.close()


async def _resolve_own_service_url() -> str:
    """Resolve this Authority's service URL from the DPYC registry."""
    signer = _get_nostr_signer()
    s = _get_settings()
    registry = DPYCRegistry(
        url=DEFAULT_REGISTRY_URL,
        cache_ttl_seconds=s.dpyc_registry_cache_ttl_seconds,
    )
    try:
        member = await registry.check_membership(signer.npub)
        services = member.get("services", [])
        if services:
            return services[0]["url"]
        raise ValueError(
            f"Authority {signer.npub[:16]}... has no services registered."
        )
    except RegistryError:
        raise ValueError(
            f"Authority {signer.npub[:16]}... not found in DPYC registry. "
            "Register as a member first."
        )
    finally:
        await registry.close()


async def _register_via_oracle(
    authority_npub: str,
    display_name: str,
    service_url: str,
    upstream_authority_npub: str,
) -> str:
    """Call the Oracle's register_authority tool via MCP-to-MCP."""
    from tollbooth.registry import resolve_oracle_service

    signer = _get_nostr_signer()
    oracle_info = await resolve_oracle_service(signer.npub)
    oracle_url = oracle_info["url"]

    from fastmcp import Client

    async with Client(oracle_url, auth="oauth") as client:
        result = await client.call_tool(
            "register_authority",
            {
                "authority_npub": authority_npub,
                "display_name": display_name,
                "service_url": service_url,
                "upstream_authority_npub": upstream_authority_npub,
            },
        )
        # Parse CallToolResult
        if hasattr(result, "data") and result.data:
            return result.data.get("commit_url", "")
        if hasattr(result, "content"):
            for block in result.content:
                if hasattr(block, "text"):
                    import json
                    try:
                        data = json.loads(block.text)
                        return data.get("commit_url", "")
                    except (json.JSONDecodeError, TypeError):
                        return block.text
        return str(result)


async def _register_operator_via_oracle(
    operator_npub: str,
    display_name: str,
    service_url: str,
    authority_npub: str,
) -> str:
    """Call the Oracle's register_operator tool via MCP-to-MCP."""
    from tollbooth.registry import resolve_oracle_service

    signer = _get_nostr_signer()
    oracle_info = await resolve_oracle_service(signer.npub)
    oracle_url = oracle_info["url"]

    from fastmcp import Client

    async with Client(oracle_url, auth="oauth") as client:
        result = await client.call_tool(
            "register_operator",
            {
                "operator_npub": operator_npub,
                "display_name": display_name,
                "service_url": service_url,
                "authority_npub": authority_npub,
            },
        )
        if hasattr(result, "content"):
            for block in result.content:
                if hasattr(block, "text"):
                    import json
                    try:
                        data = json.loads(block.text)
                        return data.get("commit_url", "")
                    except (json.JSONDecodeError, TypeError):
                        return block.text
        return str(result)


async def _update_operator_via_oracle(
    operator_npub: str,
    service_url: str,
    display_name: str,
    authority_npub: str,
) -> str:
    """Call the Oracle's update_operator tool via MCP-to-MCP."""
    from tollbooth.registry import resolve_oracle_service

    signer = _get_nostr_signer()
    oracle_info = await resolve_oracle_service(signer.npub)
    oracle_url = oracle_info["url"]

    from fastmcp import Client

    args: dict = {"operator_npub": operator_npub, "authority_npub": authority_npub}
    if service_url:
        args["service_url"] = service_url
    if display_name:
        args["display_name"] = display_name

    async with Client(oracle_url, auth="oauth") as client:
        result = await client.call_tool("update_operator", args)
        if hasattr(result, "content"):
            for block in result.content:
                if hasattr(block, "text"):
                    import json
                    try:
                        data = json.loads(block.text)
                        return data.get("commit_url", block.text)
                    except (json.JSONDecodeError, TypeError):
                        return block.text
        return str(result)


async def _deregister_operator_via_oracle(
    operator_npub: str,
    authority_npub: str,
) -> str:
    """Call the Oracle's deregister_operator tool via MCP-to-MCP."""
    from tollbooth.registry import resolve_oracle_service

    signer = _get_nostr_signer()
    oracle_info = await resolve_oracle_service(signer.npub)
    oracle_url = oracle_info["url"]

    from fastmcp import Client

    async with Client(oracle_url, auth="oauth") as client:
        result = await client.call_tool(
            "deregister_operator",
            {
                "operator_npub": operator_npub,
                "authority_npub": authority_npub,
            },
        )
        if hasattr(result, "content"):
            for block in result.content:
                if hasattr(block, "text"):
                    import json
                    try:
                        data = json.loads(block.text)
                        return data.get("commit_url", block.text)
                    except (json.JSONDecodeError, TypeError):
                        return block.text
        return str(result)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _get_current_user_id() -> str | None:
    try:
        headers = get_http_headers(include_all=True)
        return headers.get("fastmcp-cloud-user")
    except Exception:
        return None


def _require_user_id() -> str:
    user_id = _get_current_user_id()
    if not user_id:
        raise ValueError(
            "Cannot identify user. This tool requires FastMCP Cloud authentication."
        )
    return user_id


# ---------------------------------------------------------------------------
# DPYC identity (npub-primary: npub is the sole credit identity)
# ---------------------------------------------------------------------------
#
# The npub is the SOLE identity for all ledger/credit operations.
# Horizon OAuth is the transport auth layer (gates access to the MCP)
# but DOES NOT determine which npub is acting.
#
_dpyc_registry: DPYCRegistry | None = None


def _resolve_npub(npub: str) -> str:
    """Validate and return the npub. Falls back to operator's own npub if empty."""
    if not npub or not npub.startswith("npub1") or len(npub) < 60:
        return _get_operator_npub()
    return npub


def _get_dpyc_registry() -> DPYCRegistry | None:
    """Return a DPYCRegistry if enforcement is enabled, else None."""
    global _dpyc_registry
    s = _get_settings()
    if not s.dpyc_enforce_membership:
        return None
    if _dpyc_registry is None:
        _dpyc_registry = DPYCRegistry(
            url=DEFAULT_REGISTRY_URL,
            cache_ttl_seconds=s.dpyc_registry_cache_ttl_seconds,
        )
    return _dpyc_registry


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

_shutdown_triggered = False
_reconciled_users: set[str] = set()


async def _graceful_shutdown() -> None:
    global _shutdown_triggered, _ledger_cache, _btcpay_client, _vault, _dpyc_registry
    if _shutdown_triggered:
        return
    _shutdown_triggered = True

    if _ledger_cache is not None:
        dirty = _ledger_cache.dirty_count
        logger.info("Graceful shutdown: flushing %d dirty entries...", dirty)
        try:
            await asyncio.wait_for(
                _shutdown_flush_and_stop(), timeout=8.0
            )
        except asyncio.TimeoutError:
            logger.error("Graceful shutdown timed out after 8s — some entries may be lost.")
        _ledger_cache = None

    if _btcpay_client is not None:
        await _btcpay_client.close()
        _btcpay_client = None

    if _vault is not None:
        _closer = getattr(_vault, "close", None)
        if _closer is not None:
            await _closer()
        _vault = None

    if _dpyc_registry is not None:
        await _dpyc_registry.close()
        _dpyc_registry = None

    pass  # session cache removed


async def _shutdown_flush_and_stop() -> None:
    """Flush and stop the ledger cache (extracted for wait_for wrapping)."""
    assert _ledger_cache is not None
    flushed = await _ledger_cache.flush_all()
    await _ledger_cache.stop()
    logger.info("Shutdown: flushed %d entries.", flushed)


def _register_shutdown_handlers() -> None:
    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig, lambda: asyncio.ensure_future(_graceful_shutdown())
            )
    except (RuntimeError, NotImplementedError):
        pass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@tool
async def register_operator(
    npub: Annotated[
        str,
        Field(
            description=(
                "Your Nostr public key in bech32 format (npub1...). "
                "This becomes your persistent operator identity for all "
                "ledger operations. Get one from the dpyc-oracle's how_to_join() tool."
            ),
        ),
    ] = "",
    service_url: Annotated[
        str,
        Field(
            description=(
                "The operator's public MCP endpoint URL (e.g. "
                "'https://my-service.fastmcp.app/mcp'). Required for "
                "community registry registration."
            ),
        ),
    ] = "",
) -> dict[str, Any]:
    """Provision an operator in the Authority ledger via Horizon OAuth identity.

    This is the **Authority-side** handler for operator registration.
    In the full DPYC flow, a requester sends a Nostr DM delegation
    request to this Authority's npub; the Authority then calls this tool
    to provision the new Operator.  Currently implemented as a direct
    MCP tool call (Horizon OAuth), pending the DM-based approval workflow.

    Not to be confused with **citizen** registration, which the Operator
    handles directly via the Oracle's request_citizenship /
    confirm_citizenship flow — no Authority involvement.

    Creates a ledger entry for the authenticated operator so they can
    purchase credits and certify purchase orders. Idempotent — safe
    to call again if already registered (returns current balance).

    Your DPYC npub (Nostr public key) is required — it serves as your
    persistent identity for all ledger and credit operations. Obtain one from
    the dpyc-oracle's how_to_join() tool if you don't have one yet.

    Returns:
        success: Always True on completion.
        operator_id: Your npub (use this for certify_credits calls).
        balance_sats: Current credit balance (0 for new registrations).
        message: Human-readable confirmation.

    Next step: Call purchase_credits to fund your credit balance.

    Errors: Fails if not authenticated via Horizon (FastMCP Cloud required)
    or if npub is invalid.
    """
    if not npub.startswith("npub1") or len(npub) < 60:
        return {
            "success": False,
            "error": (
                "Invalid npub format. Must start with 'npub1' and be at least 60 characters. "
                "Get your npub from the dpyc-oracle's how_to_join() tool."
            ),
        }

    cache = _get_ledger_cache()
    ledger = await cache.get(npub)
    cache.mark_dirty(npub)
    await cache.flush_user(npub)

    # Provision isolated Neon schema for this operator
    neon_url = ""
    try:
        config_vault = _get_config_vault()
        if config_vault:
            from tollbooth_authority.tenant_provisioner import (
                ensure_bootstrap_table,
                provision_operator_schema,
                store_operator_config,
                neon_url_with_schema,
            )
            await ensure_bootstrap_table(config_vault)
            schema = await provision_operator_schema(config_vault, npub)
            s = _get_settings()
            if s.neon_database_url:
                neon_url = neon_url_with_schema(s.neon_database_url, schema)
                await store_operator_config(config_vault, npub, "neon_database_url", neon_url)
                await store_operator_config(config_vault, npub, "schema", schema)
                logger.info("Provisioned Neon tenant for operator %s schema=%s", npub[:16], schema)

                # Send bootstrap config to operator via Nostr DM
                try:
                    from tollbooth.bootstrap_relay import send_bootstrap_config
                    signer = _get_nostr_signer()
                    sent = send_bootstrap_config(
                        authority_nsec=signer.nsec,
                        operator_npub=npub,
                        config={"neon_database_url": neon_url, "schema": schema},
                    )
                    if sent:
                        logger.info("Bootstrap config DM sent to operator %s", npub[:16])
                    else:
                        logger.warning("Failed to send bootstrap config DM to operator %s", npub[:16])
                except Exception as dm_exc:
                    logger.warning("Bootstrap config DM failed (non-fatal): %s", dm_exc)

    except Exception as exc:
        logger.warning("Neon tenant provisioning failed (non-fatal): %s", exc)

    # Register operator in community registry via Oracle (MCP-to-MCP)
    commit_url = ""
    try:
        signer = _get_nostr_signer()
        commit_url = await _register_operator_via_oracle(
            operator_npub=npub,
            display_name=npub[:16] + "...",
            service_url=service_url,
            authority_npub=signer.npub,
        )
    except Exception as exc:
        logger.warning("Oracle operator registration failed (non-fatal): %s", exc)

    return {
        "success": True,
        "operator_id": npub,
        "balance_sats": ledger.balance_api_sats,
        "dpyc_npub": npub,
        "neon_database_url": neon_url,
        "commit_url": commit_url,
        "message": f"Operator {npub} registered with Authority. Use purchase_credits to fund your balance.",
    }


@tool
async def update_operator(
    npub: Annotated[
        str,
        Field(
            description="Nostr npub of the Operator to update.",
        ),
    ] = "",
    service_url: Annotated[
        str,
        Field(
            description="New MCP endpoint URL (leave empty to keep current).",
        ),
    ] = "",
    display_name: Annotated[
        str,
        Field(
            description="New display name (leave empty to keep current).",
        ),
    ] = "",
) -> dict[str, Any]:
    """Update an existing Operator's community registry entry.

    Use when an Operator moves to a new MCP endpoint or changes its
    display name. Forwards the update to the Oracle via MCP-to-MCP.
    """
    if not npub.startswith("npub1") or len(npub) < 60:
        return {"success": False, "error": "Invalid npub format."}
    if not service_url and not display_name:
        return {"success": False, "error": "Nothing to update. Provide service_url and/or display_name."}

    try:
        signer = _get_nostr_signer()
        commit_url = await _update_operator_via_oracle(
            operator_npub=npub,
            service_url=service_url,
            display_name=display_name,
            authority_npub=signer.npub,
        )
        return {
            "success": True,
            "commit_url": commit_url,
            "message": f"Operator {npub[:16]}... updated in community registry.",
        }
    except Exception as exc:
        logger.warning("Oracle operator update failed: %s", exc)
        return {"success": False, "error": f"Update failed: {exc}"}


@tool
async def deregister_operator(
    npub: Annotated[
        str,
        Field(
            description="Nostr npub of the Operator to deregister.",
        ),
    ] = "",
) -> dict[str, Any]:
    """Remove an Operator from the DPYC community registry.

    An Operator cannot exist without a sponsoring Authority. When an
    Authority disowns an Operator, the Operator is removed from the
    community registry entirely, returning it to initial (unregistered)
    state. Forwards the request to the Oracle via MCP-to-MCP.
    """
    if not npub.startswith("npub1") or len(npub) < 60:
        return {"success": False, "error": "Invalid npub format."}

    try:
        signer = _get_nostr_signer()
        commit_url = await _deregister_operator_via_oracle(
            operator_npub=npub,
            authority_npub=signer.npub,
        )
        return {
            "success": True,
            "commit_url": commit_url,
            "message": f"Operator {npub[:16]}... removed from community registry.",
        }
    except Exception as exc:
        logger.warning("Oracle operator deregistration failed: %s", exc)
        return {"success": False, "error": f"Deregistration failed: {exc}"}


@tool
async def get_operator_config(
    npub: Annotated[
        str,
        Field(description="Your Nostr npub (bech32). Must match the operator_proof signature."),
    ] = "",
    operator_proof: Annotated[
        str,
        Field(description="Schnorr-signed Nostr event (kind 27235) proving npub ownership."),
    ] = "",
) -> dict[str, Any]:
    """Retrieve operator bootstrap configuration (Neon URL, schema).

    Returns the operator's isolated Neon connection string and other
    configuration provisioned during registration. Gated by a Schnorr
    signature proving ownership of the requested npub.

    This is the bootstrap endpoint: an operator with only its nsec can
    call this to retrieve its persistence layer configuration.
    """
    if not npub.startswith("npub1") or len(npub) < 60:
        return {"success": False, "error": "Invalid npub format."}

    # Verify operator proof (Schnorr signature)
    if operator_proof:
        from tollbooth.operator_proof import verify_operator_proof
        if not verify_operator_proof(operator_proof, npub, "get_operator_config"):
            return {"success": False, "error": "Invalid operator proof — signature does not match npub."}
    else:
        # No operator proof — require Horizon OAuth identity match
        return {"success": False, "error": "operator_proof is required when not providing a Schnorr signature."}

    config_vault = _get_config_vault()
    if not config_vault:
        return {"success": False, "error": "No persistence layer configured on this Authority."}

    try:
        from tollbooth_authority.tenant_provisioner import get_all_operator_config
        config = await get_all_operator_config(config_vault, npub)
    except Exception as exc:
        return {"success": False, "error": f"Failed to retrieve config: {exc}"}

    if not config:
        return {
            "success": False,
            "error": f"No configuration found for {npub[:16]}... — operator may not be registered.",
        }

    return {
        "success": True,
        "npub": npub,
        "config": config,
        "message": f"Bootstrap configuration for {npub[:16]}... ({len(config)} entries).",
    }


@tool
async def purchase_credits(
    amount_sats: Annotated[
        int,
        Field(
            description=(
                "Number of satoshis to pre-fund into the operator credit balance. "
                "This is the certification fee reserve, not the user-facing price. "
                "At 2% fee rate, 1000 sats funds ~50,000 sats of certified purchases. "
                "Minimum 1."
            ),
        ),
    ],
    operator_id: Annotated[
        str,
        Field(
            default="",
            description=(
                "Optional: the operator npub whose ledger should receive the credits. "
                "Use this when the Horizon OAuth session identity differs from the "
                "operator's DPYC npub (e.g. funding a service's NSEC-derived npub). "
                "If empty, defaults to the session's registered npub."
            ),
        ),
    ] = "",
) -> dict[str, Any]:
    """Create a Lightning invoice to pre-fund an operator credit balance.

    Call this whenever the operator's credit balance is low or zero. Returns a
    Lightning invoice with a checkoutLink — pay it with any Lightning wallet.
    After payment, call check_payment with the returned invoice_id to credit
    the balance.

    The optional operator_id parameter lets you fund a specific operator's
    ledger when your Horizon session identity differs from the target npub.
    This is common: Horizon OAuth is access control, npubs are economic
    identity, and the two are orthogonal.

    Do NOT call this if you already have a pending unpaid invoice — pay the
    existing one first, or let it expire.

    Returns:
        success: True if invoice was created.
        invoice_id: The BTCPay invoice ID (pass to check_payment).
        checkout_link: URL to pay the Lightning invoice.
        amount_sats: The amount requested.
        funded_operator: The npub whose ledger will be credited.

    Next step: Pay the invoice, then call check_payment(invoice_id, operator_id).

    Errors: Fails if not registered (call register_operator first) or if
    BTCPay is unreachable.
    """
    try:
        target_npub = _resolve_npub(operator_id.strip()) if operator_id.strip() else None
    except ValueError:
        target_npub = None
    if not target_npub:
        return {"success": False, "error": "operator_id (npub) is required for purchase_credits."}

    btcpay = _get_btcpay()
    cache = _get_ledger_cache()
    s = _get_settings()

    result = await direct_purchase_tool(
        btcpay, cache, target_npub, amount_sats,
        default_credit_ttl_seconds=None,  # Authority balances never expire
    )
    if result.get("success"):
        result["funded_operator"] = target_npub
    return result


@tool
async def check_payment(
    invoice_id: Annotated[
        str,
        Field(
            description=(
                "The BTCPay invoice ID returned by purchase_credits. "
                "Example: 'AbCdEfGh1234'. Pass exactly the value from the "
                "invoice_id field of the purchase_credits response."
            ),
        ),
    ],
    operator_id: Annotated[
        str,
        Field(
            default="",
            description=(
                "The operator npub whose ledger should be credited. "
                "Must match the operator_id used in the purchase_credits call "
                "that created this invoice."
            ),
        ),
    ] = "",
) -> dict[str, Any]:
    """Verify that a Lightning invoice has settled and credit the operator's balance.

    Call this after paying the invoice from purchase_credits. Safe to call
    multiple times — credits are only granted once per invoice. If the invoice
    hasn't settled yet, returns the current status without crediting.

    If you passed operator_id to purchase_credits, pass the same operator_id
    here so the credits land in the correct ledger.

    Returns:
        success: True if balance was credited (or already was).
        status: BTCPay invoice status (e.g., 'Settled', 'New', 'Processing').
        balance_sats: Updated credit balance after crediting.

    Next step: Call check_balance or operator_status to confirm, then
    certify_credits when ready to stamp purchase orders.

    Errors: Returns success=False if the invoice_id is invalid or expired.
    """
    try:
        target_npub = _resolve_npub(operator_id.strip()) if operator_id.strip() else None
    except ValueError:
        target_npub = None
    if not target_npub:
        return {"success": False, "error": "operator_id (npub) is required for check_payment."}

    btcpay = _get_btcpay()
    cache = _get_ledger_cache()
    s = _get_settings()

    return await check_payment_tool(
        btcpay, cache, target_npub, invoice_id,
        default_credit_ttl_seconds=None,  # Authority balances never expire
    )


@tool
async def check_balance(npub: str = "") -> dict[str, Any]:
    """Check your current operator credit balance, total deposited, total consumed, and pending invoices.

    Read-only — no side effects. Call anytime to check your funding level
    before certifying, or to monitor usage.

    Returns:
        balance_sats: Current available credit balance.
        total_deposited_sats: Lifetime credits purchased.
        total_consumed_sats: Lifetime fees deducted via certify_credits.
        pending_invoices: Number of unpaid invoices.

    Next step: If balance is low, call purchase_credits to top up.
    """
    try:
        user_id = _resolve_npub(npub)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    cache = _get_ledger_cache()
    s = _get_settings()

    # One-time reconciliation per user per process lifetime
    if user_id not in _reconciled_users:
        _reconciled_users.add(user_id)
        try:
            btcpay = _get_btcpay()
            recon = await reconcile_pending_invoices(
                btcpay, cache, user_id,
                default_credit_ttl_seconds=None,
            )
            if recon["reconciled"] > 0:
                logger.info(
                    "Reconciled %d pending invoice(s) for %s: %s",
                    recon["reconciled"], user_id, recon["actions"],
                )
        except Exception:
            logger.warning("Reconciliation failed for %s (non-fatal).", user_id)

    return await check_balance_tool(cache, user_id)


@tool
async def operator_status(npub: str = "") -> dict[str, Any]:
    """View your registration status, balance summary, and the Authority's Nostr npub.

    Call this to retrieve the Authority's npub for configuring your
    tollbooth-dpyc integration. Also useful as a health check to confirm
    registration and current balance.

    Returns:
        operator_id: Your DPYC npub.
        registered: Always True if the call succeeds.
        balance_sats: Current tax balance.
        total_deposited_sats: Lifetime credits purchased.
        total_consumed_sats: Lifetime tax deducted.
        authority_npub: Authority's Nostr npub for Schnorr certificate verification.
        nostr_certificate_enabled: Always True (Nostr is the only signing path).

    Use authority_npub in your TollboothConfig so the library can verify
    Schnorr-signed Nostr event certificates locally.
    """
    try:
        user_id = _resolve_npub(npub)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    s = _get_settings()
    nostr_signer = _get_nostr_signer()

    cache = _get_ledger_cache()
    ledger = await cache.get(user_id)

    result: dict[str, Any] = {
        "operator_id": user_id,
        "dpyc_npub": user_id,
        "registered": True,
        "balance_sats": ledger.balance_api_sats,
        "total_deposited_sats": ledger.total_deposited_api_sats,
        "total_consumed_sats": ledger.total_consumed_api_sats,
        "authority_npub": nostr_signer.npub,
        "nostr_certificate_enabled": True,
    }

    # Surface upstream chain config so operators can see the authority hierarchy
    if s.upstream_authority_address:
        result["upstream_authority_address"] = s.upstream_authority_address

    if s.dpyc_enforce_membership:
        result["dpyc_registry_enforcement"] = True

    # Vault health diagnostics
    result["vault_configured"] = bool(s.neon_database_url) or bool(
        s.thebrain_api_key and s.thebrain_vault_brain_id and s.thebrain_vault_home_id
    )
    result["vault_backend"] = "neon" if s.neon_database_url else "thebrain"
    result["cache_health"] = cache.health()

    return result


@tool
async def service_status() -> dict[str, Any]:
    """Diagnostic: report this service's software versions and runtime info.

    Free, unauthenticated. Use to verify deployment versions across the
    DPYC ecosystem.
    """
    versions: dict[str, str] = {
        "tollbooth_authority": __version__,
        "python": platform.python_version(),
    }
    for pkg in ("tollbooth-dpyc", "fastmcp"):
        try:
            versions[pkg.replace("-", "_")] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            versions[pkg.replace("-", "_")] = "unknown"

    return {
        "service": "tollbooth-authority",
        "versions": versions,
    }


@tool
async def certify_credits(
    operator_id: Annotated[
        str,
        Field(
            description=(
                "The operator's DPYC npub (from register_operator response). "
                "This is the operator's Nostr public key used as their persistent identity. "
                "Example: 'npub1abc...'."
            ),
        ),
    ],
    amount_sats: Annotated[
        int,
        Field(
            description=(
                "The total purchase amount in satoshis that the user wants to buy. "
                "The Authority computes a certification fee as "
                "max(10, ceil(amount_sats * 2 / 100)) and deducts it from the "
                "operator's pre-funded credit balance. "
                "The certificate's net_sats = amount_sats - fee_sats. "
                "Must be positive."
            ),
        ),
    ],
) -> dict[str, Any]:
    """Certify a purchase order: deduct fee and return a Schnorr-signed Nostr event certificate.

    This is the core machine-to-machine tool. Called by the operator's MCP server
    (not by end users) when a user requests to purchase credits. The returned
    certificate is a Schnorr-signed Nostr event (kind 30079) that must be verified
    by tollbooth-dpyc using the Authority's npub before the operator creates a
    Lightning invoice for the user.

    Do NOT call this as an end user — it requires operator-level context.
    Do NOT call this if the operator's credit balance is insufficient — check
    check_balance first, or handle the 'Insufficient credit balance' error.

    Returns:
        success: True if the certificate was issued.
        certificate: Schnorr-signed Nostr event JSON (the certificate).
        jti: Unique certificate ID (for audit/anti-replay).
        amount_sats: The original purchase amount.
        fee_sats: Certification fee deducted from operator balance.
        net_sats: operator accounting — effective margin after fee; not the invoice amount.
        expires_at: Unix timestamp when the certificate expires.

    On 'Insufficient credit balance' error: call purchase_credits to top up,
    pay the invoice, call check_payment, then retry certify_credits.
    """
    if amount_sats <= 0:
        return {"success": False, "error": "amount_sats must be positive."}

    s = _get_settings()
    nostr_signer = _get_nostr_signer()
    cache = _get_ledger_cache()
    replay = _get_replay_tracker()

    # Compute certification fee from the pricing model
    resolver = await _get_pricing_resolver()
    pricing = await resolver.get_tool_pricing("authority_certify_credits")
    fee_sats = pricing.compute(amount_sats=amount_sats)
    net_sats = amount_sats - fee_sats

    # Debit operator balance
    ledger = await cache.get(operator_id)
    if not ledger.debit("certify_credits", fee_sats):
        return {
            "success": False,
            "error": f"Insufficient credit balance. Need {fee_sats} sats, have {ledger.balance_api_sats}.",
        }

    cache.mark_dirty(operator_id)

    # DPYC registry membership check (fail closed) — before upstream certify
    # to avoid wasting upstream cert-sats if the registry check fails.
    registry = _get_dpyc_registry()
    if registry is not None:
        try:
            await registry.check_membership(operator_id)
        except RegistryError as e:
            ledger.rollback_debit("certify_credits", fee_sats)
            return {"success": False, "error": f"DPYC membership check failed: {e}"}

    # Non-Prime: certify upstream in real-time
    upstream_cert = None
    if s.upstream_authority_address:
        from tollbooth.authority_client import AuthorityCertifier, AuthorityCertifyError

        authority_npub = await _get_authority_npub()
        if not authority_npub:
            authority_npub = nostr_signer.npub
        certifier = AuthorityCertifier(s.upstream_authority_address, authority_npub)
        try:
            upstream_cert = await certifier.certify_credits(amount_sats)
        except AuthorityCertifyError as e:
            ledger.rollback_debit("certify_credits", fee_sats)
            return {"success": False, "error": f"Upstream certification failed: {e}"}

    # Build claims and sign Nostr event certificate
    jti = uuid.uuid4().hex
    expiration = int(time.time()) + s.certificate_ttl_seconds

    claims = {
        "sub": operator_id,
        "amount_sats": amount_sats,
        "fee_sats": fee_sats,
        "net_sats": net_sats,
        "dpyc_protocol": "dpyp-01-base-certificate",
    }

    # Record JTI for anti-replay
    replay.check_and_record(jti)

    nostr_event_json = nostr_signer.sign_certificate_event(
        claims=claims,
        jti=jti,
        operator_npub=operator_id,
        expiration=expiration,
    )

    # Flush immediately (credit-critical)
    if not await cache.flush_user(operator_id):
        logger.error("Failed to persist fee debit for %s", operator_id)

    result = {
        "success": True,
        "certificate": nostr_event_json,
        "jti": jti,
        "amount_sats": amount_sats,
        "fee_sats": fee_sats,
        "net_sats": net_sats,
        "expires_at": expiration,
    }

    if upstream_cert is not None:
        result["upstream_certificate"] = upstream_cert.get("certificate")
        result["upstream_jti"] = upstream_cert.get("jti")

    return result


@tool
async def report_upstream_purchase(
    amount_sats: Annotated[
        int,
        Field(
            description=(
                "Number of cert-sats purchased from the upstream Authority. "
                "Deprecated — upstream certification is now automatic."
            ),
        ),
    ],
) -> dict[str, Any]:
    """Deprecated — upstream certification is now automatic.

    Since v0.4.0, certify_credits automatically obtains upstream certificates
    via AuthorityCertifier when upstream_authority_address is configured.
    Manual supply management is no longer needed.
    """
    return {
        "success": False,
        "error": (
            "report_upstream_purchase is deprecated. Since v0.4.0, "
            "certify_credits automatically certifies upstream when "
            "upstream_authority_address is configured. No manual supply "
            "management is needed."
        ),
    }


# ---------------------------------------------------------------------------
# DPYC Identity Tools
# ---------------------------------------------------------------------------


@tool
async def activate_dpyc(npub: str) -> dict[str, Any]:
    """Deprecated — npub is now set during register_operator.

    This tool is kept for backward compatibility. Use register_operator(npub=...)
    instead, which registers your operator identity and activates DPYC in one step.
    """
    return {
        "success": False,
        "error": (
            "activate_dpyc is deprecated. Your npub is now set during "
            "register_operator(npub=...). Call register_operator with your npub "
            "to register and activate your DPYC identity in one step. "
            "Get your npub from the dpyc-oracle's how_to_join() tool."
        ),
    }


@tool
async def check_dpyc_membership(npub: str) -> dict[str, Any]:
    """Diagnostic: look up an npub in the DPYC community registry.

    Returns the member record if found and active, or an error message.
    Works regardless of whether enforcement is enabled.

    Args:
        npub: The Nostr public key to look up.

    Returns:
        success: True if the member was found and active.
        member: The full member record from the registry.
    """
    s = _get_settings()
    registry = DPYCRegistry(
        url=DEFAULT_REGISTRY_URL,
        cache_ttl_seconds=s.dpyc_registry_cache_ttl_seconds,
    )
    try:
        member = await registry.check_membership(npub)
        return {"success": True, "member": member}
    except RegistryError as e:
        return {"success": False, "error": str(e)}
    finally:
        await registry.close()


# ---------------------------------------------------------------------------
# Authority Onboarding Tools (3-step Nostr DM challenge-response)
# ---------------------------------------------------------------------------


@tool
async def register_authority_npub(
    candidate_npub: Annotated[
        str,
        Field(
            description=(
                "The Nostr npub of the candidate who wants to become "
                "the curator of this Authority. Must start with 'npub1'."
            ),
        ),
    ],
) -> dict[str, Any]:
    """Step 1/3 of Authority onboarding — send a Nostr DM challenge to the candidate.

    Begins the Authority curator onboarding protocol. Sends a Nostr DM
    to the candidate's npub containing a poison slug for anti-replay
    protection. The candidate must reply in their Nostr client with
    ``claim = @@@yes@@@`` and include the poison slug.

    After the candidate replies, call ``confirm_authority_claim(candidate_npub)``
    to verify the DM and escalate to the Prime Authority for approval.

    The full protocol:
    1. ``register_authority_npub(npub)`` — this tool (sends DM challenge)
    2. ``confirm_authority_claim(npub)`` — verifies candidate reply, sends to Prime
    3. ``check_authority_approval(npub)`` — checks Prime approval, activates Authority

    Only one onboarding may be in progress at a time.
    """
    if not candidate_npub.startswith("npub1") or len(candidate_npub) < 60:
        return {
            "success": False,
            "error": "Invalid npub format. Must start with 'npub1' and be at least 60 characters.",
        }

    # Reject if Authority already has a curator
    existing = await _get_authority_npub()
    if existing:
        return {
            "success": False,
            "error": (
                f"This Authority already has a curator ({existing[:16]}...). "
                "Only one curator per Authority instance."
            ),
        }

    # Start onboarding state
    try:
        challenge = _onboarding.start_claim(candidate_npub)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    # Send DM challenge
    try:
        exchange = _get_nostr_exchange()
        result = await exchange.open_channel(
            "authority_claim",
            greeting=(
                "You are requesting to become the curator of this Authority. "
                "Reply with: claim = @@@yes@@@ and include the poison slug."
            ),
            recipient_npub=candidate_npub,
        )
    except Exception as exc:
        _onboarding.complete()  # rollback state
        return {"success": False, "error": f"Failed to send DM challenge: {exc}"}

    return {
        "success": True,
        "candidate_npub": candidate_npub,
        "phase": challenge.phase,
        "instructions": (
            f"A Nostr DM challenge has been sent to {candidate_npub[:16]}... "
            "The candidate must reply in their Nostr client with:\n\n"
            "  claim = @@@yes@@@\n\n"
            "Include the poison slug shown in the DM. "
            "Then call confirm_authority_claim(candidate_npub) to proceed."
        ),
        "message": result.get("message", "DM sent."),
    }


@tool
async def confirm_authority_claim(
    candidate_npub: Annotated[
        str,
        Field(
            description=(
                "The Nostr npub of the candidate who replied to the "
                "DM challenge from register_authority_npub."
            ),
        ),
    ],
) -> dict[str, Any]:
    """Step 2/3 of Authority onboarding — verify candidate DM, escalate to Prime.

    Polls Nostr relays for the candidate's DM reply to the challenge
    sent by ``register_authority_npub``. The reply must include
    ``claim = @@@yes@@@`` and the correct poison slug (Schnorr-signed
    by the candidate's key, providing anti-replay).

    On success, sends an approval request DM to the Prime Authority
    asking them to approve this candidate. The Prime must reply with
    ``approval = @@@yes@@@``.

    Then call ``check_authority_approval(candidate_npub)`` to check
    the Prime's response.
    """
    challenge = _onboarding.get()
    if challenge is None:
        return {
            "success": False,
            "error": "No active onboarding. Call register_authority_npub first.",
        }
    if challenge.candidate_npub != candidate_npub:
        return {
            "success": False,
            "error": (
                f"Active onboarding is for {challenge.candidate_npub[:16]}..., "
                f"not {candidate_npub[:16]}..."
            ),
        }
    if challenge.phase != "claim":
        return {
            "success": False,
            "error": f"Onboarding is in '{challenge.phase}' phase, not 'claim'.",
        }

    # Poll for candidate's DM reply
    try:
        exchange = _get_nostr_exchange()
        await exchange.receive(
            sender_npub=candidate_npub,
            service="authority_claim",
        )
    except Exception as exc:
        return {
            "success": False,
            "error": f"No valid claim DM received: {exc}",
        }

    # Candidate proved ownership. Now escalate to Prime Authority.
    try:
        prime_npub = await _resolve_prime_npub()
    except Exception as exc:
        return {
            "success": False,
            "error": f"Failed to resolve Prime Authority: {exc}",
        }

    # Promote onboarding state
    try:
        _onboarding.promote_to_approval(prime_npub)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    # Send approval request to Prime
    try:
        signer = _get_nostr_signer()
        exchange2 = _get_nostr_exchange()
        await exchange2.open_channel(
            "authority_approval",
            greeting=(
                f"{candidate_npub} requests to curate the Authority at "
                f"npub {signer.npub[:16]}... "
                "Reply with: approval = @@@yes@@@ and include the poison slug."
            ),
            recipient_npub=prime_npub,
        )
    except Exception as exc:
        return {
            "success": False,
            "error": f"Candidate verified, but failed to send approval request to Prime: {exc}",
        }

    return {
        "success": True,
        "candidate_npub": candidate_npub,
        "phase": "approval",
        "prime_npub": prime_npub,
        "message": (
            f"Candidate {candidate_npub[:16]}... verified. "
            f"Approval request sent to Prime Authority ({prime_npub[:16]}...). "
            "Call check_authority_approval(candidate_npub) after Prime responds."
        ),
    }


@tool
async def check_authority_approval(
    candidate_npub: Annotated[
        str,
        Field(
            description=(
                "The Nostr npub of the candidate awaiting Prime Authority approval."
            ),
        ),
    ],
) -> dict[str, Any]:
    """Step 3/3 of Authority onboarding — check Prime approval, activate Authority.

    Polls Nostr relays for the Prime Authority's DM reply approving the
    candidate. On success:

    1. Persists the candidate's npub as this Authority's curator
    2. Registers the Authority in the DPYC community registry via the Oracle
    3. Activates immediately — no restart needed

    The Authority is now discoverable by Operators in the DPYC registry.
    """
    challenge = _onboarding.get()
    if challenge is None:
        return {
            "success": False,
            "error": "No active onboarding. Start with register_authority_npub.",
        }
    if challenge.candidate_npub != candidate_npub:
        return {
            "success": False,
            "error": (
                f"Active onboarding is for {challenge.candidate_npub[:16]}..., "
                f"not {candidate_npub[:16]}..."
            ),
        }
    if challenge.phase != "approval":
        return {
            "success": False,
            "error": f"Onboarding is in '{challenge.phase}' phase, not 'approval'.",
        }

    prime_npub = challenge.prime_npub
    if not prime_npub:
        return {
            "success": False,
            "error": "Prime Authority npub not set. This should not happen.",
        }

    # Poll for Prime's approval DM
    try:
        exchange = _get_nostr_exchange()
        await exchange.receive(
            sender_npub=prime_npub,
            service="authority_approval",
        )
    except Exception as exc:
        return {
            "success": False,
            "error": f"No approval received from Prime: {exc}",
        }

    # Persist curator npub
    await _set_authority_npub(candidate_npub)

    # Register via Oracle MCP-to-MCP
    commit_url = ""
    try:
        _get_nostr_signer()  # validate signer is available
        service_url = await _resolve_own_service_url()
        commit_url = await _register_via_oracle(
            authority_npub=candidate_npub,
            display_name=f"Authority ({candidate_npub[:16]}...)",
            service_url=service_url,
            upstream_authority_npub=prime_npub,
        )
    except Exception as exc:
        logger.warning(
            "Oracle registration failed (Authority is still activated locally): %s",
            exc,
        )

    # Complete onboarding
    _onboarding.complete()

    result: dict[str, Any] = {
        "success": True,
        "candidate_npub": candidate_npub,
        "activated": True,
        "message": (
            f"Authority curator set to {candidate_npub[:16]}... "
            "and activated immediately."
        ),
    }
    if commit_url:
        result["commit_url"] = commit_url
        result["message"] += f" Registered in DPYC community: {commit_url}"

    return result


# ---------------------------------------------------------------------------
# Account Statement Tools
# ---------------------------------------------------------------------------


@tool
async def account_statement(npub: str = "") -> dict[str, Any]:
    """Get a structured JSON account statement for the current operator.

    Returns the operator's credit balance, deposit history, fees paid,
    total certified amount, active tranches, and the Authority's fee schedule.
    """
    try:
        user_id = _resolve_npub(npub)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    cache = _get_ledger_cache()
    _get_settings()  # validate settings are available
    ledger = await cache.get(user_id)

    tranches = [
        {
            "granted_at": t.granted_at.isoformat() if hasattr(t.granted_at, "isoformat") else str(t.granted_at),
            "original_sats": t.original_sats,
            "remaining_sats": t.remaining_sats,
            "invoice_id": t.invoice_id,
        }
        for t in ledger.tranches
        if t.remaining_sats > 0
    ]

    return {
        "success": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "account_summary": {
            "balance_sats": ledger.balance_api_sats,
            "total_deposited_sats": ledger.total_deposited_api_sats,
            "total_fees_paid_sats": ledger.total_consumed_api_sats,
            "total_certified_sats": sum(
                t.original_sats - t.remaining_sats
                for t in ledger.tranches
            ),
        },
        "active_tranches": tranches,
        "fee_schedule": "See pricing model for certify_credits ad valorem rate.",
    }


@tool
async def account_statement_infographic(npub: str = "") -> dict[str, Any]:
    """Get a visual SVG infographic of the operator's account statement.

    Returns the same data as account_statement, plus an SVG rendering
    suitable for display in an AI chat or dashboard.
    """
    from tollbooth_authority.infographic import render_operator_infographic

    data = await account_statement(npub=npub)
    if not data.get("success"):
        return data

    svg = render_operator_infographic(data)
    return {
        "success": True,
        "svg": svg,
        "data": data,
    }


# ---------------------------------------------------------------------------
# Pricing CRUD tools
# ---------------------------------------------------------------------------


@tool
async def get_pricing_model() -> dict[str, Any]:
    """Get the active pricing model for this Authority. Free.

    Returns the stored model if one exists, otherwise the built-in
    default pricing (all free except certify_credits ad valorem 2%,
    account_statement_infographic 1 sat).
    """
    try:
        store = _get_pricing_store()
        operator = _get_operator_npub()
    except (ValueError, RuntimeError) as e:
        return {"status": "error", "error": str(e)}
    from tollbooth.tools.pricing import get_pricing_model_tool

    result = await get_pricing_model_tool(store, operator)

    # If no stored model, return the built-in default
    if result.get("status") == "ok" and result.get("tools") is None:
        from tollbooth_authority.default_pricing import build_default_model
        from tollbooth.tools.pricing import _model_to_response

        default = build_default_model()
        default.operator = operator
        resp = _model_to_response(default)
        resp["source"] = "default"
        return resp

    return result


@tool
async def set_pricing_model(model_json: str) -> dict[str, Any]:
    """Set or update the active pricing model.

    Free — operator self-service tool.

    Args:
        model_json: JSON string with pricing model data.
            May include "operator_proof" — a signed Nostr kind-27235 event
            JSON string proving operator identity when the caller's session
            npub differs from the operator npub.
    """
    # Extract operator_proof from inside model_json if present
    import json as _json
    operator_proof = ""
    try:
        parsed = _json.loads(model_json)
        if isinstance(parsed, dict) and "operator_proof" in parsed:
            operator_proof = parsed.pop("operator_proof", "")
            model_json = _json.dumps(parsed)
    except (ValueError, TypeError):
        pass

    try:
        store = _get_pricing_store()
        operator = _get_operator_npub()
    except (ValueError, RuntimeError) as e:
        return {"status": "error", "error": str(e)}

    # Verify caller is the operator (skip in STDIO mode)
    user_id = _get_current_user_id()
    if user_id is not None:
        if not operator_proof:
            return {"status": "error", "error": "Only the operator can modify pricing — provide operator_proof."}
        from tollbooth.operator_proof import verify_operator_proof

        if not verify_operator_proof(operator_proof, operator, "set_pricing_model"):
            return {"status": "error", "error": "Only the operator can modify pricing"}

    from tollbooth.tools.pricing import set_pricing_model_tool

    return await set_pricing_model_tool(store, operator, model_json)


@tool
async def list_constraint_types() -> dict[str, Any]:
    """List all available constraint types and their parameter schemas.

    Returns the type, category, description, and parameter specs for
    every constraint that can be used in a pricing pipeline.

    Free — no credits required.
    """
    from tollbooth.tools.pricing import list_constraint_types as _list

    return {"status": "ok", "constraint_types": _list()}

