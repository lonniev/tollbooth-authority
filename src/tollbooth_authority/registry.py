"""DPYC community registry — cached membership lookup."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class RegistryError(Exception):
    """Raised when a registry lookup fails (fail closed)."""


class DPYCRegistry:
    """Cached DPYC community membership lookup via HTTP.

    Fetches the community members.json from the registry URL and caches it
    with a monotonic-clock TTL. Any HTTP, parse, or structure error raises
    ``RegistryError`` (fail closed — no silent pass-through).
    """

    def __init__(self, url: str, cache_ttl_seconds: int = 300) -> None:
        self._url = url
        self._ttl = cache_ttl_seconds
        self._client = httpx.AsyncClient(timeout=10.0)
        self._cache: list[dict[str, Any]] | None = None
        self._cache_time: float = 0.0

    async def check_membership(self, npub: str) -> dict[str, Any]:
        """Return the member record for *npub* or raise ``RegistryError``."""
        members = await self._fetch()

        for member in members:
            if member.get("npub") == npub:
                if member.get("status") != "active":
                    raise RegistryError(
                        f"Member {npub} is not active (status={member.get('status')})."
                    )
                return member

        raise RegistryError(f"npub {npub} not found in DPYC registry.")

    async def _fetch(self) -> list[dict[str, Any]]:
        """Return the cached member list, refreshing if stale."""
        now = time.monotonic()
        if self._cache is not None and (now - self._cache_time) < self._ttl:
            return self._cache

        try:
            resp = await self._client.get(self._url)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise RegistryError(f"Registry fetch failed: {e}") from e
        except Exception as e:
            raise RegistryError(f"Registry parse failed: {e}") from e

        # Handle both bare list and {"members": [...]} wrapper formats
        if isinstance(data, dict):
            if "members" in data and isinstance(data["members"], list):
                data = data["members"]
            else:
                raise RegistryError(
                    "Registry JSON object missing 'members' list."
                )
        elif not isinstance(data, list):
            raise RegistryError("Registry JSON is not a list or object.")

        self._cache = data
        self._cache_time = now
        return data

    def invalidate_cache(self) -> None:
        """Force the next ``check_membership`` to re-fetch."""
        self._cache = None
        self._cache_time = 0.0

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
