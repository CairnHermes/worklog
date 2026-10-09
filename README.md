# Worklog — Jackal Corp / Cairn

Public record of every piece of code made or changed. One entry per job:
what was done, exactly what changed, where, tests, and the link (issue/PR/repo).

Purpose: durable evidence of capabilities and an accurate record of the work.

## Format

Each entry:

- **Date (CT)** — repo / issue / PR
- **What** — one line on the job
- **Changed** — files touched, the exact change
- **Tests / evidence**
- **Links**

---

## Entries

### 2026-10-08 — borastellar/ai-bora-stellar

- **PR #112 (issue #107, $60)** — verify_hash constant-time claim. Removed the false "constant-time" comment; documented that pdf_hash is a public commitment (retrievable via get_proposal). Branch `fix/verify-hash-constant-time-claim`, commit on fork `C:\LocalAI\projects\_bora_probe\fork`. Tests: baseline 4/4 + branch 4/4 green. Link: https://github.com/borastellar/ai-bora-stellar/pull/112
- **PR #114 (issue #59, $65)** — record_payment rejects non-positive amounts before any ledger write; total_earned stays 0. New `#[should_panic]` tests (4/4). Branch `fix/agent-registry-and-payment-guards` (split off as single-issue PR). Link: https://github.com/borastellar/ai-bora-stellar/pull/114
- **PR #115 (issue #60, $60)** — payment path checks agent `active` flag before writing earnings; deactivated agents rejected (4/4). Link: https://github.com/borastellar/ai-bora-stellar/pull/115
- **PR #116 (issue #105, $60)** — create_payment rejects a duplicate payment id; original record untouched (5/5). Link: https://github.com/borastellar/ai-bora-stellar/pull/116
- **PR #117 (issue #106, $60)** — create_payment asserts total_amount > 0 up front (zero + negative cases, 6/6). Link: https://github.com/borastellar/ai-bora-stellar/pull/117
- Full-suite run on the combined branch: agent_registry 5, payment_splitter 7, proposal_registry 4 = 16/16 green (commit 3669b26, 140 insertions).
- **Issue #7 ($60, test transitions)** — competing claim by another member; PR HELD by Jake's call. Tests already green on branch `fix/proposal-registry-transition-tests` (6/6) if released.
- **PR #118–119–120 (issues #9 / #10 / #11)** — deactivate-getters test, update-rates test, calculate-split boundary test (branches `fix/issue-9-update-rates-test`, `fix/issue-10-deactivate-getters-test`, `fix/issue-11-calculate-split-boundary`), unclaimed, no competition.

### 2026-10-08/09 — ai-bora (CairnHermes fork)

- **fix/issue-2-execute-split-test** (ef8b3ad) — issue #2 execute-split test fix, 15/15 green local.
- **issue #3/#4/#5 tests** (677e909) — test suite for remaining registry issues, pending split into per-issue PRs (1 PR = 1 issue rule).
- **issue #26** — clean roundtrip-test branch (`fix/issue-26-clean`), roundtrip verified.
- **issue #57 / #58** — fail-loud update-status (`fix/issue-57-fail-loud-update-status`), agent-registry TTL (`fix/issue-58-agent-registry-ttl`).

### 2026-10-09 — borastellar/ai-bora-stellar

- **PR #150 (issue #102, $90)** — escape client.name+email in magic-link email. Files: `back/services/magic-link-email.ts` (pure builder + escapeHtml), `back/test/magic-link-escape.test.ts`, `api/client-magic-link.ts` now uses builder. Tests: `npm run lint` (tsc --noEmit) exit 0; node --test 7/7. Link: https://github.com/borastellar/ai-bora-stellar/pull/150

### 2026-10-09 — rh_agent (Cairn internal)

- `tools/nn_snapshot_board.py` — incumbent now derived from live `research/live_positions.json` (dumped from get_equity_positions each pass; FLAT if empty) instead of hardcoded NEE. Root cause of 10-09 ghost-rotation bug (board kept scoring vs NEE after book rotated to KURA). Test-run OK: incumbent tag data-driven.

### 2026-10-07 — Outerbase/starbaseDB (CUT)

- RLS module probe: proved 4 of the project's own RLS tests fail on original code (SELECT / JOIN x2 / subquery = policy silently dropped). First fix regressed 3 tests + left subquery open; REVERTED to clean tree. Not a paid bounty; cut per Jake.

### 2026-10-07 — Cognitive-OS #5 ($3k research, benched)

- Deliverable BUILT + zipped (600 files, 5 MB): 2 of required 8+ AI systems captured honestly (Qwen 3.8-27B x3 + agent layer), 0 fabrication. Zip: `C:\Users\jakec\Desktop\bounties\Cognitive-OS_job5_pinned_2026-10-07.zip`. Fork branch pushed (d9fb1497de), no PR (awaiting close of system-count gap).

### 2026-10-06 — Jackal Corp internal build (own repos/tools)

- `tools/cairn/cairn_l2.py` — persistent L2 order-book sweep daemon (full 3,811-symbol universe, dedupe on exchange `updated_at`, verified 0 dupes over 4,835 lines) + watchdog cron.
- `tools/cairn/cairn_stockboard.py` (port 8810) — live all-stock board scoring 1,042 liquid names on Jake's ratio (mean-ret x P(up)) at 1wk/2wk/1mo/2mo, wired into hub + `/stockboard` proxy.
- `agent_api/packs.js` — upstream pacing layer: per-host min-gap (CoinGecko 3s), serialized waiters, Retry-After honored on 429. Verified: 10-wide fan-out = full-10, 0x429, 60.5s paced.
- `tools/_best_4horizon.py` / `_best_4horizon_liquid.py` — 4-horizon best-of-stocks filter (3,811 syms).
- `agent_api/hub.js` — /scorecard.json auto-rebuild when snapshot >10 min old (single-flight, 90s timeout, stale fallback); 5 concurrent = one shared build.
- 15m engine anti-edge gate (pos_range<0.15 + ret1<0 -> abstain), leaving the 64% high-range UP edge untouched; test_gate.py 6/6.
- `server.js` 429 semantics, keepalive hardening (public-URL-gated tunnel rotation; live-tested end-to-end).
