# Persistent CX II transport

The file exchange bridge is retained only as a fallback. It cannot provide the
requested UX because every response upload is a TI document transfer and may
show an “accept new file” prompt.

The CX II path will reuse the existing `libnspire` NavNet SE implementation:

* `packet_send_cx2_nowait()` and `packet_recv_cx2_timeout()` perform the CX II
  NavNet SE framing on the persistent path. The helper's single event loop
  consumes NNSE ACKs and inbound payloads; it does not block every application
  write waiting for an ACK.
* `usb_write()` and `usb_read()` are the existing libusb bulk endpoints.
* The Mac bridge will own one device handle for the lifetime of the process;
  it will not invoke `n-link upload` or `n-link download` per message.
* Application frames will be length-prefixed and carry a request id, opcode,
  and UTF-8 payload. Responses use the same request id and are delivered to
  the Lua extension in memory.

The Ndless side connects to a custom host service (`0x5001`; the older
Ndless `nsocket` example) with
`TI_NN_StartService`; the Mac helper opens that service directly after the
existing `libnspire` address handshake. We intentionally do not call
`TI_NN_NodeEnumInit` from the Lua page: that API requires the full TI/Windows
NavNet host stack (and returns status 1 with a lightweight raw helper).

The service callback stores the channel and emits one fixed bootstrap PONG; the
Lua timer then performs short, non-blocking reads and answers subsequent PINGs.
The remaining physical gate is still explicit: opening the page, sending a
request, receiving the response, and keeping the page visible throughout. The
file transport is retained only as legacy compatibility code and is not used
by `AI.tns`.

## Verification log

* 2026-09-16: Ndless extension, Luna UI, Rust helper, and 14 Python tests
  build/pass. The remote `AI.tns` and `nspire_ai_nav.luax.tns` SHA-256 values
  matched the local artifacts after upload.
* 2026-09-16: Mac helper opened the attached CX II (`cx2=true ready=true`) and
  sent repeated `0x8001` bootstrap frames. No calculator `RX` frame has yet
  been observed because the latest page instance has not completed the
  page-open/Enter physical test. This is not recorded as a transport pass.
* 2026-09-16: NSAI frames were reduced to a 1200-byte CX II-safe payload and
  long logical messages gained ordered in-memory fragmentation/reassembly;
  Python tests cover a multi-frame 5000+ byte response.
