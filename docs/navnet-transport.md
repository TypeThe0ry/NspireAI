# Persistent CX II transport

## Freeze prevention

The standalone SDL loop requests `NAV_READ_TIMEOUT=1` for `TI_NN_Read`.
This is a requested API timeout, not a verified wall-clock bound on CX II;
the synchronous call can still freeze keyboard/event processing if USB/NavNet
scheduling stops. A 2.5-second handshake watchdog only runs when the main loop
continues executing, so it cannot recover a stuck syscall.
The local rebuilt artifact currently hashes to
`7a6c5d9c97b0e52d6cb34cc1368ab4eff76de8c251cec13442d2bc2ebb3fec13`; it must
be uploaded and retested on the calculator before replacing the deployed
artifact evidence below.
The deployment wrapper also bounds `n-link upload` to 45 seconds by default,
so a stale USB session exits and can release its process instead of hanging.

The current runtime path is the standalone Ndless program
`dist/nspire_ai.tns`. It keeps one SDL page open and communicates with the
Mac bridge over project-private NavNet service `0x5001`. The Lua document and file
exchange path below is legacy compatibility only.

The file exchange bridge is retained only as a fallback. It cannot provide the
requested UX because every response upload is a TI document transfer and may
show an “accept new file” prompt.

The default host path uses TI's Java NavNet helper and `startService(0x5001)`.
The helper consumes TI's node notification callback and also performs a
read-only `getConnectedNodes()` reconciliation poll every 500 ms.  TI's RMI
server can already have a handheld attached before a new client registers its
callback, so the poller closes that host-side restart gap by emitting only
state transitions (`NODE 1`/`NODE 0`).  It never opens the calculator channel
and cannot manufacture an application `CONNECTED` event.
A direct host probe on 2026-09-20 returned `READY service=0x5001` without a
calculator connection. Neither event proves an application connection:
`CONNECTED` and an NSAI request/response round trip are still required. The TI
JVM also remained alive after logging shutdown success, so the helper now has a
shutdown watchdog and its shell wrapper removes only a server created by that
invocation. Earlier runs crashed in TI's Intel connector, so stability remains
unverified.

Before deployment, `scripts/check-nspire-usb-state.sh` performs a read-only
USB gate. The current USB topology shows `TPS DMC Family` as a child of a
CalDigit TS4 hub; the checker classifies it as a non-Nspire dock controller and
does not permit that state to be mistaken for a usable CX II NavNet device.
TI's [TPS257xx-Q1 USB firmware-update guide](https://www.ti.com/lit/ug/slvubx5c/slvubx5c.pdf)
uses the same `TPS DMC Family` identity for that controller, while
[Hackspire's USB protocol notes](https://www.hackspire.org/USB_Protocol/)
list `0xE022` as the Nspire CX II product ID.

The experimental `NSPIRE_NAVNET_TRANSPORT=raw` path uses `libnspire-rs`:

* `packet_send_cx2_nowait()` and `packet_recv_cx2_timeout()` perform the CX II
  NavNet SE framing on the persistent path. The helper's single event loop
  consumes NNSE ACKs and inbound payloads; it does not block every application
  write waiting for an ACK.
* `usb_write()` and `usb_read()` are the existing libusb bulk endpoints.
* The Mac bridge will own one device handle for the lifetime of the process;
  it will not invoke `n-link upload` or `n-link download` per message.
* Application frames will be length-prefixed and carry a request id, opcode,
  and UTF-8 payload. Responses use the same request id and are delivered to
  the standalone Ndless program in memory.

The calculator calls `TI_NN_Connect(node, 0x5001, ...)`, a project-private
service accepted by the TI macOS NavNet host. The raw helper supplies USB
framing but has not demonstrated host-side service acceptance; local writes
must not be reported as a registered host service or an application connection.

The Java service callback stores the host channel; the standalone program performs short,
non-blocking reads and answers subsequent PINGs.
The standalone NGC page is USB-idle at startup. It performs no NavNet
enumeration until the host bridge has printed `READY service=0x5001` and the
user presses Menu once. The first failed synchronous NavNet call permanently
holds transport for that page; a later single Menu press is the only retry.
An offline candidate can additionally start the historical calculator-side
`TI_NN_StartService(0x5002, ...)` only on that Menu arm, after the host is
ready (`NSPIRE_NGC_MENU_LOCAL_SERVICE=TRUE`). The Mac helper still exposes
the application service as `0x5001`; setting
`NSPIRE_BOOTSTRAP_SERVICE_ID=0x5002` makes it call the calculator's separate
bootstrap service once on `NODE 1`, write a bounded probe, read the fixed
acknowledgement, and disconnect. This follows the historical TI test's
two-service ordering and is disabled by default. The default package keeps
the option false, and deployment requires an explicit opt-in because this
Menu-local-service variant still needs a physical test.
The previous 2/3/2-second automatic retry loop was removed after the
auto-transport candidate reproduced a transition from the handheld `0xE022`
interface to the TS4 `0xACE1` dock controller. A cable replug resets that USB
endpoint, but is not a software fix.
The historical Ndless calculator NavNet test (nsptools-history commit
`fce7f26cd8d9806bc4d9e4b5db85b90d80bf26b6`) starts a local service before
`TI_NN_NodeEnumInit`; the calculator then performs the reverse client connect
only after the peer-side service has been exercised. The old NGC auto-transport,
startup local-service, and two-service Menu candidates are now build/upload
blocked after physical freezes. No CPU-IRQ, timer, `idle`, or `msleep`
workaround is enabled. The two-service candidate did produce one host
`CONNECTED` callback, but the calculator crashed on Menu and the channel
degraded to `-257`; this does not establish a usable page bridge. The next
offline v4 candidate defers its bootstrap `Read/Write` out of the service
callback into the NGC main loop; it is not yet physically tested.
The remaining physical gate is still explicit: opening the page, sending a
request, receiving the response, and keeping the page visible throughout. The
file transport is retained only as legacy compatibility code and is not used
by the standalone `nspire_ai.tns` program.

## Verification log

* 2026-09-16: Standalone Ndless program, TI Java helper, and 14 Python tests
  build/pass. Earlier legacy Lua artifacts also matched their remote uploads.
* 2026-09-19: Raw helper reached `persistent USB handle open; cx2=true ready=true`
  and opened service `0x5000`; TI Java fallback crashed in
  `nwb_nspire_connector.dylib!UsbIoMac::readAsync()` on this Apple Silicon host.
  No calculator `RX`/`CONNECTED` event has yet been observed. The physical
  standalone-program launch and Enter test remains open, so transport is not a
  pass.
* 2026-09-20: With the Java helper running, `READY service=0x4051` and
  `NODE 1` were reproduced on the earlier built-in-service trial. A separate
  direct probe then returned `READY service=0x5001`; this proves host
  registration only, not calculator `CONNECTED`/`RX`. The
  deployed standalone page was not visibly open, and a raw screenshot
  diagnostic opened the CX II handle but did not return before timeout.
* 2026-09-20: The Java lifecycle regression passed with
  `READY service=0x5001`, `STOPPED`, and no new helper/RMI processes. The
  standalone artifact was rebuilt after adding conservative NavNet enumeration
  backoff; local SHA-256 is
  `ed952ba9e836aab410f64902c036fe8151bfb3b141ddd8da804a3a4914b2f783`.
* 2026-09-21: The physical gate passed with `TI-Nspire(tm) CX II Handheld`
  (`0xE022`); the artifact uploaded successfully and the Java bridge stayed at
  `READY service=0x5001`. Launching the received document produced one real
  `CONNECTED`, followed by TI error `-257` (`TI_NN_ERR_INVALID_CONNECTION`)
  before an application `RX`. The Java reader now retries that status and
  replaces stale readers on a later callback; the watcher also restarts a
  reader that dies on another negative read status, and the callback logs the
  native handle value for the next live diagnosis. Same-page request/response
  is still unverified.
* 2026-09-21 18:58: A fresh Java bridge again reached `READY service=0x5001`
  and remained live while the USB gate was polled for 24 seconds. The handheld
  interface did not reappear; every sample was the TS4 `0xACE1` DMC controller.
  The bridge was stopped cleanly, so this host-ready observation is not counted
  as a calculator connection or an application round trip.
* 2026-09-21 19:08: The full Python-to-Java `run-navnet-bridge.sh echo`
  entrypoint reached `READY service=0x5001` and shut down through Ctrl-C with
  `STOPPED`; no helper or detached RMI process remained. The USB gate was still
  `0xACE1` only, so this is host lifecycle evidence and not a page-open round
  trip.
* 2026-09-21: `scripts/test-navnet-bridge-lifecycle.sh` now automates that
  host-only gate with an open FIFO, `READY` assertion, SIGTERM, `STOPPED`
  assertion, and PID-scoped child/RMI leak check. It passed with the expected
  outer status `143`; it does not substitute for a calculator page test.
* 2026-09-21 19:56-19:59: A controlled `0x4051` service-ID run on the same
  unchanged TS4 topology also reached `READY` but no `NODE`, so the absence is
  not caused by the project-private `0x5001` ID. The raw `libnspire` transport
  was then tested and returned `Error: NoDevice` while `ioreg` still listed the
  `0x0451:0xE022` handheld below `TS4 USB2.0 HUB@02112000`. The normal Java
  `0x5001` bridge was restored; the page-open `CONNECTED`/request/response gate
  remains unverified.
* 2026-09-21 20:38-20:40: The page displayed `NavNet enum init=-274`.
  The installed TI `navnet.jar` identifies `-274` as
  `TI_NN_ERR_ENUM_DONE`. During the same audit the usable `0xE022` handheld
  interface disappeared again: the USB gate saw only the TS4/TPS controller
  `0x0451:0xACE1`, and the raw helper reported `no TI-Nspire USB device`.
  The Java bridge reached `READY service=0x5001` but no `NODE`, `CONNECTED`, or
  `RX`; it was stopped cleanly. No application round trip is claimed.
* 2026-09-21 20:08: After the AI page was exited, restarting the Java
  `0x5001` bridge reached `READY` and produced a real `NODE 1` once the
  `0xE022` handheld reappeared below the TS4 hub. This is recovery of host-side
  node discovery only; the page was closed, so `CONNECTED`, calculator `RX`,
  and same-page request/response remain unverified.
* 2026-09-21 20:55-20:58: Ten consecutive USB polls remained the TS4/TPS
  controller (`0x0451:0xACE1`) and the raw helper returned `no TI-Nspire USB
  device`; no bridge instance was started for a false physical pass. The
  standalone artifact hash was recomputed as
  `46065900de89efb12a56b7b2b2e464c87e8b02132cbd2806c8b71f25478c5`. The
  diagnostic NavNet client now has a bounded timeout and PID-scoped child/RMI
  cleanup, preventing a failed `waitForNode()` from leaving a competing client
  behind. Standard protocol and lifecycle tests pass.
* 2026-09-21 21:20-21:35: After restarting TI Student Software, macOS kept a
  direct `0x0451:0xE022` handheld visible, while the official UI stayed at
  `No handheld selected`. The fresh Java bridge reached `READY` but no
  `NODE`; the bounded node probe, raw helper, and N-Link upload each failed to
  complete USB/NavNet initialization. All temporary clients were stopped and
  no application round trip is claimed.
* 2026-09-16: NSAI frames gained ordered in-memory fragmentation/reassembly;
  the current frame payload is 224 bytes (below TI's documented 254-byte
  NavNet service ceiling after reserving the 16-byte NSAI header). Python
  tests cover a multi-frame 5000+ byte response.
* 2026-09-21: Repeated live checks found the macOS USB descriptor (`0x0451:0xE022`) without a usable handheld session. TI `connector*.log` reports `Failed to Open Device ... kIOReturnExclusiveAccess`; the TI UI remains `No handheld selected`, and bounded raw/Java probes produce timeout or `NoDevice`. This is below the application protocol, so the page-open `CONNECTED`/request/response gate remains open.
* 2026-09-26: A fresh host-side A/B after the v4 page reported `USB held enum
  init -274 Menu retries` reproduced the boundary. With TI Student Software
  closed, and again after starting it, the USB descriptor gate saw `0xE022`
  but the Java helper stayed at `READY service=0x5001` with no `NODE`. The
  official UI remained `No handheld selected`. Both runs cleaned up normally;
  no calculator package or Menu retry was performed. Treat this as missing
  host NavNet node availability, not a framing or service-ID defect.
* 2026-09-26: The standalone helper now calls `NavNet.loadConnectors()`
  explicitly after `NavNetCommProxy.init()`. The operation returned
  `CONNECTORS status=1` and the helper reached `READY service=0x5001`, but a
  bounded live run still produced no `NODE 1`. This confirms connector-load
  initialization is successful without closing the lower-level host node
  discovery gap; no physical calculator pass is inferred.
* 2026-09-26: The same shared-server run produced a native teardown crash in
  `libnavnet.dylib!List_RemoveItem` while removing service `0x5001`.  The
  helper now leaves `NavNetCommProxy.shutdown()` disabled by default, so a
  bridge stop cannot ask TI's shared server to perform that unsafe native
  teardown.  This is a lifecycle guard, not a claim of physical node or
  calculator-page success.
* 2026-09-26: After a clean TI Student Software restart, the guarded helper
  stopped with `STOPPED` while the shared RMI server remained alive on port
  1099 and no new crash report appeared.  The run still had no `NODE 1`, and
  the official UI stayed at `No handheld selected`; lifecycle safety is fixed,
  but physical node discovery is not.
* 2026-09-26: Returning the calculator to Home produced two real `NODE 1`
  callbacks, followed by a transient empty poll that the old code reported as
  `NODE 0`.  Node reconciliation is now positive-only; callback removal stays
  authoritative.  This recovers the host node signal, but the page-open
  `CONNECTED`/request/response loop still needs to be exercised with the
  corrected bridge running.
