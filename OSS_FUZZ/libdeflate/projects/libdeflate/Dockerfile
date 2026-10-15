FROM gcr.io/oss-fuzz-base/base-builder:clang

# libdeflate is self-contained pure C; we only need the base toolchain.
RUN apt-get update && apt-get install -y --no-install-recommends \
        bison flex && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /src
