# NspireAI

Chat with an LLM from a TI-Nspire CX II. Yes, really.

> **Verification boundary:** The host-side bridge, UI implementation, protocol, and automated tests are documented here. The latest physical record is more limited: the production UI opens, but the Enter-arm path has not yet produced a trusted calculator application channel, calculator-originated `RX`, or a same-page response. Treat the physical USB/NavNet and model round-trip as **unverified** until [`docs/test-status.md`](docs/test-status.md) records a new result.

The calculator has a 320×240 screen, no network and about as much free RAM as
a 1998 flip phone, so it doesn't do any of the thinking. It's a terminal: you
type, it sends the keys over USB to a computer, and the computer talks to the
model, typesets the answer (Markdown, LaTeX, Chinese) into little grayscale
images and sends them back to be drawn. Everything that needs memory or
a brain lives on the host.

## What you get

- **Chat implementation** for DeepSeek, OpenAI, or a built-in echo backend for host-side testing.
  A key on the calculator (Var) switches the thinking effort: off, low, high, max.
- **Answer rendering.** The host can render math, code and Chinese into image blocks; physical calculator receipt still requires the page-open round trip.
  as a wall of `\frac{...}`.
- **A real input line**: cursor, arrow keys, and a live preview of what you're
  typing. Math in plain input is picked up automatically.
- **Typing that doesn't hurt.** A QWERTY-by-position layout with an on-screen
  legend (Doc cycles through layouts), and Ctrl for LaTeX symbols.
- **Chinese input.** A pinyin IME with a candidate bar. Full sentences,
  abbreviations (`zg` → 中国) and half-typed syllables work, and it learns what
  you pick. Toggle with Ctrl+Space.
- **Web access.** The bridge can offer search and page-reading tools to the model, and can show what it looked up. Menu → 7 turns it off (or set `NSPIREAI_WEB=0`).
- **Persistent sessions** in the host bridge, stored in `~/.config/nspireai/sessions/`.
  Switch with the Cat key.
- **Quick commands** in a two-level menu. Edit `bridge/commands.default.json`
  or drop your own in `~/.config/nspireai/commands.json`.
- The bridge includes USB discovery and retry logic for a calculator connected through a port, hub or dock. Successful discovery is not proof that the page has an application channel.

## How it fits together

```text
calculator (Ndless program)  <--USB / NavNet service 0x5001-->  host helper (Java)
                                                                      |
                                                              Python bridge
                                            (LLM, renderer, IME, sessions, web tools)
```

A few things that took longer to get right than I'd like to admit:

- The program in `src/page/page.c` runs from `main()` with interrupts back on
  and the OS UI task paced by hand, so USB keeps working while the stock
  OS browser is frozen.
- The calculator registers its own NavNet service. The host helper connects,
  speaks first and sends a keepalive every 250 ms.
- The page draws into its own double-buffered framebuffer, in the panel's
  odd portrait scan order.
- The wire format ("protocol 2", `bridge/protocol.py`) carries image blocks,
  overlay screens, key tables, previews, and also key injection and
  framebuffer dumps so tests can run without a human at the keyboard.
- While the page is open the calculator's file service is paused, so deploy
  only after closing it.

The implementation has been developed and exercised against a **CX II CAS running OS 6.2.0.333**. That device/version is the target test platform; it does not mean that the complete USB/NavNet or model round trip has passed. Other versions are untested. See [`docs/test-status.md`](docs/test-status.md) for the dated evidence.

## Running it

The commands below describe the intended development route. A successful build, upload, `READY`, or USB descriptor does not prove a working page-open transport. Check [`docs/test-status.md`](docs/test-status.md) before treating a physical run as complete.

You need a Mac or Linux machine, Java, Python 3 and a CX II with
[Ndless](https://ndless.me) installed.

```sh
# one-time setup
./scripts/bootstrap-upstreams.sh
./scripts/build-n-link.sh
./scripts/setup-bridge-python.sh

# API key (never goes on the calculator)
mkdir -p ~/.config/nspireai
echo 'DEEPSEEK_API_KEY=sk-...' > ~/.config/nspireai/env

# build and deploy the calculator program
make program-docker
./scripts/build-nspire-navnet-helper.sh
./scripts/deploy-program-nspire.sh      # page must be closed

# start the bridge, then open nspire_ai on the calculator
./scripts/run-navnet-bridge.sh deepseek   # or: openai, echo
```

Starting the bridge again replaces one that's already running (set
`NSPIREAI_NO_TAKEOVER=1` to make it refuse instead). The model gets no system
prompt by default; set `NSPIREAI_SYSTEM_PROMPT` in the env file to add one.
For OpenAI use `OPENAI_API_KEY` and `OPENAI_MODEL`.

### Keys

| Key | What it does |
| --- | --- |
| enter / del / esc | arm/send / erase / close the page (physical send path pending verification) |
| ↑ ↓ | scroll the chat |
| ← → | move the cursor |
| menu | quick commands (7 toggles web search) |
| cat | switch, create or delete chats |
| var | thinking effort |
| doc | keyboard layout |
| ctrl + space (or scratchpad) | Chinese input on/off |
| letters, then 1–9 / space | pick a candidate (space = first); arrows page; enter keeps the letters; esc drops them |
| ctrl + key | LaTeX symbols: `( )` → `{ }`, `÷` → `\`, `−` → `_`, `=` → `$` … |

### If the calculator resets

It comes back without Ndless. `scripts/ndless-activate.sh` reinstalls it using
remote keys (no page may be open), then open `nspire_ai` again.

## Hacking on it

- Host tests: `scripts/test-bridge.sh`
- Redeploy and reopen the page unattended: `scripts/page-cycle.sh [echo|deepseek]`
- Drive and inspect a live page: `scripts/pagectl.py` (`type`, `dump`, `status`)
- Check the USB state before touching the hardware: `scripts/check-nspire-usb-state.sh`

More detail lives in `docs/`:

- [`docs/test-status.md`](docs/test-status.md): what has actually been verified on real hardware, in order
- [`docs/navnet-transport.md`](docs/navnet-transport.md): the transport design
- [`docs/navnet-crash-investigation.md`](docs/navnet-crash-investigation.md): the freezes, and what fixed them
- [`docs/upstream-versions.md`](docs/upstream-versions.md): pinned upstream repos and licenses

## Things to know

- **Be careful with old builds.** Earlier iterations froze or crashed the
  handheld. The IRQ-scope build from 2026-09-22 (SHA256
  `ce92ae85e1d60cf9c3d4fea08ff1e897d35e13718cafd0ce23080fddd9e13c6c`) is
  one to never upload. Stick to what the current scripts build.
- The old Lua version is gone for good; it froze the device.
- Frames are capped at 224 bytes (NavNet's service limit is 254, minus our
  header). Longer messages are split and reassembled, up to 64 KiB.
- One calculator at a time; there's no device picker.
- The USB interface is exclusive. Don't run the bridge, N-Link and the helper
  scripts at the same time.
- A calculator USB descriptor or host `READY` state is not a `CONNECTED` application channel. The latest physical test has no trusted calculator-originated `RX` or same-page response.
- Do not describe the model as having completed a calculator round trip until the echo path succeeds on the open page; model API checks and host tests are separate evidence.

## License

GPL-3.0, see [`LICENSE`](LICENSE). Upstream projects keep their own licenses, listed in
`docs/upstream-versions.md`.
