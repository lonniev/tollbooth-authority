"""Tests for per-operator PostgreSQL role isolation.

Verifies that the migration utility and provisioner helpers emit the
correct SQL for role creation, ownership transfer, and access revocation.
Uses mocked vault._execute() — no real Neon connection needed.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from tollbooth_authority.tenant_provisioner import (
    create_operator_role,
    extract_authority_role,
    generate_operator_password,
    neon_url_for_operator,
    neon_url_with_schema,
    revoke_authority_access,
    schema_name_for_npub,
    transfer_schema_ownership,
)


# -- Fixtures ----------------------------------------------------------------

@pytest.fixture
def vault():
    v = MagicMock()
    v._execute = AsyncMock(return_value={"rows": []})
    return v


SAMPLE_NPUB = "npub1testoperator1234567890abcdef1234567890abcdef1234567890ab"
SAMPLE_SCHEMA = schema_name_for_npub(SAMPLE_NPUB)
SAMPLE_PASSWORD = "test-password-abc123"
SAMPLE_BASE_URL = "postgresql://authority_admin:secret@ep-prod.us-east-2.aws.neon.tech/tollbooth_db"


# -- URL construction --------------------------------------------------------

def test_extract_authority_role():
    role = extract_authority_role(SAMPLE_BASE_URL)
    assert role == "authority_admin"


def test_extract_authority_role_no_user():
    assert extract_authority_role("postgresql://ep-prod.aws.neon.tech/db") == ""


def test_neon_url_for_operator():
    url = neon_url_for_operator(SAMPLE_BASE_URL, SAMPLE_SCHEMA, SAMPLE_PASSWORD)
    assert f"{SAMPLE_SCHEMA}:{SAMPLE_PASSWORD}@" in url
    assert "authority_admin" not in url
    assert "secret" not in url
    # urlencode may encode the = as %3D
    assert SAMPLE_SCHEMA in url
    assert "search_path" in url
    assert url.startswith("postgresql://")
    assert "ep-prod.us-east-2.aws.neon.tech" in url


def test_neon_url_for_operator_escapes_special_chars():
    pw = "p@ss/w0rd+with=special&chars"
    url = neon_url_for_operator(SAMPLE_BASE_URL, SAMPLE_SCHEMA, pw)
    # Password must be URL-encoded
    assert "p%40ss" in url
    assert f"{SAMPLE_SCHEMA}:" in url


def test_neon_url_for_operator_preserves_port():
    url_with_port = "postgresql://user:pass@host:5432/db"
    result = neon_url_for_operator(url_with_port, SAMPLE_SCHEMA, SAMPLE_PASSWORD)
    assert ":5432/" in result


# -- Role creation -----------------------------------------------------------

@pytest.mark.asyncio
async def test_role_creation_sql(vault):
    await create_operator_role(vault, SAMPLE_SCHEMA, SAMPLE_PASSWORD)

    calls = [c.args[0] for c in vault._execute.call_args_list]
    assert any(f'CREATE ROLE "{SAMPLE_SCHEMA}"' in c for c in calls)
    assert any(f"PASSWORD '{SAMPLE_PASSWORD}'" in c for c in calls)


@pytest.mark.asyncio
async def test_schema_grants(vault):
    await create_operator_role(vault, SAMPLE_SCHEMA, SAMPLE_PASSWORD)

    calls = [c.args[0] for c in vault._execute.call_args_list]
    assert any(f'GRANT USAGE ON SCHEMA "{SAMPLE_SCHEMA}" TO "{SAMPLE_SCHEMA}"' in c for c in calls)
    assert any(f'GRANT CREATE ON SCHEMA "{SAMPLE_SCHEMA}" TO "{SAMPLE_SCHEMA}"' in c for c in calls)
    assert any("ALTER DEFAULT PRIVILEGES" in c and "TABLES" in c for c in calls)
    assert any("ALTER DEFAULT PRIVILEGES" in c and "SEQUENCES" in c for c in calls)


@pytest.mark.asyncio
async def test_idempotent_role_creation(vault):
    """CREATE ROLE fails with 'already exists' → falls back to ALTER ROLE."""
    call_count = 0

    async def _execute_side_effect(query, params=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1 and "CREATE ROLE" in query:
            raise Exception("role already exists")
        return {"rows": []}

    vault._execute = AsyncMock(side_effect=_execute_side_effect)

    await create_operator_role(vault, SAMPLE_SCHEMA, SAMPLE_PASSWORD)

    calls = [c.args[0] for c in vault._execute.call_args_list]
    assert any("ALTER ROLE" in c and "PASSWORD" in c for c in calls)


# -- Ownership transfer ------------------------------------------------------

@pytest.mark.asyncio
async def test_table_ownership_transfer(vault):
    await transfer_schema_ownership(vault, SAMPLE_SCHEMA)

    calls = [c.args[0] for c in vault._execute.call_args_list]
    assert any(f'ALTER SCHEMA "{SAMPLE_SCHEMA}" OWNER TO "{SAMPLE_SCHEMA}"' in c for c in calls)
    assert any(f'"{SAMPLE_SCHEMA}".ledger OWNER TO "{SAMPLE_SCHEMA}"' in c for c in calls)
    assert any(f'"{SAMPLE_SCHEMA}".ledger_journal OWNER TO' in c for c in calls)
    assert any(f'"{SAMPLE_SCHEMA}".credentials OWNER TO' in c for c in calls)
    assert any(f'"{SAMPLE_SCHEMA}".anchors OWNER TO' in c for c in calls)


@pytest.mark.asyncio
async def test_transfer_skips_missing_tables(vault):
    """Tables that don't exist should not fail the migration."""
    call_count = 0

    async def _execute_side_effect(query, params=None):
        nonlocal call_count
        call_count += 1
        if "ledger_journal" in query and "ALTER TABLE" in query:
            raise Exception("relation does not exist")
        return {"rows": []}

    vault._execute = AsyncMock(side_effect=_execute_side_effect)

    # Should not raise
    await transfer_schema_ownership(vault, SAMPLE_SCHEMA)


# -- Authority access revocation ---------------------------------------------

@pytest.mark.asyncio
async def test_authority_access_revoked(vault):
    await revoke_authority_access(vault, SAMPLE_SCHEMA, "authority_admin")

    calls = [c.args[0] for c in vault._execute.call_args_list]
    assert any("REVOKE ALL ON ALL TABLES" in c and "FROM PUBLIC" in c for c in calls)
    assert any("REVOKE ALL ON SCHEMA" in c and "FROM PUBLIC" in c for c in calls)
    assert any("FROM \"authority_admin\"" in c for c in calls)


@pytest.mark.asyncio
async def test_revoke_skips_empty_authority_role(vault):
    await revoke_authority_access(vault, SAMPLE_SCHEMA, "")

    calls = [c.args[0] for c in vault._execute.call_args_list]
    # PUBLIC revocations should still happen
    assert any("FROM PUBLIC" in c for c in calls)
    # No revocation targeting an empty role
    assert not any('FROM ""' in c for c in calls)


# -- Password generation ----------------------------------------------------

def test_password_generation():
    pw = generate_operator_password()
    assert len(pw) >= 32
    assert isinstance(pw, str)


def test_password_uniqueness():
    passwords = {generate_operator_password() for _ in range(10)}
    assert len(passwords) == 10


# -- Migration utility -------------------------------------------------------

@pytest.mark.asyncio
async def test_migrate_single_operator():
    from tollbooth_authority.role_migration import migrate_single_operator

    vault = MagicMock()
    vault._execute = AsyncMock(return_value={"rows": []})

    with patch(
        "tollbooth_authority.role_migration.get_all_operator_config",
        new_callable=AsyncMock,
        return_value={"schema": SAMPLE_SCHEMA, "neon_database_url": SAMPLE_BASE_URL},
    ), patch(
        "tollbooth_authority.role_migration.store_operator_config",
        new_callable=AsyncMock,
    ) as mock_store, patch(
        "tollbooth.vault_encryption.VaultCipher",
    ) as mock_cipher_cls:
        mock_cipher = MagicMock()
        mock_cipher.encrypt.return_value = "encrypted_password"
        mock_cipher_cls.return_value = mock_cipher

        result = await migrate_single_operator(
            vault, SAMPLE_NPUB, SAMPLE_BASE_URL, "deadbeef" * 8,
        )

        assert result["success"] is True
        assert result["schema"] == SAMPLE_SCHEMA

        # Password was encrypted before storage
        mock_cipher.encrypt.assert_called_once()

        # Config was updated with encrypted password and new URL
        store_calls = mock_store.call_args_list
        keys_stored = {c.args[2] for c in store_calls}
        assert "role_password" in keys_stored
        assert "neon_database_url" in keys_stored

        # The stored URL should contain operator credentials, not Authority's
        url_call = next(c for c in store_calls if c.args[2] == "neon_database_url")
        stored_url = url_call.args[3]
        assert SAMPLE_SCHEMA in stored_url
        assert "authority_admin" not in stored_url


@pytest.mark.asyncio
async def test_migrate_single_operator_no_schema():
    from tollbooth_authority.role_migration import migrate_single_operator

    vault = MagicMock()
    vault._execute = AsyncMock(return_value={"rows": []})

    with patch(
        "tollbooth_authority.role_migration.get_all_operator_config",
        new_callable=AsyncMock,
        return_value={},
    ):
        result = await migrate_single_operator(
            vault, SAMPLE_NPUB, SAMPLE_BASE_URL, "deadbeef" * 8,
        )
        assert result["success"] is False
        assert "No schema" in result["error"]


@pytest.mark.asyncio
async def test_migrate_all_enumerates_operators():
    from tollbooth_authority.role_migration import migrate_all_operators

    vault = MagicMock()

    # First call: enumerate operators. Subsequent calls: migration SQL.
    npub2 = "npub1secondoperator234567890abcdef1234567890abcdef1234567890a"
    vault._execute = AsyncMock(return_value={
        "rows": [{"npub": SAMPLE_NPUB}, {"npub": npub2}],
    })

    with patch(
        "tollbooth_authority.role_migration.migrate_single_operator",
        new_callable=AsyncMock,
        return_value={"success": True, "npub": "", "schema": ""},
    ) as mock_migrate:
        results = await migrate_all_operators(vault, SAMPLE_BASE_URL, "deadbeef" * 8)

        assert len(results) == 2
        assert mock_migrate.call_count == 2
        migrated_npubs = {c.args[1] for c in mock_migrate.call_args_list}
        assert SAMPLE_NPUB in migrated_npubs
        assert npub2 in migrated_npubs
