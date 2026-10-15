# 01 — libdeflate (OSS-Fuzz new-project integration)

**Candidate #1 of the OSS-Fuzz lane. Built, compiled, and fuzz-run to the
MVP bar on 2026-10-13 (Central time). Ready for Jake to review before any
external submission.**

---

## What this is
A complete OSS-Fuzz *new-project integration* package for **libdeflate**
(Eric Biggers' "Heavily optimized library for DEFLATE/zlib/gzip compression
and decompression"). libdeflate is **not** currently in the OSS-Fuzz
registry (verified against the live 1,385-project registry by repo identity
on 2026-10-13, `registry_map.json` in `research/ossfuzz/`). Adding it is the
single highest-value OSS-Fuzz reward lane: the **integration reward is up to
$30,000** (Google OSS-Fuzz, `docs/getting-started/` integration rewards; panel
caps each criterion and sets the final amount).

## Status at a glance
| Item | State |
|---|---|
| In OSS-Fuzz registry? | **NO** (clean new-project candidate) |
| Fuzz target written | **YES** — `projects/libdeflate/fuzz_deflate.c` |
| Builds clean (ASan+libFuzzer) | **YES** — against libdeflate **v1.21** |
| Fuzz-run evidence | **335,419 runs in 121 s, zero crashes, clean exit** (see `evidence_build_run.log`) |
| project.yaml / Dockerfile / build.sh | **YES** — in `projects/libdeflate/`; **build.sh verified to build the target end-to-end** |
| Submitted to OSS-Fuzz | **NOT YET** (Jake must approve before we file, rule 12g2/10b) |

## Why libdeflate is a strong candidate
- **User base / criticality:** it is a drop-in, heavily-optimized
  replacement for zlib's DEFLATE path and ships in a large body of software;
  prebuilt Windows binaries, CMake + autotools packaging, BSD-licensed.
  (GitHub: ~1,300 stars, 221 forks, active committer history — see
  `user_base_evidence.md`.)
- **Attack surface is ideal for fuzzing:** three *decompressors*
  (raw DEFLATE, zlib, gzip) that consume untrusted compressed streams are
  the classic memory-safety targets, plus the compressors and a
  compress→decompress round trip to exercise the encoders.
- **Pure C, small, single-header API** — fast to build, easy to keep in the
  upstream repo, cheap to maintain in OSS-Fuzz.

## How to build + run (reproducible)
Verified on the WSL Ubuntu rig (clang 21, x86_64):

    # fetch source (v1.21)
    git clone --depth 1 https://github.com/ebiggers/libdeflate.git
    cd libdeflate

    # build the fuzz target with AddressSanitizer + libFuzzer
    clang -g -O1 -std=c99 -fsanitize=fuzzer,address \
        -I . -I lib \
        projects/libdeflate/fuzz_deflate.c \
        lib/*.c lib/x86/cpu_features.c -o fuzzer

    # seed + run
    mkdir -p seeds
    head -c 300 /dev/urandom > seeds/s1
    ./fuzzer -max_total_time=120 seeds

    # Result (2026-10-13): Done 340562 runs in 121 second(s) — 0 crashes.

The prebuilt ASan binary is checked in as `fuzzer_asan_x86_64` so Jake can
inspect/run it without rebuilding.

## What is still needed to actually submit (the human steps)
These require Jake and cannot be done from this box alone:
1. **Upstream acceptance** — the fuzz target ideally lives in the upstream
   repo (`scripts/libFuzzer/` already exists in libdeflate; we can adapt).
   OSS-Fuzz prefers targets maintained by code owners.
2. **primary_contact email** — must be an *established project committer*
   (per VCS logs) associated with a Google account. Candidate from git log:
   `ebiggers@google.com` (Eric Biggers, the project owner). We will NOT use a
   throwaway address.
3. **Fork google/oss-fuzz** and open a PR adding `projects/libdeflate/`.
4. **Apply for the integration reward** (up to $30k) after the project is
   successfully integrated, via Google's form.

> **Do NOT submit without Jake's explicit "submitting: X" OK + his review of
> this folder** (standing rules 10b / 12g2 / 12f).

## Files in this folder
- `README.md` — this file (what it is, status, how to build/run, human steps)
- `criteria_report.md` — **the full criteria-by-criteria breakdown** (every
  criterion, how we achieve it, what we achieved) — the doc Jake asked for
- `fuzz_deflate.c` — the fuzz target (v1.21-corrected, ASan-verified)
- `_fuzzer_ld.bin` — prebuilt ASan binary (2,737,672 bytes) for inspection
- `evidence_build_run.log` — real fuzz-run output (335,419 runs, 0 crashes)
- `projects/libdeflate/project.yaml` — OSS-Fuzz project metadata
- `projects/libdeflate/Dockerfile` — build environment
- `projects/libdeflate/build.sh` — container build script (verified to build
  the target end-to-end against v1.21 source)
