# Verification record

Last updated: 2026-09-15 (macOS arm64, no CX II attached).

| Layer | Result | Evidence |
| --- | --- | --- |
| Lua syntax | PASS | `lua -e 'assert(loadfile(...))'` for both scripts |
| Luna packaging | PASS | `.deps/luna/luna` built at commit `a9924a9`; produced `dist/AI-ui-demo.tns` and `dist/AI.tns` |
| Mac bridge echo | PASS | `./scripts/test-bridge.sh` (6 tests); UTF-8, multi-line, 10k text, repeated IDs, incomplete request, conversation reset, retry-after-upload-failure, and response-size guard |
| N-Link CLI build | PASS | Rust 1.98.1; `.deps/n-link/.../target/release/n-link --help` and `license` |
| Ndless C extension compile | PASS in Docker | Debian ARM GCC + newlib container plus the checked SDK produced ARM EABI `dist/nspire_ai.luax.tns`; plain Homebrew compiler alone has no `stdint.h` sysroot |
| UI on CX II | NOT RUN | No calculator connected to this Mac during this run |
| USB file transfer with page open | NOT RUN | Requires the user's CX II, cable, Ndless runtime and the compiled extension |
| OpenAI SDK adapter | IMPORT PASS, API NOT RUN | Official SDK `3.14.0` installed in Python 3.12 venv and `OpenAIBackend` constructed with a placeholder key; no live model call was made |

The last two rows are intentionally not called “pass”. The acceptance point is
specifically that the AI page stays open while the Mac reads/writes the four
ordinary exchange files; that cannot be inferred from a successful TNS build.
