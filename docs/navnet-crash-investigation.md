# Standalone NavNet freeze investigation (2026-09-22)

## Confirmed observations

- 2026-09-23 a clean TI Student Software restart replaced the old
  `JavaAppLauncher`/`RemoteNavnetServer` processes. In the fresh session,
  bounded read-only `info` and `screen` probes both received the node callback
  immediately and captured the same Home-screen image. This recovers the host
  connector session but still leaves the calculator application page unopened.

- 2026-09-23 a read-only root listing and download confirmed the handheld's
  current `/nspire_ai.tns` is 55,420 bytes with SHA-256
  `e0282e26c2d5017b76e893a15d51aa8c44a78f94cebcbf23a8d1ff651029d75c`, the
  previously blocked `lcd_blit` candidate. The startup-isolation SHA
  `91820a3a...` is not present on the device. `/nspireai-backup-0916` exists but
  is empty; no deletion was performed during this read-only check.

- The recorded device Data Abort instruction address `0x13A9F5F8` cannot be
  passed directly to `addr2line` for the local NGC ELF: the Ndless image is
  linked at virtual address zero and relocated by the loader, and the loaded
  base (or a contemporaneous RAM map) is unavailable. The candidate ELF has
  `.text` at `0x0`, `main` at `0xACA4`, and `ngc_draw` at `0xA2D0`; direct
  `addr2line` of the crash address yields `??:0`. Do not claim the Data Abort
  was caused by RTC, GC, LCD, or NavNet from that address alone.

- The isolated-candidate upload wrapper now uses the TI Java NavNet session,
  not raw N-Link, because the former completed read-only list/download while
  TI Student Software owned USB. Both the wrapper and Java direct-upload path
  require the exact candidate SHA and the explicit stage-upload confirmation
  variable. No stage candidate was uploaded while making this change.

- 2026-09-23 fresh TI GUI check: the Connected Handhelds pane showed the same
  `TI-Nspire CX II CAS A757` row that the read-only NavNet client enumerated,
  but the window still reported `No handheld selected... please connect a
  handheld`. Single- and double-clicking the device cell did not change the
  status, and `Capture Selected Handheld` stayed disabled. No upload or
  calculator launch was attempted in this check; this strengthens the
  host-GUI/session mismatch evidence without proving calculator-side state.

- 2026-09-23 after two short NavNet client timeouts, a bounded 30-second
  read-only probe recovered the node callback; an immediate screen capture then
  succeeded. The 320x240 image (SHA-256
  `95fc137eebc082cdc7659f426de6d295d97d1ff36a5433fffb9c0f580d08e21a`) shows
  the handheld Home screen (`Scratchpad`, `Documents`, `A Calculate`), not
  `nspire_ai`. This separates intermittent host node recovery from the still
  missing calculator page and does not authorize another package upload.

- 2026-09-23 clean SDL candidate `53f5f149...` built and readback-verified at
  `/nspire_ai.tns`. Handheld Browse selected `nspire_ai` (252 KB); Enter left
  the file list visible with a persistent wait cursor. Another Enter and Menu
  had no visible effect, while Mac Java still enumerated the node and captured
  the screen. No NspireAI UI, CONNECTED, RX, or response was observed. The
  candidate is blocked from re-upload. This is a startup-stall observation,
  not an identified faulting instruction or proof that SDL alone caused it.

- 2026-09-23 the shared full-chat source linked successfully in NGC mode after
  bypassing the Debian/newlib-incompatible `nspire-ld` wrapper. The resulting
  `.build/ngc-make-trial/nspire_ai.tns` is 53 KB with SHA-256
  `bc2c2099934f622cf0b3c137f7bb46416f04c1c45f9935f2351f03763bdc495f`.
  Static symbols show NGC graphics, RTC `gettimeofday`, matrix keys and the
  NavNet calls, with no SDL/`msleep`/`idle` symbols.

- 2026-09-23 that NGC candidate was uploaded through the TI Java session as
  `/nspire_ai.tns` (54,444 bytes), and a root listing confirmed that size. The
  exact readback verifier then timed out after 45 seconds. Screen capture still
  returned the pre-upload Browse image, and virtual `Enter`/`Home` key calls
  returned without any visible screen change. No NspireAI page, CONNECTED, RX,
  or response appeared. The candidate SHA is blocked from repeat upload; this
  does not identify whether the stall is loader, OS UI, or program entry.

- 2026-09-23 the NGC graphics path was changed to the SDK's new
  `lcd_blit`/`lcd_type` API and rebuilt cleanly as SHA
  `e0282e26c2d5017b76e893a15d51aa8c44a78f94cebcbf23a8d1ff651029d75c` (55,420
  bytes). Static symbols showed the new LCD calls and no SDL, `idle`, `msleep`,
  or old GC-blit helper. The package uploaded successfully and `/` listed the
  matching 55,420-byte file. A single controlled `~enter~` followed by a fresh
  screen capture left the image byte-identical to the pre-key capture: the old
  Browse list still displayed the stale 252 KB row. No NspireAI page,
  CONNECTED, RX, or response was observed. This SHA is blocked from repeat
  upload; the evidence still cannot distinguish stale/frozen UI from loader or
  program-entry failure.

- 2026-09-23 host-side crash evidence: TI's
  `NN-crash-20260923-160155.log` reports SIGSEGV in
  `libnavnet.dylib!Java_com_ti_eps_navnet_server_NavNet_stopService+0x1e`
  while servicing `RemoteNavnetServer.stopService(0x5001)`. The null access is
  in the Mac NavNet teardown path, separate from calculator entry and protocol
  RX. `NspireNavnetHelper` now avoids `NavNet.stopService()` by default and
  leaves that call behind the explicit `NSPIRE_NAVNET_STOP_SERVICE=1` switch;
  normal cleanup disconnects and bounds the remaining proxy shutdown.

- 2026-09-26 shared-server teardown evidence: the fresh
  `NN-crash-20260926-203006.log` reports SIGSEGV in
  `libnavnet.dylib!List_RemoveItem+0x34` while the RMI server handles
  `NavNet.stopService(0x5001)`.  The paired `navnetlog0.log` removes the
  shared `0x5001` service at the same timestamp, and `appmuxServer0.log` shows
  several callback unregisters followed by server shutdown.  This is a
  host-side native teardown failure, not calculator-side protocol evidence.
  The helper therefore no longer calls `NavNetCommProxy.shutdown()` by default:
  `NSPIRE_NAVNET_PROXY_SHUTDOWN=1` is now an explicit controlled-test switch,
  matching the existing `NSPIRE_NAVNET_STOP_SERVICE=1` guard.  The wrapper
  still cleans only a detached server created by that invocation.

- 2026-09-23 after restarting TI Student Software, a fresh server launch
  failed again before node enumeration. `NN-crash-20260923-182923.log` shows
  `CoreFoundation!_CFGetNonObjCTypeID` called by
  `nwb_nspire_connector.dylib!UsbNotifyThread::unregisterPowerMgmt` during
  `TI_NN_Init`/connector shutdown, and the client received RMI EOF with
  `NavNetInitException -304`. The USB checker still reports the CX II USB
  candidate, so the remaining physical gate is a TI/macOS connector state
  problem, not evidence of calculator-side `CONNECTED`.

- The read-only direct N-Link fallback also hung on `n-link ls /` for more
  than 30 seconds and was terminated. macOS ioreg still reports the active
  1105:0xE022 CX II at USB 2 high speed, so physical enumeration is present
  while both host transport implementations lack a responsive session.

- At 19:06 a fresh bounded `run-nspire-remote.sh info` completed successfully
  and enumerated the same CX II node/serial. This confirms host-side NavNet
  enumeration recovered after the Data Abort; it is still host-only evidence
  and did not launch the `.tns` or establish calculator-side `CONNECTED`.

- At 19:33 the same bounded probe timed out without a `NODE` callback even
  though the USB descriptor remained present. Restarting TI Student Software
  (without rebooting macOS or resetting the calculator) recreated the
  connector/server; at 19:34 a fresh read-only `info` returned the same node
  ID/serial with exit 0. This is a repeatable host recovery observation, not
  page-open or application protocol evidence.

- At 18:38 the TI application window was open, but its UI reported `No
  handheld selected`. This confirms the current failure is before the
  calculator-side page or NavNet service can be tested.

- The host connection later recovered without a Mac reboot. Java enumerated
  the expected node, Browse refreshed to the current `nspire_ai` row at 55K,
  and one controlled Enter returned the calculator to Home with `A Calculate`
  selected. A five-second follow-up capture stayed on Home. No NspireAI page,
  CONNECTED, RX, or response appeared, and no new TI crash log was produced.
  This directly confirms the current `e0282e26...` candidate does not leave a
  visible running page after launch; it is blocked from repeat upload.

- The TI Student Software log then recorded calculator-side crash telemetry in
  the same node-recovery window. At 18:49:01, immediately after the node ADD
  event, NavNet reported `6.2.0.333 CX II CAS Data Abort` with
  `IA=0x13A9F5F8`, `DA=0xE8BD4149`, and `USB`, then removed and re-added the
  node. This identifies a device Data Abort rather than a clean application
  return, but it does not map the instruction address to a source line or
  prove that the current package alone caused it. The package remains blocked
  and no repeat upload is authorized from this evidence.

- The offline NGC control probe was corrected to use `lcd_blit`/`lcd_type`
  instead of the SDK's legacy raw-framebuffer helper, and its build audit now
  checks the source object for accidental NavNet/SDL/timer references before
  linking. The ELF-only build succeeded (SHA-256
  `acf8255d81d33dc0cd54f783c9b5e4e7db32104551ef98b1c861ff72971cd2da`); it was
  not packaged or uploaded, so it is only a future startup-stage diagnostic.

- A separate NGC startup-isolation candidate now draws its first frame before
  the first RTC `gettimeofday()` call. The clean ARM object and custom-linked
  ELF passed the reviewed symbol audit (ELF SHA-256
  `f3bde04df2d78bc06981038d2538004fe1d735a010de5c134f5ee103a9f5aa13`). The
  packaged TNS is kept only under `.build/ngc-stage-isolation/` (SHA-256
  `91820a3ac0ec564a995f0136e64b2c8d384ce9507ea8d410d9f99bb97fb70858`); it was
  not copied to `dist` or uploaded, so no hardware claim is attached to it.
`scripts/check-ngc-startup-order.py` is now part of `make program-test` so
this ordering cannot silently regress in a later build.

The candidate remains isolated from `dist` and has an opt-in upload wrapper at
`scripts/deploy-ngc-stage-candidate.sh`. The wrapper hard-pins SHA
`91820a3ac0ec564a995f0136e64b2c8d384ce9507ea8d410d9f99bb97fb70858`, checks
the adjacent successful manifest and CX II USB descriptor, and requires
`NSPIRE_ALLOW_STAGE_CANDIDATE_UPLOAD=1`. No upload was invoked while adding
or testing this guard.

- Offline follow-up: the SDK examples initialize the new LCD API with
  `lcd_init()` before the first `lcd_blit()`. The previous stage-isolation
  source only selected `lcd_type()` and blitted, so that package is now
  superseded and blocked. A fresh Docker build with `lcd_type()`/`lcd_init()`
  before the first frame produced SHA
  `34345e069a1bbaacd1b4b289e37ada9d04d8f1105c11fa259b5d519937998f26`
  (56,868 bytes). This is an offline candidate only; no device upload or
  runtime claim has been made.

- Original standalone program: repeated enum-init -274, no application echo.
- With that program open, the Mac connector sample was blocked in
  `UsbReader::addDevice -> sendAddressRelease -> WritePipe`. Exiting the program
  produced NODE 1 in the same host process without cable reconnection.
- IRQ-scoped candidate ce92ae85: user reported unresponsive keys and required
  Reset. It was removed from the handheld; do not redeploy it.
- Legacy Lua also froze and was removed at the user's request. It is not a
  fallback implementation for this investigation.

## Source findings, not crash proof

Pinned Ndless 9484d8d:

- `ndless/src/resources/ploaderhook.c`: disables interrupts around the
  executable entry point and restores them on return.
- `ndless-sdk/libndls/_show_msgbox.c`: temporarily enables interrupts around
  the OS dialog. That is a different execution context from SDL/NavNet.
- `ndless/src/resources/ints.c`: SWI handler stores saved SPSR/LR in shared
  slots, with one additional recursion level. `resources/syscalls.c` warns
  extension syscalls about non-reentrancy. This is a reason not to assume
  interrupt-enabled calls are safe, NOT proof of the observed crash mechanism.
- `ndless-sdk/libsyscalls/stubs.cpp`: five-argument TI_NN_Read uses a naked
  wrapper preserving the fifth stack argument and a shared saved-LR stack.
  The SDK declares its return unsigned 16-bit; program captures int16_t.
  Passing the receive-size pointer as uint32_t matches this SDK declaration
  on the 32-bit target. No ABI mismatch is established by this review.

Pinned nsocket 6e90b2cccdb3b51f72a2dc03a85a1c9325339cbe, fetched read-only:

- `ns_client/nsocket.c` enumerates a host and connects as client, with no
  explicit interrupt toggling. Its demo uses OS graphics (`ngc`), not SDL.
- This supports the client/server direction, but provides no CX II OS 6.2
  compatibility evidence. Do not treat the old demo as hardware validation.

## Next discriminating checks

1. Inspect nSDL initialization/timer hardware changes against pinned sources.
2. Resolve the generated syscall address table for the installed Ndless/OS,
   not only IDC labels. Current review located labels but not that table.
3. Before any next handheld run, isolate stages: UI-only, operation creation,
   enumeration, connection, then read/write. Show the last entered and last
   returned stage. No automatic retry storm, no interrupt modifications.
4. An SDL-only control is diagnostic, not a replacement for the required
   same-page request/response outcome. A main-loop watchdog cannot rescue a
   syscall that does not return; don't call it freeze protection.

Do not upload another speculative candidate merely because host tests pass.
The exact crashing instruction remains unknown; there is no firmware crash
dump or emulator reproduction yet.

## Actual linked SDL timer evidence

`link-sdl` is a Link-character game demo, not a USB link example; its name
provides no networking evidence. Upstream hoffa/nSDL commit
`de58382bd540c26cbd614259541f967277662b72` provides timer source, but the locally
linked archive was inspected directly rather than assuming byte identity.

`arm-none-eabi-objdump -dr .deps/ndless/ndless-sdk/lib/libSDL.a` confirms:

- SDL_InitSubSystem calls SDL_StartTicks on its first invocation even without
  SDL_INIT_TIMER (call relocation at offset 0x90). VIDEO-only does not avoid it.
- SDL_StartTicks modifies 0x900B0018, writes clock select 0xA at 0x900C0080,
  and writes 0x82 (interrupt disabled/free-running) to 0x900C0008 on color HW.
- SDL_Delay branches to msleep. Local libndls/sleep.c reprograms a second
  timer and masks IRQ sources during its wait before restoring their mask.

These are concrete hardware-state changes during the program lifetime. Whether
the CX II OS USB scheduler depends on those timers has not been established.
Do not combine this finding with another speculative IRQ-enabled deployment.
Next isolation must avoid SDL timer takeover, not merely remove SDL_INIT_TIMER.

## Post-reset check and reproducible mapping audit (17:48)

- Read-only `run-nspire-remote.sh list /` completed with exit 0 using the
  existing Java server. The CX II CAS directory listing contains no root
  `nspire_ai.tns`. No package was uploaded or started during this check.
- `python3 scripts/audit-navnet-syscalls.py` audits the pinned generator's OS
  ordering, SDK syscall numbers, and target IDC labels without USB access.
  CAS CX II 6.2 is index 46; required NavNet labels each occur once.
- The local `ndless-sdk/include/syscall-addrs.h` is absent. The script reports
  this explicitly; IDC consistency is NOT verification of the generated table
  or the Ndless runtime installed on the calculator. If a generated table is
  supplied later, the script compares its relevant entries with the IDC.
- SDK `TI_NN_GetConnMaxPktSize` has no same-named target IDC label (the IDC has
  `TI_NN_GetNodeMaxPktSize`). The current standalone source does not call it;
  do not treat this unrelated discrepancy as the crash cause or alias it by
  guesswork.
- Host `make program-test` and all 17 bridge tests pass. No timer-neutral
  diagnostic or manual stage stepping has been implemented/deployed yet.

## Timer-free caller control (offline only)

`src/program/diagnostic_ngc.c` is a separate NGC drawing/key-transition control,
not a replacement chat program. It is deliberately excluded from the normal
build/deploy target. No package has been produced or uploaded for this control.
It contains no SDL, NavNet, interrupt-control, idle or sleep calls. It polls
Enter/Esc and redraws only on an Enter transition. Busy polling is unsuitable
for normal application power management and is not a USB scheduling fix.
ARM object compilation passed with `-Wall -Wextra -Werror`; undefined-symbol
inspection showed only the expected NGC/key/string/OS-id functions. This is
compile-only evidence: no link, packaged executable, or hardware run occurred.

Further source findings:

- `libndls/idle.c` masks all interrupt sources except timer 19, waits, and
  acknowledges that timer. Replacing SDL_Delay with idle is not an OS yield.
- The loader restores its saved interrupt mask only after the program returns
  and the exit key is released. Removing SDL alone therefore does not establish
  that OS USB processing can run while a standalone program is open.
- IDC contains four different `TMT_Retreive_Clock` labels; SDK has no public
  syscall for it. Do not select a clock address by its name or last occurrence.
- NGC's `gui_gc_blit_to_screen` follows internal GC buffer pointers and uses
  the old screen API (upstream explicitly calls it nonportable). The control
  refuses OS indexes other than 46, but that guard is NOT hardware validation.
  This unresolved graphics-layout dependency must be reviewed before running.

Next requirement remains a safe, supported execution context for concurrent
OS USB work, not simply a different drawing library. No IRQ-enable deployment
is authorized by these source observations.

## Actual handheld runtime readback (17:55)

`list /ndless` exposed an overlooked legacy extension:
`/ndless/nspire_ai.luax.tns` (26144 bytes). Earlier root-only checks did not
establish its absence. It was deleted under the user's existing legacy cleanup
request, without a backup; a second directory listing confirmed absence.
Both installers, ndless.cfg.tns and ndless_resources.tns were preserved.

Added `download <remote> <absolute-new-local-path>` to the Java diagnostic
client. It reserves a new local output (refuses existing targets), downloads
without writing the device, prints size/SHA-256, and removes a partial output
on failure. The actual runtime readback is:

- `.build/runtime-audit/ndless_resources-0922.tns`, 193356 bytes.
- SHA-256 `5994e5096d16e2c4289bc9bc976f0e50f6703c6c603cf9476e043c5a41a41330`.
- Contains `Do you really want to uninstall Ndless r2022?`.
- `python3 scripts/audit-navnet-syscalls.py --runtime
  .build/runtime-audit/ndless_resources-0922.tns` finds all ten audited
  syscall slots at the correct relative offsets in a single row starting at
  file offset `0x2d324`. This compares actual runtime file bytes, not only IDC
  names. Isolated address occurrences do not count as a matching row.

This deprioritizes a disk runtime syscall-address mismatch. It does not prove
the loaded RAM image is identical, dispatch selects this row, target function
semantics match, or any interrupt-enabled calling context is safe. No new
calculator executable was uploaded or run. Java compilation and both readback
and post-delete listing exited successfully; existing RMI server was retained.

## Historical supported test context and RTC discovery

Upstream issue 215 comment 642230613 explicitly says interrupts are necessary
for USB and links the 2013 NavNet test. Inspected source at nsptools-history
commit `fce7f26cd8d9806bc4d9e4b5db85b90d80bf26b6`, path
`Ndless/trunk/arm/tests/navnet/ndless_navnet_tests.c`:

- Enables interrupts once at startup, does not use SDL, and defines calculator
  sleeps as empty. It waits with gettimeofday polling.
- Tests PC-to-calculator service invocation before calculator-to-PC. It warns
  Connect can succeed without a remote service and repeated Write availability
  polling did not work on calculator. This is historical evidence, not CX II
  OS 6.2 verification and not permission to repeat the failed IRQ experiment.
- Issue 171 comment 703779619 explains msleep handles the timer itself so OS
  task switching does not occur. This corroborates the inspected SDK code.

The prior header-only clock search missed newlib's implementation:
`ndless-sdk/libsyscalls/stdlib.cpp:_gettimeofday` reads volatile 0x90090000,
returns seconds and zero microseconds, and does not write timer registers.
The NGC control now has a 15-second RTC exit condition (and exits on backward
clock movement). It is not a watchdog: it cannot recover a stuck graphics
call or a clock that stops. This source addition has not run on hardware.

Firebird commit `b10f3b51a9ab8e27d2703444b5e6bd278d4c5e6e` supports CX II,
but its headless entry requires Boot1 and flash images. Targeted local scans
of Downloads, repository and Applications found no usable Boot1/flash pair
or OS 6.2 update. The TI app has 6.3 update files, not a matching test image;
they were not installed or modified. Emulator reproduction remains unavailable.

Sources: https://github.com/ndless-nspire/Ndless/issues/215 and
https://github.com/ndless-nspire/Ndless/issues/171 .

## Linked control audit rejects hidden IRQ path

`bash scripts/build-ngc-control-offline.sh` now performs an isolated read-only
SDK build, emitting only `.build/ngc-control/control.elf`, map and symbols.
No TNS packaging or upload is included. `--graphics` on the syscall audit
adds the ten NGC/string slots; all twenty inspected slots match the runtime
readback row at `0x2d324`.

The control links after explicitly loading SDK syscall implementations before
newlib (otherwise SDK/newlib allocation routines conflict). However the
post-link check intentionally exits 1: linked newlib support pulls SDK abort,
which pulls `_show_msgbox`, which calls `TCT_Local_Control_Interrupts`.
Disassembly confirms those calls at ELF 0xaaec and 0xab20. Source-level
absence of IRQ calls was insufficient. The control is rejected by the audit,
not approved for hardware, even though its ordinary UI path has no explicit
IRQ toggles. Do not disable the audit or override abort merely to get green.
Need a reviewed error/teardown path before claiming an IRQ-neutral control.

## Error-path review and current evidence boundary

Disassembly of the linked control shows the direct call to `abort` under
`__assert_func`; startup `initialise_monitor_handles` saves the framebuffer,
reads standard stream variables and registers exit cleanup. It does not
unconditionally call abort or enable IRQ. The presence of the abort/dialog
path is a conservative audit rejection, NOT an observed startup failure or
the root cause of the previous device freeze. Preserve SDK error handling;
do not remove it just to satisfy a symbol denylist.

The actual `_gettimeofday` implementation in this ELF reads 0x90090000 and
writes the expected seconds/microseconds fields; it does not configure a timer.
This supports the RTC diagnostic design but cannot establish device liveness.
The production Read timeout comment has been corrected: timeout=1 is not a
verified wall-clock bound and cannot protect a synchronous stalled UI.

At this point the next discriminating evidence is a matching CX II CAS 6.2
Boot1/flash image or a captured crash context (PC/LR/CPSR/stack) for offline
reproduction. None is available in the inspected local locations. Disk table
matching, host tests and another standalone control compile cannot supply
that evidence. No new executable should be run on the handheld to turn this
unknown into another forced Reset. Waiting for a suitable image or a reviewed
reproduction method is a real runtime-validation blocker, not completion.

## Continued development without a device dump

User confirmed no backup and requested continued work. `nav_clock.h` now
separates communication deadlines from SDL: default SDL backend remains,
and `NSPIRE_NAV_CLOCK_RTC` selects the SDK/newlib RTC clock experimentally.
Retry, ping and handshake deadline comparisons use signed modular subtraction
with intervals below 2^31 milliseconds. Host tests cover before/at/after a
deadline, wraparound and coarse ticks. `make program-test` passes.

This is preparatory production-code refactoring, not a USB fix: SDL still
initializes the display/event loop and takes over its timer. RTC also has
second granularity and wall-clock adjustment limitations. No IRQ changes,
new package, or upload accompanies this change. UI/event-loop replacement
and safe OS scheduling remain unresolved; the full same-page echo gate is
unchanged.

## Experimental full-chat NGC backend

`NSPIRE_UI_NGC` now selects `ui_ngc.h` in the actual standalone main.c,
sharing its existing session, NSAI framing, fragmentation and response logic.
The default SDL build is preserved. NGC uses SDK graphics and matrix keys,
RTC deadlines, and one transport poll per second, with no SDL/idle/msleep
calls in that backend. Initial held keys are ignored until a new transition.
It supports basic ASCII typing, Shift letters, Enter send, Ctrl+N new,
Delete and Escape. Typography, remaining punctuation, power usage and all
device behavior remain unverified. This is not a Lua restoration.

No IRQ modification was added. Therefore this removes one interference source
but does not solve the loader's masked-IRQ scheduling restriction. No package
or upload is produced, and SDK abnormal-exit dialog risk remains. A coarse
one-second poll is provisional, not the final responsiveness target.

## Shared transport validation fixes

Both UI backends now avoid Read before the delayed initial PING is due.
Only a matching PONG clears the handshake-pending flag; malformed or unrelated
nonempty packets no longer suppress handshake timeout. The timeout clock is
sampled again after Read returns. Receive sizes larger than the supplied frame
buffer are rejected before parsing (this cannot undo an OS buffer overwrite).

Fragment validation now requires a pending request, RESPONSE/ERROR opcode,
positive progress, stable total length and consistent accumulated offset,
using subtraction for bounds checks. Added host regression cases for those
conditions and uint32 boundary inputs. `make program-test`, all 17 bridge
tests and scoped diff checks pass. These are real protocol correctness fixes,
not evidence of the historical freeze cause or safe hardware execution.

## Calculator-side compile gate

The shared calculator source was compiled in both preprocessor modes on
September 22, 2026 using ARM GCC with `-Wall -Wextra -Werror -Os`:
`compiled-sdl` and `compiled-ngc`. This checks the real `main.c` transport
and UI branches, including the new fragment validation, rather than only a
standalone helper. It was compile-only; no ELF/TNS was linked or uploaded.

The reproducible Docker build now accepts `NSPIRE_UI_NGC=TRUE|FALSE` and
passes `UI_NGC` into `src/program/Makefile`; `make program-docker-ngc` selects
the experimental backend while `make program-docker` remains the default SDL
artifact. Shell syntax, invalid-value rejection and diff checks pass. The full
bootstrap/build was not run because it mutates pinned dependency worktrees and
overwrites `dist/nspire_ai.tns`; no artifact was uploaded.

After adding an explicit clean and passing `UI_NGC` into the Docker container,
the first `nspire-ld` NGC link failed in Ndless/newlib linking with duplicate
`_malloc_r`, `_free_r`, `_realloc_r`, `_calloc_r` and a missing `_sbrk`; this was
a toolchain/link configuration failure, not a runtime result. The Makefile now
uses the same explicit manual link shape as the offline NGC control when
`UI_NGC=TRUE`, and the full-chat NGC candidate links and packages successfully
under `.build/ngc-make-trial/`. It remains unuploaded pending review and a
safe physical startup run; do not treat the candidate as hardware-validated.

The SDK's `bin/arm-none-eabi-ld.gold` wrapper confirms the expected model: it
injects `-lc` plus `-lndls -lsyscalls` (or the nspireio variant) and uses the
SDK-built toolchain. Debian's system GCC/newlib is not that toolchain. The
known SDL startup-stall SHA-256
`53f5f14965d3c4280f86f82565f22916db7eabbace8f6b7b9a4c410b4d94db2e` is now
blocked in both deployment paths. This is a safety gate, not a claim that the
artifact was ever uploaded by either guarded path.

Deployment gate regression: with the existing N-Link binary, the shell upload
entrypoint rejected the current stale SHA-256 with exit 65 before USB-state
inspection/upload. The Java remote upload path is also guarded by the same
SHA list; its attempted CLI check found no connected node and performed no
upload. These are safety-gate observations, not runtime bridge evidence.

Bridge lifecycle regression checks were rerun after the transport/build edits:
`test-nspire-java-helper-lifecycle.sh` passed with READY/STOPPED and no new
helper/RMI process leaks; `test-navnet-bridge-lifecycle.sh` passed with READY,
STOPPED and no child/RMI leaks (expected outer SIGTERM status 143). These
tests validate host cleanup only, not calculator-side CONNECTED or response.

A read-only alternative-link experiment also failed: selecting only
`stubs.o/osvar.o/realpath.o` still causes `stdlib.o` through the SDK/newlib
dependency graph, while the Debian image lacks the SDK-matched `libstdc++` and
`libm`; a hand-written `_gettimeofday` then duplicates the SDK definition.
This confirms that object-level surgery is not a valid substitute for the
Ndless-built toolchain. It was not committed to the build and produced no
deployable artifact.

No Ndless-specific compiler is installed locally: `toolchain/install/bin` is
absent, and the repository only contains `toolchain/build_toolchain.sh`.
That script builds GCC 14.2/newlib 4.5 from source and is deliberately not run
in this iteration. The original `nspire-ld`/Debian link is rejected rather than
patched with duplicate-symbol overrides; the new NGC Makefile path uses the
explicit `libsyscalls`/`libndls` link shape and produced the separate candidate
recorded above.
Current `dist/nspire_ai.tns` is the NGC candidate SHA recorded above. It was
uploaded once for the controlled physical attempt and is now blocked by both
deployment paths after the startup/readback stall. `make program-test` and all
18 bridge tests still pass; the physical page-open loop remains unverified.

The build/deploy boundary now writes `dist/nspire_ai.tns.meta` only after a
successful Docker build, containing SHA-256, UI backend and
`build_status=success`. Shell upload verifies both the manifest status and
digest before USB-state inspection; Java upload requires the same adjacent
manifest. The current NGC TNS does have a success manifest, but its known
failed-startup SHA is explicitly rejected with exit 65 before any USB
operation. The earlier SDL failed-startup SHA is blocked as well. This
prevents failed/partial or previously stalled packages from being mistaken for
deployable packages.
