"""Neon tenant provisioning for operator isolation.

Each registered operator gets its own PostgreSQL schema within the
Authority's Neon database. The schema name is derived from the operator's
npub to ensure uniqueness and prevent cross-tenant access.

The Authority's bootstrap_config table stores operator-specific settings
(like the schema-qualified Neon URL) in the Authority's own schema,
gated by Schnorr proof in the get_operator_config tool.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

logger = logging.getLogger(__name__)


def schema_name_for_npub(npub: str) -> str:
    """Derive a Postgres-safe schema name from an npub.

    Uses first 16 chars of SHA-256 hex digest — short, unique, safe.
    Prefixed with 'op_' so it doesn't collide with system schemas.
    """
    digest = hashlib.sha256(npub.encode()).hexdigest()[:16]
    return f"op_{digest}"


def neon_url_with_schema(base_url: str, schema: str) -> str:
    """Append search_path option to a Neon connection URL.

    The operator connects with this URL and sees tables in their schema
    first, falling back to ``public`` for shared tables (e.g. ``balances``
    created before per-schema isolation was added).
    """
    parsed = urlparse(base_url)
    params = parse_qs(parsed.query)
    params["options"] = [f"-c search_path={schema},public"]
    new_query = urlencode(params, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


async def ensure_bootstrap_table(vault: Any) -> None:
    """Create the bootstrap_config table if it doesn't exist.

    Stores operator-specific key-value pairs in the Authority's schema.
    Access is gated by Schnorr proof in the get_operator_config tool.
    """
    await vault._execute("""
        CREATE TABLE IF NOT EXISTS bootstrap_config (
            npub TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (npub, key)
        )
    """)


async def provision_operator_schema(vault: Any, npub: str) -> str:
    """Create an isolated Postgres schema for an operator.

    Creates the schema and the standard tollbooth tables within it.
    Returns the schema name.
    """
    schema = schema_name_for_npub(npub)

    # Create schema (idempotent)
    await vault._execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

    # Create standard tollbooth tables in the new schema
    await vault._execute(f"""
        CREATE TABLE IF NOT EXISTS "{schema}".ledger (
            user_id TEXT PRIMARY KEY,
            ledger_json TEXT NOT NULL DEFAULT '{{}}',
            version INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    await vault._execute(f"""
        CREATE TABLE IF NOT EXISTS "{schema}".ledger_journal (
            id BIGSERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            snapshot TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    await vault._execute(f"""
        CREATE TABLE IF NOT EXISTS "{schema}".credentials (
            service TEXT NOT NULL,
            npub TEXT NOT NULL,
            encrypted_json TEXT NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (service, npub)
        )
    """)
    await vault._execute(f"""
        CREATE TABLE IF NOT EXISTS "{schema}".anchors (
            id BIGSERIAL PRIMARY KEY,
            root_hash TEXT NOT NULL,
            leaf_count INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'submitted',
            ots_receipts_json TEXT,
            snapshot_json TEXT,
            leaf_hashes_json TEXT,
            created_at TIMESTAMPTZ,
            confirmed_at TIMESTAMPTZ
        )
    """)

    logger.info("Provisioned schema '%s' for operator %s", schema, npub[:16])
    return schema


async def store_operator_config(
    vault: Any, npub: str, key: str, value: str
) -> None:
    """Store a config entry in the bootstrap table."""
    await vault._execute(
        """
        INSERT INTO bootstrap_config (npub, key, value)
        VALUES ($1, $2, $3)
        ON CONFLICT (npub, key)
        DO UPDATE SET value = $3, created_at = now()
        """,
        [npub, key, value],
    )


async def get_operator_config_value(
    vault: Any, npub: str, key: str
) -> str | None:
    """Retrieve a config entry from the bootstrap table."""
    result = await vault._execute(
        "SELECT value FROM bootstrap_config WHERE npub = $1 AND key = $2",
        [npub, key],
    )
    rows = result.get("rows", [])
    if rows:
        return rows[0][0] if isinstance(rows[0], list) else rows[0].get("value")
    return None


async def get_all_operator_config(vault: Any, npub: str) -> dict[str, str]:
    """Retrieve all config entries for an operator."""
    result = await vault._execute(
        "SELECT key, value FROM bootstrap_config WHERE npub = $1",
        [npub],
    )
    config: dict[str, str] = {}
    for row in result.get("rows", []):
        if isinstance(row, list):
            config[row[0]] = row[1]
        else:
            config[row.get("key", "")] = row.get("value", "")
    return config
