"""FastMCP app — Tollbooth Authority service with 7 MCP tools."""

from __future__ import annotations

import asyncio
import logging
import math
import signal
import sys
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

from tollbooth import BTCPayClient, BTCPayError, LedgerCache
from tollbooth.tools.credits import (
    check_balance_tool,
    check_payment_tool,
    purchase_credits_tool,
)

from tollbooth_authority.certificate import create_certificate_claims
from tollbooth_authority.config import AuthoritySettings
from tollbooth_authority.replay import ReplayTracker
from tollbooth_authority.signing import AuthoritySigner
from tollbooth_authority.vault import TheBrainVault

# ---------------------------------------------------------------------------
# FastMCP app
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "tollbooth-authority",
    instructions=(
        "Tollbooth Authority — Certified Purchase Order Service.\n\n"
        "Manages operator registrations, collects operator tax via Lightning,\n"
        "and certifies purchase orders with EdDSA-signed JWT certificates.\n"
    ),
)

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

_signer: AuthoritySigner | None = None
_btcpay_client: BTCPayClient | None = None
_vault: TheBrainVault | None = None
_ledger_cache: LedgerCache | None = None
_replay_tracker: ReplayTracker | None = None


def _get_signer() -> AuthoritySigner:
    global _signer
    if _signer is not None:
        return _signer
    s = _get_settings()
    if not s.authority_signing_key:
        raise ValueError("AUTHORITY_SIGNING_KEY is required.")
    _signer = AuthoritySigner(s.authority_signing_key)
    logger.info("Authority signer initialized.")
    return _signer


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


def _get_vault() -> TheBrainVault:
    global _vault
    if _vault is not None:
        return _vault
    s = _get_settings()
    if not s.thebrain_api_key or not s.thebrain_vault_brain_id or not s.thebrain_vault_home_id:
        raise ValueError(
            "Vault not configured. Set THEBRAIN_API_KEY, THEBRAIN_VAULT_BRAIN_ID, THEBRAIN_VAULT_HOME_ID."
        )
    _vault = TheBrainVault(
        api_key=s.thebrain_api_key,
        brain_id=s.thebrain_vault_brain_id,
        home_thought_id=s.thebrain_vault_home_id,
    )
    logger.info("TheBrain vault initialized for operator ledger persistence.")
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
# Shutdown
# ---------------------------------------------------------------------------

_shutdown_triggered = False


async def _graceful_shutdown() -> None:
    global _shutdown_triggered, _ledger_cache, _btcpay_client, _vault
    if _shutdown_triggered:
        return
    _shutdown_triggered = True

    if _ledger_cache is not None:
        dirty = _ledger_cache.dirty_count
        logger.info("Graceful shutdown: flushing %d dirty entries...", dirty)
        ts = datetime.now(timezone.utc).isoformat()
        await _ledger_cache.snapshot_all(ts)
        flushed = await _ledger_cache.flush_all()
        await _ledger_cache.stop()
        logger.info("Shutdown: flushed %d entries.", flushed)
        _ledger_cache = None

    if _btcpay_client is not None:
        await _btcpay_client.close()
        _btcpay_client = None

    if _vault is not None:
        await _vault.close()
        _vault = None


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
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def register_operator() -> dict[str, Any]:
    """Register as an operator via Horizon OAuth identity.

    Creates a ledger entry for the authenticated operator so they can
    purchase tax credits and certify purchase orders.
    """
    user_id = _require_user_id()
    cache = _get_ledger_cache()

    ledger = await cache.get(user_id)
    cache.mark_dirty(user_id)
    await cache.flush_user(user_id)

    return {
        "success": True,
        "operator_id": user_id,
        "balance_sats": ledger.balance_api_sats,
        "message": f"Operator {user_id} registered. Purchase tax credits to begin certifying.",
    }


@mcp.tool()
async def purchase_tax_credits(amount_sats: int) -> dict[str, Any]:
    """Create a Lightning invoice to pre-fund your operator tax balance.

    Args:
        amount_sats: Number of satoshis to purchase (minimum 1).
    """
    user_id = _require_user_id()
    btcpay = _get_btcpay()
    cache = _get_ledger_cache()

    return await purchase_credits_tool(btcpay, cache, user_id, amount_sats)


@mcp.tool()
async def check_tax_payment(invoice_id: str) -> dict[str, Any]:
    """Verify invoice settlement and credit your operator tax balance.

    Args:
        invoice_id: The BTCPay invoice ID from purchase_tax_credits.
    """
    user_id = _require_user_id()
    btcpay = _get_btcpay()
    cache = _get_ledger_cache()

    return await check_payment_tool(btcpay, cache, user_id, invoice_id)


@mcp.tool()
async def tax_balance() -> dict[str, Any]:
    """Check your current operator tax balance and usage summary."""
    user_id = _require_user_id()
    cache = _get_ledger_cache()

    return await check_balance_tool(cache, user_id)


@mcp.tool()
async def operator_status() -> dict[str, Any]:
    """Registration status and Authority public key info."""
    user_id = _require_user_id()

    try:
        signer = _get_signer()
        public_key_pem = signer.public_key_pem
    except ValueError:
        public_key_pem = "<signing key not configured>"

    cache = _get_ledger_cache()
    ledger = await cache.get(user_id)

    return {
        "operator_id": user_id,
        "registered": True,
        "balance_sats": ledger.balance_api_sats,
        "total_deposited_sats": ledger.total_deposited_api_sats,
        "total_consumed_sats": ledger.total_consumed_api_sats,
        "authority_public_key": public_key_pem,
    }


@mcp.tool()
async def certify_purchase(operator_id: str, amount_sats: int) -> dict[str, Any]:
    """Certify a purchase order: deduct tax from operator balance, return signed JWT.

    This is the core machine-to-machine tool. The returned JWT should be
    verified by tollbooth-dpyc using the Authority's public key before
    the operator creates a user invoice.

    Args:
        operator_id: The operator's Horizon user ID.
        amount_sats: The purchase amount in satoshis to certify.
    """
    if amount_sats <= 0:
        return {"success": False, "error": "amount_sats must be positive."}

    s = _get_settings()
    signer = _get_signer()
    cache = _get_ledger_cache()
    replay = _get_replay_tracker()

    # Compute tax
    tax_sats = max(
        s.tax_min_sats,
        math.ceil(amount_sats * s.tax_rate_percent / 100),
    )

    # Debit operator balance
    ledger = await cache.get(operator_id)
    if not ledger.debit("certify_purchase", tax_sats):
        return {
            "success": False,
            "error": f"Insufficient tax balance. Need {tax_sats} sats, have {ledger.balance_api_sats}.",
        }

    cache.mark_dirty(operator_id)

    # Build and sign certificate
    claims = create_certificate_claims(
        operator_id=operator_id,
        amount_sats=amount_sats,
        tax_sats=tax_sats,
        ttl_seconds=s.certificate_ttl_seconds,
    )

    # Record JTI for anti-replay
    replay.check_and_record(claims["jti"])

    token = signer.sign_certificate(claims)

    # Flush immediately (credit-critical)
    if not await cache.flush_user(operator_id):
        logger.error("Failed to persist tax debit for %s", operator_id)

    return {
        "success": True,
        "certificate": token,
        "jti": claims["jti"],
        "amount_sats": amount_sats,
        "tax_paid_sats": tax_sats,
        "net_sats": claims["net_sats"],
        "expires_at": claims["exp"],
    }


@mcp.tool()
async def refresh_config() -> dict[str, Any]:
    """Hot-reload environment variables without redeploy.

    Resets all singletons so they pick up new env vars on next use.
    """
    global _settings, _settings_loaded, _signer, _btcpay_client, _vault, _ledger_cache, _replay_tracker

    # Flush before reset
    if _ledger_cache is not None:
        await _ledger_cache.flush_all()
        await _ledger_cache.stop()

    if _btcpay_client is not None:
        await _btcpay_client.close()

    if _vault is not None:
        await _vault.close()

    _settings = None
    _settings_loaded = False
    _signer = None
    _btcpay_client = None
    _vault = None
    _ledger_cache = None
    _replay_tracker = None

    _ensure_settings_loaded()

    return {
        "success": True,
        "message": "Configuration reloaded. Singletons will be re-created on next use.",
    }
