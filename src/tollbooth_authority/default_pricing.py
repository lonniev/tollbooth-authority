"""Default pricing model for the Authority.

Returns a synthesized PricingModel when no stored model exists in Neon.
All tools are free except:
  - certify_credits: ad valorem 2% of amount_sats (min 10 sats)
  - account_statement_infographic: 1 sat flat
  - get/set_pricing_model: restricted (free, operator-only)
  - purchase_credits: auth tier (free)
"""

from __future__ import annotations

from tollbooth.pricing_model import PricingModel, ToolPrice


def build_default_model() -> PricingModel:
    """Build the Authority's default pricing model from the tool catalog."""
    tools = [
        # ── Ad valorem (taxation protocol) ────────────────────────
        ToolPrice(
            tool_name="authority_certify_credits",
            price_sats=2,
            category="heavy",
            intent="Certify a credit purchase for an operator.",
            price_type="percent",
            price_formula="amount_sats",
            min_cost=10,
        ),
        # ── 1 sat ─────────────────────────────────────────────────
        ToolPrice(
            tool_name="authority_account_statement_infographic",
            price_sats=1,
            category="read",
            intent="Visual summary of the operator's account.",
        ),
        # ── Auth tier (free) ──────────────────────────────────────
        ToolPrice(
            tool_name="authority_purchase_credits",
            price_sats=0,
            category="auth",
            intent="Create a Lightning invoice for credit purchase.",
        ),
        # ── Restricted (free, operator-only) ──────────────────────
        ToolPrice(
            tool_name="authority_get_pricing_model",
            price_sats=0,
            category="restricted",
            intent="Get the active pricing model.",
        ),
        ToolPrice(
            tool_name="authority_set_pricing_model",
            price_sats=0,
            category="restricted",
            intent="Set or update the active pricing model.",
        ),
        # ── Everything else: free ─────────────────────────────────
        ToolPrice(
            tool_name="authority_register_operator",
            price_sats=0,
            category="free",
            intent="Register a new operator in the ledger.",
        ),
        ToolPrice(
            tool_name="authority_operator_status",
            price_sats=0,
            category="free",
            intent="Return the calling operator's registration info.",
        ),
        ToolPrice(
            tool_name="authority_check_balance",
            price_sats=0,
            category="free",
            intent="Return the calling operator's credit balance.",
        ),
        ToolPrice(
            tool_name="authority_account_statement",
            price_sats=0,
            category="free",
            intent="Return the calling operator's transaction history.",
        ),
        ToolPrice(
            tool_name="authority_check_payment",
            price_sats=0,
            category="free",
            intent="Poll a Lightning invoice for settlement status.",
        ),
        ToolPrice(
            tool_name="authority_check_dpyc_membership",
            price_sats=0,
            category="free",
            intent="Check whether an npub is a registered DPYC member.",
        ),
        ToolPrice(
            tool_name="authority_service_status",
            price_sats=0,
            category="free",
            intent="Return the Authority's health and version info.",
        ),
        ToolPrice(
            tool_name="authority_report_upstream_purchase",
            price_sats=0,
            category="free",
            intent="Deprecated — upstream certification is now automatic.",
        ),
        ToolPrice(
            tool_name="authority_register_authority_npub",
            price_sats=0,
            category="free",
            intent="Step 1/3 of Authority onboarding.",
        ),
        ToolPrice(
            tool_name="authority_confirm_authority_claim",
            price_sats=0,
            category="free",
            intent="Step 2/3 of Authority onboarding.",
        ),
        ToolPrice(
            tool_name="authority_check_authority_approval",
            price_sats=0,
            category="free",
            intent="Step 3/3 of Authority onboarding.",
        ),
    ]

    return PricingModel(
        model_id="default",
        name="Authority Default Pricing",
        is_active=True,
        tools=tools,
        pipeline=[],
    )
