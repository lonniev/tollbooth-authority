"""Tests for TheBrainVault with mocked internal methods.

These tests verify the vault's ledger operations (store, fetch, snapshot)
by mocking _discover_members and the low-level API helpers. This mirrors
the upstream tollbooth-dpyc test pattern.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from tollbooth.vaults import TheBrainVault


def _mock_response(status: int = 200, json_data: dict | None = None, text: str = "") -> httpx.Response:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.text = text or (json.dumps(json_data) if json_data else "")
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture
def vault():
    v = TheBrainVault(
        api_key="test-key",
        brain_id="brain-1",
        home_thought_id="home-1",
    )
    v._client = AsyncMock(spec=httpx.AsyncClient)
    return v


# ---------------------------------------------------------------------------
# store_ledger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_ledger_creates_parent_and_child(vault: TheBrainVault):
    """New user: creates ledger parent, registers as hasMember, writes daily child."""
    vault._discover_members = AsyncMock(return_value={})
    vault._create_thought = AsyncMock(return_value={"id": "parent-1"})
    vault._set_note = AsyncMock()
    vault._get_graph = AsyncMock(return_value={"children": [], "links": []})
    vault._register_member = AsyncMock()
    vault._get_children = AsyncMock(return_value=[])

    # Second call to _create_thought for the daily child
    vault._create_thought = AsyncMock(side_effect=[
        {"id": "parent-1"},  # ledger parent
        {"id": "child-1"},   # daily child
    ])

    result = await vault.store_ledger("op-1", '{"balance": 500}')
    assert result == "child-1"
    assert vault._register_member.call_count == 1


@pytest.mark.asyncio
async def test_store_ledger_existing_user_reuses_parent(vault: TheBrainVault):
    """Existing user: finds member via _discover_members, writes to daily child."""
    vault._discover_members = AsyncMock(return_value={"op-1/ledger": "parent-1"})
    vault._get_children = AsyncMock(return_value=[])
    vault._create_thought = AsyncMock(return_value={"id": "child-1"})
    vault._set_note = AsyncMock()

    result = await vault.store_ledger("op-1", '{"balance": 500}')
    assert result == "child-1"


# ---------------------------------------------------------------------------
# fetch_ledger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_ledger_returns_none_for_unknown_user(vault: TheBrainVault):
    vault._discover_members = AsyncMock(return_value={})

    result = await vault.fetch_ledger("unknown-user")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_ledger_reads_most_recent_child(vault: TheBrainVault):
    """Finds ledger parent via _discover_members, reads most recent daily child."""
    vault._discover_members = AsyncMock(return_value={"op-1/ledger": "parent-1"})
    vault._get_children = AsyncMock(return_value=[
        {"id": "day-1", "name": "2026-02-17"},
        {"id": "day-2", "name": "2026-02-18"},
    ])
    vault._get_note = AsyncMock(return_value='{"balance": 300}')

    result = await vault.fetch_ledger("op-1")
    assert result == '{"balance": 300}'
    # Should read the most recent child (day-2)
    vault._get_note.assert_called_with("day-2")


# ---------------------------------------------------------------------------
# snapshot_ledger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_ledger_creates_timestamped_child(vault: TheBrainVault):
    vault._discover_members = AsyncMock(return_value={"op-1/ledger": "parent-1"})
    vault._create_thought = AsyncMock(return_value={"id": "snap-1"})
    vault._set_note = AsyncMock()

    result = await vault.snapshot_ledger("op-1", '{"balance": 100}', "2026-02-18T12:00:00Z")
    assert result == "snap-1"


@pytest.mark.asyncio
async def test_snapshot_returns_none_without_ledger(vault: TheBrainVault):
    vault._discover_members = AsyncMock(return_value={})

    result = await vault.snapshot_ledger("op-1", '{}', "2026-02-18T12:00:00Z")
    assert result is None
