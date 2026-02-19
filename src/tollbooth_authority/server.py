"""FastMCP app — Tollbooth Authority service with 7 MCP tools."""

from __future__ import annotations

import asyncio
import logging
import math
import signal
import sys
from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import Field

logger = logging.getLogger(__name__)

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

from tollbooth import BTCPayClient, BTCPayError, LedgerCache
from tollbooth.tools.credits import (
    check_balance_tool,
    check_payment_tool,
    purchase_tax_credits_tool,
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
        "The Authority is the institutional backbone of the Tollbooth ecosystem. "
        "It registers MCP operators, collects a small tax on every purchase order "
        "via Bitcoin Lightning, and issues EdDSA-signed JWT certificates that prove "
        "an operator has paid before collecting a fare from a user.\n\n"
        "## First-Time Bootstrap (follow these steps in order)\n\n"
        "1. Call `register_operator` — creates your ledger entry. Returns your "
        "operator_id and a zero balance.\n"
        "2. Call `purchase_tax_credits` with the number of sats to pre-fund "
        "(e.g., 1000). Returns a Lightning invoice with a checkoutLink.\n"
        "3. Pay the invoice using any Lightning wallet.\n"
        "4. Call `check_tax_payment` with the invoice_id from step 2. "
        "On settlement, your tax balance is credited.\n"
        "5. Call `tax_balance` or `operator_status` to confirm your funded balance "
        "and retrieve the Authority's Ed25519 public key.\n\n"
        "## Tax Computation\n\n"
        "Tax per certification = max(TAX_MIN_SATS, ceil(amount_sats * TAX_RATE_PERCENT / 100)). "
        "Defaults: 2% rate, 10 sat minimum. The tax is deducted from the operator's "
        "pre-funded balance when `certify_purchase` is called.\n\n"
        "## Tool Overview\n\n"
        "- `register_operator` — First step. Idempotent; safe to call again.\n"
        "- `purchase_tax_credits` — Creates a Lightning invoice. Call whenever balance is low.\n"
        "- `check_tax_payment` — Polls an invoice. Call after payment; safe to call multiple times.\n"
        "- `tax_balance` — Read-only balance check. No side effects.\n"
        "- `operator_status` — Registration info + Authority public key for JWT verification.\n"
        "- `certify_purchase` — Core machine-to-machine tool. Deducts tax, returns signed JWT.\n"
        "- `refresh_config` — Admin tool. Hot-reloads env vars without redeploy.\n\n"
        "## Low-Balance Recovery\n\n"
        "If `certify_purchase` returns 'Insufficient tax balance', the operator must "
        "fund more credits: call `purchase_tax_credits`, pay, then `check_tax_payment`. "
        "The operator's MCP server should surface this to the admin, not the end user.\n\n"
        "## Key Generation\n\n"
        "The Authority signs certificates with an Ed25519 key. Generate one with "
        "`python scripts/generate_keypair.py`. The private key goes in "
        "AUTHORITY_SIGNING_KEY; the public key is hardcoded in tollbooth-dpyc "
        "for verification.\n"
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
_reconciled_users: set[str] = set()


async def _graceful_shutdown() -> None:
    global _shutdown_triggered, _ledger_cache, _btcpay_client, _vault
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
        await _vault.close()
        _vault = None


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
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def register_operator() -> dict[str, Any]:
    """Register as an operator on the Tollbooth turnpike via Horizon OAuth identity.

    Call this first. Creates a ledger entry for the authenticated operator so
    they can purchase tax credits and certify purchase orders. Idempotent — safe
    to call again if already registered (returns current balance).

    Returns:
        success: Always True on completion.
        operator_id: Your Horizon user ID (use this for certify_purchase calls).
        balance_sats: Current tax balance (0 for new registrations).
        message: Human-readable confirmation.

    Next step: Call purchase_tax_credits to fund your tax balance.

    Errors: Fails if not authenticated via Horizon (FastMCP Cloud required).
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
async def purchase_tax_credits(
    amount_sats: Annotated[
        int,
        Field(
            description=(
                "Number of satoshis to pre-fund into your tax balance. "
                "This is the tax reserve, not the user-facing price. "
                "At 2% tax rate, 1000 sats funds ~50,000 sats of certified purchases. "
                "Minimum 1."
            ),
        ),
    ],
) -> dict[str, Any]:
    """Create a Lightning invoice to pre-fund your operator tax balance.

    Call this whenever your tax balance is low or zero. Returns a Lightning
    invoice with a checkoutLink — pay it with any Lightning wallet. After
    payment, call check_tax_payment with the returned invoice_id to credit
    your balance.

    Do NOT call this if you already have a pending unpaid invoice — pay the
    existing one first, or let it expire.

    Returns:
        success: True if invoice was created.
        invoice_id: The BTCPay invoice ID (pass to check_tax_payment).
        checkout_link: URL to pay the Lightning invoice.
        amount_sats: The amount requested.

    Next step: Pay the invoice, then call check_tax_payment(invoice_id).

    Errors: Fails if not registered (call register_operator first) or if
    BTCPay is unreachable.
    """
    user_id = _require_user_id()
    btcpay = _get_btcpay()
    cache = _get_ledger_cache()
    s = _get_settings()

    return await purchase_tax_credits_tool(
        btcpay, cache, user_id, amount_sats,
        tier_config_json=s.btcpay_tier_config,
        user_tiers_json=s.btcpay_user_tiers,
    )


@mcp.tool()
async def check_tax_payment(
    invoice_id: Annotated[
        str,
        Field(
            description=(
                "The BTCPay invoice ID returned by purchase_tax_credits. "
                "Example: 'AbCdEfGh1234'. Pass exactly the value from the "
                "invoice_id field of the purchase_tax_credits response."
            ),
        ),
    ],
) -> dict[str, Any]:
    """Verify that a Lightning invoice has settled and credit the payment to your tax balance.

    Call this after paying the invoice from purchase_tax_credits. Safe to call
    multiple times — credits are only granted once per invoice. If the invoice
    hasn't settled yet, returns the current status without crediting.

    Returns:
        success: True if balance was credited (or already was).
        status: BTCPay invoice status (e.g., 'Settled', 'New', 'Processing').
        balance_sats: Updated tax balance after crediting.

    Next step: Call tax_balance or operator_status to confirm, then
    certify_purchase when ready to stamp purchase orders.

    Errors: Returns success=False if the invoice_id is invalid or expired.
    """
    user_id = _require_user_id()
    btcpay = _get_btcpay()
    cache = _get_ledger_cache()
    s = _get_settings()

    return await check_payment_tool(
        btcpay, cache, user_id, invoice_id,
        tier_config_json=s.btcpay_tier_config,
        user_tiers_json=s.btcpay_user_tiers,
    )


@mcp.tool()
async def tax_balance() -> dict[str, Any]:
    """Check your current operator tax balance, total deposited, total consumed, and pending invoices.

    Read-only — no side effects. Call anytime to check your funding level
    before certifying, or to monitor usage.

    Returns:
        balance_sats: Current available tax balance.
        total_deposited_sats: Lifetime credits purchased.
        total_consumed_sats: Lifetime tax deducted via certify_purchase.
        pending_invoices: Number of unpaid invoices.

    Next step: If balance is low, call purchase_tax_credits to top up.
    """
    user_id = _require_user_id()
    cache = _get_ledger_cache()
    s = _get_settings()

    # One-time reconciliation per user per process lifetime
    if user_id not in _reconciled_users:
        _reconciled_users.add(user_id)
        try:
            btcpay = _get_btcpay()
            from tollbooth.tools.credits import reconcile_pending_invoices
            recon = await reconcile_pending_invoices(
                btcpay, cache, user_id,
                tier_config_json=s.btcpay_tier_config,
                user_tiers_json=s.btcpay_user_tiers,
            )
            if recon["reconciled"] > 0:
                logger.info(
                    "Reconciled %d pending invoice(s) for %s: %s",
                    recon["reconciled"], user_id, recon["actions"],
                )
        except Exception:
            logger.warning("Reconciliation failed for %s (non-fatal).", user_id)

    return await check_balance_tool(
        cache, user_id,
        tier_config_json=s.btcpay_tier_config,
        user_tiers_json=s.btcpay_user_tiers,
    )


@mcp.tool()
async def operator_status() -> dict[str, Any]:
    """View your registration status, balance summary, and the Authority's Ed25519 public key.

    Call this to retrieve the Authority's public key for hardcoding into your
    tollbooth-dpyc integration. Also useful as a health check to confirm
    registration and current balance.

    Returns:
        operator_id: Your Horizon user ID.
        registered: Always True if the call succeeds.
        balance_sats: Current tax balance.
        total_deposited_sats: Lifetime credits purchased.
        total_consumed_sats: Lifetime tax deducted.
        authority_public_key: PEM-encoded Ed25519 public key for JWT verification.

    The authority_public_key should be hardcoded in your tollbooth-dpyc
    TollboothConfig so the library can verify certificates locally.
    """
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
async def certify_purchase(
    operator_id: Annotated[
        str,
        Field(
            description=(
                "The operator's Horizon user ID (from register_operator response). "
                "This is the FastMCP Cloud user ID, not a GitHub username. "
                "Example: 'user_01KGZY...'."
            ),
        ),
    ],
    amount_sats: Annotated[
        int,
        Field(
            description=(
                "The total purchase amount in satoshis that the user wants to buy. "
                "The Authority computes tax as max(10, ceil(amount_sats * 2 / 100)) "
                "and deducts it from the operator's pre-funded tax balance. "
                "The certificate's net_sats = amount_sats - tax_sats. "
                "Must be positive."
            ),
        ),
    ],
) -> dict[str, Any]:
    """Certify a purchase order: deduct tax from the operator's balance and return an EdDSA-signed JWT.

    This is the core machine-to-machine tool. Called by the operator's MCP server
    (not by end users) when a user requests to purchase credits. The returned JWT
    certificate must be verified by tollbooth-dpyc using the Authority's public key
    before the operator creates a Lightning invoice for the user.

    Do NOT call this as an end user — it requires operator-level context.
    Do NOT call this if the operator's tax balance is insufficient — check
    tax_balance first, or handle the 'Insufficient tax balance' error.

    Returns:
        success: True if the certificate was issued.
        certificate: The EdDSA-signed JWT string (pass to tollbooth-dpyc for verification).
        jti: Unique certificate ID (for audit/anti-replay).
        amount_sats: The original purchase amount.
        tax_paid_sats: Tax deducted from operator balance.
        net_sats: amount_sats minus tax (what the user effectively receives).
        expires_at: Unix timestamp when the certificate expires.

    On 'Insufficient tax balance' error: call purchase_tax_credits to top up,
    pay the invoice, call check_tax_payment, then retry certify_purchase.
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
    """Hot-reload environment variables without redeploying the service.

    Admin-only tool. Flushes all dirty ledger entries to persistent storage,
    closes BTCPay and vault connections, then resets all singletons so they
    pick up new env vars on next use. Use after updating env vars in the
    FastMCP Cloud dashboard.

    Returns:
        success: True if reload completed.
        message: Confirmation that singletons will be re-created on next use.

    Warning: Causes a brief interruption — all cached state is flushed and
    connections are closed. Active requests may see transient errors.
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
