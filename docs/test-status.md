# Verification record

Last updated: 2026-09-16 (macOS arm64, TI-Nspire CX II detected).

| Layer | Result | Evidence |
| --- | --- | --- |
| Lua syntax | PASS | `lua -e 'assert(loadfile(...))'` for both scripts |
| Luna packaging | PASS | `.deps/luna/luna` built at commit `a9924a9`; produced `dist/AI-ui-demo.tns` and `dist/AI.tns` |
| Mac bridge echo and NavNet protocol tests | PASS | `./scripts/test-bridge.sh` plus `PYTHONPATH=. bridge/.venv/bin/python -m unittest bridge.test_protocol bridge.test_bridge bridge.test_navnet_bridge` (14 tests); UTF-8, multi-line, long-message fragmentation, repeated IDs, cancellation, conversation reset, incomplete request, retry-after-upload-failure, and response-size guard |
| N-Link CLI build | PASS | Rust 1.98.1; `.deps/n-link/.../target/release/n-link --help` and `license` |
| Ndless C extension compile | PASS in Docker | Debian ARM GCC + newlib container plus the checked SDK produced ARM EABI `dist/nspire_ai_nav.luax.tns`; service id is Mac-valid `0x5001`, not the old device-side `StartService(0x8001)` attempt |
| Host device detection | PASS | `nspireai-usb-helper`: `persistent USB handle open; cx2=true ready=true`; N-Link upload and download both succeeded |
| Deployed artifact identity | PASS | Remote `/AI.tns` SHA-256 `e8e6b9036c327aec1524af10ecd4725118c4a8ec2827886ce2f05c14d3c9bd99`; remote `/nspire_ai_nav.luax.tns` SHA-256 `0873f12283211e079f6ab0e4a19e0b88d3c03f3a47e4bd47675298e9712f2f7e`; both match `dist/` |
| Persistent page-open USB round trip | NOT YET VERIFIED | Host sent repeated `0x8001` bootstrap PINGs, but no calculator `RX` frame was observed because the handheld still had the old Lua page open; the new page must be reopened on the physical CX II |
| OpenAI SDK adapter | IMPORT PASS, API NOT RUN | Official SDK `3.14.0` installed in Python 3.12 venv and `OpenAIBackend` constructed with a placeholder key; no live model call was made |

The persistent transport row is intentionally not called “pass”. The acceptance
point is specifically that `AI.tns` stays open while the Mac receives PING,
request, and response frames over the Ndless service; that cannot be inferred
from a successful TNS build or a successful file upload. The old file-exchange
helpers remain only for compatibility and are not used by the current page.
