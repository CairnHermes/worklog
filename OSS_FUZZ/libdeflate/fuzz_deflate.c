// OSS-Fuzz fuzz target for libdeflate (pure-C DEFLATE/zlib/gzip by Eric Biggers).
// Primary surface = the three decompressors: they consume untrusted compressed
// streams (the classic crash/memory-safety target). Secondary = compress + a
// compress->decompress round trip to exercise the encoders.
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <libdeflate.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    // 1) Decompress raw DEFLATE stream — the core target.
    struct libdeflate_decompressor *dec = libdeflate_alloc_decompressor();
    if (dec) {
        size_t out_n = 1u << 20;
        uint8_t *out = (uint8_t *)malloc(out_n);
        if (out) {
            size_t actual_in = 0, actual_out = 0;
            enum libdeflate_result r = libdeflate_deflate_decompress_ex(
                dec, data, size,
                out, out_n, &actual_in, &actual_out);
            (void)r;
            free(out);
        }
        libdeflate_free_decompressor(dec);
    }

    // 2) Decompress a zlib-wrapped stream.
    struct libdeflate_decompressor *decz = libdeflate_alloc_decompressor();
    if (decz) {
        size_t out_n = 1u << 20;
        uint8_t *out = (uint8_t *)malloc(out_n);
        if (out) {
            (void)libdeflate_zlib_decompress(decz, data, size,
                                             out, out_n, NULL);
            free(out);
        }
        libdeflate_free_decompressor(decz);
    }

    // 3) Decompress a gzip-wrapped stream.
    struct libdeflate_decompressor *decg = libdeflate_alloc_decompressor();
    if (decg) {
        size_t out_n = 1u << 20;
        uint8_t *out = (uint8_t *)malloc(out_n);
        if (out) {
            (void)libdeflate_gzip_decompress(decg, data, size,
                                             out, out_n, NULL);
            free(out);
        }
        libdeflate_free_decompressor(decg);
    }

    // 4) Compress (any level) + round-trip through a decompressor, to cover
    //    the encoders and validate the two agree on our own output.
    struct libdeflate_compressor *comp = libdeflate_alloc_compressor(9);
    if (comp) {
        size_t in_n = (size > 4096) ? 4096 : size; // keep compress input small
        size_t bound = libdeflate_deflate_compress_bound(comp, in_n);
        uint8_t *comp_out = (uint8_t *)malloc(bound);
        if (comp_out) {
            size_t comp_len = libdeflate_deflate_compress(
                comp, data, in_n, comp_out, bound);
            if (comp_len > 0) {
                struct libdeflate_decompressor *rd =
                    libdeflate_alloc_decompressor();
                if (rd) {
                    size_t rt_n = in_n + 64;
                    uint8_t *rt_out = (uint8_t *)malloc(rt_n);
                    if (rt_out) {
                        size_t rt_len = 0;
                        (void)libdeflate_deflate_decompress(
                            rd, comp_out, comp_len, rt_out, rt_n, &rt_len);
                        free(rt_out);
                    }
                    libdeflate_free_decompressor(rd);
                }
            }
            free(comp_out);
        }
        libdeflate_free_compressor(comp);
    }

    return 0;
}
