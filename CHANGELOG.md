# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.9.0] — 2026-05-16

### Changed — collapse to thin wheel consumer

Adopts the `tollbooth.authority` mixin from tollbooth-dpyc 0.22.0.
Every piece of generic Authority code that used to live in this repo
now lives in the wheel:

- `actor.py` — deleted (no external consumers; the protocol-conformance
  helper is dropped)
- `config.py` — deleted (AuthoritySettings is now in
  `tollbooth.authority.settings`)
- `nostr_signing.py` — deleted (in `tollbooth.authority.nostr_signing`)
- `onboarding.py` — deleted (in `tollbooth.authority.onboarding`)
- `registry.py` — deleted (was already a thin re-export of
  `tollbooth.registry`)
- `replay.py` — deleted (in `tollbooth.authority.replay`)
- `role_migration.py` — deleted (in
  `tollbooth.authority.role_migration` — invoke with `python -m
  tollbooth.authority.role_migration`)
- `tenant_provisioner.py` — deleted (in
  `tollbooth.authority.tenant_provisioner`)

`server.py` collapses from ~1000 lines of tool definitions and helpers
to ~80 lines of actor-specific configuration: FastMCP instance name,
human-readable instructions, OperatorRuntime construction, and the
two `register_*_tools(mcp, runtime)` calls. The 10 Authority @tool
definitions now live in `tollbooth.authority.tools` and are mounted by
`register_authority_tools(mcp, runtime)`.

Net diff: 8 module files deleted, server.py shrinks by ~900 lines.
Behavior is identical — this is a pure restructuring.

Pin bumped to `tollbooth-dpyc[nostr]==0.22.0`.


## [0.8.0] — 2026-05-16

### Changed — escalate onboarding to the registered parent, not always Prime

`confirm_authority_claim` and `check_authority_approval` previously
hardcoded Prime as the only approver via a local `_resolve_prime_npub`
helper. Replaced with the wheel-side `resolve_my_parent_npub` (added
in tollbooth-dpyc 0.20.0), which reads THIS Authority's own entry from
dpyc-community and returns its `upstream_authority_npub`.

For Lonnie-Authority the upstream IS Prime, so observable behavior is
unchanged. The change matters for sub-Authorities like NewEngland
whose registered upstream is NorthAmerica — their onboarding now
escalates to NA, not Prime. Chain depth is transparent.

Renamed `OnboardingChallenge.prime_npub` → `parent_npub` to reflect
the generalized role (any registered Authority can be the approver).

Pin bumped to `tollbooth-dpyc[nostr]==0.20.0`. Local
`_resolve_prime_npub` helper deleted (~14 lines removed; the wheel
helper subsumes it).

## [0.7.0] — 2026-05-16

### Changed — adopt tollbooth-dpyc v0.19.0, drop local proof helper

- Pinned `tollbooth-dpyc[nostr]==0.19.0`. The wheel's standard
  `authority_check_balance` now requires non-empty npub + proof and
  verifies the proof via `tollbooth.identity_proof.require_proof`.
- Deleted the local `_verify_operator_proof` helper. Its 5 callers
  (`register_operator`, `update_operator`, `deregister_operator`,
  `get_operator_config`, `operator_status`) now import `require_proof`
  from the wheel — DRY restored.
- Deleted the Authority's `check_balance` override. The wheel's
  standard implementation now does what the override did (verify
  proof, require explicit npub).
- Deleted the `mcp._tool_manager._tools.pop(...)` workaround that
  silenced the duplicate-registration warning. With no override
  there is no duplicate.

Net diff: ~50 lines removed, one import added.

## [0.6.7] — 2026-03-03

- Release 0.6.7

## [0.6.0] — 2026-04-13

- security: add proof parameter to all tools with npub
- certify_credits reads fee from runtime._last_debit_cost (C-3)

## [0.5.9] — 2026-04-12

- chore: pin tollbooth-dpyc>=0.5.0 — Horizon OAuth removed from wheel

## [0.5.8] — 2026-04-11

- chore: pin tollbooth-dpyc>=0.4.9 — credential validator fix

## [0.5.7] — 2026-04-11

- chore: pin tollbooth-dpyc>=0.4.8 — ncred fix, courier diagnostics

## [0.5.6] — 2026-04-11

- chore: pin tollbooth-dpyc>=0.4.6 — force_relay, btcpay validation, tranche expiry

## [0.5.5] — 2026-04-11

- fix: enable OTS notarization on Authority

## [0.5.4] — 2026-04-11

- chore: pin tollbooth-dpyc>=0.4.2 — OTS tools excluded when disabled

## [0.5.3] — 2026-04-11

- remove per-transaction upstream certification from certify_credits

## [0.5.2] — 2026-04-11

- fix: override check_balance to fall back to operator npub

## [0.5.1] — 2026-04-11

- fix: pin tollbooth-dpyc>=0.4.1 — trust-root vault bootstrap

## [0.5.0] — 2026-04-11

- refactor Authority onto OperatorRuntime with ad valorem certify_credits
- chore: pin tollbooth-dpyc>=0.3.3
- chore: pin tollbooth-dpyc>=0.3.2 — lazy MCP name resolution
- chore: pin tollbooth-dpyc>=0.3.1 — function name MCP stamping
- chore: pin tollbooth-dpyc>=0.3.0 — single tool identity model
- chore: pin tollbooth-dpyc>=0.2.17 for slug namespace filtering
- chore: pin tollbooth-dpyc>=0.2.16
- fix: remove Horizon OAuth coupling — npub + proof is the sole auth model
- chore: bump to v0.4.12 — drop Oracle OAuth, pin wheel >=0.2.15
- fix: drop OAuth on outbound Oracle calls — Oracle is now public
- chore: pin tollbooth-dpyc>=0.2.14
- chore: pin tollbooth-dpyc>=0.2.13
- feat: UUID-keyed internals — paid_tool and registry use UUID, not short names
- chore: pin tollbooth-dpyc>=0.2.11
- chore: pin tollbooth-dpyc>=0.2.10
- chore: pin tollbooth-dpyc>=0.2.9
- chore: pin tollbooth-dpyc>=0.2.8
- chore: pin tollbooth-dpyc>=0.2.7
- chore: pin tollbooth-dpyc>=0.2.6 for reset_pricing_model
- chore: pin tollbooth-dpyc>=0.2.5
- chore: pin tollbooth-dpyc>=0.2.4 for security fix + legacy UUID fallback
- chore: pin tollbooth-dpyc>=0.2.3 for pricing cache invalidation
- feat: UUID-based tool identity — TOOL_COSTS → TOOL_REGISTRY
- fix: clarify authority check_balance is the operator's tax account
- fix: lint — move resolve_relays import to top of file (E402)
- fix: remove DPYC_AUTHORITY_NPUB env var fallback — vault config only
- chore: pin tollbooth-dpyc>=0.2.0 — clean Neon schema isolation
- fix: include public in operator search_path for new registrations
- chore: pin tollbooth-dpyc>=0.1.173 for onboarding late-attach fix
- chore: pin tollbooth-dpyc>=0.1.171 — don't cache empty ledgers on cold start
- chore: pin tollbooth-dpyc>=0.1.170 for cold start fixes
- chore: pin tollbooth-dpyc>=0.1.169 for session_status lifecycle
- feat: use wheel's themed infographic, delete local copy, pin >=0.1.167
- fix: update tests for operator_id → npub rename
- chore: pin tollbooth-dpyc>=0.1.165 for demurrage constraint rename
- chore: pin tollbooth-dpyc>=0.1.164 for tranche_expiration constraint
- chore: pin tollbooth-dpyc>=0.1.163 for authority_client npub fix
- fix: rename operator_id to npub across all tool parameters
- chore: pin tollbooth-dpyc>=0.1.162 for patron onboarding status
- fix: pin tollbooth-dpyc>=0.1.161
- chore: pin tollbooth-dpyc>=0.1.160

## [0.4.10] — 2026-03-30

- chore: bump to v0.4.10
- fix: resend bootstrap DM on register, update, and get_operator_config

## [0.4.9] — 2026-03-29

- chore: pin tollbooth-dpyc>=0.1.159, bump to 0.4.9
- chore: pin tollbooth-dpyc>=0.1.159, bump version
- refactor: delegate relay resolution to tollbooth wheel's resolve_relays()
- refactor: annotate npub params with operator-self-service descriptions
- chore: bump tollbooth-dpyc to >=0.1.155
- fix: CI matrix Python 3.12+3.13 (matches requires-python >=3.12)
- chore: bump tollbooth-dpyc to >=0.1.152
- chore: require Python >=3.12 (matches Horizon)
- chore: bump tollbooth-dpyc to >=0.1.150
- chore: bump tollbooth-dpyc to >=0.1.147
- chore: bump tollbooth-dpyc to >=0.1.144
- chore: bump tollbooth-dpyc to >=0.1.143
- chore: bump tollbooth-dpyc to >=0.1.138
- chore: bump tollbooth-dpyc to >=0.1.137
- chore: bump tollbooth-dpyc to >=0.1.136
- chore: bump tollbooth-dpyc to >=0.1.135
- chore: bump tollbooth-dpyc to >=0.1.134
- chore: bump tollbooth-dpyc to >=0.1.132
- fix: remove unused _get_settings() calls (F841)
- chore: bump tollbooth-dpyc to >=0.1.131
- fix: remove tier params, add npub fallback for Authority
- feat: send bootstrap config DM to operator at registration
- fix: remove _dpyc_sessions references from tests after session cache removal
- fix: ruff lint cleanup — unused imports + formatting
- ci: add ruff lint step to CI workflow
- refactor: npub is required on all credit tools — no session cache
- refactor: _get_effective_user_id accepts explicit npub override
- feat: update_operator, deregister_operator + service_url on register
- feat: Neon tenant isolation — per-operator schema provisioning
- feat: register_operator calls Oracle to register in community

## [0.4.8] — 2026-03-22

- fix: pricing model name → "Live Tool Pricing" for consistency

## [0.4.7] — 2026-03-22

- chore: bump version to 0.4.7 for release
- fix: rename "Authority Default Pricing" → "Default Pricing"
- chore: bump tollbooth-dpyc to >=0.1.100 (notarization catalog + remove get_tax_rate)

## [0.4.6] — 2026-03-22

- chore: bump tollbooth-dpyc to >=0.1.98 (cache migration fix)
- chore: bump tollbooth-dpyc to >=0.1.97 (tranche TTL expiry)
- refactor: remove tax special-case, use pricing model for certify_credits fee
- revert: pass original amount_sats to upstream certify_credits
- fix: pass net_sats to upstream certify_credits instead of full amount
- chore: bump tollbooth-dpyc to >=0.1.95 for certify_credits rename
- refactor: rename certifier.certify() to certify_credits()
- chore: bump tollbooth-dpyc to >=0.1.94 for rollback tranche expiry
- chore: nudge deploy for tollbooth-dpyc v0.1.93 PyPI release
- chore: sync uv.lock with v0.4.6
- chore: bump tollbooth-dpyc to >=0.1.93
- feat: add operator_id targeting to purchase_credits and check_payment
- chore: add fastmcp.json for Horizon deployment config
- Merge pull request #74 from lonniev/chore/bump-tollbooth-0.1.92
- feat: add default pricing model with ad valorem certify_credits
- Merge pull request #73 from lonniev/chore/bump-tollbooth-0.1.92
- chore: bump tollbooth-dpyc to >=0.1.92 for ACL support
- fix: extract operator_proof from model_json instead of separate tool arg (#72)
- fix: update catalog completeness test for pricing tools, bump to 0.4.5

## [0.4.4] — 2026-03-14

- chore: bump tollbooth-dpyc to >=0.1.91
- chore: redeploy to pick up tollbooth-dpyc tranche details in check_balance
- feat: wire pricing CRUD tools for operator self-service (#71)
- docs: note fee floor validation in README

## [0.4.3] — 2026-03-11

- Merge pull request #70 from lonniev/feat/fee-floor-validation
- feat: reject config where local fee rate < upstream rate
- chore: bump tollbooth-dpyc to >=0.1.83 (#69)

## [0.4.2] — 2026-03-09

- chore: bump tollbooth-dpyc to >=0.1.82, version 0.4.2 (#68)
- chore: bump tollbooth-dpyc to >=0.1.81, version 0.4.1 (#67)

## [0.4.0] — 2026-03-08

- Merge pull request #66 from lonniev/docs/auto-certify-readme
- docs: update README for v0.4.0 auto-certify upstream
- Merge pull request #65 from lonniev/feat/auto-certify-upstream
- feat: auto-certify upstream in certify_credits via AuthorityCertifier

## [0.3.9] — 2026-03-08

- chore: bump version to 0.3.9
- Merge pull request #64 from lonniev/refactor/lookup-cache-path
- refactor: remove redundant dpyc_registry_url config

## [0.3.8] — 2026-03-07

- fix: remove legacy royalty payout + update certify_credits docstring (#63)

## [0.3.7] — 2026-03-06

- Merge pull request #62 from lonniev/feat/authority-onboarding
- feat: add Authority curator onboarding via Nostr DM challenge-response
- chore: trigger FastMCP Cloud redeploy for v0.3.6

## [0.3.6] — 2026-03-06

- fix: rename DPYC_AUTHORITY_ADMIN_NPUB → DPYC_AUTHORITY_NPUB (#61)

## [0.3.5] — 2026-03-06

- Merge pull request #60 from lonniev/fix/admin-npub-env-var
- fix: support DPYC_AUTHORITY_ADMIN_NPUB for report_upstream_purchase
- chore: clarify operator registration tool docstring (#59)
- chore: trigger FastMCP Cloud redeploy for tollbooth-dpyc 0.1.66

## [0.3.4] — 2026-03-03

- Merge pull request #58 from lonniev/feat/slug-prefixing
- feat: slug-prefix all MCP tools with "authority_" to avoid name collisions
- feat: AuthorityActor protocol conformance (#57)
- Merge pull request #56 from lonniev/feat/dynamic-relay-negotiation
- feat: dynamic relay negotiation for audit publisher
- Merge pull request #55 from lonniev/chore/bump-tollbooth-dpyc-0.1.62
- chore: bump tollbooth-dpyc to >=0.1.62

## [0.3.2] — 2026-03-02

- Merge pull request #54 from lonniev/feat/account-statement-infographic
- feat: account statement infographic + fee_sats rename + E2E tests (v0.3.2)
- Merge pull request #53 from lonniev/chore/bump-tollbooth-dpyc-0.1.57
- chore: bump tollbooth-dpyc to >=0.1.57
- chore: bump tollbooth-dpyc to >=0.1.53 (#52)
- Merge pull request #51 from lonniev/chore/bump-tollbooth-dpyc-0.1.52
- chore: bump tollbooth-dpyc to >=0.1.52

## [0.3.1] — 2026-03-01

- chore: force redeploy after NSEC-only identity migration
- Merge pull request #50 from lonniev/feat/nsec-only-identity
- NSEC-only identity: derive Authority npub from nsec, purge env vars (v0.3.1)
- Fix stale JWT/Ed25519 references in docstrings (#49)
- Merge pull request #48 from lonniev/feat/readme-update
- Update README with Nostr certificates, missing tools, and architecture docs
- Bump tollbooth-dpyc minimum to >=0.1.39 (base64 padding fix) (#47)
- Bump tollbooth-dpyc minimum to >=0.1.38 (NIP-17 gift-wrapped DMs) (#46)
- Bump tollbooth-dpyc minimum to >=0.1.37 (ConstraintGate middleware) (#45)
- Bump tollbooth-dpyc minimum to >=0.1.35 (SecureCourierService) (#44)
- Merge pull request #43 from lonniev/fix/dep-bump-0.1.34
- Bump tollbooth-dpyc minimum to >=0.1.34 (relay diagnostics + DM notifications)
- Merge pull request #42 from lonniev/fix/dep-bump-0.1.33
- Bump tollbooth-dpyc minimum to >=0.1.33 (conversational DM + NIP-17)
- Merge pull request #41 from lonniev/fix/dep-bump-0.1.32
- Bump tollbooth-dpyc minimum to >=0.1.32 (welcome DM + profile)
- Bump tollbooth-dpyc minimum to >=0.1.31 (credential vaulting) (#40)
- Merge pull request #39 from lonniev/fix/dep-bump-0.1.29
- Bump tollbooth-dpyc minimum to >=0.1.29 (Secure Courier)
- Merge pull request #38 from lonniev/fix/dep-bump-0.1.28
- Bump tollbooth-dpyc minimum to >=0.1.28 (NIP-44 encrypted audit)

## [0.3.0] — 2026-02-25

- Release 0.3.0

## [0.1.158] — 2026-03-29

- refactor: delegate relay resolution to tollbooth wheel's resolve_relays()
- refactor: annotate npub params with operator-self-service descriptions
- chore: bump tollbooth-dpyc to >=0.1.155
- fix: CI matrix Python 3.12+3.13 (matches requires-python >=3.12)
- chore: bump tollbooth-dpyc to >=0.1.152
- chore: require Python >=3.12 (matches Horizon)
- chore: bump tollbooth-dpyc to >=0.1.150
- chore: bump tollbooth-dpyc to >=0.1.147
- chore: bump tollbooth-dpyc to >=0.1.144
- chore: bump tollbooth-dpyc to >=0.1.143
- chore: bump tollbooth-dpyc to >=0.1.138
- chore: bump tollbooth-dpyc to >=0.1.137
- chore: bump tollbooth-dpyc to >=0.1.136
- chore: bump tollbooth-dpyc to >=0.1.135
- chore: bump tollbooth-dpyc to >=0.1.134
- chore: bump tollbooth-dpyc to >=0.1.132
- fix: remove unused _get_settings() calls (F841)
- chore: bump tollbooth-dpyc to >=0.1.131
- fix: remove tier params, add npub fallback for Authority
- feat: send bootstrap config DM to operator at registration
- fix: remove _dpyc_sessions references from tests after session cache removal
- fix: ruff lint cleanup — unused imports + formatting
- ci: add ruff lint step to CI workflow
- refactor: npub is required on all credit tools — no session cache
- refactor: _get_effective_user_id accepts explicit npub override
- feat: update_operator, deregister_operator + service_url on register
- feat: Neon tenant isolation — per-operator schema provisioning
- feat: register_operator calls Oracle to register in community
- fix: pricing model name → "Live Tool Pricing" for consistency
- chore: bump version to 0.4.7 for release
- fix: rename "Authority Default Pricing" → "Default Pricing"
- chore: bump tollbooth-dpyc to >=0.1.100 (notarization catalog + remove get_tax_rate)
- chore: bump tollbooth-dpyc to >=0.1.98 (cache migration fix)
- chore: bump tollbooth-dpyc to >=0.1.97 (tranche TTL expiry)
- refactor: remove tax special-case, use pricing model for certify_credits fee
- revert: pass original amount_sats to upstream certify_credits
- fix: pass net_sats to upstream certify_credits instead of full amount
- chore: bump tollbooth-dpyc to >=0.1.95 for certify_credits rename
- refactor: rename certifier.certify() to certify_credits()
- chore: bump tollbooth-dpyc to >=0.1.94 for rollback tranche expiry
- chore: nudge deploy for tollbooth-dpyc v0.1.93 PyPI release
- chore: sync uv.lock with v0.4.6
- chore: bump tollbooth-dpyc to >=0.1.93
- feat: add operator_id targeting to purchase_credits and check_payment
- chore: add fastmcp.json for Horizon deployment config
- Merge pull request #74 from lonniev/chore/bump-tollbooth-0.1.92
- feat: add default pricing model with ad valorem certify_credits
- Merge pull request #73 from lonniev/chore/bump-tollbooth-0.1.92
- chore: bump tollbooth-dpyc to >=0.1.92 for ACL support
- fix: extract operator_proof from model_json instead of separate tool arg (#72)
- fix: update catalog completeness test for pricing tools, bump to 0.4.5
- chore: bump tollbooth-dpyc to >=0.1.91
- chore: redeploy to pick up tollbooth-dpyc tranche details in check_balance
- feat: wire pricing CRUD tools for operator self-service (#71)
- docs: note fee floor validation in README
- Merge pull request #70 from lonniev/feat/fee-floor-validation
- feat: reject config where local fee rate < upstream rate
- chore: bump tollbooth-dpyc to >=0.1.83 (#69)
- chore: bump tollbooth-dpyc to >=0.1.82, version 0.4.2 (#68)
- chore: bump tollbooth-dpyc to >=0.1.81, version 0.4.1 (#67)
- Merge pull request #66 from lonniev/docs/auto-certify-readme
- docs: update README for v0.4.0 auto-certify upstream
- Merge pull request #65 from lonniev/feat/auto-certify-upstream
- feat: auto-certify upstream in certify_credits via AuthorityCertifier
- chore: bump version to 0.3.9
- Merge pull request #64 from lonniev/refactor/lookup-cache-path
- refactor: remove redundant dpyc_registry_url config
- fix: remove legacy royalty payout + update certify_credits docstring (#63)
- Merge pull request #62 from lonniev/feat/authority-onboarding
- feat: add Authority curator onboarding via Nostr DM challenge-response
- chore: trigger FastMCP Cloud redeploy for v0.3.6
- fix: rename DPYC_AUTHORITY_ADMIN_NPUB → DPYC_AUTHORITY_NPUB (#61)
- Merge pull request #60 from lonniev/fix/admin-npub-env-var
- fix: support DPYC_AUTHORITY_ADMIN_NPUB for report_upstream_purchase
- chore: clarify operator registration tool docstring (#59)
- chore: trigger FastMCP Cloud redeploy for tollbooth-dpyc 0.1.66
- Merge pull request #58 from lonniev/feat/slug-prefixing
- feat: slug-prefix all MCP tools with "authority_" to avoid name collisions
- feat: AuthorityActor protocol conformance (#57)
- Merge pull request #56 from lonniev/feat/dynamic-relay-negotiation
- feat: dynamic relay negotiation for audit publisher
- Merge pull request #55 from lonniev/chore/bump-tollbooth-dpyc-0.1.62
- chore: bump tollbooth-dpyc to >=0.1.62
- Merge pull request #54 from lonniev/feat/account-statement-infographic
- feat: account statement infographic + fee_sats rename + E2E tests (v0.3.2)
- Merge pull request #53 from lonniev/chore/bump-tollbooth-dpyc-0.1.57
- chore: bump tollbooth-dpyc to >=0.1.57
- chore: bump tollbooth-dpyc to >=0.1.53 (#52)
- Merge pull request #51 from lonniev/chore/bump-tollbooth-dpyc-0.1.52
- chore: bump tollbooth-dpyc to >=0.1.52
- chore: force redeploy after NSEC-only identity migration
- Merge pull request #50 from lonniev/feat/nsec-only-identity
- NSEC-only identity: derive Authority npub from nsec, purge env vars (v0.3.1)
- Fix stale JWT/Ed25519 references in docstrings (#49)
- Merge pull request #48 from lonniev/feat/readme-update
- Update README with Nostr certificates, missing tools, and architecture docs
- Bump tollbooth-dpyc minimum to >=0.1.39 (base64 padding fix) (#47)
- Bump tollbooth-dpyc minimum to >=0.1.38 (NIP-17 gift-wrapped DMs) (#46)
- Bump tollbooth-dpyc minimum to >=0.1.37 (ConstraintGate middleware) (#45)
- Bump tollbooth-dpyc minimum to >=0.1.35 (SecureCourierService) (#44)
- Merge pull request #43 from lonniev/fix/dep-bump-0.1.34
- Bump tollbooth-dpyc minimum to >=0.1.34 (relay diagnostics + DM notifications)
- Merge pull request #42 from lonniev/fix/dep-bump-0.1.33
- Bump tollbooth-dpyc minimum to >=0.1.33 (conversational DM + NIP-17)
- Merge pull request #41 from lonniev/fix/dep-bump-0.1.32
- Bump tollbooth-dpyc minimum to >=0.1.32 (welcome DM + profile)
- Bump tollbooth-dpyc minimum to >=0.1.31 (credential vaulting) (#40)
- Merge pull request #39 from lonniev/fix/dep-bump-0.1.29
- Bump tollbooth-dpyc minimum to >=0.1.29 (Secure Courier)
- Merge pull request #38 from lonniev/fix/dep-bump-0.1.28
- Bump tollbooth-dpyc minimum to >=0.1.28 (NIP-44 encrypted audit)
- Merge pull request #37 from lonniev/fix/dep-bump-0.1.27
- Bump tollbooth-dpyc minimum to >=0.1.27 (Nostr-only)
- Merge pull request #36 from lonniev/feat/nostr-only
- Remove JWT/Ed25519 signing — Nostr-only certificates (Phase 2+3)
- Merge pull request #35 from lonniev/feat/nostr-certificate
- Add Nostr dual-mode certificate signing to certify_credits
- Merge pull request #34 from lonniev/fix/cryptography-floor
- Merge pull request #33 from lonniev/fix/admin-auth-upstream
- Bump cryptography floor to >=46.0.5 and tollbooth-dpyc to >=0.1.25
- Add admin authorization to report_upstream_purchase
- Merge pull request #32 from lonniev/refactor/self-similar-operator
- Rename Authority tools to standard Tollbooth Operator names (v0.2.0)
- Trigger redeploy for tollbooth-dpyc 0.1.23
- Merge pull request #31 from lonniev/chore/bump-tollbooth-0.1.23
- Bump tollbooth-dpyc to >=0.1.23
- Merge pull request #30 from lonniev/chore/bump-tollbooth-0.1.22
- Bump tollbooth-dpyc to >=0.1.22
- Trigger redeploy for NeonVault env vars
- Merge pull request #29 from lonniev/feat/neonvault-cutover
- Cut over operator vault from TheBrainVault to NeonVault + AuditedVault
- Merge pull request #27 from lonniev/feat/account-statement
- Pin tollbooth-dpyc >= 0.1.17 for account_statement_tool
- Merge pull request #26 from lonniev/feat/tranche-credit-expiration
- Adopt tranche-based credit expiration from tollbooth-dpyc 0.1.16
- Merge pull request #25 from lonniev/refactor/remove-refresh-config
- Remove refresh_config tool — Horizon redeploy makes it unnecessary
- Merge pull request #24 from lonniev/feat/service-status
- Add service_status diagnostic tool
- Merge pull request #23 from lonniev/pin/tollbooth-dpyc-0.1.15
- Pin tollbooth-dpyc>=0.1.15 for soft-delete vault support
- Merge pull request #22 from lonniev/fix/pin-tollbooth-0.1.14
- Pin tollbooth-dpyc >= 0.1.14 for child-based vault discovery fix
- Merge pull request #21 from lonniev/fix/pin-tollbooth-0.1.13
- Pin tollbooth-dpyc>=0.1.13 for Azure affinity fix
- Merge pull request #20 from lonniev/fix/dep-pin-0.1.12
- Pin tollbooth-dpyc>=0.1.12 and update vault tests for link-based discovery
- Merge pull request #19 from lonniev/feat/use-shared-vault
- Use canonical TheBrainVault from tollbooth-dpyc
- Bump version to 0.1.1 to force FastMCP Cloud rebuild
- Handle TheBrain API returning 500 on successful creates
- Fix vault note API endpoints to match TheBrain API
- Add vault health diagnostics to operator_status response
- Force redeploy to pick up vault URL fix (api.bra.in)
- Merge pull request #18 from lonniev/fix/vault-api-url
- Fix vault API base URL: api.thebrain.com → api.bra.in
- Merge pull request #17 from lonniev/feat/vault-persistence-docs
- Add vault persistence and VIP tier documentation to MCP instructions
- Merge pull request #16 from lonniev/fix/pin-tollbooth-dpyc-0.1.10
- Pin tollbooth-dpyc >= 0.1.10 for payout processor detection

## [0.1.0] — 2026-02-20

- Merge pull request #15 from lonniev/feat/dpyp-protocol-versioning
- Add dpyc_protocol claim to JWT certificates
- Merge pull request #14 from lonniev/feat/npub-primary-identity
- Make npub the sole DPYC identity for all ledger operations
- Merge pull request #13 from lonniev/fix/registry-parser-wrapper-format
- Fix registry parser to accept members.json wrapper format
- Merge pull request #12 from lonniev/feat/dpyc-identity-and-registry
- Wire DPYC identity, registry enforcement, and authority_npub into Tollbooth Authority
- Merge pull request #11 from lonniev/feat/upstream-supply-constraint
- Enforce upstream cert-sats supply constraint for non-Prime Authorities
- Merge pull request #10 from lonniev/feat/upstream-tax-payout
- Add upstream tax payout for Authority chain revenue backflow
- Merge pull request #9 from lonniev/feat/nostr-keypair-docs
- Add DPYC identity (Nostr npub) section to README
- Merge pull request #8 from lonniev/feat/vault-flush-durability
- Vault caching, shutdown timeout, reconciliation hook
- Pin tollbooth-dpyc >= 0.1.5 (requires purchase_tax_credits_tool)
- Merge pull request #7 from lonniev/feat/vip-tier-config
- Switch to purchase_tax_credits_tool for Authority tax collection
- Wire VIP tier config through to tollbooth-dpyc credit tools
- Merge pull request #6 from lonniev/chore/redeploy
- Trigger Horizon redeploy
- Merge pull request #5 from lonniev/fix/license-mit-to-apache
- Change license from MIT to Apache 2.0
- Merge pull request #4 from lonniev/feat/hero-banner
- Add hero banner image to match tollbooth-dpyc branding
- Merge pull request #3 from lonniev/feat/authority-readme-and-tool-metadata
- Add narrative README and rich tool metadata for LLM bootstrap
- Merge pull request #2 from lonniev/feat/arch-diagram
- Add three-party protocol architecture diagram
- Add Tollbooth Authority — Certified Purchase Order Service (#1)
- Initial commit

