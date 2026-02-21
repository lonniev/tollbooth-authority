"""Tests for TheBrainVault with mocked httpx responses."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.mark.asyncio
async def test_store_ledger_creates_parent_and_child(vault: TheBrainVault):
    # Home note is empty (no index)
    vault._client.get = AsyncMock(side_effect=[
        _mock_response(200, text="{}"),  # _read_index -> empty
        _mock_response(200, json_data={"children": []}),  # _get_children
    ])
    vault._client.post = AsyncMock(side_effect=[
        _mock_response(200, json_data={"id": "parent-1"}),  # _create_thought (parent)
        _mock_response(200),  # _write_index
        _mock_response(200, json_data={"id": "child-1"}),  # _create_thought (child)
        _mock_response(200),  # _set_note
    ])

    result = await vault.store_ledger("op-1", '{"balance": 500}')
    assert result == "child-1"


@pytest.mark.asyncio
async def test_fetch_ledger_returns_none_for_unknown_user(vault: TheBrainVault):
    vault._client.get = AsyncMock(return_value=_mock_response(200, text="{}"))
    result = await vault.fetch_ledger("unknown-user")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_ledger_reads_most_recent_child(vault: TheBrainVault):
    index = json.dumps({"op-1/ledger": "ledger-parent-1"})
    vault._client.get = AsyncMock(side_effect=[
        _mock_response(200, text=index),  # _read_index
        _mock_response(200, json_data={"children": [  # _get_children
            {"id": "day-1", "name": "2026-02-17"},
            {"id": "day-2", "name": "2026-02-18"},
        ]}),
        _mock_response(200, text='{"balance": 300}'),  # _get_note (most recent)
    ])

    result = await vault.fetch_ledger("op-1")
    assert result == '{"balance": 300}'


@pytest.mark.asyncio
async def test_snapshot_ledger_creates_timestamped_child(vault: TheBrainVault):
    index = json.dumps({"op-1/ledger": "ledger-parent-1"})
    vault._client.get = AsyncMock(return_value=_mock_response(200, text=index))
    vault._client.post = AsyncMock(side_effect=[
        _mock_response(200, json_data={"id": "snap-1"}),  # _create_thought
        _mock_response(200),  # _set_note
    ])

    result = await vault.snapshot_ledger("op-1", '{"balance": 100}', "2026-02-18T12:00:00Z")
    assert result == "snap-1"


@pytest.mark.asyncio
async def test_snapshot_returns_none_without_ledger(vault: TheBrainVault):
    vault._client.get = AsyncMock(return_value=_mock_response(200, text="{}"))
    result = await vault.snapshot_ledger("op-1", '{}', "2026-02-18T12:00:00Z")
    assert result is None
