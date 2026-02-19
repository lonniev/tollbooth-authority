"""Tests for server tools with mocked dependencies."""

from __future__ import annotations

import base64
import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from tollbooth import UserLedger, LedgerCache

from tollbooth_authority.config import AuthoritySettings
from tollbooth_authority.replay import ReplayTracker
from tollbooth_authority.signing import AuthoritySigner


def _make_signer() -> AuthoritySigner:
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return AuthoritySigner(base64.b64encode(pem).decode())


def _make_settings(**overrides) -> AuthoritySettings:
    defaults = {
        "authority_signing_key": "",
        "btcpay_host": "",
        "btcpay_store_id": "",
        "btcpay_api_key": "",
        "thebrain_api_key": "",
        "thebrain_vault_brain_id": "",
        "thebrain_vault_home_id": "",
        "tax_rate_percent": 2.0,
        "tax_min_sats": 10,
        "certificate_ttl_seconds": 600,
    }
    defaults.update(overrides)
    return AuthoritySettings(**defaults)


# ---------------------------------------------------------------------------
# certify_purchase logic tests (isolated from FastMCP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_certify_purchase_success():
    """Successful certification deducts tax and returns JWT."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600)

    # Mock ledger with sufficient balance
    ledger = UserLedger(balance_api_sats=1000)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
    ):
        result = await srv.certify_purchase("op-1", 1000)

    assert result["success"] is True
    assert "certificate" in result
    assert result["amount_sats"] == 1000
    # Tax: max(10, ceil(1000 * 2.0 / 100)) = max(10, 20) = 20
    assert result["tax_paid_sats"] == 20
    assert result["net_sats"] == 980
    cache.mark_dirty.assert_called_once_with("op-1")
    cache.flush_user.assert_called_once_with("op-1")


@pytest.mark.asyncio
async def test_certify_purchase_insufficient_balance():
    """Certification fails when operator balance is too low."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600)

    # Mock ledger with zero balance
    ledger = UserLedger(balance_api_sats=0)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
    ):
        result = await srv.certify_purchase("op-1", 1000)

    assert result["success"] is False
    assert "Insufficient" in result["error"]


@pytest.mark.asyncio
async def test_certify_purchase_negative_amount():
    """Negative amount is rejected."""
    import tollbooth_authority.server as srv

    result = await srv.certify_purchase("op-1", -100)
    assert result["success"] is False
    assert "positive" in result["error"]


@pytest.mark.asyncio
async def test_certify_purchase_zero_amount():
    """Zero amount is rejected."""
    import tollbooth_authority.server as srv

    result = await srv.certify_purchase("op-1", 0)
    assert result["success"] is False


@pytest.mark.asyncio
async def test_certify_purchase_applies_minimum_tax():
    """Tax floor (tax_min_sats) is enforced."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600)

    # For amount=100: ceil(100 * 2.0 / 100) = 2 < min_sats=10, so tax=10
    ledger = UserLedger(balance_api_sats=500)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
    ):
        result = await srv.certify_purchase("op-1", 100)

    assert result["success"] is True
    assert result["tax_paid_sats"] == 10  # min_sats, not 2%
    assert result["net_sats"] == 90


# ---------------------------------------------------------------------------
# register_operator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_operator():
    import tollbooth_authority.server as srv

    ledger = UserLedger(balance_api_sats=0)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    with (
        patch.object(srv, "_require_user_id", return_value="op-1"),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
    ):
        result = await srv.register_operator()

    assert result["success"] is True
    assert result["operator_id"] == "op-1"


# ---------------------------------------------------------------------------
# operator_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_operator_status():
    import tollbooth_authority.server as srv

    signer = _make_signer()
    ledger = UserLedger(balance_api_sats=500, total_deposited_api_sats=1000, total_consumed_api_sats=500)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)

    with (
        patch.object(srv, "_require_user_id", return_value="op-1"),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
    ):
        result = await srv.operator_status()

    assert result["operator_id"] == "op-1"
    assert result["registered"] is True
    assert result["balance_sats"] == 500
    assert "BEGIN PUBLIC KEY" in result["authority_public_key"]
