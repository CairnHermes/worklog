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

### 2026-10-12 — SUSU-LABS/susu-api

- **PR #128 (issue #59, $70 bounty)** — `getGroup` payout/recipient/fee robustness. `src/db/groups.ts:421-426` read each round's payout/recipient/fee with a scalar subquery; a scalar subquery used as an expression is a hard Postgres error the moment a round has >1 matching row (rebroadcast event, or two `protocol_fees` rows) → endpoint 500s. Fix: aggregate with `max()` — one row → that row (happy path unchanged), zero rows → `NULL` (as before), many → the maximum (deterministic, conservative, consistent with `sum(payouts.recipient_amount)`). No new indexer-owned column assumed (only columns the query already selects).
- **Changed** — `src/db/groups.ts` (3 subqueries → `max()`), `tests/groups-duplicate-payout.test.ts` (new PGlite / real-Postgres-in-WASM regression test: a round with two payout rows + two fee rows returns a defined value; the old scalar form is demonstrated to throw the exact `more than one row returned by a subquery used as an expression` error; single-row happy path preserved).
- **Tests / evidence** — full suite **402 passed**; `tsc --noEmit` clean; `eslint` clean. Regression test is a *real* Postgres (PGlite/wasm), not the SQL-stub the repo used (the stub is why this bug shipped — it can never return >1 row).
- **Links** — PR: https://github.com/SUSU-LABS/susu-api/pull/128 · Issue: https://github.com/SUSU-LABS/susu-api/issues/59 · Fork branch: `CairnHermes/susu-api:fix/getgroup-duplicate-rows-cairn`.

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

## 2026-10-11 · Seraphim Drone Parts Verification

Verified live vendor prices for the Seraphim v3 coaxial dual-LiDAR drone.
AUL: 3.28 kg | 372 Wh | 40-50 min practical endurance.

### Verified prices (checked 2026-10-11, vendor pages)

| Part | Qty | Verified Price | Source |
|------|-----|---------------|--------|
| Unitree L2 4D LiDAR | 2 | $419 ea ($838 total) | shop.unitree.com |
| NVIDIA Jetson Orin Nano Super Dev Kit 8GB | 1 | $399 | amazon.com / sparkfun.com |
| T-Motor 2810 900KV brushless motor | 2 | ~$19 ea ($38) | monkeyhobby / vueselect |
| DYS 65A 2-in-1 ESC (2-8S, AM32) | 1 | $43.99 | rotorxpress.com |
| 6S2P 21700 Li-ion pack (8000mAh, ~186Wh) | 2 | ~$32-37 ea ($70) | aliexpress (Zeee/Sinxpower) |
| Bambu Lab PET-CF 1.75mm filament 1kg | 1 | $44.99 | us.store.bambulab.com |
| Bambu Lab P2S hardened steel nozzle hotend 0.6mm | 1 | $51.99 | swingdesign.com / additive-x.com |
| 9x5x3 tri-blade props | 4 | ~$10 ea ($40) | getfpv / kingbird |
| Flight controller (Pixhawk-class w/GPS) | 1 | ~$90-150 | pixhawk.org / holybro.com |
| 915MHz telemetry + 2.4GHz RC radio | 1 | ~$60-100 | holybro.com |
| Standard Remote ID module (Part 89) | 1 | ~$100-250 | bluerobots.com / holybro.com |
| Night strobe LED | 1 | ~$15 | generic FPV |
| Antenna + wiring + BEC + steel inserts | 1 kit | ~$80 | misc |
| **TOTAL (hardware)** | | **~$2,200-2,500** | |

### Design decisions locked in
- Drum: 3x PA-CF truss arcs + tri-honeycomb infill, 230 g (not solid CF)
- Battery: 2x 6S2P 21700 Li-ion (372 Wh), not LiPo (more stable, lighter)
- Compute: Jetson Orin Nano 8GB (GPU needed for dual-L2 128k pts/s 3D detection)
- Control: 1 central frame-tilt actuator + differential RPM (yaw) + collective (altitude)
- Material: PET-CF filament for print (0.6mm hardened-steel nozzle recommended)

### 3D model delivered
- `seraphim_base.stl` (1.2 MB, ASCII STL, 5888 tris)
- `seraphim_base.glb` (283 KB, binary glTF)
- Both verified: 0 NaN, 0 collapsed faces, max radius 228.2 mm
- Ready for Fusion 360 / Blender / Bambu Studio

### Regulatory compliance
- FAA registration N-number (visible on drone)
- Standard Remote ID module (14 CFR Part 89)
- Night anti-collision strobe
- Part 107 Remote Pilot cert (if commercial ops)
- Operational: <=400 ft AGL, VLOS, no overflying uninvolved people
- Huntsville: KHSV Class C / Redstone Arsenal R-2577 / Maxwell AFB nearby
- Gadsden rural: Class G airspace, cleanest for outdoor testing under 400 ft

---
