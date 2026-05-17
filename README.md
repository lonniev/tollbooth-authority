# Tollbooth Authority

<p align="center">
  <img src="https://raw.githubusercontent.com/lonniev/tollbooth-dpyc/main/docs/tollbooth-hero.png" alt="Milo drives the Lightning Turnpike — Don't Pester Your Customer" width="800">
</p>

**The institution that built the infrastructure.**

> *The metaphors in this project are drawn with admiration from* The Phantom Tollbooth *by Norton Juster, illustrated by Jules Feiffer (1961). Milo, Tock, the Tollbooth, Dictionopolis, and Digitopolis are creations of Mr. Juster's extraordinary imagination. We just built the payment infrastructure.*

---

## The Turnpike Authority

Every turnpike has an authority. Not the operators who run the booths, and not the drivers who pay the fares — but the institution that poured the concrete, erected the signs, and stamped the purchase orders.

The Tollbooth Authority is the Massachusetts Turnpike Authority of the Lightning economy. It doesn't operate any toll booths. It doesn't touch operator BTCPay stores. It never sees user payment data. What it does is simpler and more essential:

- It **registers operators** who want to run toll booths on the turnpike.
- It **collects a modest certification fee** — 2% of every fare, minimum 10 sats — paid in advance via Lightning.
- It **stamps purchase orders** — Schnorr-signed Nostr event certificates (kind 30079) that prove an operator has paid their fee before collecting a fare.
- It **never touches the fare itself**. The operator collects from the user directly.

The Authority's signature is the proof that the turnpike is legitimate. Without the stamp, the toll booth doesn't open.

## Architecture

As of v0.9.0, this Authority is an ~80-line thin consumer of the [tollbooth-dpyc](https://github.com/lonniev/tollbooth-dpyc) wheel. Every piece of generic Authority code — onboarding state machine, Schnorr certificate signer, replay tracker, Neon tenant provisioner, and the full 10-tool MCP surface — lives in the wheel's `tollbooth.authority` package. This repo's `server.py` supplies only actor-specific configuration: identity name, human-readable instructions, OperatorRuntime construction, and two `register_*_tools(mcp, runtime)` calls.

```python
# The entire server.py, distilled
from fastmcp import FastMCP
from tollbooth.authority import (
    AUTHORITY_TOOL_REGISTRY,
    OPERATOR_CREDENTIAL_TEMPLATE,
    register_authority_tools,
)
from tollbooth.runtime import OperatorRuntime, register_standard_tools
from tollbooth.tool_identity import STANDARD_IDENTITIES

mcp = FastMCP("tollbooth-authority", instructions="…")
runtime = OperatorRuntime(
    tool_registry={**STANDARD_IDENTITIES, **AUTHORITY_TOOL_REGISTRY},
    purchase_mode="direct",           # Authority is its own trust root
    ots_enabled=True,
    operator_credential_template=OPERATOR_CREDENTIAL_TEMPLATE,
)
register_standard_tools(mcp, "authority", runtime, ...)
register_authority_tools(mcp, runtime)
```

Refactor history:

- **v0.5.0** introduced `OperatorRuntime` and delegated standard tools to the wheel — ~1,900 lines → ~970.
- **v0.19.0** required+verified proof on every tool that names it; the local `_verify_operator_proof` helper was promoted to wheel-side `tollbooth.identity_proof.require_proof`.
- **v0.21.0** (wheel) promoted 6 supporting modules (onboarding, nostr_signing, replay, tenant_provisioner, role_migration, settings) from forked Authority code into `tollbooth.authority.*`.
- **v0.22.0** (wheel) promoted the 10 Authority `@tool` definitions into `register_authority_tools(mcp, runtime)`.
- **v0.9.0** (this release) deleted the eight now-redundant modules and slimmed server.py from ~970 lines to ~80. NorthAmerica and NewEngland followed suit in their own `0.4.0` releases.

### Three-Party Protocol

The Tollbooth ecosystem is a three-party protocol spanning three repositories:

| Repo | Role |
|------|------|
| **tollbooth-authority** (this repo) | The institution — fee collection, Schnorr signing, purchase order certification |
| [tollbooth-dpyc](https://github.com/lonniev/tollbooth-dpyc) | The booth — operator-side credit ledger, BTCPay client, tool gating |
| [thebrain-mcp](https://github.com/lonniev/thebrain-mcp) | The first city — reference MCP server powered by Tollbooth |

### How It Works

1. **Register.** An operator connects to the Authority via [Horizon MCP](https://www.fastmcp.cloud/) and calls `register_operator(npub=...)`. The Authority creates a ledger entry and provisions an isolated Neon schema for the operator.

2. **Fund.** The operator calls `purchase_credits` with the number of sats they want to pre-fund. The Authority returns a Lightning invoice from its own BTCPay Server. The operator pays. After settlement, `check_payment` credits the balance.

3. **Certify.** When a user wants to buy credits from an operator, the operator's server calls `certify_credits`. The Authority deducts the 2% ad valorem fee (via the `@runtime.paid_tool` decorator), signs a Schnorr-based Nostr event certificate, and returns it.

4. **Verify.** The operator's [tollbooth-dpyc](https://github.com/lonniev/tollbooth-dpyc) library verifies the certificate using the Authority's Nostr npub. Only if the stamp is valid does the operator create a Lightning invoice for the user. No stamp, no fare.

### Nostr Certificate Format (kind 30079)

Certificates are Schnorr-signed Nostr events (NIP-33 parameterized replaceable events) rather than Ed25519 JWTs. Each certificate contains the operator npub in a `p` tag, the certified amount and protocol in `t`/`L` tags, an `expiration` tag, and the content field holds the structured claim data. Verification uses BIP-340 Schnorr signatures against the Authority's Nostr npub.

### DPYC Registry Enforcement

The Authority checks the [dpyc-community `members.json`](https://github.com/lonniev/dpyc-community/blob/main/members.json) registry at certification time. Operators must have `"status": "active"` in the registry. The registry is HTTP-cached with a configurable TTL. Design is fail-closed: if the registry is unreachable, certification is denied.

### Upstream Topology Is Registry Metadata

Parent Authority relationships live in the `dpyc-community` registry — each Authority's `upstream_authority_npub` points at its sponsor. Operator MCPs resolve their certifying Authority via `resolve_authority_service(operator_npub)` walking that registry chain; no per-Authority env var configures the upstream. The Authority's own `certify_credits` simply collects the ad valorem fee from its operator's pre-funded balance and signs the certificate — no per-transaction upstream call.

### Ad Valorem Pricing

`certify_credits` is registered as a `@runtime.paid_tool` with 2% ad valorem pricing on the `amount_sats` parameter (minimum 10 sats). The fee is debited by the decorator, and the cost is read from `runtime._last_debit_cost` — no double computation.

### OTS Notarization

OpenTimestamps notarization is enabled (`ots_enabled=True` on the runtime). Ledger state can be notarized and verified through the standard `notarize_ledger` and `get_notarization_proof` tools provided by the wheel.

### Anti-Replay (ReplayTracker)

Every certificate includes a unique JTI (JWT ID). The Authority tracks seen JTIs in an in-memory ordered dict with TTL-based pruning. This prevents certificate replay attacks even if a certificate is intercepted before expiration.

## MCP Tools

All tools are now wheel-defined and registered by the two `register_*_tools` calls in `server.py`. For the full canonical tables (Authority tools mounted by `register_authority_tools`, standard tools mounted by `register_standard_tools`) see the [`tollbooth-dpyc` README](https://github.com/lonniev/tollbooth-dpyc#authority-extension-tollboothauthority). The short version:

- **10 Authority tools** (Schnorr cert signing, operator lifecycle, 3-step Authority onboarding, registry membership check) — `register_authority_tools(mcp, runtime)`.
- **~20 standard tools** (credit/payment, identity, secure courier, npub proof, pricing model, OpenTimestamps notarization, oracle delegation) — `register_standard_tools(mcp, "authority", runtime, ...)`.

Every tool that accepts `npub` requires a non-empty `proof` parameter and verifies it via `tollbooth.identity_proof.require_proof` (wheel v0.19.0+). No exceptions, no fallbacks.

## Getting Started

### Connecting via Horizon MCP

The Authority runs on [FastMCP Cloud](https://www.fastmcp.cloud/). Any MCP client (Claude Desktop, Cursor, your own agent) can connect via Horizon:

```
https://www.fastmcp.cloud/server/lonniev/tollbooth-authority
```

Authentication is automatic — Horizon OAuth identifies you as an operator. No API keys to manage.

### First Connection Walkthrough

Once connected, walk through the bootstrap in order:

1. **`register_operator(npub="npub1...")`** — Creates your ledger entry and provisions a Neon schema. Returns your npub, balance, and Neon URL.
2. **`purchase_credits(amount_sats=1000)`** — Returns a Lightning invoice. Pay it with any Lightning wallet.
3. **`check_payment(invoice_id="...")`** — Pass the invoice ID from step 2. Confirms settlement and credits your balance.
4. **`check_balance`** — Verify your balance is funded.
5. **`operator_status`** — See your registration, balance, vault backend, and the Authority's public key (you'll hardcode this in your tollbooth-dpyc integration).

### Self-Hosting

To run your own Authority instance, set these environment variables:

#### Required

| Variable | Purpose | Example |
|----------|---------|---------|
| `NEON_DATABASE_URL` | Neon Postgres URL for persistent operator ledgers. The Authority IS the trust root -- it reads this from env (unlike certified operators, which bootstrap it from Authority via Nostr DM). At startup, the Authority injects `search_path=authority` into the connection string for per-schema isolation. | `postgresql://...` |
| `TOLLBOOTH_NOSTR_OPERATOR_NSEC` | Nostr secret key (nsec) for Schnorr certificate signing and Secure Courier DMs | `nsec1...` |

#### Credentials via Secure Courier (NOT env vars)

BTCPay credentials are delivered via Secure Courier, not set as environment variables:

| Credential | Description |
|------------|-------------|
| `btcpay_host` | Authority's BTCPay Server URL for fee collection |
| `btcpay_api_key` | BTCPay API key with invoice + payout permissions |
| `btcpay_store_id` | BTCPay store ID for the Authority's fee store |

#### Optional

| Variable | Purpose | Default |
|----------|---------|---------|
| `TOLLBOOTH_NOSTR_RELAYS` | Comma-separated relay URLs | built-in defaults |
| `TOLLBOOTH_NOSTR_AUDIT_ENABLED` | Enable NIP-78 audit trail on vault writes | `false` |
| `CERTIFICATE_TTL_SECONDS` | How long a signed certificate remains valid | `600` (10 min) |
| `DPYC_ENFORCE_MEMBERSHIP` | Enable registry enforcement at certification time | `true` |
| `DPYC_REGISTRY_CACHE_TTL_SECONDS` | How long to cache the DPYC community registry | `300` |

#### Per-Operator Schema Isolation

Each registered operator receives an isolated Neon schema (`op_{hash}`) with a dedicated Postgres LOGIN role. The Authority schema (`authority`) holds the Authority's own ledger. Operator schemas are provisioned automatically by `register_operator` and access is enforced via role-based grants -- no cross-operator data access is possible.

#### Deprecated Alternatives

Legacy deployments may still use `THEBRAIN_API_KEY`, `THEBRAIN_BRAIN_ID`, and `THEBRAIN_VAULT_THOUGHT_ID` environment variables for TheBrain-based vault storage. These are superseded by NeonVault and will be removed in a future release.

## Development

```bash
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -q
```

## Key Generation

### Nostr Signing Key (Schnorr certificates)

The Authority signs certificates with a Nostr nsec/npub keypair. Generate one using any Nostr key generator (e.g., `nak key generate`). The nsec goes in `TOLLBOOTH_NOSTR_OPERATOR_NSEC`; the npub is surfaced via `operator_status` for tollbooth-dpyc verification.

### DPYC Identity (Nostr npub)

Each Authority has a Nostr keypair that identifies it on the DPYC Honor Chain:

```bash
pip install nostr-sdk
python -c "from nostr_sdk import Keys; k = Keys.generate(); print(f'npub: {k.public_key().to_bech32()}'); print(f'nsec (back up!): {k.secret_key().to_bech32()}')"
```

## Further Reading

[The Phantom Tollbooth on the Lightning Turnpike](https://stablecoin.myshopify.com/blogs/our-value/the-phantom-tollbooth-on-the-lightning-turnpike) — the full story of how we're monetizing the monetization of AI APIs, and then fading to the background.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE) for details.

---

*Because every turnpike needs an authority. Not to control the road — just to make sure the stamps are real and the fares are fair.*
