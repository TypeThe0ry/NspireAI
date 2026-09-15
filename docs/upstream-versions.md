# Upstream inputs

These are the source revisions inspected for the first implementation. They are
not vendored into this repository; `scripts/bootstrap-upstreams.sh` fetches the
same repositories into `.deps/` and verifies the recorded revisions.

| Component | Repository | Revision inspected | License | Use |
| --- | --- | --- | --- | --- |
| Ndless SDK | [ndless-nspire/Ndless](https://github.com/ndless-nspire/Ndless) | `9484d8da7c7a4dde9766138c2e42e1d1e3acfcd4` (`master`) | MPL 1.1 (repository also contains other notices) | `ndless-sdk/samples/luaext`, ARM toolchain and Luna copy |
| N-Link | [lights0123/n-link](https://github.com/lights0123/n-link) | `0472908ef4961e7eec92cc0b97376420a71e5bc7` (`main`) | GPL-3.0 | Existing CLI syntax and device/file-transfer behavior |
| libnspire-rs | [lights0123/libnspire-rs](https://github.com/lights0123/libnspire-rs) | `098b3f5fdc09a5b5b0d97688672c10d34365786c` (`main`) | GPL-3.0 | `Handle::read_file`, `write_file`, `list_dir`, CX II VID/PID |
| Luna | [ndless-nspire/Luna](https://github.com/ndless-nspire/Luna) | `a9924a9a968954eba9adcc161a58ac607f97ce8c` (`master`) | MPL 1.1 | Lua-to-TNS packaging (`luna INPUT.lua OUTPUT.tns`) |

The N-Link and libnspire-rs licenses are copyleft licenses. Do not copy their
implementation into a differently licensed binary without preserving the
corresponding license and source obligations. This project invokes N-Link as a
separate transport process by default, so the bridge itself remains a small
Python program.

## Local environment inspected on 2026-09-15

* macOS `26.6.2`, `arm64` (Apple Silicon).
* Initially present: `clang`, `cmake`, `make`, Homebrew `libusb 1.0.30`, Lua
  `5.5.1`, Docker `29.3.1`, and native arm64 Python `3.12.7` (the default
  `python3` is an older x86_64 Python `3.9.0`).
* Prepared during this work: Rust `1.98.1`, N-Link CLI `0.1.6`, Homebrew ARM GCC
  `16.2.0`, Debian Docker ARM GCC `12.2` plus newlib `3.3.0`, and OpenAI Python
  SDK `3.14.0` in `bridge/.venv`.
* The checked Ndless SDK wrappers, libraries, `genzehn`, and Zehn loader were
  built under Docker; the host shell does not globally install `nspire-tools`.
* No CX II was connected during this inspection. USB and page-open transfer are
  therefore explicitly **not yet verified**.
