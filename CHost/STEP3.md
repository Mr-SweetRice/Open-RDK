# Step 3 Status: Standalone Native Framing

Step 3 implements protocol framing only. It does not discover or open devices.

Delivered:

- `include/openrdkc/framing.h`: bounded public framing API.
- `src/core/framing.c`: incremental allocation-free steady-state parser.
- Stream frame builder for CMD, TEST, TELEMETRY, and CONTROL.
- Control frame builder and module-name response parser.
- Partial-frame buffering and noise resynchronization.
- Invalid length and unknown message-type recovery.
- 24-bit sequence handling, wraparound, and duplicate reporting.
- Parser statistics.
- Native fixture and malformed-input tests.
- Deterministic random-input fuzz smoke test.
- Mandatory AddressSanitizer and UndefinedBehaviorSanitizer runner.

Normal validation:

```bash
cd /home/openrdk/Open-RDK/CHost
.venv/bin/cmake -S . -B build/tests -DORDKC_BUILD_TESTS=ON
.venv/bin/cmake --build build/tests --parallel
.venv/bin/ctest --test-dir build/tests --output-on-failure
```

Mandatory sanitizer validation:

```bash
PATH="$PWD/.venv/bin:$PATH" tools/run_sanitizers.sh
```

Sanitizer dependencies are installed by:

```bash
tools/install_build_requirements_debian.sh
```

On the current Raspberry Pi 4, both GCC 14 ASan and Clang 19 ASan exit during
their own shadow-memory initialization before executing `main`. The kernel is
built with `CONFIG_ARM64_VA_BITS=39`, while the sanitizer allocator attempts to
reserve a range beginning at `0x500000000000`, which the kernel cannot map.

The validation script treats that condition as a failure; it does not skip
ASan. UBSan, normal tests, and fuzz tests run on the Pi, while the mandatory
ASan pass must run on a compatible CI/Linux environment.

The parser allocates once in `ordkc_frame_parser_create`. Calls to
`ordkc_frame_parser_feed` perform no allocation and use a fixed 200-byte payload
buffer.
