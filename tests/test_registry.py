"""Tests for DPYCRegistry — cached membership lookup."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tollbooth_authority.registry import DPYCRegistry, RegistryError


SAMPLE_MEMBERS_LIST = [
    {"npub": "npub1active000000000000000000000000000000000000000000000000abc", "name": "Alice", "status": "active"},
    {"npub": "npub1inactive0000000000000000000000000000000000000000000000xyz", "name": "Bob", "status": "inactive"},
]

SAMPLE_MEMBERS_WRAPPED = {"members": SAMPLE_MEMBERS_LIST}

# Alias for backwards compat with existing tests
SAMPLE_MEMBERS = SAMPLE_MEMBERS_LIST

REGISTRY_URL = "https://example.com/members.json"


def _mock_response(data, status_code=200):
    resp = httpx.Response(status_code=status_code, json=data, request=httpx.Request("GET", REGISTRY_URL))
    return resp


@pytest.mark.asyncio
async def test_active_member_lookup():
    registry = DPYCRegistry(REGISTRY_URL, cache_ttl_seconds=300)
    registry._client.get = AsyncMock(return_value=_mock_response(SAMPLE_MEMBERS))

    member = await registry.check_membership("npub1active000000000000000000000000000000000000000000000000abc")
    assert member["name"] == "Alice"
    assert member["status"] == "active"
    await registry.close()


@pytest.mark.asyncio
async def test_inactive_member_raises():
    registry = DPYCRegistry(REGISTRY_URL, cache_ttl_seconds=300)
    registry._client.get = AsyncMock(return_value=_mock_response(SAMPLE_MEMBERS))

    with pytest.raises(RegistryError, match="not active"):
        await registry.check_membership("npub1inactive0000000000000000000000000000000000000000000000xyz")
    await registry.close()


@pytest.mark.asyncio
async def test_unknown_npub_raises():
    registry = DPYCRegistry(REGISTRY_URL, cache_ttl_seconds=300)
    registry._client.get = AsyncMock(return_value=_mock_response(SAMPLE_MEMBERS))

    with pytest.raises(RegistryError, match="not found"):
        await registry.check_membership("npub1unknown0000000000000000000000000000000000000000000000000")
    await registry.close()


@pytest.mark.asyncio
async def test_http_error_raises():
    registry = DPYCRegistry(REGISTRY_URL, cache_ttl_seconds=300)
    registry._client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    with pytest.raises(RegistryError, match="fetch failed"):
        await registry.check_membership("npub1active000000000000000000000000000000000000000000000000abc")
    await registry.close()


@pytest.mark.asyncio
async def test_invalid_json_raises():
    registry = DPYCRegistry(REGISTRY_URL, cache_ttl_seconds=300)
    # Return a response whose .json() raises
    bad_resp = httpx.Response(200, content=b"not json", request=httpx.Request("GET", REGISTRY_URL))
    registry._client.get = AsyncMock(return_value=bad_resp)

    with pytest.raises(RegistryError, match="parse failed"):
        await registry.check_membership("npub1active000000000000000000000000000000000000000000000000abc")
    await registry.close()


@pytest.mark.asyncio
async def test_wrapper_format_accepted():
    """members.json with {"members": [...]} wrapper is parsed correctly."""
    registry = DPYCRegistry(REGISTRY_URL, cache_ttl_seconds=300)
    registry._client.get = AsyncMock(return_value=_mock_response(SAMPLE_MEMBERS_WRAPPED))

    member = await registry.check_membership("npub1active000000000000000000000000000000000000000000000000abc")
    assert member["name"] == "Alice"
    await registry.close()


@pytest.mark.asyncio
async def test_object_without_members_key_raises():
    """JSON object missing 'members' key is rejected."""
    registry = DPYCRegistry(REGISTRY_URL, cache_ttl_seconds=300)
    registry._client.get = AsyncMock(return_value=_mock_response({"data": []}))

    with pytest.raises(RegistryError, match="missing 'members' list"):
        await registry.check_membership("npub1active000000000000000000000000000000000000000000000000abc")
    await registry.close()


@pytest.mark.asyncio
async def test_non_list_non_object_raises():
    """Scalar JSON (string, number) is rejected."""
    registry = DPYCRegistry(REGISTRY_URL, cache_ttl_seconds=300)
    registry._client.get = AsyncMock(return_value=_mock_response("not a list"))

    with pytest.raises(RegistryError, match="not a list or object"):
        await registry.check_membership("npub1active000000000000000000000000000000000000000000000000abc")
    await registry.close()


@pytest.mark.asyncio
async def test_cache_hit_within_ttl():
    registry = DPYCRegistry(REGISTRY_URL, cache_ttl_seconds=300)
    mock_get = AsyncMock(return_value=_mock_response(SAMPLE_MEMBERS))
    registry._client.get = mock_get

    await registry.check_membership("npub1active000000000000000000000000000000000000000000000000abc")
    await registry.check_membership("npub1active000000000000000000000000000000000000000000000000abc")

    # Only one HTTP call — second was a cache hit
    assert mock_get.call_count == 1
    await registry.close()


@pytest.mark.asyncio
async def test_cache_expired_refetches():
    registry = DPYCRegistry(REGISTRY_URL, cache_ttl_seconds=1)
    mock_get = AsyncMock(return_value=_mock_response(SAMPLE_MEMBERS))
    registry._client.get = mock_get

    await registry.check_membership("npub1active000000000000000000000000000000000000000000000000abc")

    # Simulate cache expiry by backdating
    registry._cache_time = time.monotonic() - 2

    await registry.check_membership("npub1active000000000000000000000000000000000000000000000000abc")

    assert mock_get.call_count == 2
    await registry.close()


@pytest.mark.asyncio
async def test_invalidate_cache_forces_refetch():
    registry = DPYCRegistry(REGISTRY_URL, cache_ttl_seconds=300)
    mock_get = AsyncMock(return_value=_mock_response(SAMPLE_MEMBERS))
    registry._client.get = mock_get

    await registry.check_membership("npub1active000000000000000000000000000000000000000000000000abc")
    registry.invalidate_cache()
    await registry.check_membership("npub1active000000000000000000000000000000000000000000000000abc")

    assert mock_get.call_count == 2
    await registry.close()
