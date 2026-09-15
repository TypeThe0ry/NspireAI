# NspireAI

TI-Nspire CX II AI proof of concept using the native document runtime, a small
Ndless Lua extension, and a Mac-side file bridge. The project intentionally does
not draw a web-style full-screen chat UI.

## What is implemented

* `src/ai-ui-demo.lua` uses TI's `D2Editor.newRichText()` for both the question
  and answer areas. `toolpalette.register()` supplies the native AI menu. Send
  produces the offline fixed echo `Mac received: ...`.
* `src/ai.lua` uses the same controls and polls the optional `nspire_ai` Lua
  extension from `on.timer()`. No USB or model request runs inside a paint or
  menu callback. `Cancel` drops the pending ID, so a late response cannot replace
  the new conversation.
* `src/extension/nspire_ai.c` is a deliberately small `luaext`-style module. It
  only reads/writes `/documents/nspireai/request.id.tns`, `request.tns`,
  `response.id.tns`, and `response.tns`; it never overwrites `AI.tns`.
* `bridge/bridge.py` is the Mac bridge. The default backend is a deterministic
  echo backend. The optional `--backend openai` uses the official Python SDK and
  keeps the API key on the Mac (`OPENAI_API_KEY`), never in a TNS file.
* `bridge/transport.py` has a local-directory simulator for repeatable tests and
  an N-Link CLI adapter. The latter invokes the existing `n-link download` and
  `n-link upload` commands rather than reimplementing USB.

Request IDs are `session-conversation-counter` values. The session prefix comes
from the calculator timer, so reopening the page does not reuse stale response
IDs. The bridge deduplicates IDs,
resets the model context when the conversation prefix changes, and publishes
the response body before its ready-ID marker. A failed upload is retried without
calling the model a second time.

## Build the first UI deliverable

The checked upstream revisions and licenses are in
[docs/upstream-versions.md](docs/upstream-versions.md). Fetch them and build
Luna once:

```sh
./scripts/bootstrap-upstreams.sh
./scripts/build-luna.sh
LUNA_BIN="$PWD/.deps/luna/luna" ./scripts/build-ui.sh
```

This produces `dist/AI-ui-demo.tns` for the first visual check and `dist/AI.tns`
for the later extension-backed page. `AI-ui-demo.tns` is the artifact to put on
the calculator first; it needs no USB or API key.

The UI scripts are deliberately only statically/syntax tested here. The TI
runtime is the authority for actual focus, selection, copy, scrolling, and menu
behavior.

## Build the Mac transport

The existing N-Link source currently builds with Rust and the historical Tauri
manifest if its `desktop/dist` directory exists:

```sh
./scripts/build-n-link.sh
export N_LINK_BIN="$PWD/.deps/n-link/desktop/src-tauri/target/release/n-link"
```

The N-Link CLI usage is `n-link download <remote-file> <local-dir>` and
`n-link upload <local-file> <remote-dir>`. It recognizes TI-Nspire USB VID `0x0451`
and CX II PID `0xe022` through libnspire-rs. With no calculator attached its
historical CLI can panic while opening a device; this is a transport error, not
evidence that the page/bridge protocol is working.

N-Link's file-service root already represents the calculator's Documents area.
Accordingly, the extension's native path `/documents/nspireai` maps to N-Link's
default remote path `/nspireai`; the bridge does not prepend `/documents` on the
Mac side. This mapping still belongs in the real-device verification checklist.

## Run the deterministic echo bridge test

No hardware is needed for this test:

```sh
./scripts/test-bridge.sh

DEVICE_DIR="$(mktemp -d /tmp/nspireai-device.XXXXXX)"
printf 'Hello\n第二行' > "$DEVICE_DIR/request.tns"
printf '1' > "$DEVICE_DIR/request.id.tns"
python3 -m bridge.bridge --transport dir --transport-dir "$DEVICE_DIR" --backend echo --once
cat "$DEVICE_DIR/response.id.tns"
cat "$DEVICE_DIR/response.tns"
```

The CX II file service only accepts TI document extensions over N-Link, so the
exchange files deliberately use `.tns` suffixes. The bridge writes `response.tns`
before `response.id.tns`; the ID is a ready marker.
This is the same ordering used by the calculator extension, so a reader never
needs to open or replace the document to observe a complete response.

## Run the real Mac bridge

With a compiled extension installed on the calculator and the N-Link binary on
the Mac:

```sh
export N_LINK_BIN="$PWD/.deps/n-link/desktop/src-tauri/target/release/n-link"
python3 -m bridge.bridge --transport nlink --backend echo
```

The two runtime files can be uploaded without opening a desktop GUI:

```sh
./scripts/deploy-nspire.sh
```

This uploads the project artifacts to the calculator's Documents root using
N-Link's normal `write_file` behavior. Back up important `.tns` files first.

For the real model backend, install the official SDK in the project venv and
keep the key local. This Mac's default `python3` is an old x86_64 Python 3.9;
the setup script prefers the installed native arm64 Python 3.12:

```sh
./scripts/setup-bridge-python.sh
export OPENAI_API_KEY='...'
export OPENAI_MODEL='gpt-5'
bridge/.venv/bin/python -m bridge.bridge --transport nlink --backend openai
```

The SDK call has a 45-second default timeout; set
`OPENAI_TIMEOUT_SECONDS` or pass `--api-timeout` to change it.

The model result is only displayed as text. The bridge never executes code or
commands returned by a model.

## Ndless extension build

`make extension` uses `nspire-gcc`, `nspire-ld`, `genzehn`, and `make-prg` from
the Ndless SDK. A normal Git clone contains the wrapper scripts but not an ARM
toolchain/newlib sysroot; do not treat a host C compiler as an Ndless compiler.
The reproducible build entry point supplies that toolchain in Docker:

```sh
make extension-docker
```

This currently builds against Debian's ARM GCC/newlib packages and a small
compatibility edit in the ignored checkout under `.deps/` (`PATH_MAX` and the
`fini_array` linker wildcard). It does not touch the calculator or install
Ndless. A native `make extension` remains available when the user's own Ndless
toolchain is installed.

Transfer `dist/AI.tns` and `dist/nspire_ai.luax.tns` to the calculator. The
first hardware test must stay inside the AI page while the bridge is running;
test a short echo, a long/multi-line request, repeated IDs, USB unplug/replug,
and key responsiveness during the bridge wait. No hardware test is claimed in
this repository yet.

## Scope boundaries

* No OS upgrade or Ndless reinstall is performed.
* The current exchange is complete-response file exchange, not streaming.
* The first UI deliberately treats formulas as text. Native Math Box integration
  and Chinese font/input behavior require separate CX II tests.
* The four exchange files have `.tns` suffixes because N-Link rejects arbitrary
  `.txt`/`.id` names. They are ordinary exchange files, not documents to open.
* A N-Link/libnspire file transfer succeeding by itself does not prove that a Lua
  page remains interactive; that is the explicit real-device acceptance check.
