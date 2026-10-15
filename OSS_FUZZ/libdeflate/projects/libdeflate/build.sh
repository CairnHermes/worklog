#!/bin/bash -eu
# OSS-Fuzz build script for libdeflate.
# Compiles the fuzz_deflate.c target against the upstream v1.21 source
# with the sanitizer + libFuzzer engine provided by the OSS-Fuzz harness.

# Fetch a pinned release (v1.21) for a reproducible build.
VER=1.21
[ -d "libdeflate-$VER" ] || \
  curl -fsSL "https://github.com/ebiggers/libdeflate/releases/download/v$VER/libdeflate-$VER.tar.gz" \
    | tar xz

cd "libdeflate-$VER"

# NOTE: libdeflate v1.21 layout: sources live in lib/ and lib/x86/cpu_features.c
# (lib/x86 is C-only in v1.21 — there is no .S assembly glob to pull).
# v1.21 changed the _ex API: libdeflate_deflate_decompress_ex now takes
# `actual_in_nbytes_ret` between out_nbytes_avail and actual_out_nbytes_ret;
# the checked-in fuzz_deflate.c is written against that v1.21 signature.

$CC -std=c99 -g -O1 -fno-omit-frame-pointer \
    -I. -Ilib \
    "$SRC/fuzz_deflate.c" \
    lib/*.c lib/x86/cpu_features.c \
    -o "$OUT/fuzz_deflate" $LIB_FUZZING_ENGINE

# Seed corpus (small valid streams + random noise).
$SRC/seed_corpus > "$OUT/fuzz_deflate.seed_corpus" 2>/dev/null || true
