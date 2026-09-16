# NspireAI

TI-Nspire CX II AI terminal with a native TI document page, a small Ndless
Lua extension, and a persistent Mac USB bridge. The live chat path does **not**
upload request/response documents and does not open an “accept new file” UI.

## Architecture

```text
AI.tns (D2Editor + Menu + Enter)
    ↕ in-memory NSAI frames
nspire_ai_nav.luax.tns (Ndless TI_NN_Connect client, private service 0x5001)
    ↕ one persistent CX II USB/NNSE session
nspireai-usb-helper (Rust + libnspire)
    ↕ stdin/stdout frames
navnet_bridge.py (echo or OpenAI Responses API)
```

The custom service number is `0x5001`, in the valid Mac NavNet private-service
range. The older Ndless `nsocket`
example. The calculator page registers the service as soon as it opens and
answers the Mac bootstrap PING without blocking a paint, menu, or key callback.
The Rust helper owns exactly one USB handle and one event loop; CX II writes do
not synchronously wait for ACKs because the same event loop consumes ACKs and
inbound frames.

The old file helpers remain inside the extension only for compatibility with
previous builds. `src/ai.lua` never calls them. Live chat does not read or write
`request.tns`, `response.tns`, or `AI.tns`.

## Current verification status

| Requirement | Current evidence |
| --- | --- |
| Native UI and Enter handler | Lua source built by Luna into `dist/AI.tns` |
| Ndless extension | ARM/Ndless build produces `dist/nspire_ai_nav.luax.tns` |
| Persistent Mac helper | Rust helper builds against the pinned local `libnspire-rs` source |
| Binary protocol, IDs, cancellation, dedupe | Python unit tests pass |
| Echo backend | Unit-tested; physical round trip still pending |
| OpenAI backend | Official SDK installed and code path present; live API needs `OPENAI_API_KEY` |
| Page-open USB round trip | **Not yet claimed** until a CX II returns PONG/request/response while `AI.tns` remains open |
| Unplug/replug, long text, repeated sends | **Not yet claimed**; run after the first physical echo succeeds |

“Build passed” and “uploaded” are not treated as proof of the physical USB
round trip.

## Build

Fetch the pinned upstream repositories and build Luna/N-Link once:

```sh
./scripts/bootstrap-upstreams.sh
./scripts/build-luna.sh
./scripts/build-n-link.sh
./scripts/setup-bridge-python.sh
```

Build the TI documents, Ndless extension, and Mac helper:

```sh
./scripts/build-ui.sh
make extension-docker
cargo build --manifest-path bridge/nspire-helper/Cargo.toml
```

Run all protocol/backend tests:

```sh
PYTHONPATH=. bridge/.venv/bin/python -m unittest \
  bridge.test_protocol bridge.test_bridge bridge.test_navnet_bridge
```

The checked upstream revisions and licenses are recorded in
[`docs/upstream-versions.md`](docs/upstream-versions.md). The live transport
design and remaining hardware gates are in
[`docs/navnet-transport.md`](docs/navnet-transport.md).

## Deploy

Close the persistent bridge before deployment because N-Link and the helper
cannot claim the same USB interface simultaneously:

```sh
./scripts/deploy-nspire.sh
```

This uploads:

* `dist/AI-ui-demo.tns`
* `dist/AI.tns`
* `dist/nspire_ai_nav.luax.tns`

It does not upgrade the calculator OS or reinstall Ndless.

After uploading, fully close any already-open `AI.tns` page and reopen the
file from the calculator document browser. An open Lua page is an in-memory
instance and does not reload merely because a same-named document was uploaded.

## Run the persistent echo bridge

Start it with one command:

```sh
./scripts/run-navnet-bridge.sh echo
```

Expected Mac startup output:

```text
navnet bridge ready: persistent=TI-NavNet service=0x5001
persistent USB handle open; cx2=true ready=true
NavNet AI service open; sid=0x5001
TX bootstrap PING
```

Open `AI.tns`, type `Hello`, and press Enter. The expected answer in the same
page is:

```text
Mac received: Hello
```

No request/response document should appear and the TI page must stay open.

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

1. Keep `AI.tns` open; receive bootstrap PONG and echo `Hello` in that page.
2. Send several requests with Enter without reopening the document.
3. Verify the editor remains responsive while waiting.
4. Verify cancel/new-conversation prevents an old response replacing new state.
5. Send multiline and long UTF-8 text within the currently documented frame
   limit.
6. Unplug/replug USB and verify recovery or record the exact reconnect limit.
7. Start the OpenAI backend with a real key and verify a real model response.

## Scope and known limits

* The UI uses real `D2Editor` and `toolpalette` controls, not SDL or a simulated
  full-screen TI shell.
* Each NSAI frame is capped at 1200 bytes to fit the CX II transport. Larger
  questions and model responses are split and reassembled in memory (up to
  64 KiB per logical message); this still needs physical long-text testing.
* Formula rendering and Chinese font/input behavior are separate device tests.
* The helper currently targets the attached CX II directly; multiple devices
  are not selected interactively.
