# NspireAI

> **Do not run the IRQ-scope 0922 handheld build**: the user reported a freeze
> followed by a crash. Its SHA256 is
> `ce92ae85e1d60cf9c3d4fea08ff1e897d35e13718cafd0ce23080fddd9e13c6c`.
> IRQ toggling has been reverted in source. This is not a USB fix; no working
> replacement is currently verified.

> 2026-09-24: the user reported a whole-device freeze after opening the NGC
> AI program and manually reset the calculator. The default replacement build
> is now timer-neutral and starts with NavNet idle; press Menu to arm the
> experimental transport. Do not reopen an older freezing package.

> 2026-09-22: the user rejected and requested removal of the legacy Lua path
> after it froze on the handheld. Its sources, artifacts and build/deploy
> scripts have been removed locally. Continue on `src/program/main.c` and
> `nspire_ai.tns`; `make` builds that program. Older Lua instructions below
> are obsolete. Standalone USB operation is still unresolved: Ndless masks
> interrupts during execution, and exiting the program restored host NODE 1.
> Neither READY nor NODE 1 proves the required same-page request/response loop.

TI-Nspire CX II AI terminal implemented as a standalone Ndless program and a
persistent Mac NavNet bridge. The live chat path keeps one program page open,
sends with Enter, and never uploads request/response documents.

## Architecture

```text
nspire_ai.tns (standalone Ndless SDL program + Enter)
    ↕ in-memory NSAI frames over project-private NavNet service 0x5001
Mac NavNet helper (TI Java host service registration)
    ↕ one persistent CX II USB/NNSE session
navnet_bridge.py (echo or OpenAI Responses API)
```

The service number is `0x5001`, a project-private NavNet service exposed by the
calculator and the Mac helper. The TI Java host accepted `startService(0x5001)`
in a direct probe; its `0x8001` custom-service probe returned `-281`. Keeping
NSAI off built-in `0x4051` avoids sharing TI's Message service with the OS. The
calculator program keeps all chat state in memory, polls with a short timeout,
and never touches a TI document.

The old Lua page and extension have been removed. Live chat does not use
request/response document transfers.

## Current verification status

| Requirement | Current evidence |
| --- | --- |
| Standalone Ndless UI and Enter handler | ARM/Ndless Docker build produces `dist/nspire_ai.tns` |
| Persistent Mac helper | TI Java NavNet `startService(0x5001)` is the default; raw `libnspire-rs` remains experimental |
| Binary protocol, IDs, cancellation, dedupe | Python unit tests pass |
| Echo backend | Unit-tested; physical round trip still pending |
| OpenAI backend | Official SDK installed and code path present; live API needs `OPENAI_API_KEY` |
| Page-open USB round trip | **Not yet claimed** until `nspire_ai.tns` returns request/response while its UI remains open |
| Unplug/replug, long text, repeated sends | **Not yet claimed**; run after the first physical echo succeeds |

“Build passed” and “uploaded” are not treated as proof of the physical USB
round trip.

## Build

Fetch the pinned upstream repositories and build N-Link once:

```sh
./scripts/bootstrap-upstreams.sh
./scripts/build-n-link.sh
./scripts/setup-bridge-python.sh
```

Build the standalone Ndless program:

```sh
make program-docker
./scripts/build-nspire-navnet-helper.sh
```

Run all protocol/backend tests:

```sh
PYTHONPATH=. bridge/.venv/bin/python -m unittest \
  bridge.test_protocol bridge.test_bridge bridge.test_navnet_bridge
```

Verify the TI Java helper also exits without leaving its detached RMI server:

```sh
./scripts/test-nspire-java-helper-lifecycle.sh
```

Verify the complete Python bridge entrypoint also reaches the Java service and
cleans up its helper/RMI children on SIGTERM:

```sh
./scripts/test-navnet-bridge-lifecycle.sh
```

Check the physical USB gate before touching the calculator (the deploy script
also enforces this gate automatically):

```sh
./scripts/check-nspire-usb-state.sh
```

Exit status `0` means a normal CX II USB candidate was found; status `1` means
no usable TI-Nspire interface is present (including a `TPS DMC Family` USB
controller belonging to a dock); the deploy script refuses every non-zero
state.

The standalone page deliberately waits 2 seconds before its first NavNet node
enumeration, retries failed enumeration every 3 seconds, and retries a dropped
channel every 2 seconds. These delays reduce pressure on a USB/host stack that
is still settling after the document opens; they do not replace the physical
same-page round-trip test.

The checked upstream revisions and licenses are recorded in
[`docs/upstream-versions.md`](docs/upstream-versions.md). The live transport
design and remaining hardware gates are in
[`docs/navnet-transport.md`](docs/navnet-transport.md).

## Deploy

Close the persistent bridge before deployment because N-Link and the helper
cannot claim the same USB interface simultaneously:

```sh
./scripts/deploy-program-nspire.sh
```

The current runtime artifact is `dist/nspire_ai.tns`. Upload it to the
calculator root; do not deploy the removed Lua artifacts.

It does not upgrade the calculator OS or reinstall Ndless.

Close the running program before uploading. Reopen `nspire_ai.tns` only when
the current physical test calls for it; USB operation is not yet validated.

## Run the persistent echo bridge

Start the bridge before opening the calculator page:

```sh
./scripts/run-navnet-bridge.sh echo
# Experimental raw transport: NSPIRE_NAVNET_TRANSPORT=raw ./scripts/run-navnet-bridge.sh echo
```

The NavNet entrypoint takes an exclusive lock for the entire USB session. A
second launch exits with status `75` before starting Java, the raw helper, or
any RMI child. Stop the first entrypoint cleanly before running diagnostics;
do not start `nspireai-usb-helper`, `run-nspire-java-helper.sh`, or N-Link
while the bridge is active, since TI's USB interface is exclusive.

Before starting, the entrypoint also requires the read-only USB gate to see a
CX II handheld (`0x0451:0xE022`). If only the TS4 dock controller is visible,
it exits with status `69` and starts no helper. The lifecycle test bypasses this
physical gate explicitly with `NSPIRE_SKIP_USB_GATE=1`.

Expected Mac startup output:

```text
navnet bridge starting: service=0x5001; waiting for helper and calculator
helper: READY service=0x5001
helper: NODE 1
```

After the bridge prints `helper: READY service=0x5001`, open `nspire_ai.tns`
on the calculator. The page starts USB-idle and does not enumerate NavNet by
itself. Press Menu once to arm one transport attempt, then type `Hello` and
press Enter. The expected answer in the same program page is:

```text
Mac received: Hello
```

No request/response document should appear and the TI page must stay open.

If the first attempt reports `USB held`, leave the page open and fix the host
side first; do not keep pressing Menu. The page deliberately stops calling
the synchronous TI NavNet syscalls after the first error. A later single Menu
press is the only retry. This prevents the repeated enumeration loop that can
leave macOS seeing only the TS4 `0xACE1` dock controller until a physical
replug.

## Run the real model backend

The model call runs only on the Mac. Keep the API key in the environment; do
not put it in a TNS file, source file, or log.

```sh
export OPENAI_API_KEY='...'
export OPENAI_MODEL='gpt-5'
./scripts/run-navnet-bridge.sh openai
```

`OPENAI_BASE_URL` and `OPENAI_TIMEOUT_SECONDS` are optional. The implementation
uses the official Python SDK and the Responses API. Conversation history is
kept on the Mac and is reset by the page’s “New conversation” action. Requests
carry a request ID and conversation ID; duplicates and late canceled responses
are ignored. Model output is displayed as text and is never executed.

## Physical acceptance checklist

Do not mark the transport complete until all of these are observed on the same
CX II:

1. Keep `nspire_ai.tns` open; receive bootstrap PONG and echo `Hello` in that page.
2. Send several requests with Enter without reopening the document.
3. Verify the editor remains responsive while waiting.
4. Verify cancel/new-conversation prevents an old response replacing new state.
5. Send multiline and long UTF-8 text within the currently documented frame
   limit.
6. Unplug/replug USB and verify recovery or record the exact reconnect limit.
7. Start the OpenAI backend with a real key and verify a real model response.

## Scope and known limits

* The current runtime is a standalone Ndless SDL program. It does not depend on
  TI document pages or response-file transfers. The old Lua/D2Editor path has
  been removed at the user's request.
* Each NSAI payload is capped at 224 bytes. TI's NavNet service API documents
  a 254-byte service payload ceiling; reserving room for the 16-byte NSAI
  header keeps each frame below that ceiling. Larger questions and model
  responses are split and reassembled in memory (up to 64 KiB per logical
  message); this still needs physical long-text testing.
* Formula rendering and Chinese font/input behavior are separate device tests.
* The helper currently targets the attached CX II directly; multiple devices
  are not selected interactively.
