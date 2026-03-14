"""Concrete AuthorityProtocol implementation.

Thin delegation layer over existing server.py tool functions.
No business logic lives here — every method delegates to the
corresponding @mcp.tool() function in server.py via lazy import.
"""

from __future__ import annotations

from typing import Any

from tollbooth.actor_types import ActorRole, ToolPath, ToolPathInfo
from tollbooth.authority_protocol import AuthorityProtocol

_CATALOG: list[ToolPathInfo] = [
    # ── Hot-path (local ledger) ──────────────────────────────────
    ToolPathInfo(
        tool_name="certify_credits",
        path=ToolPath.HOT,
        requires_auth=True,
        cost_tier="FREE",
        agent_hint="Core product — certify a credit purchase for an operator.",
    ),
    ToolPathInfo(
        tool_name="register_operator",
        path=ToolPath.HOT,
        requires_auth=True,
        cost_tier="FREE",
        agent_hint="Register a new operator in the ledger.",
    ),
    ToolPathInfo(
        tool_name="operator_status",
        path=ToolPath.HOT,
        requires_auth=True,
        cost_tier="FREE",
        agent_hint="Return the calling operator's registration info.",
    ),
    ToolPathInfo(
        tool_name="check_balance",
        path=ToolPath.HOT,
        requires_auth=True,
        cost_tier="FREE",
        agent_hint="Return the calling operator's credit balance.",
    ),
    ToolPathInfo(
        tool_name="account_statement",
        path=ToolPath.HOT,
        requires_auth=True,
        cost_tier="FREE",
        agent_hint="Return the calling operator's transaction history.",
    ),
    ToolPathInfo(
        tool_name="account_statement_infographic",
        path=ToolPath.HOT,
        requires_auth=True,
        cost_tier="FREE",
        agent_hint="Return a visual summary of the operator's account.",
    ),
    ToolPathInfo(
        tool_name="service_status",
        path=ToolPath.HOT,
        requires_auth=False,
        cost_tier="FREE",
        agent_hint="Return the Authority's health and version info.",
    ),
    ToolPathInfo(
        tool_name="report_upstream_purchase",
        path=ToolPath.HOT,
        requires_auth=True,
        cost_tier="FREE",
        agent_hint="Deprecated — upstream certification is now automatic via certify_credits.",
    ),
    # ── Cold-path (BTCPay) ───────────────────────────────────────
    ToolPathInfo(
        tool_name="purchase_credits",
        path=ToolPath.COLD,
        requires_auth=True,
        cost_tier="FREE",
        agent_hint="Create a Lightning invoice for credit purchase.",
    ),
    ToolPathInfo(
        tool_name="check_payment",
        path=ToolPath.COLD,
        requires_auth=True,
        cost_tier="FREE",
        agent_hint="Poll a Lightning invoice for settlement status.",
    ),
    # ── Cold-path (registry) ─────────────────────────────────────
    ToolPathInfo(
        tool_name="check_dpyc_membership",
        path=ToolPath.COLD,
        requires_auth=True,
        cost_tier="FREE",
        agent_hint="Check whether an npub is a registered DPYC member.",
    ),
    # ── Pricing CRUD ────────────────────────────────────────────
    ToolPathInfo(
        tool_name="get_pricing_model",
        path=ToolPath.HOT,
        requires_auth=False,
        cost_tier="FREE",
        agent_hint="Get the active pricing model for this operator.",
    ),
    ToolPathInfo(
        tool_name="set_pricing_model",
        path=ToolPath.HOT,
        requires_auth=False,
        cost_tier="FREE",
        agent_hint="Set or update the active pricing model.",
    ),
    # ── Authority onboarding (Nostr DM challenge-response) ────
    ToolPathInfo(
        tool_name="register_authority_npub",
        path=ToolPath.COLD,
        requires_auth=False,
        cost_tier="FREE",
        agent_hint=(
            "Step 1/3 of Authority onboarding. Sends a Nostr DM challenge "
            "to the candidate npub. The candidate must reply in their Nostr client."
        ),
    ),
    ToolPathInfo(
        tool_name="confirm_authority_claim",
        path=ToolPath.COLD,
        requires_auth=False,
        cost_tier="FREE",
        agent_hint=(
            "Step 2/3 of Authority onboarding. Verifies candidate's DM reply "
            "and escalates to Prime Authority for approval."
        ),
    ),
    ToolPathInfo(
        tool_name="check_authority_approval",
        path=ToolPath.COLD,
        requires_auth=False,
        cost_tier="FREE",
        agent_hint=(
            "Step 3/3 of Authority onboarding. Checks if Prime approved, "
            "persists curator npub, registers Authority in Oracle registry."
        ),
    ),
]


class AuthorityActor:
    """Concrete AuthorityProtocol implementation.

    Every method delegates to the corresponding @mcp.tool() function
    in tollbooth_authority.server via lazy import.
    """

    @property
    def slug(self) -> str:
        return "authority"

    @classmethod
    def tool_catalog(cls) -> list[ToolPathInfo]:
        return list(_CATALOG)

    # ── Hot-path (local ledger) ──────────────────────────────────

    async def certify_credits(
        self, operator_id: str, amount_sats: int
    ) -> dict[str, Any]:
        from tollbooth_authority.server import certify_credits

        return await certify_credits(
            operator_id=operator_id, amount_sats=amount_sats
        )

    async def register_operator(self, npub: str) -> dict[str, Any]:
        from tollbooth_authority.server import register_operator

        return await register_operator(npub=npub)

    async def operator_status(self) -> dict[str, Any]:
        from tollbooth_authority.server import operator_status

        return await operator_status()

    async def check_balance(self) -> dict[str, Any]:
        from tollbooth_authority.server import check_balance

        return await check_balance()

    async def account_statement(self) -> dict[str, Any]:
        from tollbooth_authority.server import account_statement

        return await account_statement()

    async def account_statement_infographic(self) -> dict[str, Any]:
        from tollbooth_authority.server import account_statement_infographic

        return await account_statement_infographic()

    async def service_status(self) -> dict[str, Any]:
        from tollbooth_authority.server import service_status

        return await service_status()

    async def report_upstream_purchase(
        self, amount_sats: int
    ) -> dict[str, Any]:
        from tollbooth_authority.server import report_upstream_purchase

        return await report_upstream_purchase(amount_sats=amount_sats)

    # ── Cold-path (BTCPay) ───────────────────────────────────────

    async def purchase_credits(self, amount_sats: int) -> dict[str, Any]:
        from tollbooth_authority.server import purchase_credits

        return await purchase_credits(amount_sats=amount_sats)

    async def check_payment(self, invoice_id: str) -> dict[str, Any]:
        from tollbooth_authority.server import check_payment

        return await check_payment(invoice_id=invoice_id)

    # ── Cold-path (registry) ─────────────────────────────────────

    async def check_dpyc_membership(self, npub: str) -> dict[str, Any]:
        from tollbooth_authority.server import check_dpyc_membership

        return await check_dpyc_membership(npub=npub)

    # ── Authority onboarding ─────────────────────────────────────

    async def register_authority_npub(
        self, candidate_npub: str
    ) -> dict[str, Any]:
        from tollbooth_authority.server import register_authority_npub

        return await register_authority_npub(candidate_npub=candidate_npub)

    async def confirm_authority_claim(
        self, candidate_npub: str
    ) -> dict[str, Any]:
        from tollbooth_authority.server import confirm_authority_claim

        return await confirm_authority_claim(candidate_npub=candidate_npub)

    async def check_authority_approval(
        self, candidate_npub: str
    ) -> dict[str, Any]:
        from tollbooth_authority.server import check_authority_approval

        return await check_authority_approval(candidate_npub=candidate_npub)
