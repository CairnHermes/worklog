# 01 — libdeflate — Criteria-by-Criteria Report

Jake's ask: *"a report on every criteria they have and how we achieve it,
as well as what we achieved."*

Two different sets of "criteria" apply. I cover **both**:
- **A. Acceptance criteria** — what OSS-Fuzz requires to *accept* a new
  project into the service (free, continuous fuzzing).
- **B. Reward criteria** — what Google's **OSS-Fuzz Reward Program** (part of
  the Patch Rewards Program) evaluates to pay out the **integration reward**.

Reward money only exists under set B; the service under set A is free. Both
must be met to bank the reward, so this is the full picture.

---

## Reward value + payee (grounded, primary sources)
- **Total reward possible per project integration: up to $30,000.** Google
  expanded it from $20,000 to $30,000 in 2023 ("depending on the criticality
  of the project"). Sources: Google Security Blog (Feb 2023), The Register,
  ZDNet. The program has paid **$600k+ to 65+ contributors** to date.
- **How it is scored:** the reward panel first sets a **cap of up to $5,000
  per criterion**, then decides the actual amount (**$0 up to that cap**) for
  each criterion "depending on how well the criteria is satisfied." Final
  total is at panel discretion.
- **Who gets paid:** the **person who wrote the fuzzers and submits the
  rewards request** — "oftentimes a project maintainer but sometimes it is
  not" (OSS-Fuzz maintainer, GitHub issue #2855). So **we can be the payee**,
  not just the upstream project.
- **Timing:** you apply **after** the project is successfully integrated
  (building in OSS-Fuzz), via Google's integration-reward form.

### Important current-status flag (verify before we bank on it)
- **Google paused the OSS *Vulnerability* Rewards Program (OSS VRP) on
  2026-10-01** due to a "significant rise" in automated/AI-generated invalid
  reports; update expected Q1 2027. This is the *bug-bounty* lane, **not**
  the *integration reward* lane we are using. The OSS-Fuzz service and the
  integration-reward pathway are separate and were not named in that pause.
  **Action:** before we file the PR + reward application, I will re-verify
  the live Patch Rewards / OSS-Fuzz Reward Program status page so we don't
  aim at a paused lane. Flagged now so it isn't a surprise.

---

## A. Acceptance criteria (get the project IN)

### A1. "Significant user base and/or critical to global IT infrastructure"
- **How we achieve it:** libdeflate is a heavily-optimized, drop-in
  replacement for zlib's DEFLATE path, BSD-licensed, shipped in production
  stacks, packaged for Windows (prebuilt binaries), CMake + autotools, and
  widely adopted. This is the reviewer's judgment call, not a checklist box,
  so we arm it with concrete adoption evidence.
- **What we achieved:** gathered `user_base_evidence.md` (GitHub ~1,300
  stars / 221 forks, active committer history, cross-platform packaging,
  drop-in zlib replacement role). This is the weakest-of-the-criteria item
  because it is subjective; the other clean candidates (cmark-gfm, libde265)
  have an even stronger "criticality" story.

### A2. Pull request adding `projects/libdeflate/project.yaml`
- **How we achieve it:** fork google/oss-fuzz, add the project directory.
- **What we achieved:** `project.yaml` drafted (below). All four mandatory
  fields present:
  - `homepage` → https://github.com/ebiggers/libdeflate
  - `main_repo` → https://github.com/ebiggers/libdeflate.git
  - `language` → `c`
  - `primary_contact` → **`ebiggers@google.com`** (Eric Biggers, the project
    owner, confirmed as the dominant committer from git log). This satisfies
    the "established project committer, associated with a Google account"
    requirement and is why we did NOT use a throwaway address.

### A3. Dockerfile (build environment)
- **How we achieve it:** pin a base image, install clang/llvm, provide the
  project source so build.sh can compile.
- **What we achieved:** `Dockerfile` drafted (below) from the OSS-Fuzz base
  image + clang toolchain, matching the pattern of existing C projects.

### A4. build.sh (container build script)
- **How we achieve it:** use OSS-Fuzz's build env vars (`$SRC`, `$OUT`,
  `$CC`, `$CXX`, `$LIB_FUZZING_ENGINE`) to compile the target and emit it to
  `$OUT`.
- **What we achieved:** `build.sh` drafted (below) — compiles
  `fuzz_deflate.c` + the library against `$LIB_FUZZING_ENGINE`.

### A5. Working fuzz target(s)
- **How we achieve it:** write `LLVMFuzzerTestOneInput` exercising the
  highest-risk surface (the three decompressors), plus compress + a
  compress→decompress round trip to hit the encoders.
- **What we achieved:** `fuzz_deflate.c` (corrected for the v1.21 API — the
  earlier draft used the v1.19 `_ex` signature and did not compile; fixed).
  Verified: **builds clean with clang + AddressSanitizer + libFuzzer against
  libdeflate v1.21** and **ran 340,562 inputs in 121 s with zero crashes and
  a clean exit** (`evidence_build_run.log`).

### A6. Ideal-integration niceties (improve reward score, not strictly required)
- **Seed corpus:** we seed from `/dev/urandom` + a tiny valid stream; a richer
  corpus (e.g. the project's own test vectors in `scripts/libFuzzer/`) will be
  added when we place the target upstream.
- **Dictionary:** the 121 s run auto-suggested one (printed in the log) — we
  will bake it into a `fuzz_deflate.dict` for submission.
- **Maintained by code owners:** OSS-Fuzz *prefers* the target live in the
  upstream repo. libdeflate already has a `scripts/libFuzzer/` dir upstream;
  our target adapts cleanly there.
- **Fast, no OOM:** the target keeps the compressor input capped at 4 KB and
  output at 1 MB, so it stays fast and won't OOM the fuzzer.

---

## B. Reward criteria (get PAID)

### B1. Project criticality / importance
- **How we achieve it:** the strongest lever on the reward size ("up to
  $30,000 depending on criticality"). We pick by downstream reach.
- **What we achieved:** libdeflate = core decompression path in many
  production stacks. (Ranked candidates #1–3: libdeflate, cmark-gfm,
  libde265; all three are "clean" = not already in OSS-Fuzz.)

### B2. Quality + completeness of the integration
- **How we achieve it:** meet every ideal-integration item in A5/A6 (corpus,
  dictionary, upstream maintenance, ASan-clean, fast).
- **What we achieved:** target compiles ASan-clean, runs long without
  crashing, and has a dictionary candidate + upstream home. Not yet: a
  curated seed corpus committed to the repo (queued).

### B3. Effort + complexity
- **How we achieve it:** the more surface we responsibly cover (all three
  decompressors + both compressors + round trip) the stronger the case.
- **What we achieved:** single target covering decompress (3 formats) +
  compress + round trip; more targets can be split out if it helps.

### B4. Payee + application
- **How we achieve it:** submit the integration-reward form **as the author
  of the fuzzers** once the project is live in OSS-Fuzz.
- **What we achieved:** payee path understood (we can be the payee). NOT yet
  done: the form application — this is a human step gated on (1) the PR being
  accepted + building, and (2) Jake's go.

---

## Net status of 01_libdeflate
- **Acceptance criteria A1–A6:** A2–A6 drafted and A5 *verified* (built +
  fuzzed clean). A1 is evidence-backed. **All are in place to open the PR.**
- **Reward criteria B1–B4:** B1–B3 in place; **B4 (the payout) is not
  claimed** until integration is live and we apply.
- **Hard gate:** **do not open the PR / apply for reward without Jake's
  explicit OK** (rules 10b / 12f / 12g2) and after re-verifying the reward
  lane is not affected by the Oct-2026 VRP pause.
