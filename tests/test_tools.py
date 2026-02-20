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
    settings = _make_settings()
    ledger = UserLedger(balance_api_sats=500, total_deposited_api_sats=1000, total_consumed_api_sats=500)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)

    with (
        patch.object(srv, "_require_user_id", return_value="op-1"),
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
    ):
        result = await srv.operator_status()

    assert result["operator_id"] == "op-1"
    assert result["registered"] is True
    assert result["balance_sats"] == 500
    assert "BEGIN PUBLIC KEY" in result["authority_public_key"]
    # Prime Authority — no upstream config surfaced
    assert "upstream_authority_address" not in result


@pytest.mark.asyncio
async def test_operator_status_shows_upstream():
    """operator_status surfaces upstream chain config when configured."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(
        upstream_authority_address="upstream@btcpay.example.com",
        upstream_tax_percent=3.0,
    )
    ledger = UserLedger(balance_api_sats=500, total_deposited_api_sats=1000, total_consumed_api_sats=500)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)

    with (
        patch.object(srv, "_require_user_id", return_value="op-1"),
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
    ):
        result = await srv.operator_status()

    assert result["upstream_authority_address"] == "upstream@btcpay.example.com"
    assert result["upstream_tax_percent"] == 3.0


# ---------------------------------------------------------------------------
# check_tax_payment — upstream tax payout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_tax_payment_fires_upstream_payout():
    """When upstream is configured, check_tax_payment passes royalty params."""
    import tollbooth_authority.server as srv

    settings = _make_settings(
        upstream_authority_address="upstream@btcpay.example.com",
        upstream_tax_percent=2.0,
        upstream_tax_min_sats=10,
    )

    mock_btcpay = MagicMock()
    cache = MagicMock(spec=LedgerCache)

    mock_result = {"success": True, "status": "Settled", "balance_api_sats": 1000}

    with (
        patch.object(srv, "_require_user_id", return_value="op-1"),
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_btcpay", return_value=mock_btcpay),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch("tollbooth_authority.server.check_payment_tool", new_callable=AsyncMock, return_value=mock_result) as mock_cpt,
    ):
        result = await srv.check_tax_payment("inv-123")

    assert result["success"] is True
    # Verify upstream royalty params were passed
    mock_cpt.assert_called_once()
    call_kwargs = mock_cpt.call_args
    assert call_kwargs.kwargs["royalty_address"] == "upstream@btcpay.example.com"
    assert call_kwargs.kwargs["royalty_percent"] == 0.02  # 2.0 / 100
    assert call_kwargs.kwargs["royalty_min_sats"] == 10


@pytest.mark.asyncio
async def test_check_tax_payment_no_upstream_for_prime():
    """Prime Authority (no upstream) passes None for royalty_address."""
    import tollbooth_authority.server as srv

    settings = _make_settings(upstream_authority_address="")

    mock_btcpay = MagicMock()
    cache = MagicMock(spec=LedgerCache)

    mock_result = {"success": True, "status": "Settled", "balance_api_sats": 1000}

    with (
        patch.object(srv, "_require_user_id", return_value="op-1"),
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_btcpay", return_value=mock_btcpay),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch("tollbooth_authority.server.check_payment_tool", new_callable=AsyncMock, return_value=mock_result) as mock_cpt,
    ):
        result = await srv.check_tax_payment("inv-123")

    call_kwargs = mock_cpt.call_args
    assert call_kwargs.kwargs["royalty_address"] is None


# ---------------------------------------------------------------------------
# Upstream supply constraint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_certify_purchase_deducts_supply():
    """Non-Prime Authority: certify_purchase debits amount_sats from supply ledger."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(
        tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600,
        upstream_authority_address="upstream@example.com",
    )

    operator_ledger = UserLedger(balance_api_sats=1000)
    supply_ledger = UserLedger(balance_api_sats=5000)

    async def fake_get(user_id: str) -> UserLedger:
        if user_id == srv.SUPPLY_USER_ID:
            return supply_ledger
        return operator_ledger

    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(side_effect=fake_get)
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
    # Supply should be debited by amount_sats (1000), not tax_sats
    assert supply_ledger.balance_api_sats == 4000
    # Verify supply was marked dirty
    cache.mark_dirty.assert_any_call(srv.SUPPLY_USER_ID)


@pytest.mark.asyncio
async def test_certify_purchase_insufficient_supply():
    """Non-Prime Authority: fails and rolls back tax when supply is too low."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(
        tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600,
        upstream_authority_address="upstream@example.com",
    )

    operator_ledger = UserLedger(balance_api_sats=1000)
    supply_ledger = UserLedger(balance_api_sats=500)  # Not enough for 1000

    async def fake_get(user_id: str) -> UserLedger:
        if user_id == srv.SUPPLY_USER_ID:
            return supply_ledger
        return operator_ledger

    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(side_effect=fake_get)
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

    assert result["success"] is False
    assert "Insufficient upstream supply" in result["error"]
    # Operator tax balance should be rolled back (tax_sats=20 was debited then restored)
    assert operator_ledger.balance_api_sats == 1000
    # Supply should be unchanged
    assert supply_ledger.balance_api_sats == 500


@pytest.mark.asyncio
async def test_certify_purchase_prime_skips_supply():
    """Prime Authority (no upstream): no supply check at all."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(
        tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600,
        upstream_authority_address="",  # Prime
    )

    operator_ledger = UserLedger(balance_api_sats=1000)

    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=operator_ledger)
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
    # cache.get should only be called once (for operator), not for supply
    cache.get.assert_called_once_with("op-1")


@pytest.mark.asyncio
async def test_report_upstream_purchase_credits_supply():
    """report_upstream_purchase credits the supply ledger and returns new balance."""
    import tollbooth_authority.server as srv

    supply_ledger = UserLedger(balance_api_sats=500)

    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=supply_ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    with patch.object(srv, "_get_ledger_cache", return_value=cache):
        result = await srv.report_upstream_purchase(1000)

    assert result["success"] is True
    assert result["supply_balance_sats"] == 1500
    assert result["credited_sats"] == 1000
    cache.mark_dirty.assert_called_once_with(srv.SUPPLY_USER_ID)
    cache.flush_user.assert_called_once_with(srv.SUPPLY_USER_ID)


@pytest.mark.asyncio
async def test_report_upstream_purchase_negative_rejected():
    """report_upstream_purchase rejects non-positive amounts."""
    import tollbooth_authority.server as srv

    result = await srv.report_upstream_purchase(-100)
    assert result["success"] is False
    assert "positive" in result["error"]

    result = await srv.report_upstream_purchase(0)
    assert result["success"] is False


@pytest.mark.asyncio
async def test_operator_status_shows_supply():
    """Non-Prime Authority: operator_status includes upstream supply fields."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(
        upstream_authority_address="upstream@example.com",
        upstream_tax_percent=3.0,
    )

    operator_ledger = UserLedger(
        balance_api_sats=500, total_deposited_api_sats=1000, total_consumed_api_sats=500,
    )
    supply_ledger = UserLedger(
        balance_api_sats=3000, total_consumed_api_sats=2000,
    )

    async def fake_get(user_id: str) -> UserLedger:
        if user_id == srv.SUPPLY_USER_ID:
            return supply_ledger
        return operator_ledger

    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(side_effect=fake_get)

    with (
        patch.object(srv, "_require_user_id", return_value="op-1"),
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
    ):
        result = await srv.operator_status()

    assert result["upstream_supply_sats"] == 3000
    assert result["upstream_supply_consumed_sats"] == 2000
