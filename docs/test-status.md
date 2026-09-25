# Verification record

## 2026-09-24 user-reported NGC freeze and timer-neutral startup mitigation

The user reported that opening the AI program after Ndless activation made the
calculator freeze (keys stopped responding), and manually reset the handheld.
This is authoritative evidence that the previous production NGC package was
not safe to keep testing; no exact crash instruction or device dump is
available, so the report is not being mislabelled as proof of one particular
syscall.

The source now applies a conservative startup boundary:

* the default NGC build does not call `msleep()`. Ndless's CX II implementation
  rewrites the SP804 timer and masks every IRQ except timer 19 while waiting;
  that is not a verified USB scheduler and is removed from the default loop;
* automatic `NodeEnumInit`/`Connect`/`Read` calls are disabled on page open;
  pressing the native Menu key explicitly arms the experimental transport;
* framebuffer redraws are dirty-only instead of being forced once per RTC
  tick, reducing repeated entry into the raw GC/LCD path;
* `NGC_AUTO_TRANSPORT=TRUE` remains available only for a controlled build
  experiment, and is not the default package.

Verification after the change:

* `make program-test`: PASS, including the new safe-loop static gate;
* clean default Docker NGC build: PASS;
* explicit auto-transport Docker build: PASS, then the safe default package
  was rebuilt and restored in `dist/`;
* safe default package SHA-256 at that checkpoint:
  `6c0174b790e5e9aa0f67490340122f2b0a00b98b1ffaa3126901eed3d2013262`
  (25,616 bytes), manifest `ngc_auto_transport=FALSE`;
* ELF/Zehn relocation and startup-order checks: PASS.

This is a host/build mitigation, not yet physical proof of a working USB
round trip. The previous freezing package must not be reopened.

After a read-only post-reset node check (`TI-Nspire CX II CAS`, serial
`0000000001049C94`, runLevel 4), the safe package was uploaded to
`/nspire_ai.tns` and read back byte-for-byte at the same SHA. The resulting
screen was only TI's `Document Sent` confirmation; no open key was sent and no
AI program was started by the host. The next physical gate is a single manual
open of this safe package with transport still idle.

## 2026-09-24 page-open USB visibility comparison

The user then manually opened the safe package and reported the visible page
status `NGC/RTC USB idle Menu enables`. A read-only screen attempt while that
page was open did not receive a TI node within 30 seconds. This is not counted
as a bridge `CONNECTED` event: the default package has transport disabled until
the native Menu key is pressed, and the standalone Ndless loader masks
interrupts for the duration of the executable.

After the user exited the page, the same read-only screen command received a
real node callback and captured `/tmp/nspire-ai-after-exit.png`. The image is
the TI Home screen (`Scratchpad`, `Documents`, `Browse`), not the AI page; the
host log identifies the same CX II CAS serial `0000000001049C94`. A subsequent
`info` probe reproduced `NODE` again. This A/B result establishes that the
current failure is page-open USB scheduling, not a missing cable or a stale Mac
helper. No `CONNECTED`, calculator-originated `RX`, or same-page response has
been observed.

The old Lua/D2Editor page remains deleted by explicit user request. The former
SDL-native UI still exists only as the `NSPIRE_UI_NGC=FALSE` source branch; the
uploaded artifact is the NGC/RTC branch, so its appearance is intentionally
different. No old UI artifact or backup was restored.

## 2026-09-24 opt-in IRQ-window candidate (not uploaded)

To address the page-open scheduler boundary without changing the safe package,
the source now contains an explicit `NSPIRE_NGC_USB_IRQ_WINDOW=TRUE` candidate.
It follows Ndless's own controller sequence: mask SP804 timer IRQ 19 at the
interrupt controller, restore CPU IRQ delivery for the running page, then mask
CPU IRQs and restore the saved controller/CPU masks before exit. The candidate
is not enabled by default and is not selected by any deployment target.

The candidate built cleanly through the Docker ARM toolchain and passed the
static/startup/relocation checks. Its SHA-256 is
`8173753b4ae255861ac8eb0548bfa568e1a06c252beb517a515495cf371a735c` and its
manifest records `ngc_irq_window=TRUE`. It has not been uploaded because an
earlier unscoped IRQ experiment froze/crashed the handheld. The default
artifact was rebuilt immediately afterward and is restored at
`6c0174b790e5e9aa0f67490340122f2b0a00b98b1ffaa3126901eed3d2013262` with
`ngc_irq_window=FALSE`.

After adding the Menu-gated candidate path, the safe default was rebuilt and
restored again. The current `dist/nspire_ai.tns` is SHA-256
`77b33e21661be5556c1f6a9c14e9ba3d0959c8da81ae1fca8546c41a49a5daec` with
`ngc_irq_window=FALSE` and `ngc_irq_menu=FALSE`; it has not been uploaded.

Both the N-Link deployment script and Java remote upload path now refuse a
manifest with `ngc_irq_window=TRUE` unless
`NSPIRE_ALLOW_NGC_IRQ_WINDOW_UPLOAD=1` is explicitly set. The candidate is
still local only; this upload gate does not make the experiment safe or prove
USB connectivity. On 2026-09-24, the Mac bridge reached `READY service=0x5001`
and observed a `NODE 1` callback while the calculator was at Home. It was then
stopped cleanly. There was no `CONNECTED` or calculator-originated request.

## 2026-09-24 host-gate fix and page-launch reproduction

The bridge entrypoint had a deterministic shell bug: with `set -o pipefail`,
the USB gate used `tee | grep -q`. Once `grep` found the valid
`STATE=CX2_USB_CANDIDATE` line it exited early, causing `tee` to receive
SIGPIPE and making the gate reject a real calculator. The gate now captures and
prints the complete read-only result before matching it.

After the fix, a live run reached `READY service=0x5001` and `NODE 1` for the
same CX II CAS serial `0000000001049C94`. The host then navigated the
calculator's Home → Documents → Browse view and selected the current
`/nspire_ai.tns` (the 26K safe NGC/RTC package). Sending one Enter to launch it
produced `getNodeInfo: fail. ret = -2`; the remote command then timed out with
`no connected TI-Nspire node within 30000 ms`. A separate read-only `screen`
probe reproduced the same launch-time failure and produced no screenshot.
This is stronger than a generic “not connected” report: the node is present at
Home and disappears at the standalone program launch boundary. No
`CONNECTED`, calculator `RX`, or same-page response was observed, and no
reboot, reset, cable change, or experimental package upload was performed.

## 2026-09-24 Ndless loader-hook oracle (authoritative blocker)

The calculator-side action was performed through the purpose-built remote key
channel; the visible Mac application state was inspected with Computer Use
(CUA). The `screen` call was read-only evidence after the key events.

The device already contained the official Ndless r2022 resource package at
`/ndless/ndless_resources.tns` (local/readback SHA prefix
`5994e5096d16e2c42`, 193,356 bytes). From the Browse screen, the exact row
`ndless_resources` was selected and opened. The handheld displayed the
authoritative dialog:

```
This document format is not supported.
ndless_resources.tns
```

This is the non-destructive loader-hook oracle: an active Ndless hook would
enter the resource program and show its own `Do you really want to uninstall
Ndless r2022?` confirmation. The generic TI dialog proves that this reset
session is not dispatching native `.tns` documents through the resident
Ndless loader (or that the hook has been lost from RAM). It is not evidence
against the NspireAI RPF/Zehn payload: the official resource package fails at
the same boundary.

The production NGC package was rebuilt once after removing the non-fatal OS-id
hard return that could otherwise turn an index mismatch into an indistinguish-
able loader error:

* SHA-256: `41f3442279ad126f16780772e25845a51fb4bbfc418bcffa4450c9a448c2557b`
* size: 25,912 bytes
* ELF/Zehn relocation check: PASS
* startup-order check: PASS
* `make program-test`: PASS

The existing `ndless_installer_4.5.5-6.2.0-6.4.0.tns` was then opened without
activating it; the handheld showed `Ndless for OS 6.2/6.4` and
`Press any key to start installation...`. This is the exact external step
still required before another package or bridge run can provide meaningful
runtime evidence.

The bridge also now constructs the selected backend before spawning the Java
USB helper. A backend configuration failure therefore cannot leave a helper
holding the calculator interface; `bridge.test_navnet_bridge` covers this
startup-cleanup path.

It was transferred successfully, but opening it produced the same generic
dialog because the loader-hook oracle was already negative. It must not be
called a package rejection. No further NspireAI upload or protocol test is
meaningful until the user reactivates Ndless's resident loader after the
reset, then opens the already-present resource package and obtains the
uninstall confirmation. That action is intentionally not automated here:
reinstalling or resetting Ndless changes calculator state and was explicitly
kept out of the current workflow.

## 2026-09-24 repeatable unsupported-document boundary

The visible Mac TI application was inspected and operated through Computer
Use (CUA), not by clicking screenshot coordinates. Calculator actions were
sent as remote `key` events. The handheld `screen` command was read-only
evidence after those key events.

On the live CX II CAS, three distinct, exact-SHA packages were transferred
and selected from the handheld Browse file list:

| SHA-256 prefix | Bytes | Result after remote Enter |
| --- | ---: | --- |
| `a88bd700651bac38` | 65,996 | `This document format is not supported.` |
| `86b883f680a41f3c` | 25,920 | Same unsupported-document dialog |
| `9cdf132883b00012` | 63,720 | Same unsupported-document dialog |

The 25,920-byte build removed the `printf`/`dtoa` link chain by using a small
status formatter. It built cleanly, passed the ELF/Zehn relocation check, and
the handheld Browse list displayed it as 26K before the rejection. The
smaller file therefore disproves a simple 65KB package-size limit. The
stage-8 package's repeat rejection also corrects the earlier inference that
absence of the dialog meant it had launched: the earlier screen stayed in
Browse and never showed the NspireAI page. No package in this attempt reached
`CONNECTED`, calculator-originated RX, or a same-page response.

Both package variants have a valid `PRG` wrapper with an embedded Zehn v1
header at byte 492; their header file sizes match their actual lengths and
their flags match. This excludes obvious truncation/header/flag mismatches,
but does not establish that the Ndless document hook is active in the current
OS session. A generic TI unsupported-document dialog may occur before the
Zehn loader is entered, so the next discriminating test should validate the
Ndless hook with a known-good native program already on the handheld before
building or uploading another NspireAI candidate. Do not reset or reinstall
Ndless merely from this dialog.

The three exact SHAs are now permanently blocked in the normal shell deploy
gate, the Java direct-upload gate, and their candidate wrappers. The current
`dist/nspire_ai.tns` remains a rejected diagnostic artifact, not a verified
release.

## 2026-09-24 NGC startup probes and scheduler-yield candidate

Computer Use (CUA) was used for the visible TI Student Software state. The
handheld screen images below were read-only NavNet screen probes; calculator
key events were sent through the purpose-built remote key channel.

The staged NGC probes isolated the startup boundary on CX II CAS 6.2:

* stages 0–5 (Zehn entry, `lcd_init`, global GUI GC, first `lcd_blit` frame,
  status redraw, and RTC read) all returned without the unsupported-document
  dialog;
* stages 6–8 (one NavNet enumeration attempt and redraw) also returned cleanly;
* stages 9–10 (1000 key scans and a 30-second RTC/key loop) returned cleanly;
* stage 11 executed one `nav_poll` call, but the calculator never exposed a
  service connection, so no `TI_NN_Read` occurred.

The Mac bridge was live throughout the final probe and logged `READY service=0x5001`
and `NODE 1`, but never `CONNECTED`, calculator-originated `RX`, or a response.
The host-side node/READY state is therefore not being counted as application
connectivity.

The production NGC loop now sleeps 20 ms on every iteration. The RTC backend is
second-resolution; without that yield the loop spins millions of times per
second while the clock is unchanged and can starve CX II USB/NavNet scheduling.
The clean candidate was uploaded once and read back at the exact SHA
`a88bd700651bac3876a23e4b0e45428535102f1834524169219b04a249b499ef` (65,996
bytes). At that time opening it did not visibly reproduce the generic
unsupported-document dialog, but the screen remained the handheld file browser and no NspireAI page or
calculator-side `CONNECTED`/request/response evidence appeared. Keep this
candidate permanently blocked after the repeat open above produced the dialog;
it is not a completed physical loop.

## 2026-09-24 relocation-candidate physical rejection (Computer Use control)

Computer Use (CUA) was used for the desktop interaction and application-state
inspection. The Java `screen` command was used only as a read-only handheld
state probe; it was not used to click or operate the calculator. The only
calculator actions in this attempt were the purpose-built remote `key` calls
for opening the transferred document.

The relocation-preserving build passed the offline ELF/Zehn relocation gate
and was uploaded through the explicit candidate path:

* path: `dist/nspire_ai.tns`
* SHA-256: `51ca73922afbc0ba2f0b488a98f4ff703eaa8081c64edac951ba1335d833a301`
* remote readback: `VERIFIED /nspire_ai.tns bytes=130300` at the same SHA

The handheld first showed `Document Received` for `nspire_ai.tns`. A CUA-
controlled open sequence then produced the authoritative handheld dialog
`This document format is not supported.` No NspireAI page appeared, and there
is no calculator-side `CONNECTED`, request RX, TX, or same-page response
evidence. This disproves the hypothesis that preserving ELF relocations alone
made the package launchable on this CX II CAS.

The exact SHA is now blocked in both the normal shell deployment gate and the
Java upload path. `scripts/deploy-ngc-reloc-candidate.sh` is retained only as
a negative-test record and exits before any USB operation. A new package must
have a different SHA and a separately reviewed loader/startup explanation;
re-uploading this artifact is not a valid next test.

## 2026-09-24 SDK-linker layout candidate (physical rejection)

The loader source showed that the generic dialog also covers a loader return
of `1`, so the next discriminating host-side change uses the SDK's native
`nspire-ld` wrapper instead of the hand-written ARM-GCC link. This restores the
same crt/library/linker combination used by the Ndless samples and removes the
second `.got` that had appeared after `.bss` in the rejected package.

The clean Docker build completed and passed the relocation gate:

* path: `dist/nspire_ai.tns`
* SHA-256: `5f3d5213ccc3ff5ef60054981541df03565f69b943ac734f0ea73dafeea62cc9`
* package size: `65532` bytes
* Zehn: `599` relocations, `alloc_size=101144`, no NOBITS-after-GOT warning

The artifact was uploaded once through its explicit candidate path and read
back at the exact same 65532-byte SHA. The handheld showed `Document Sent`;
the purpose-built remote key sequence opened `nspire_ai.tns`, and the resulting
read-only screen showed `This document format is not supported.` again. The
second-GOT layout is therefore not the sole loader failure. This package did
not reach an NspireAI page, `CONNECTED`, request RX, TX, or same-page response.

Its exact SHA is now blocked by the normal shell deployment gate, the Java
upload path, and the candidate wrapper. Do not re-upload it.

Offline follow-up isolated a likely pre-first-frame crash hazard in the NGC
entry path: the old source acquired the global GUI GC before `lcd_init()`,
while the SDK framebuffer examples initialize the LCD before allocating or
using the drawing buffer. The source now performs `lcd_init()` first and only
then obtains `gui_gc_global_GC()`. `scripts/check-ngc-startup-order.py` and
`make program-test` enforce this ordering. Staged physical probes later showed
that this ordering is safe, so the remaining scheduler/transport work is
tracked in the startup-probe section above.

The clean rebuild produced a new exact artifact, SHA-256
`b821080614c2d3eb839b38f8a1f45105485f7a4bee26f49dea149bba82e42d20`.
It was uploaded once through the exact candidate path, read back byte-for-byte,
and opened with the purpose-built remote key channel. The handheld again showed
`This document format is not supported.` The exact SHA is now permanently
blocked by the normal shell deployment gate, Java upload path, and candidate
wrapper. Do not re-upload it. The LCD-before-GC ordering hypothesis is therefore
not sufficient; the failure is still in loader/entry/runtime startup before a
visible NspireAI page.

## 2026-09-23 Computer Use host-session check and lcd-init candidate rejection

The TI-Nspire Student Software UI was operated through Computer Use (AX/UI
actions), not through a handheld screenshot-control loop. The UI exposed the
connected row `TI-Nspire CX II CAS A757`, but the row was disabled and the
status remained `No handheld selected... please connect a handheld`; clicking
the row, double-clicking it, and refreshing libraries did not change that
state. `Capture Selected Handheld` remained disabled. The connector log records
USB discovery followed by `addDevice(): Opening USB\\01\\NSP_00100000`, after
which the log stops; this is a host connector/session problem, not proof that
the calculator program is running.

When the TI window became unresponsive, Computer Use was used to select the
`java` process in Activity Monitor and force-quit the stuck
`RemoteNavnetServer` child. The TI Student Software main process remained
running and its AX tree became readable again, but the handheld row stayed
disabled and no handheld was selected; this recovered the host child only, not
the calculator application state.

After that cleanup, Computer Use relaunched TI-Nspire once. The fresh session
again stalled before exposing a usable handheld selection; Activity Monitor
showed a new `java` child, which was force-quit through the same UI path. The
remaining `JavaAppLauncher` supervisor still has no usable handheld selection,
so this is reproducible host-session behavior rather than a stale screenshot.

The reviewed NGC lcd-init candidate was uploaded only through the guarded Java
USB transport and read back byte-for-byte:

* path: `.build/ngc-lcdinit-trial/nspire_ai.tns`
* SHA-256: `34345e069a1bbaacd1b4b289e37ada9d04d8f1105c11fa259b5d519937998f26`
* remote size: `56868` bytes

Selecting that file on the handheld produced the calculator's `This document
format is not supported` dialog for `nspire_ai.tns`. It did not reach the
NspireAI page, `CONNECTED`, request RX, or same-page response. The package is
therefore rejected as a physical runtime candidate even though transfer and
readback passed. No bridge session was started from this state, and no stale
artifact was substituted.

## 2026-09-23 NavNet slow-node recovery and read-only screen evidence

After the TI process and its stuck Java child were cleaned through Computer
Use, a fresh NavNet session recovered the handheld node repeatedly, but with a
variable startup delay. A read-only `info` probe received:

`NODE id=1C50000000001049C94E681A757 name=TI-Nspire CX II CAS serial=0000000001049C94 electronicId=1C50000000001049C94E681A757 runLevel=4 connectionType=0`

The corresponding read-only `screen` probe returned a 320x240 PNG with SHA-256
`862e1634de3bd212de27e3ccac8d291dbe5f94deb060f3f8cbcb05d7d5e8642e`; visual
inspection showed the handheld root file browser with `nspire_ai` at 56K, not
the NspireAI page. A subsequent read-only `list /` succeeded after extending
the node wait to 30 seconds and showed `/nspire_ai.tns` at 56868 bytes.

The remote helper now accepts `NSPIRE_NODE_WAIT_MS` (default 30000 ms), and
`run-nspire-remote.sh` uses a 60-second default wrapper window so slow RMI/USB
startup is not mislabeled as a node absence. `test-nspire-java-helper-lifecycle.sh`
still passes READY -> STOPPED with no new helper/RMI process leak. These are
host/node and screen-state improvements only; they do not prove calculator-side
`CONNECTED`, request RX, or same-page response.

Offline root-cause work then corrected the NGC link command to preserve the
Ndless SDK's `--pic-veneer --emit-relocs` semantics. The resulting unreviewed
package in `dist/nspire_ai.tns` is 130300 bytes with SHA-256
`51ca73922afbc0ba2f0b488a98f4ff703eaa8081c64edac951ba1335d833a301`.
`src/program/nspire_ai.elf` now contains `.rel.text` and `.rel.data`, and the
embedded Zehn has 548 relocations (including GOT and ADD_BASE types), unlike
the rejected 56,868-byte candidate whose final ELF had no relocations. A new
offline gate `scripts/check-ngc-relocations.py` and `make program-test` verify
this invariant. This exact SHA is blocked from upload until a physical launch
review is performed; it has not been sent to the calculator.

## 2026-09-23 TI GUI selection mismatch (fresh check)

The TI-Nspire Student Software window displayed a connected-handheld row for
`TI-Nspire CX II CAS A757` (the same node ID returned by the read-only NavNet
probe), while the status text still said `No handheld selected... please
connect a handheld`. A single click and a double click on the device cell did
not change that status, and `Capture Selected Handheld` remained disabled. This
is host GUI state only; no upload, key event to the calculator, or application
launch was performed during this check.

Immediately after a longer read-only NavNet probe recovered the node callback,
the Java `screen` command succeeded and captured a 320x240 handheld image
(SHA-256 `95fc137eebc082cdc7659f426de6d295d97d1ff36a5433fffb9c0f580d08e21a`).
The image shows the TI-Nspire Home screen (`Scratchpad`, `Documents`, and
`A Calculate`), not an `nspire_ai` page. This is authoritative current-screen
evidence that the requested page-open bridge loop is still not active; the
capture was read-only and did not upload or send a key.

After a TI Student Software restart through the application UI, the new
`JavaAppLauncher`/`RemoteNavnetServer` session produced successful `info` and
`screen` probes in parallel. Both received the node callback without the
earlier 15-second delay; the screen hash remained
`95fc137eebc082cdc7659f426de6d295d97d1ff36a5433fffb9c0f580d08e21a` and still
showed the Home screen. This is a repeatable host-session recovery, not an
application-level bridge result.

The first stable post-restart directory read showed the handheld root still
contains `/nspire_ai.tns` at 55,420 bytes. A read-only download from the device
then produced SHA-256
`e0282e26c2d5017b76e893a15d51aa8c44a78f94cebcbf23a8d1ff651029d75c`, exactly
matching the known blocked `lcd_blit` candidate; the isolated candidate has
not been deployed. The root also contains an empty `/nspireai-backup-0916`
directory, with no files inside it.

To keep this identity check repeatable, `scripts/audit-device-artifact.sh` now
performs the same bounded read-only download and classifies the exact SHA as
the known blocked package, the reviewed stage candidate, or an unknown package
that must not be deployed. Its no-write properties are covered by
`scripts/test-device-artifact-audit.sh` and `make program-test`.

## 2026-09-23 clean SDL build and physical startup attempt

`NSPIRE_UI_NGC=FALSE ./scripts/build-program-docker.sh` completed after a
clean object rebuild. The resulting standalone SDL package is 257324 bytes,
SHA-256 `53f5f14965d3c4280f86f82565f22916db7eabbace8f6b7b9a4c410b4d94db2e`;
its manifest records `ui_backend=FALSE` and `build_status=success`. The TI Java
client reported node `1C50000000001049C94E681A757`, uploaded the package to
`/nspire_ai.tns`, and downloaded it for an exact-byte `VERIFIED` comparison at
the same hash. This proves build, transfer, and artifact identity, not runtime
communication. N-Link could not open the USB device while TI Student Software
owned the connection; the Java path used that existing TI session.

On the handheld, remote screen captures showed Home -> Browse -> root file
`nspire_ai` at 252 KB. After selecting it and pressing Enter, subsequent
captures continued to show the same file list with a wait cursor. A further
Enter and Menu produced no visible change. The Java client could still
enumerate the node and capture the screen, so host USB reachability survived,
but the calculator app did not reach a visible NspireAI page. There is no
`CONNECTED`, request RX, or same-page response evidence. Treat this candidate
as a failed physical startup test; both upload paths now reject its hash.
No reset or cleanup of the calculator was performed after this observation.

Host protocol/session tests pass (18 tests), including PING/PONG, fragmented
request/response, NEW, and CANCEL. They do not imply handheld runtime success.
The next calculator candidate needs a discriminating, reviewed startup-stage
diagnostic; another unchanged SDL rebuild must not be uploaded.

## 2026-09-23 NGC first-frame-before-RTC isolation candidate

The NGC entry path was adjusted so its first `ngc_draw()` occurs before the
first RTC `gettimeofday()` call. This is a stage-isolation change only: it does
not claim that RTC or USB scheduling is safe, and it leaves the OS-id and
global-GC guards in place. A clean ARM object compile passed with
`-Wall -Wextra -Werror`; a separate custom-link ELF passed the transport/UI
symbol audit and has SHA-256
`f3bde04df2d78bc06981038d2538004fe1d735a010de5c134f5ee103a9f5aa13`.

The candidate was packaged only under
`.build/ngc-stage-isolation/nspire_ai.tns` (55,888 bytes, SHA-256
`91820a3ac0ec564a995f0136e64b2c8d384ce9507ea8d410d9f99bb97fb70858`). Its
Zehn flags advertise the new LCD API and 240x320 support. It was not copied to
`dist`, uploaded, or run on the calculator; it is therefore not physical
runtime evidence and must not be deployed without a reviewed upload decision.
`make program-test` now runs `scripts/check-ngc-startup-order.py`, which keeps
the first-frame-before-RTC ordering and the OS-id/global-GC guards as an
explicit source-level invariant.

The isolated candidate also has a separate deployment wrapper,
`scripts/deploy-ngc-stage-candidate.sh`. It is deliberately not wired to the
normal `dist` deployment path: it pins the exact candidate path and SHA,
requires a matching successful manifest, requires the read-only USB gate, and
will not invoke `n-link upload` unless
`NSPIRE_ALLOW_STAGE_CANDIDATE_UPLOAD=1` is explicitly set. The gate itself is
covered by `scripts/test-ngc-stage-upload-gate.sh`; this turn ran that test
without touching the calculator.
The wrapper now uses the TI Java NavNet session (whose read-only list/download
paths work while TI Student Software owns USB), not the raw N-Link handle.
Java's direct upload command independently rejects the exact stage-candidate
SHA unless the same explicit confirmation variable is set. Neither path was
used to upload during this change.

The old stage-isolation candidate is now superseded. Offline SDK review found
that its `lcd_blit()` call did not first perform the SDK-required `lcd_init()`
setup. The NGC source now performs `lcd_type()`/`lcd_init()` once before the
first frame (and still before the first RTC read or NavNet poll). The
replacement was compiled and packaged in a clean Docker invocation as
`.build/ngc-lcdinit-trial/nspire_ai.tns`, 56,868 bytes, SHA-256
`34345e069a1bbaacd1b4b289e37ada9d04d8f1105c11fa259b5d519937998f26`.
It has only offline/static evidence; it has not been uploaded or run on the
handheld. The Java upload path rejects the superseded SHA and requires the
same explicit confirmation gate for this replacement.

The latest read-only Java screen capture (2026-09-23 20:31 SGT) still shows
the calculator Home screen with a `Document Sent` dialog naming
`nspire_ai.tns`, not the NspireAI page. The 320x240 PNG SHA-256 is
`6643e79b3d86ffeec93e281c7e83ef6620041d51c114bcc83ce0592bd04c7bdd`.
This is UI state only; it is not evidence that the sent document launched.

## 2026-09-23 NGC `lcd_blit` candidate and stale handheld UI

The NGC graphics path was then changed from the SDK's old
`gui_gc_blit_to_screen()` helper to the new-API `lcd_blit()`/`lcd_type()` pair.
That candidate was built from a clean Docker invocation with a successful
manifest: 55,420 bytes, SHA-256
`e0282e26c2d5017b76e893a15d51aa8c44a78f94cebcbf23a8d1ff651029d75c`. Static
inspection showed `TI_NN_NodeEnumInit`, `TI_NN_Read`, `lcd_blit`, and
`lcd_type`, with no SDL, `idle`, `msleep`, or old GC-blit symbol.

The package was uploaded through the TI Java session and `/` independently
reported `FILE 55420 nspire_ai.tns`. One controlled `~enter~` was sent to the
selected `nspire_ai` row. A fresh screen capture two seconds later was byte
identical to the pre-key capture (SHA-256
`5067b100cf32f8d12e6d085172c7bc74bc6867e0db29a17a2ac5a6dfd9a01f98`), still
showing the old Browse row text `252 KB`. No NspireAI page, CONNECTED, request
RX, or response appeared. The file was therefore not accepted as a runtime
success and this exact SHA is blocked from repeat upload. This evidence is
ambiguous between a stale/frozen handheld UI and a loader/program-start stall;
it does not identify a faulting instruction.

The same session produced a host-side TI crash log at
`~/Library/Preferences/Texas Instruments/TI-Nspire CX CAS Student Software/logs/NN-crash-20260923-160155.log`:
SIGSEGV in `libnavnet.dylib` at
`Java_com_ti_eps_navnet_server_NavNet_stopService+0x1e`, with service ID
`0x5001` in the registers. This is a separate macOS NavNet teardown failure,
not evidence that the calculator program reached its entry point. The Java
helper now skips `stopService()` by default; it disconnects, reports `STOPPED`,
and lets the wrapper clean only a server created by that invocation. The risky
call is available only with explicit `NSPIRE_NAVNET_STOP_SERVICE=1`.

After restarting TI Student Software, the next `NspireRemoteControl info`
attempt still could not enumerate a node. The TI server then generated
`NN-crash-20260923-182923.log`; its native stack is
`CoreFoundation!_CFGetNonObjCTypeID` ->
`nwb_nspire_connector.dylib!UsbNotifyThread::unregisterPowerMgmt` ->
`TI_CN_Shutdown` -> `TI_NN_Init`, and the Java client received RMI EOF / `-304`.
This is a second, independent host connector initialization/shutdown failure.
The USB checker still sees `STATE=CX2_USB_CANDIDATE product=0xE022`, but that
does not prove a usable NavNet node. No further key, upload, or calculator
reset was attempted after this crash.

The host connection later recovered without a Mac reboot: Java enumerated node
`1C50000000001049C94E681A757`, and the handheld Browse screen refreshed to the
current `nspire_ai` row at `55K` (not the old 252 KB image). A single Enter was
then sent to that selected row. The next screen capture returned to Home with
`A Calculate` highlighted, and a second capture after five seconds was still
Home. No NspireAI page, `CONNECTED`, request RX, or response appeared, and no
new TI crash log was produced. This is the first direct launch failure for the
current `e0282e26...` package after the stale Browse cache was cleared; its
upload guard remains active.

The TI Student Software host log adds stronger crash evidence for the same
recovery window. When the node was added again at 18:49:01, the NavNet client
reported a calculator-side `Data Abort` before removing and re-adding the
node: `6.2.0.333 CX II CAS Data Abort`, `IA=0x13A9F5F8`,
`DA=0xE8BD4149`, `USB`. This is device crash telemetry, not a proof of the
faulting source line; the direct screen result still remains Home with no
page or protocol frames. It does, however, rule out treating the launch as a
normal clean exit and is an additional reason not to upload this SHA again.

The read-only fallback check was also attempted after the TI server failure:
the pinned N-Link CLI `ls /` did not return within 30 seconds and was
terminated. This leaves a useful distinction in the evidence: macOS ioreg
still sees an active 1105:0xE022 CX II at 480 Mbps, but neither the TI NavNet
server nor the direct N-Link path currently has a responsive session.

At 19:06, a fresh bounded read-only `run-nspire-remote.sh info` completed with
exit 0 and enumerated the same node ID/serial. This confirms that the host
NavNet enumeration path recovered after the Data Abort; it did not launch the
calculator package and therefore adds no `CONNECTED`, RX, or response evidence.

At 19:33 the same bounded `info` probe timed out with no `NODE` callback while
macOS still reported the `0xE022` descriptor. The TI Student Software process
was then restarted gracefully (no Mac reboot and no calculator reset). Its new
connector opened the same USB interface, and a fresh `info` at 19:34 returned
exit 0 with the same node ID/serial. This confirms the host-side recovery
procedure, but no candidate was uploaded or launched during the recovery.

At 18:38 the TI application was visible again, but its accessibility tree
reported `No handheld selected`; there was still no usable node despite the
USB checker seeing the 1105:0xE022 device. This is the authoritative current
host state after the latest restart attempt.

For the next discriminating startup check, the offline NGC control probe was
also updated to the new `lcd_blit`/`lcd_type` API. Its ELF-only build passed
the source-object transport/timer audit and produced SHA-256
`acf8255d81d33dc0cd54f783c9b5e4e7db32104551ef98b1c861ff72971cd2da`. It is
deliberately not a `.tns` upload and is not hardware evidence.

## 2026-09-23 NGC startup-path candidate and physical attempt

The full shared `main.c` now links in `UI_NGC=TRUE` mode through an explicit
Ndless SDK link line (`crt0/crti/crtn`, whole-archive `libsyscalls`, then
`libndls`/newlib) instead of the Debian/newlib-incompatible `nspire-ld`
wrapper. The candidate was generated only under `.build/ngc-make-trial/` and
has SHA-256 `bc2c2099934f622cf0b3c137f7bb46416f04c1c45f9935f2351f03763bdc495f`
(53 KB). Its symbol audit contains no SDL, `msleep`, or `idle` references and
does contain `TI_NN_NodeEnumInit`, `TI_NN_Read`, RTC `gettimeofday`, NGC
graphics, and matrix-key paths. This is a static/build gate only: it has not
been uploaded or run on the calculator, so it is not evidence of CONNECTED,
request RX, or response.

The candidate was then built into `dist` with a matching success manifest and
uploaded through the existing TI Java session. The host reported
`UPLOADED /nspire_ai.tns bytes=54444`; a root listing independently reported
`FILE 54444 nspire_ai.tns`, confirming the remote file identity. The subsequent
`verify-program` readback did not return `VERIFIED` and was terminated by its
45-second timeout. Remote screen capture continued to work, but the image
remained the old Browse screen (cached row text still showed 252 KB); virtual
`Enter` and `Home` commands returned `KEY` without changing the screen. No
NspireAI page, `CONNECTED`, request RX, or response was observed. This is a
failed physical startup/interaction attempt, not bridge success; the NGC SHA
is now blocked by both upload paths. The current `dist` remains this exact
NGC candidate with its manifest so the failure is reproducible, but the upload
guards refuse it.

## 2026-09-21 freeze-prevention change

The standalone program now calls `TI_NN_Read` with the smallest finite
timeout (`NAV_READ_TIMEOUT=1`) instead of `10`. TI's CX II builds do not use
the timeout unit consistently; the larger value could block the SDL event
loop long enough to look like a frozen calculator when the host was absent or
stale. The existing 2.5-second handshake watchdog and reconnect backoff remain
in place. `dist/nspire_ai.tns` was rebuilt locally at SHA-256
`7a6c5d9c97b0e52d6cb34cc1368ab4eff76de8c251cec13442d2bc2ebb3fec13`; this
rebuild has not yet been uploaded or counted as a new physical-runtime pass.
The deployment wrapper now bounds `n-link upload` to 45 seconds by default
(`NSPIRE_UPLOAD_TIMEOUT_SECONDS` overrides it), so a stale USB session cannot
leave the host command hanging indefinitely.

## Follow-up observation

2026-09-19: macOS IOUSB lists an active TI-Nspire CX II Handheld (vendor
1105), while N-Link returns `NoDevice`. This does not prove physical USB
absence or establish exclusive ownership as the cause. The bridge and its
helper were stopped for diagnosis. No new deployment or application round
trip was verified in this observation. Ndless installation success and the
handheld OS version still require confirmation; installer filenames alone
are not evidence of OS compatibility.

Subsequent check: N-Link directory access recovered without a new deployment.
Downloading `/nspire_ai.tns` to `/tmp/nspire-verify.9mbvyD/nspire_ai.tns`
produced SHA-256
`f9bb309b9ad0e10204e5b03beb3baeb7fa77aa0353330d6d6df941a6d1483162`,
identical to `dist/nspire_ai.tns`. The raw helper's screenshot request then
failed with `Busy`; handheld installation/runtime state remains unknown.

The NavNet backend now limits responses to the standalone receiver's 16384
UTF-8 byte capacity and returns OP_ERROR for oversized results. All 18 host
tests pass, including exact-limit, over-limit, multibyte, over-protocol-limit,
the 254-byte NavNet frame-budget check, and a complete host-side session test
covering PING, fragmented request/response, NEW, and CANCEL. This is host
regression evidence only, not a physical USB test.

Historical snapshot from 2026-09-21 (macOS arm64). Its USB-absent and Lua
rows are superseded by the later dated observations above and the user's
explicit removal of the legacy Lua implementation; do not read this table as
the current device or deliverable state.

| Layer | Result | Evidence |
| --- | --- | --- |
| Lua syntax | PASS | `lua -e 'assert(loadfile(...))'` for both scripts |
| Legacy Luna packaging | PASS | `.deps/luna/luna` built at commit `a9924a9`; produced `dist/AI-ui-demo.tns` and `dist/AI.tns` (not used by the standalone runtime) |
| Mac bridge echo and NavNet protocol tests | PASS | `./scripts/test-bridge.sh` (18 tests); UTF-8, multi-line, long-message fragmentation, repeated IDs, cancellation, conversation reset, incomplete request, retry-after-upload-failure, response-size guard, serialized-worker behavior, frame-budget guard, and complete session sequencing |
| N-Link CLI build | PASS | Rust 1.98.1; `.deps/n-link/.../target/release/n-link --help` and `license` |
| Standalone Ndless C build | PASS | `./scripts/build-program-docker.sh` produced ARM EABI `dist/nspire_ai.tns`; current build uses project-private NavNet service `0x5001`, a 2s initial NavNet enumeration delay, 3s failure backoff, a delayed first PING, and a 2.5s handshake watchdog |
| Legacy Lua/extension build | PASS in Docker | Kept for compatibility only; not used by the standalone runtime |
| Host device detection | PASS | Raw `nspireai-usb-helper`: `persistent USB handle open; cx2=true ready=true`; N-Link upload and download both succeeded |
| Deployed standalone artifact identity | PASS | Remote `/nspire_ai.tns` downloaded and SHA-256 matched local `dist/nspire_ai.tns`: `f9bb309b9ad0e10204e5b03beb3baeb7fa77aa0353330d6d6df941a6d1483162` |
| TI host service registration | PASS for registration probe | Java helper returned `READY service=0x5001` on 2026-09-20; no calculator connection was present, so application stability remains unverified. |
| Java helper lifecycle cleanup | PASS | `./scripts/test-nspire-java-helper-lifecycle.sh`: `READY service=0x5001`, `STOPPED`, and no new helper/RMI processes after shutdown |
| Physical USB gate checker | DESCRIPTOR ONLY / currently absent | `./scripts/check-nspire-usb-state.sh` distinguishes the CX II descriptor (`0x0451:0xE022`) from the TS4 dock controller (`0x0451:0xACE1`). On the latest check the handheld descriptor was absent and the raw helper returned `no TI-Nspire USB device`; this is not a usable-session proof. TI's `connector*.log` previously reported `Failed to Open Device ... kIOReturnExclusiveAccess`. |
| Persistent page-open USB round trip | NOT YET VERIFIED | Default host helper still uses TI Java service registration; the TI UI remains `No handheld selected`, with no physical `NODE`, `CONNECTED`, or `RX`. |

The NavNet entrypoint now takes an exclusive process lock before starting any
Java, raw-helper, or RMI child. A second launch exits with status `75`; normal
and signal shutdown paths release the lock. This prevents the host bridge from
creating a second USB owner, but cannot replace the TI connector's own
handheld session acquisition.

It also enforces the physical USB gate before starting the bridge: when the
CX II descriptor is absent, the normal entrypoint exits `69` without starting
Java/RMI/helper. The host-only lifecycle test sets
`NSPIRE_SKIP_USB_GATE=1` explicitly and therefore does not claim an attached
handheld.
| OpenAI SDK adapter | IMPORT PASS, API NOT RUN | Official SDK is installed; `OPENAI_API_KEY` is absent in the current shell, so no live model call has been made |

The persistent transport row is intentionally not called “pass”. The acceptance
point is specifically that `nspire_ai.tns` stays open while the Mac receives PING,
request, and response frames over the Ndless service; that cannot be inferred
from a successful TNS build or a successful file upload. The old file-exchange
helpers remain only for compatibility and are not used by the current page.

Follow-up host regression: 15 tests pass after moving conversation resets onto
the serialized model worker. A deliberately stalled answer no longer blocks
NEW/PING handling, and its late response is suppressed. Tests now explicitly
wait for asynchronous workers rather than relying on thread scheduling.
Java service registration and device-add were reproduced at 01:55; application
CONNECTED/RX and real AI calls remain unverified.

Latest host verification (2026-09-19): the CX II is visible on macOS USB and the
raw helper reports `cx2=true ready=true`; N-Link `ls /` sees the deployed
`/nspire_ai.tns`; `cargo check`, Java helper compilation, and the ARM program
build all pass. An earlier raw bridge run emitted the expected `0x4051`
built-in-service probes; the active path is now the custom `0x5001` service.
The bridge and launcher now forward SIGINT/SIGTERM through the Python child,
clean up the helper, and snapshot TI's detached `RemoteNavnetServer` PID set so
only a server created by the current run is terminated. A launcher-termination
probe left no helper, bridge, or detached RMI child behind. This is still host
and lifecycle evidence only: the standalone program was not visibly open on the
handheld, so no `CONNECTED`/`RX`/same-page response is claimed.

Latest live observation (2026-09-19 09:47): TI Java helper stayed alive through
RMI startup, returned `READY service=0x4051`, and received `NODE 1` for the
connected CX II. No `CONNECTED` or calculator-originated `RX` followed while
the standalone program was not visibly running. The earlier `0x5000` trial returned an
unchanged bootstrap PING, proving only the built-in external-echo behavior.
The runtime briefly tested custom service `0x8001`, matching upstream `nsocket`,
but the TI macOS host API rejected it with `-281`.

Follow-up service probe (2026-09-20 17:55): the same TI Java host stack
returned `READY service=0x5001` for the project-private service. It logged
successful service/NavNet shutdown but left its client JVM alive, so this run
also drove the helper watchdog and PID-scoped RMI cleanup changes. This
establishes host-side registration and cleanup behavior only; the calculator
was still not in normal CX II USB mode, so no `CONNECTED`/`RX` is claimed.

Latest live observation (2026-09-20 10:13): with the Java bridge kept running,
the helper returned `READY service=0x4051` and `NODE 1` on the earlier
built-in-service trial; no application
`CONNECTED`, `RX`, or same-page response followed. Stopping that bridge released
the USB interface: N-Link then listed `/nspire_ai.tns` and the other deployed
files successfully. TI Student Software still showed “No handheld selected”,
so it was not used as evidence of the calculator page being open. A direct raw
helper screenshot request opened the CX II handle but did not complete before
the diagnostic timeout. The physical acceptance gate therefore remains open:
the calculator itself must visibly open the deployed `nspire_ai.tns` page while
the Java bridge is running, then produce `CONNECTED` and a request/response
pair without leaving that page.

The same 2026-09-20 check also read the device info through the raw helper:
`TI-Nspire CX II CAS`, OS `6.2.0 build 333`, LCD `320x240x16`, and
`file_extension=.tns`. N-Link listed `/ndless/ndless_resources.tns` and
`/ndless/ndless.cfg.tns`, which is evidence of the deployed Ndless support
files but not proof that the standalone program is currently running.

The TI-Nspire CX CAS Student Software was opened for a read-only recovery
check on the current USB state. Its UI reported `No handheld selected`, and
both handheld capture and OS-install actions were disabled. The app was then
closed; this is corroborating absence-of-device evidence, not a page-open or
deployment result.

The bridge was started again at 10:25 for the physical page-open test and
produced `READY` and `NODE 1` only before the later diagnostic shutdown. No
`CONNECTED`/`RX` is claimed until the handheld page is visibly open and sends
the NSAI bootstrap/request frames.

## Latest runtime attempt (2026-09-20)

The remote-control diagnostic captured the real calculator screen showing the
deployed `nspire_ai.tns` in the TI document-received dialog and then in the
file browser. Launching that previously deployed build first exposed an
on-device nSDL error dialog: `SDL not configured with thread support`. The
program did not reach its UI, so this was a real runtime failure rather than a
bridge-connect failure.

The program was rebuilt with `SDL_Init(SDL_INIT_VIDEO)` only. nSDL on this
target is compiled with `SDL_THREADS_DISABLED`; requesting `SDL_INIT_TIMER`
caused the popup before the UI initialized. The video-only ARM build passed and
was deployed, but the subsequent page-open attempt still produced no
calculator `CONNECTED`/`RX` evidence.

The current source also waits 2 seconds before its first NavNet node
enumeration, retries failed enumeration every 3 seconds, and retries a dropped
channel every 2 seconds. This is a transport-risk mitigation for a page that
opens while USB/host state is still settling; it does not prove that the prior
USB disconnect or dock-controller observation was caused by enumeration.

## Latest runtime attempt (2026-09-21)

The physical USB gate now passes: macOS enumerated `TI-Nspire(tm) CX II
Handheld` with product `0xE022` under the TS4 hub. The raw helper opened a
persistent CX II handle and reported OS `6.2.0.333`, device name
`TI-Nspire CX II CAS`, and `ready=true`. `scripts/deploy-program-nspire.sh`
then uploaded the current `dist/nspire_ai.tns` artifact successfully.
The current local source and distribution files still match byte-for-byte at
SHA-256 `ed952ba9e836aab410f64902c036fe8151bfb3b141ddd8da804a3a4914b2f783`;
`file` identifies the package as RAGE/RPF and its header begins with `PRG`, so
the pending gate is calculator execution/transport rather than an unverified
host-side copy of the artifact.

The first page-open bridge attempt exposed a host-wrapper lifetime bug: Java
printed `READY service=0x5001` and immediately saw stdin EOF, so it emitted
`STOPPED` before the calculator could connect. The wrapper now explicitly
passes its stdin descriptor to the asynchronous Java child; after that fix,
`run-navnet-bridge.sh echo` stays alive with `READY service=0x5001` and no
premature `STOPPED`. The remaining physical step is to launch
`/nspire_ai.tns` on the handheld and observe `CONNECTED`, calculator-originated
`RX`, and the same-page echo response while the page remains open.

A live screen capture then showed the handheld's `Document Received` dialog for
`nspire_ai.tns`, followed by the file browser with `nspire_ai` selected. The
Java host did emit one real `CONNECTED`, but its first read returned TI's
`-257` (`TI_NN_ERR_INVALID_CONNECTION`). The helper reader now treats that
status as retryable and will replace the reader when a later service callback
provides a new handle; this removes the one-shot reader exit, but does not by
itself prove an application request/response.
The watcher also restarts a reader that terminates on any other negative read
status while the same connection handle remains current, and the next live
callback will log the native connection-handle pointer for diagnosis.

After that attempt macOS enumerated a `TPS DMC Family` child under a CalDigit
TS4 USB hub: vendor `0x0451`, product `0xACE1`. This is the dock's TPS257xx
controller, not evidence that the calculator itself entered recovery mode. The
normal CX II USB PID is `0xE022`; no such handheld interface is currently
enumerated. The current raw helper therefore reports `no TI-Nspire USB device`,
and TI Student Software has no selected handheld. This is an external
device-state boundary: the current local artifact was rebuilt again after the
NavNet retry hardening (local SHA-256
`ed952ba9e836aab410f64902c036fe8151bfb3b141ddd8da804a3a4914b2f783`) but was
not claimed as deployed while no handheld interface is enumerated. Do not infer
a page-open round trip from the build, the earlier file upload, `READY`, or
`NODE 1`; a direct/known-good data path to the calculator and a fresh
deployment are still required before the same-page `CONNECTED` → request →
response gate can be run.

The `TPS DMC Family` identification is consistent with Texas Instruments'
TPS257xx USB firmware-update documentation, while the `0xE022` CX II PID is
listed in Hackspire's Nspire USB protocol notes. These sources support the
device classification; the parent-child topology above is from the local
`ioreg` capture, not from a name-only inference.

At 18:58 on the same date, a fresh `run-navnet-bridge.sh echo` instance stayed
alive at `READY service=0x5001` while the read-only USB gate was polled for 24
seconds. Every poll remained `NO_NSPIRE_DOCK_DMC_CONTROLLER product=0xACE1
vendor=0x0451`; no new `0xE022` interface, `CONNECTED`, or `RX` was observed.
The bridge was then stopped and its helper/RMI cleanup path was allowed to run.

At 19:08, the complete `run-navnet-bridge.sh echo` entrypoint was exercised
again with the same absent-handheld state. Python launched the Java helper and
TI RMI server, the helper emitted `READY service=0x5001`, and Ctrl-C produced
`STOPPED`; a post-run process scan found no new helper or
`RemoteNavnetServer`. This verifies host startup/cleanup only and is not being
counted as calculator `CONNECTED` or an application request/response.

The same host-only gate is now automated by
`scripts/test-navnet-bridge-lifecycle.sh`. Its FIFO keeps stdin open, waits for
the full entrypoint's `READY`, sends SIGTERM, requires `helper: STOPPED`, and
checks that no new bridge/helper/RMI child remains. The first run passed with
outer status `143` (the expected SIGTERM status).

At 20:08 on the same date, after the AI page was exited, the stale bridge was
stopped and the standard `0x5001` entrypoint was started again. The new Java
helper reached `READY service=0x5001`; macOS then enumerated the CX II
(`0xE022`) below the TS4 hub and the helper received a real `NODE 1` event.
This confirms host-side node discovery recovered after the restart. The page
was closed during this observation, so no calculator `CONNECTED`, `RX`, or
same-page request/response is claimed. The previous stop also showed TI RMI
EOF/connection-refused diagnostics after the detached server had already
exited, but the helper still emitted `STOPPED` and the subsequent restart
created a fresh server successfully.

At 20:11, reopening the existing page while that bridge was live produced a
real `CONNECTED handle=0x1`, but the Java host immediately returned repeated
`-257` (`TI_NN_ERR_INVALID_CONNECTION`) reads and no `RX`. Stopping and
restarting the host while leaving that page open did not produce a new `NODE`
or reconnect callback. This supports a stale calculator-side page/channel
state, but does not yet prove that elapsed uptime alone causes the failure.
The calculator source now delays its first PING by 250 ms and disconnects a
connection that receives no valid frame within 2.5 s, so the next deployment
can exercise automatic recovery instead of requiring a page relaunch. The
rebuilt artifact is `dist/nspire_ai.tns` with SHA-256
`46065900de89efb12a56b7b2b2e464c87e8b02125132cbd2806c8b71f25478c5`; it is
now uploaded to the calculator after the old page was exited; page-open
runtime verification with this artifact is still pending.

On the fresh goal audit at 20:30, the user reported that the newly uploaded
page was running. A new Java bridge nevertheless stayed at
`READY service=0x5001` for more than two minutes with no `NODE`, `CONNECTED`,
or `RX`. The independent TI `NspireRemoteControl info` diagnostic also found
no connected node within its 15-second window. USB still enumerated the CX II
as `0xE022`. This observation is not counted as a bridge round trip; the
calculator display/status text is still needed to distinguish the actual
`nspire_ai.tns` Ndless page from a file-browser or another AI interface.

At 20:38-20:40, the calculator page reported `NavNet enum init=-274` while
the bridge was restarted. TI's installed `navnet.jar` constant table maps
`-274` to `TI_NN_ERR_ENUM_DONE`; this is an enumeration-state failure, not a
successful `CONNECTED` event. The same live audit then classified macOS USB as
`NO_NSPIRE_DOCK_DMC_CONTROLLER product=0xACE1 vendor=0x0451`, and the raw
helper returned `no TI-Nspire USB device`. The Java bridge reached
`READY service=0x5001` but never received `NODE`, `CONNECTED`, or `RX` and was
stopped cleanly. This makes the immediate blocker the missing usable `0xE022`
handheld interface, not the application request/response protocol.

## Latest runtime attempt (2026-09-21 19:56-19:59)

The live Java bridge was stopped cleanly and restarted with the standard
service `0x5001`. A controlled service-ID comparison using
`NSPIRE_SERVICE_ID=0x4051` on the same USB topology and unchanged handheld
session `414573196544` also returned `READY` without a `NODE` event. This
rules out the project-private service ID as the reason for the missing node
advertisement.

The experimental raw transport was then started without Java. Its helper
returned `Error: NoDevice` even though macOS `ioreg` still listed the
`TI-Nspire(tm) CX II Handheld` (`0x0451:0xE022`) below
`TS4 USB2.0 HUB@02112000`. This is stronger evidence that the dock path
exposes an enumeration/name record but not a usable interface to either
libusb or TI's NavNet connector. The raw test was stopped and the normal Java
`0x5001` bridge was restored. No `CONNECTED`, calculator-originated `RX`, or
same-page request/response is claimed from this host-only evidence.

## Latest host-side hardening (2026-09-21 20:55-20:58)

The live USB gate remained stable at `NO_NSPIRE_DOCK_DMC_CONTROLLER
product=0xACE1 vendor=0x0451` for ten consecutive one-second samples. The raw
helper still returned `no TI-Nspire USB device`, so no page-open test was
started and no `CONNECTED`/`RX` claim is made. The current artifact hash was
recomputed as
`46065900de89efb12a56b7b2b2e464c87e8b02132cbd2806c8b71f25478c5`.

The diagnostic launcher `scripts/run-nspire-remote.sh` now has a bounded
`NSPIRE_REMOTE_TIMEOUT_SECONDS` (30 seconds by default), forwards termination
to its Java child, and removes only newly spawned `RemoteNavnetServer` PIDs.
A two-second timeout run returned after the deadline with no new helper or RMI
process; the existing pre-run TI server was preserved. The standard protocol
suite (17 tests), Java-helper lifecycle test, and full bridge lifecycle test
all pass after this change.

## Latest physical retry (2026-09-21 21:20-21:35)

The TI Student Software process was restarted and a fresh NavNet server was
created. macOS continuously enumerated the direct handheld as
`TI-Nspire(tm) CX II Handheld` (`0x0451:0xE022`), but the Student Software UI
still reported `No handheld selected`. A single Java bridge reached
`READY service=0x5001` for more than 20 seconds without `NODE`; the bounded
`NspireRemoteControl info` probe also timed out after 15 seconds. The raw
helper and N-Link deployment both stalled during USB initialization and were
terminated. No new bridge, helper, or diagnostic process remained afterward;
`CONNECTED`, `RX`, and same-page response are still unverified.

## 2026-09-22: diagnostic cleanup correction

The user confirmed the calculator displays `Bridge not connected`; lack of RX
is not evidence that the application was never opened. Earlier raw binary logs
reported service 0x4051, whereas current calculator source connects to the Mac
service 0x5001. Raw PING output did not establish application connectivity.
The Java bridge was started in a retained command session with file logging and
reported READY service=0x5001; CONNECTED and a page-open round trip remain unproven.

A timeout diagnostic was observed leaving Python/helper descendants alive.
`scripts/run-with-timeout.py` now starts an isolated process group and terminates
that group on timeout or interruption, with a one-second TERM grace before KILL.
Local checks passed for normal completion, invalid timeout rejection, and cleanup
of a spawned child. This does not cover children that explicitly detach into a
new session; Java wrapper ownership cleanup is still required for those servers.

At the subsequent 2026-09-22 afternoon check, retained Java bridge session
98042 remained alive with only READY service=0x5001. A separate client using
the same RMI server completed its 15-second node wait with the explicit error
`no connected TI-Nspire node within 15000 ms`; its wrapper then bounded shutdown.
No CONNECTED or RX appeared in /tmp/nspire-java-live.log. The current calculator
error detail beyond the user's `Bridge not connected` is still unknown.
Neither this result nor earlier DeviceWillPowerOff notifications establishes
that the calculator screen was asleep, or that the application was not running.
The bridge is intentionally left running for the user's next page observation;
no additional raw USB probe or transfer should compete with this session.

### 2026-09-22: photo identifies enumeration failure

User photo `IMG_4965.HEIC` shows the running standalone program, input `test`,
and repeated `NavNet ENUM INIT=-274`. This supersedes the unknown-error note
above; opening the program is not the missing user action.

Read-only disassembly of the installed TI Mac `libnavnet.dylib` shows
`TI_NN_NodeEnumInit` calling `TI_NN_NH_IsNodeListPopulated`; an empty list
returns `0xfeee` (signed -274). The library also contains the message
`Error(-274): No nodes to give.` This establishes the Mac library meaning,
not independently the handheld firmware meaning or the reason the peer is
absent. It does not establish a cable, sleep, or application-open fault.

Program source now suppresses consecutive identical enumeration-error history
entries and labels -274 as a possible missing peer while retaining the code.
Contradictory service-direction comments were corrected. These are diagnostic
changes, not a connection fix. No calculator deployment or physical echo is
claimed. Retained Java session 60197 remains alive without new CONNECTED/RX.

Verification for these diagnostic changes: all 17 bridge unit tests passed;
the Docker Ndless ARM build completed successfully. The rebuilt local
`dist/nspire_ai.tns` has not been uploaded, so the handheld still runs the
previous artifact. These tests do not validate enumeration or physical USB.

### 2026-09-22: controlled program-exit comparison

The Java API constant is `TI_NN_ERR_ENUM_DONE = -274` (verified with
`javap -constants` on installed navnet.jar). Added optional
`NSPIRE_NAVNET_LOG_LEVEL=0..3` to the helper; default remains 0.
Old session 60197 stopped with STOPPED/exit 130 and its two JVMs disappeared.
Diagnostic session 17966 started with log level 3 and READY service=0x5001.
Connector logs show the CX II interface opened successfully. A one-second
sample of its server PID 22052 showed all 700 samples of the device-add thread
in `UsbReader::addDevice -> UsbIo::sendAddressRelease -> UsbIoMac::write ->
IOUSBInterfaceClass::WritePipe`. This occurred before NavNet node discovery.

Asked user to exit only the calculator program, retaining cable and bridge.
After user confirmed, the SAME session emitted NODE 1 at 16:54:43. A second
sample no longer contained that addDevice/sendAddressRelease stack. This
supports program-state interference, not a cable replacement diagnosis.
Ndless source `ndless/src/resources/ploaderhook.c` disables interrupts before
calling the standalone executable entry point and restores them after return.
This provides a concrete mechanism consistent with the comparison; it does
not yet prove every contributor or validate an interrupt-enabling workaround.
Next implementation path: existing native Lua page plus short extension
callbacks, preserving OS event processing. Do not enable interrupts blindly
inside SDL or claim standalone stability. NODE 1 is not CONNECTED/echo.

### Native-page deployment following the comparison

Rebuilt `AI.tns` (label `Native Lua / NavNet 0922`) and its extension. Extension
read timeout changed from unverified zero semantics to 1; actual callback
latency is still a physical test gate. Added directory/upload operations to
the Java remote client, sharing the existing RMI server instead of opening a
competing raw USB client. Directory enumeration succeeded. Fixed CLI JVM
termination after proxy shutdown; a missing local upload exited 1, successful
uploads exited 0, while bridge session 17966 remained running.

At 17:00:30 TI putFile reported upload of `/nspire_ai_nav.luax.tns` (62876 bytes);
at 17:00:58 it reported `/AI.tns` (4209 bytes). Both used absolute local paths.
No readback hash or page-open echo is claimed. Standalone `/nspire_ai.tns`
was not replaced. All 17 bridge unit tests passed and scoped diff check passed.
Next handheld action is to open **AI.tns**, not the standalone nspire_ai file.

At 17:03 the received-document Open action was selected through TI remote
events. Captured `var/native-open-0922.png` shows the native AI document,
`Native Lua / NavNet 0922`, `Extension: loaded`, and `Service: waiting for Mac`.
USB screen capture continues while that document remains open. This proves
neither UI responsiveness nor the NSAI loop: subsequent remote typing/menu
events returned successfully but `var/native-menu-0922.png` remained unchanged,
and bridge session 17966 had no CONNECTED/RX. Requested a manual menu/probe
check to distinguish remote-input limitations from a blocked UI callback.
Source menu callbacks now bind actions directly rather than depending on
undocumented callback argument values. This last Lua change is not deployed.

### User correction: legacy Lua rejected and removed

User reports the Lua page is frozen and explicitly rejects returning to that
old implementation. This supersedes the proposed Lua continuation above.
Deleted local Lua page sources, extension sources/build products, four legacy
dist artifacts, and their UI/extension build and deployment scripts. No new
backup or Trash copy was created. Git history was not rewritten. Current
`dist` contains only standalone `nspire_ai.tns`; `make -n all` selects its
Docker build. Calculator-side legacy files have NOT been removed because the
page is frozen; no restart has been performed. Continue debugging the newer
standalone program and its USB/interrupt interaction, not the old Lua route.

After the user's Reset/Ndless reinstall confirmation, the existing Java bridge
reported NODE remove/add without a host restart. Root directory enumeration
identified and deletion removed exactly `/AI.tns`, `/AI-ui-demo.tns`,
`/nspire_ai.luax.tns`, `/nspire_ai_nav.luax.tns`. A subsequent root listing
confirmed their absence and retained `/nspire_ai.tns` (257068 bytes), ndless,
and unrelated documents. Both `/nspireai-backup-0916` and `/nspireai/legacy`
listed no entries. Removed five enumerated legacy exchange files from
`/nspireai`: `.exchange.body.tmp`, `request.tns`, `request.id.tns`,
`response.tns`, `response.id.tns`. No backup or Trash copy was made. Empty
directories were retained; no recursive device deletion was performed.

### Standalone IRQ-scoped candidate (not runtime verified)

Added `src/program/nav_os_call.h`: save interrupt mask, enable interrupts for
each NavNet syscall, restore the exact mask immediately on return. This follows
the convention in upstream libndls `_show_msgbox.c`, not evidence that all
NavNet calls are safe. SDL/direct hardware operations remain outside the scope.
A syscall that never returns still cannot be bounded by the main-loop watchdog.
Host C regression checks verify single evaluation, signed error/pointer return,
and restoration from enabled/disabled states (`make program-test`). These and
17 bridge tests passed; ARM build passed. TI Java putFile reported successful
upload of `/nspire_ai.tns` at 17:16:17, 257408 bytes, CLI exit 0.
Local SHA256: `ce92ae85e1d60cf9c3d4fea08ff1e897d35e13718cafd0ce23080fddd9e13c6c`.
Startup label is `IRQ-scope 0922`. No readback hash, CONNECTED, or echo proven.

At 17:18:36 the new `verify-program` CLI downloaded `/nspire_ai.tns` through
the existing RMI server and compared every byte with the local build: VERIFIED,
257408 bytes, SHA256 `ce92ae85e1d60cf9c3d4fea08ff1e897d35e13718cafd0ce23080fddd9e13c6c`.
The temporary readback file was deleted in finally; no backup retained.
This supersedes only the missing-readback statement above. Screenshot
`var/irq-scope-current.png` showed the received-document Open prompt, not
the running candidate. Bridge session 17966 remains live without CONNECTED/RX.

Subsequent read-path review: installed Java constants define -1 as
TI_NN_ERR_STORAGE_FULL, not timeout. Mac TI_NN_CH_Read maps incomplete
transaction (-258) to zero; handheld parity is not established. Removed the
standalone source's unsupported special case ignoring -1 and retain the actual
negative error in its status. This source change is not built or deployed yet:
keep the verified IRQ-scope candidate stable for the pending physical test.

### IRQ-scope candidate FAILED physical test

User explicitly reports running it caused a freeze followed by a crash.
The readback-verified ce92ae85 build must not be described as stable or working.
No CONNECTED/RX was observed on bridge session 17966 after this report.
Reverted NAV_OS_CALL to a passthrough and changed its regression test to require
unchanged interrupt state. This removes the failed experiment, not the original
USB defect. The actual crashing instruction is unknown; no handheld crash dump
is available. Do not infer it from a host-only macro test or from the OS dialog
helper, which runs in a different execution context. No replacement upload or
device reset has been performed in response to this report.

User clarified the keys were unresponsive (whole-device freeze), then confirmed
a manual Reset. At 17:37 the same host server enumerated a new node handle and
listed the failed 257408-byte `/nspire_ai.tns`. Recovery deleted exactly that
file through TI's file API (17:37:56, CLI exit 0). No replacement was uploaded;
Ndless and unrelated documents were not targeted. Source IRQ rollback is not
a verified replacement. The failed local build remains identifiable by its
recorded hash and blocked from both deployment paths, not a usable release.

The source also contains a narrower `NSPIRE_NGC_USB_IRQ_MENU=TRUE` candidate:
the default package remains IRQ-neutral, and only a user-triggered Menu arm
enters the existing IRQ window; toggling Menu off restores the saved state.
At the time this source note was written, the candidate was local-only and
independently gated by `NSPIRE_ALLOW_NGC_IRQ_MENU_UPLOAD=1`. It was later
built, uploaded, and physically tested; that test froze/crashed the handheld.
The exact SHA-256 was
`c8c564c2910a2f907fc792b47329a591cbc93dcbfc9e8f61327e73d2ac75aadf`; the
candidate manifest records `ngc_irq_menu=TRUE`. It is now permanently blocked
and the environment override no longer enables it.

### Host RMI bootstrap fix (2026-09-24)

The TI Student Software process was still running while its UI reported
`No handheld selected`. After that process was closed, a fresh Java helper
still reproduced `got registry` followed by repeated `server is not started`
lookups when NavNetCommProxy tried to launch `RemoteNavnetServer` itself.
Starting the same TI `RemoteNavnetServer` command explicitly before the helper
made the helper reach `READY service=0x5001` reliably. `scripts/run-nspire-java-helper.sh`
now performs that bootstrap only when TCP 1099 has no listener, preserves any
pre-existing TI server, and removes only the server PID created by this
invocation during cleanup.

Evidence after the change:

* `./scripts/test-nspire-java-helper-lifecycle.sh` — PASS: READY → STOPPED,
  no new helper/RMI process.
* `./scripts/test-navnet-bridge-lifecycle.sh` — PASS: bridge READY → STOPPED,
  no new bridge/helper/RMI process (outer SIGTERM status 143 is expected).

This repairs host startup/lifecycle only. It does not establish the missing
physical calculator-side `CONNECTED` → request → same-page response loop.

Immediately after the attempted safe-package upload, the read-only USB gate
reported `STATE=NO_NSPIRE_DOCK_DMC_CONTROLLER product=0xACE1 vendor=0x0451`
(`TPS DMC Family` under `TS4 USB2.0 HUB`) and no `0xE022` CX II interface.
The upload therefore did not reach a handheld, and no device-side claim is
made from that attempt. The next physical prerequisite is an actual
`STATE=CX2_USB_CANDIDATE product=0xE022` observation; the dock controller is
explicitly rejected by the gate.

### Safe-package physical retest after endpoint recovery

On the next attempt the gate reported `STATE=CX2_USB_CANDIDATE product=0xE022`.
The remote client enumerated serial `0000000001049C94`, identified the device
as `TI-Nspire CX II CAS`, and reported `runLevel=4`. The default package was
uploaded to `/nspire_ai.tns` and read back byte-for-byte:

```text
UPLOADED /nspire_ai.tns bytes=25624
VERIFIED /nspire_ai.tns bytes=25624 sha256=77b33e21661be5556c1f6a9c14e9ba3d0959c8da81ae1fca8546c41a49a5daec
```

The calculator screenshot before launch showed the upload confirmation, and
the file browser then showed `/nspire_ai` selected. After sending Enter to
launch the standalone program, the USB gate still saw `0xE022`, but a fresh
NavNet client waited 30 seconds with no connected node. The production bridge
reached `READY service=0x5001` and then remained without `NODE` or
`CONNECTED`; Ctrl-C produced `helper: STOPPED` and cleanup completed. This is
the first current physical reproduction of the remaining launch-boundary
failure with the safe package; no request/response claim is made.

The remote CLI also exposed and fixed a host-only issue: because it must run
from TI's JAR directory, relative local paths such as `dist/nspire_ai.tns`
were previously resolved under the application bundle. `run-nspire-remote.sh`
now resolves local arguments before changing directory, and the static gate
checks that behavior.

### Menu-gated IRQ candidate attempt (not uploaded)

The explicitly approved candidate was prepared and independently hashed before
touching USB:

```text
sha256=c8c564c2910a2f907fc792b47329a591cbc93dcbfc9e8f61327e73d2ac75aadf
ui_backend=TRUE
ngc_auto_transport=FALSE
ngc_irq_window=FALSE
ngc_irq_menu=TRUE
build_status=success
```

The upload gate was enabled only for this candidate. At the first attempt the
USB descriptor still reported `0xE022`, but `run-nspire-remote.sh upload` and a
read-only `info` probe both reached NavNet initialization and then timed out
waiting for a `NODE`; no `UPLOADED`, `NODE`, or device-side SHA was reported.
Computer Use independently showed the TI Student Software window with
`No handheld selected`. After ending the unresponsive TI processes, the
handheld descriptor disappeared and the read-only USB gate correctly returned
only the TS4 `0xACE1` dock controller. Therefore this attempt is a transport/
enumeration blocker, not evidence that the Menu candidate froze or ran, and it
must not be counted as a physical candidate test.

### Menu candidate upload and physical launch attempt

After the handheld endpoint re-enumerated as `0xE022`, the explicitly approved
Menu-gated candidate was uploaded and read back from the same CX II CAS node:

```text
NODE id=1C50000000001049C94E681A757 name=TI-Nspire CX II CAS serial=0000000001049C94 runLevel=4
UPLOADED /nspire_ai.tns bytes=25828
VERIFIED /nspire_ai.tns bytes=25828 sha256=c8c564c2910a2f907fc792b47329a591cbc93dcbfc9e8f61327e73d2ac75aadf
```

The first Enter dismissed TI's `Document Sent` dialog; a second Enter was
attempted to launch the selected file. That remote key call did not return its
`KEY` marker before the 45-second safety bound, and a subsequent read-only
NavNet screen probe found no node while the USB descriptor remained present.
No further remote key was sent. The production bridge was then started and
reached `helper: READY service=0x5001`, but has not yet observed `NODE` or
`CONNECTED`. This is intentionally recorded as an incomplete launch/Menu
boundary. At the time of this log entry the candidate had not yet been
physically classified; the later physical Menu test is recorded below.

### Menu candidate physical failure — permanently rejected

After the launch-boundary attempt, the user physically held/pressed `Menu` on
the calculator. The handheld immediately froze/crashed. No `NODE`,
`CONNECTED`, request, RX, or same-page response was observed; the bridge was
stopped cleanly (`helper: STOPPED`) and no further key was sent.

This is authoritative physical failure evidence against the candidate, not a
cable or host timeout. The failing selectable path is
`NSPIRE_NGC_USB_IRQ_MENU` at `nav_irq_window_enter()`, which writes the IRQ
controller and CPU interrupt-mask state on the CX II. The exact artifact
`c8c564c2910a2f907fc792b47329a591cbc93dcbfc9e8f61327e73d2ac75aadf` is now
permanently rejected by both upload paths. Any future package with
`ngc_irq_menu=TRUE` is also rejected unconditionally; do not retry this path
or any candidate that writes the same IRQ mask sequence.

Post-failure safety verification: the read-only USB state checker still saw
the `0xE022` CX II descriptor, but a bounded `list /` probe found no NavNet
`NODE` and was cleaned up without sending a key or changing calculator files.
No bridge/helper/RMI process was left running. The upload/build gates were
then exercised locally: the Menu candidate build returns exit 65, and
`check-ngc-irq-window.py`, `make program-test`, the 19 host bridge/protocol
tests, both lifecycle tests, and `git diff --check` pass. This validates the
prevention and host-side cleanup only; it is not a physical CONNECTED or
request/response result.

### Safe package reopened after Ndless activation

After Ndless was activated again, the previously uploaded Menu-crash artifact
was read back at the exact SHA and deleted from `/nspire_ai.tns`. The current
IRQ-neutral package was then uploaded and read back from the same CX II CAS
node:

```text
VERIFIED /nspire_ai.tns bytes=25624 sha256=77b33e21661be5556c1f6a9c14e9ba3d0959c8da81ae1fca8546c41a49a5daec
ngc_irq_window=FALSE
ngc_irq_menu=FALSE
```

The user opened that package and reported the on-page status
`NGC RTC USB idle Menu enables`. A read-only screen/node probe observed no
stable `NODE` while the page remained open. The production bridge reached
`helper: READY service=0x5001`, then was stopped with
`unregisterNotifyCallback` and `helper: STOPPED`; no `CONNECTED`, request RX,
or response was observed. No Menu key was sent. This confirms the safe package
does not reproduce the earlier Menu-gated IRQ crash, but the standalone-page
USB scheduling blocker remains unresolved.

On a fresh read-only check with the page still reported open, macOS exposed the
`0xE022` CX II USB descriptor, but the Java `info` client initialized and
registered its callback without receiving a stable node before its 25-second
outer timeout. Relaunching TI Student Software changed neither its visible
`No handheld selected` state nor the outcome of a second bounded `info` probe.
Both probes cleaned up; no helper/RMI child was left running. `READY`, USB
descriptor visibility, and successful host NavNet initialization therefore
remain separate from the missing `NODE`/`CONNECTED`/request/response evidence.

A standard 45-second `run-navnet-bridge.sh echo` session after launching TI
Student Software reproduced the same boundary: `helper: READY service=0x5001`
with no `NODE`, `CONNECTED`, or `RX`, followed by a controlled
`helper: STOPPED` shutdown. No background helper/RMI process remained.

The tempting calculator-side `TI_NN_Init(NULL)` hypothesis was rejected before
editing or uploading code: the [Ndless NavNet API notes](https://www.hackspire.org/Syscalls/)
document `TI_NN_Init` and `TI_NN_Shutdown` as *computer-side* stack lifecycle
calls, while calculator-side
clients enumerate nodes directly. Calling host initialization from the
handheld would be an unsupported experiment, not an established fix.

### 2026-09-24 connector boundary and CPU-IRQ-only candidate

After a controlled TI Student Software restart, the fresh connector log found
the actual handheld and opened its USB configuration:

```text
DeviceAdded(): IORegistryEntryGetName: TI-Nspire(tm) CX II Handheld
ConfigureNspireDevice(): Successfully set configuration to value 1
addDevice(): Opening USB\\18\\NSP_02112400
```

The same `connector0.log` then stopped at
`kIOMessageServiceIsAttemptingOpen` while the server process remained alive.
This is stronger evidence than the earlier descriptor-only result: the device
is present, but the USB/OS scheduling path does not progress after page launch.
The process was not force-killed and no package was uploaded during this
observation.

Ndless's loader masks CPU IRQ delivery before entering a standalone program;
the production NGC loop then intentionally busy-spins with no scheduler call.
An opt-in `NSPIRE_NGC_CPU_IRQ` candidate was added for the next controlled
experiment. It enables only `TCT_Local_Control_Interrupts(0)` after the first
frame, performs no interrupt-controller or timer writes, and restores the
previous CPU mask on exit. The flag defaults to `FALSE`, both deployment paths
require an explicit `NSPIRE_ALLOW_NGC_CPU_IRQ_UPLOAD=1` gate, and the existing
safe artifact remains unchanged. The candidate passed source/static audits and
host program tests; it has not been packaged or uploaded because the local
Ndless ARM Docker toolchain is unavailable in this session.

### 2026-09-24 23:02--23:05 fresh USB/node and screen probe

The CX II became visible again on the direct USB path as
`0x0451:0xE022`. A bounded `run-nspire-remote.sh info` completed with the
actual handheld node:

```text
NODE id=1C50000000001049C94E681A757 name=TI-Nspire CX II CAS
serial=0000000001049C94 runLevel=4 connectionType=0
```

The production Java bridge then reached `helper: NODE 1` and
`helper: READY service=0x5001`, but a 20-second session produced no
`CONNECTED`, calculator-originated `RX`, or response before controlled
shutdown. This is expected for the currently verified artifact because its
manifest has `ngc_auto_transport=FALSE`; it is not physical round-trip
evidence. A read-only screen capture showed the calculator at Home/Scratchpad,
not inside `nspire_ai.tns`, so the page-open gate was not satisfied in this
probe. No Menu key, reset, upload, or calculator file deletion was performed.

The root file was then opened through the calculator's own file browser using
`Ctrl+O`, directory navigation, and one `Enter` on `nspire_ai`. The open call
did not return within the bounded 20-second remote window. A subsequent
read-only key probe could still enumerate the USB descriptor, but NavNet's
`getNodeInfo` returned `-2` and the wrapper timed out. This is a reproducible
safe-package page-open stall, not `CONNECTED` evidence; the helper was not left
running and no further key or Menu input was sent.

Docker Desktop was then started and the previously unavailable candidate was
rebuilt from a clean program directory with `UI_NGC=TRUE`,
`NGC_CPU_IRQ=TRUE`, and `NGC_AUTO_TRANSPORT=TRUE`. The result is
`dist/nspire_ai.tns` SHA-256
`6fbafc2c81be00e05baf62c898b895e3cbce4a0f6bddd58c5730254369759238`, with a
successful manifest declaring `ngc_irq_window=FALSE` and
`ngc_irq_menu=FALSE`. `make program-test`, `check-ngc-cpu-irq.py`, and
`git diff --check` pass. This candidate has not been uploaded: after the safe
package was opened, the next bounded node probe returned NavNet `getNodeInfo`
`-2` and timed out, so the handheld is not currently in a safe upload-ready
state.

### 2026-09-25 CPU-IRQ candidate physical failure

The candidate above was subsequently uploaded only after the USB checker
reported the normal CX II node. Its exact artifact SHA was
`6fbafc2c81be00e05baf62c898b895e3cbce4a0f6bddd58c5730254369759238` and its
manifest enabled both `ngc_cpu_irq=TRUE` and `ngc_auto_transport=TRUE`.
Immediately after launch the handheld flashed once and then froze. A bounded
readback/screen probe timed out, and a controlled bridge run reached only
`helper: READY service=0x5001`; it produced no `NODE`, `CONNECTED`, or
calculator-originated `RX` before clean shutdown. This is a physical runtime
failure, not a cable or model-backend result.

The exact SHA is now rejected by both upload entry points. CPU-IRQ
re-enablement is not a scheduler fix: this experiment did not establish a
safe interrupt or NavNet execution context. No further CPU-IRQ candidate
should be uploaded until a different, documented runtime mechanism is
identified and host-tested.

The TI Student Software logs provide an independent host-side correlation for
the same failure window. At 11:53:13 the host added node `200.1` for
`Nspire 05523712`; the node-info PTTID, metrics, and PTTENTRY requests all
completed successfully. At 11:54:26 the stream reported
`TI_CN_ERR_STREAM_DISCONNECT`, and at 11:54:28 the host removed node `200.1`.
The later helper run could only reach `READY`, with no calculator-originated
`CONNECTED` or `RX`. This confirms that the host initially saw the handheld,
but the launched candidate did not sustain the NavNet stream; it is not
evidence of a bad cable or an unavailable Mac node.

### 2026-09-25 local-service bootstrap candidate (not uploaded)

The historical calculator-side NavNet ordering was compared against the
current NGC path. The next candidate starts a local `TI_NN_StartService`
endpoint before the first RTC read and stops it during normal exit; it does
not enable CPU IRQs, touch timers, call `idle`/`msleep`, or change the wire
protocol. A clean Docker build produced:

```text
sha256=cc49702f6fa3aa182b0e8daf8ca1dd62eeee14136d17678b70c8bb35f823f14c
ui_backend=TRUE
ngc_auto_transport=TRUE
ngc_cpu_irq=FALSE
ngc_irq_window=FALSE
ngc_irq_menu=FALSE
build_status=success
```

`make program-test`, the startup-order audit, the CPU-IRQ removal audit, and
the bridge tests pass. This is a source/build hypothesis only. It has not
been uploaded or counted as `CONNECTED`; the previous handheld freeze means
the next physical test requires a recovered Home screen and a fresh USB gate.
