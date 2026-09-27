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

## Production-cadence post-frame probe (stage 13)

To separate stage 12's high-frequency diagnostic clock reads from the real
NGC scheduler cadence, stage 13 used the production-shaped loop: one LCD/GC
frame, matrix-key sampling every 512 spins, RTC sampling every 128 spins, no
NavNet calls, and a 30-second lifetime. The reviewed artifact was
`7afc998f9014236dbf45dd1cb33b74b5437329460c066516a66e25114163ba12` (26,364
bytes). It uploaded and read back byte-for-byte on the CX II (`0xE022`).

The calculator stayed enumerable, but the launch/key call timed out and a
later read-only screen call also timed out while the bridge had no
`CONNECTED`, `RX`, or response. This is the same post-launch USB/screen stall
class as stage 12, now reproduced with the production cadence. The exact SHA
is permanently blocked by the stage wrapper, normal shell deploy path, and
Java remote-upload path. It is not a usable AI package and does not advance
the same-page bridge gate.

## Stage 14 no-key residency probe (pending launch)

Stage 14 keeps the same LCD/GC frame and 128-spin RTC cadence as stage 13 but
removes `ngc_keys()` entirely. The reviewed artifact is
`31d1d46554d72cf0958a55a84d9e93858cd849cab437f804b5062e640e07edd3` (26,160
bytes). It has uploaded and read back byte-for-byte on the CX II (`0xE022`).

At the current checkpoint the handheld is still showing TI's `Document Sent`
dialog with `nspire_ai` selected underneath. The bridge is running and has
`READY service=0x5001` plus `NODE 1`, but the probe has not been launched, so
there is no stage-14 runtime or `CONNECTED` claim yet. The user must dismiss
the dialog and open the selected file locally; the remote virtual-key path is
not reliable on this TI session.

## Stage 14 launch checkpoint update (2026-09-27)

The next read-only screen capture after the upload showed the handheld's file
browser, with `/nspire_ai` (26 KB) selected; it did not show the AI page or a
Home screen. A fresh Java bridge reached `READY service=0x5001` and `NODE 1`,
and the screen API returned that same file-browser image. This remains host
enumeration evidence only. One bounded `key ~enter~` attempt reached the same
node but blocked inside TI's `sendEventToNode` until the wrapper killed the
child at 25 seconds; it printed no `KEY` confirmation. A subsequent read-only
probe could not reacquire a node within 15 seconds, while the USB descriptor
still remained `0x0451:0xE022`. This is evidence that the remote key path is
not a safe substitute for a local calculator keypress; it is not evidence that
stage 14 launched or that the calculator crashed. The exact stage-14 SHA stays
available only for the already-authorized controlled test and is not promoted
to production.

The same key path was then tested after TI Student Software itself was closed,
leaving only the independent NavNet server. The helper still enumerated
`NODE 1`, but `sendEventToNode(~enter~)` timed out inside the TI proxy after
the configured 8-second key bound, and a subsequent read-only probe could not
reacquire a node within 15 seconds while USB remained `0xE022`. This A/B
result rules out the Student Software window as the sole cause of the remote
key hang. `NspireRemoteControl` now hard-stops on that timeout so its outer
shell cleanup can reap the detached RMI server without entering a second
unbounded `proxy.shutdown()` call. This protects the host session; it does not
claim that a virtual key was delivered to the calculator.

## Fresh manual-key audit (2026-09-27 11:44--11:46)

The user confirmed that the calculator key was pressed manually; no second
manual or virtual key was sent by the host during this audit. A bounded,
read-only screen probe then received a NavNet callback but timed out before a
screen image was produced. The expected image file was not created. The host
side TI window listed `TI-Nspire CX II CAS A757` under Connected Handhelds but
still displayed `No handheld selected... please connect a handheld`.

A fresh Java bridge subsequently reached `NODE 1` and
`READY service=0x5001`, but produced no `CONNECTED`, `RX`, or `TX` event during
the observation window. The host USB diagnostic still classified the device
as `CX2_USB_CANDIDATE product=0xE022`; `system_profiler` did not expose a
stable USB record, and no new TI `NN-crash-*` report was created at the audit
time (the newest report was from 08:22). Therefore this checkpoint proves only
host-side enumeration and bridge readiness. It does not prove that the
calculator is still on the nspire_ai page, that the manual key reached the
program, or that the same-page request/response loop works.

The helper was hardened at this checkpoint as well: a `NODE 0` callback now
clears the volatile service handle and emits `DISCONNECTED reason=node-removed`
before the reader can reuse the stale handle. The reader exits on that clear,
and a later `NODE 1` installs a new service connection. The rebuilt helper
passed the lifecycle test and the 19 host bridge/protocol tests; this is a
host-side recovery improvement, not physical proof of calculator `CONNECTED`.

The current workspace artifact is independently gated: `dist/nspire_ai.tns`
has SHA-256 `fc7f3e32dec85b5e860cbd03e080e52d149dbabba63b552137ccf897bd063471`
and its adjacent manifest declares `ngc_auto_transport=FALSE`. Both upload
entry points reject this known stale/blocked SHA, so it was not uploaded during
this audit. Until a newly built, non-blocked package is available and launched
on the handheld, `READY` on the Mac cannot be expected to produce a calculator
`CONNECTED` event by itself.

## Launch-versus-crash audit (2026-09-27)

There is one confirmed macOS-side NavNet crash in the available logs:
`NN-crash-20260927-082215.log` records `SIGILL` from
`_pthread_mutex_corruption_abort`, with the native stack ending in
`navnet-connectors-mac_sc-embedded.dylib!USB_Process_Transactions` and
`TI_NS_event_timedwait`. This is the shared TI Java/NavNet server crash that
occurred at 08:41:43; it is not evidence that the calculator program itself
executed a faulting instruction.

The later launch attempts at approximately 10:51, 10:53, and 11:40 have no
new `NN-crash-*` file. Their read-only screen artifacts
(`var/latest-1790477462.png`, `var/goal-check-1790477612.png`, and
`var/reopen-ti-1790480436.png`) all show the calculator's file browser with
`nspire_ai` selected at about 26 KB, not the NspireAI page. The corresponding
logs show host-side `NODE`/screen acquisition only and no calculator
`CONNECTED`, `RX`, request, or response. Therefore the recent “闪一下后回到
文件列表/卡住” behavior is best classified as a launch/loader or
calculator-side program-exit failure, not a new macOS Java crash. The host
logs cannot distinguish an immediate calculator-side flash-out from a loader
rejection without a successful page capture or calculator-local crash log.

Package-level inspection matches that runtime boundary: `strings` on the
workspace `fc7f3e32...` RPF contains the Zehn notice
`NGC/RTC UI; USB idle until Menu; timer-neutral` and the visible banner
`NspireAI NGC - EXPERIMENTAL`. This identifies the 26 KB file seen in the
handheld browser as the experimental USB-idle NGC package, not a production
package that should auto-register the Mac service. Its selection in the file
browser is therefore not evidence that the requested AI page ever remained
running.

## 2026-09-27 bounded screen probe while bridge stayed live

After TI Student Software was closed, a fresh Java bridge reached `READY
service=0x5001` and `NODE 1`; the helper process remained alive. A separate
read-only `screen` probe was then bounded to 12 seconds. It received the node
callback but timed out before producing an image (`NspireRemoteControl timed
out after 12s`), while the original bridge continued running with no
`CONNECTED`, `RX`, or `TX` event. Stopping the original bridge produced the
normal `unregisterNotifyCallback` and `helper: STOPPED` lines, and no new
`NN-crash-*` file or Java/RMI child remained. This is a repeatable USB/screen
stall boundary after the calculator-side open attempt, not a bridge process
crash and not proof of a running AI page.

## 2026-09-27 stage-14 manual-open checkpoint

Before the manual launch, a bounded Java readback verified that the handheld's
`/nspire_ai.tns` was exactly the reviewed stage-14 artifact: 26,160 bytes with
SHA-256 `31d1d46554d72cf0958a55a84d9e93858cd849cab437f804b5062e640e07edd3`.
With the bridge already at `READY service=0x5001` and `NODE 1`, the user pressed
the calculator's local Enter once while `nspire_ai` was highlighted. The bridge
emitted no `CONNECTED`, `RX`, or `TX` event. A separate read-only screen probe
received the same node callback but timed out after 12 seconds, while the
original bridge and helper remained alive. There was no new `NN-crash-*` file.
The bridge was then stopped normally (`unregisterNotifyCallback`,
`helper: STOPPED`) with no Java/RMI child left behind. This is the strongest
current evidence that the stage-14 program-open path stalls the handheld USB
screen channel after the local Enter; it is not evidence of a successful page
or of a Mac bridge crash.

## Stage-15 scheduler discriminator (2026-09-27)

The reviewed stage-14 probe reproduced a post-Enter screen/USB stall while
using a NOP-only tail and no NavNet calls. To separate loop starvation from
LCD/GC entry, an offline stage-15 probe was built with the same LCD/GC frame,
RTC deadline, and 30-second residency, but with no NavNet or matrix-key work;
it calls Ndless `idle()` once per loop. The artifact is
`fe4d9a26dd1bd62cb385da5f9f62df0f82b6f239c2b3f0f2d17e0043fdbe82ec` and is
isolated under `.build/ngc-stage15/`. It passed the Docker build, relocation,
startup-order, safe-loop, and manifest checks. With explicit authorization it
was uploaded to `/nspire_ai.tns` (26,280 bytes) and a later readback verified
the same SHA-256 byte-for-byte. The bridge then reached `READY service=0x5001`
and `NODE 1`; the user pressed local Enter once while the file was highlighted.
There was no `CONNECTED`, `RX`, or `TX`, as expected because this diagnostic
does not call NavNet. A read-only screen probe performed more than 30 seconds
after the reported Enter showed the file list again. This does not distinguish
"the probe ran and exited" from "the probe never opened"; stage-15 therefore
does not prove that `idle()` resolved the stage-14 scheduler stall.
No new TI crash log appeared, and bridge shutdown emitted unregister and
`helper: STOPPED` cleanly. `idle()` masks all IRQs except the timer interrupt
and its CX II NavNet interaction remains unestablished; this is a scheduler
discriminator, not a claimed USB fix or protocol result.

The follow-up source changes in commits `cdfc74b` and `9b58f34` applied the
experimental yield only while the production NGC page has no active channel:
the page yields with `idle()` when disarmed or held after a transport failure, but
retains the NOP tail for an active NavNet channel. Menu no longer silently
turns an active channel into a stale hidden handle. They pass the source-level
safe-loop truth-table checks and host tests, but have not been built into a new
TNS or uploaded; no physical result is claimed for this follow-up. The
underlying synchronous `NodeEnumInit`/`Connect`/`Read`/`Write` calls still have
no verified wall-clock bound on CX II and remain an unresolved runtime risk.

The guarded source was then rebuilt through the Docker Ndless path into a
production candidate at `dist/nspire_ai.tns`: 26,552 bytes,
SHA-256 `2a20676747b78f87fab2f2d11d1e56ad70d6390097c3c9319affc60bcf4aefa8`,
with a successful manifest and all auto-transport/IRQ flags disabled. It was
uploaded to `/nspire_ai.tns` with explicit authorization and read back at the
same size and SHA-256. The host bridge reached `READY service=0x5001` and
`NODE 1`, but never logged `CONNECTED`, `RX`, or `TX`. A bounded read-only
screen probe timed out after 12 seconds; this is not proof of calculator
crash or page residency. The user subsequently reported that the calculator
was frozen, without yet confirming whether the freeze began on opening the
program or after Menu. No newer `NN-crash-*` file appeared, and the USB
descriptor remained `0xE022`. The host bridge was stopped cleanly with
unregister and `helper: STOPPED`, leaving no helper/RMI process. The exact
candidate SHA is quarantined in both upload paths pending root-cause work;
the requested live protocol loop remains unverified.

## 2026-09-27 production `idle()` dead-wait diagnosis

The latest freeze exposes a concrete standalone-runtime hazard. Ndless's
`ploaderhook.c` calls `TCT_Local_Control_Interrupts(-1)` before entering a Zehn
executable. The candidate's production NGC loop then called the SDK `idle()`
whenever transport was disarmed. Ndless's `idle()` executes ARM WFI and waits
for an interrupt; with CPU IRQ delivery still masked by the loader, that path
can dead-wait before the user can press Menu or USB work can progress. The
stage-15 `idle()` call is diagnostic-only and is not a valid production
scheduler.

The production source now removes `idle()`/WFI from the live loop and retains
only a bounded NOP slice. This prevents the newly identified unconditional
dead-wait, but it is not evidence that a standalone program can share the CX II
screen/USB scheduler: the earlier no-WFI probes still stalled at the
post-launch boundary. The frozen candidate SHA
`2a20676747b78f87fab2f2d11d1e56ad70d6390097c3c9319affc60bcf4aefa8` remains
blocked and must not be uploaded again. No new TNS is authorized until the
standalone loader/USB scheduling boundary has a separately reviewed solution.
## 2026-09-27 loader IRQ boundary reconfirmed

The current crash report was followed by a host-side recheck showing only
`0xACE1` (TS4 dock controller), with no bridge/helper process alive.  The
Ndless source itself confirms why the standalone route can lose the handheld
USB interface: `ploaderhook.c` calls `TCT_Local_Control_Interrupts(-1)` before
`entry(argc, argv)` and restores the saved mask only after the program
returns.  The production NGC loop has no verified cooperative USB/OS yield;
`idle()`/WFI, `msleep()`, `get_event()`, and the scoped CPU-IRQ experiments
were separately rejected after freezes or crashes.  A synchronous
`TI_NN_Read` also has no proven wall-clock bound, so the page can remain
inside that syscall while the host endpoint disappears.

This is stronger evidence for a standalone loader/scheduler incompatibility
than for a NavNet frame-order bug.  The calculator-first host handshake fix is
still valid source work, but it cannot be physically tested until a standalone
entry path that preserves CX II USB scheduling is established.  Do not upload
another unchanged NGC/RTC package or treat `E022` recovery/`READY` as the
requested page-open protocol loop.

## 2026-09-27 stage-5 local/physical mismatch

The rebuilt stage-5 diagnostic (`e46e13c8ba6a00efea305321378a7014f596792d2b1c5cd1fa20ba5fd3b375f6`)
passed local container, relocation, startup-order, safe-loop, and manifest
checks. It was uploaded once after the handheld enumerated as `0xE022`, but
the operator reported that opening it immediately closed and never produced a
visible page. This is consistent with the intentionally transient stage-5
branch, which returns immediately after LCD/GC/frame/RTC work; it is not
evidence of a working Ndless page. The exact SHA is now blocked in the shell
and Java upload paths. Further physical tests require a new resident design,
not another upload of this diagnostic.

## 2026-09-27 offline cooperative-scheduler candidate

The next offline-only candidate is stage-17, which calls the CX II CAS
6.2.0.333 Nucleus symbol `TCT_Schedule` at `0x10623ABC` while avoiding NavNet,
RTC, and matrix-key syscalls. This is distinct from the rejected CPU-IRQ and
`idle()`/WFI experiments: it does not change the CPU IRQ mask. The candidate
build passed local relocation and source gates at SHA
`8fd7dacfaa9551e254e0595d21dfe23797f684c1cbb9894b72543a14388a9b94`. The
controlled `~enter~` launch produced a TI screen-probe crash record:
`6.2.0.333 CX II CAS Data Abort`, instruction address `0x10623AC0`, data
address `0x00000096`, subsystem `USB`. USB later recovered to `0xE022` and
the host helper cleaned up. This permanently rejects the raw scheduler call;
the stage-17 SHA is blocked in every upload path. A local build cannot prove
that an OS-internal address is safe from an Ndless-owned screen task.

## 2026-09-27 production NGC memory-budget candidate

The first clean non-probe package used the SDK wrapper without section
garbage-collection and was rejected by TI with the generic unsupported-document
dialog. Its Zehn `alloc_size` was `61888` bytes. The production NGC build now
uses `-ffunction-sections -fdata-sections` and `--gc-sections`, and excludes the
unused local-service teardown from the default binary. The resulting package
`3b6c2b2cd8d5cb9a34276ce67b05f5117ff64d6e00b8342c912de850a40a2a7a` has
`alloc_size=51980` and passes the new `60000`-byte local budget gate.

It was uploaded after the clean local build and host tests. The handheld stayed
at `Document Received`; the remote `~enter~` event timed out, while a subsequent
read-only screen still showed that dialog. USB recovered to `0xE022` and helper
cleanup completed. This narrows the remaining failure to the standalone
entry/USB scheduling boundary, but does not establish a resident page or any
`CONNECTED`/request/RX evidence.

## 2026-09-27 touchpad I2C key-scan guard

The default NGC loop previously called `any_key_pressed()` for its fast path.
On the touchpad CX II, that helper first performs `touchpad_scan()` over I2C;
the operation is a poor fit for the IRQ-masked standalone Ndless entry path and
can prevent the first Enter arm from reaching the NavNet poll. The production
loop now bypasses that I2C call on `is_touchpad` hardware and scans only the
direct key matrix. The local candidate
`c05c7c5640ac8f685b8ae8e5ec77fe77497fbbb44672d713397fa63da47b984d` passed all
offline gates, but was not uploaded because the previous page still held the
USB endpoint; no physical claim is made.

The replacement Home-guard package
`6430301d3edf5f214854c3f8be6b7c182ec520e832a11d095e7ac9d0e673ffc8` was later
uploaded once after `E022` returned. The screen API showed the `Document
Received` dialog, but the single controlled `~enter~` event timed out after
8 seconds and a follow-up screen read timed out after 15 seconds. The bridge
stayed at `READY`/`NODE 1` with no calculator-originated `CONNECTED`. This SHA
is permanently blocked in both upload paths; the result is physical
entry/scheduling failure evidence, not NavNet request/response success.

Post-failure recovery was also bounded: a screen read timed out after 8
seconds, and one `~home~` event timed out inside TI's key API and required the
wrapper's hard child cleanup. The handheld remained enumerated as `E022`, but
the page did not respond; no additional bridge or key traffic was attempted.

## 2026-09-27 Home teardown guard

The production NGC key loop now checks the direct matrix mapping for
`KEY_NSPIRE_HOME` and exits through the normal NavNet/local-service teardown
path, just like Esc. This is intended to make a stuck foreground page
recoverable without touching the known-crashing Menu path or experimenting
with IRQ/scheduler calls. It is a source-level recovery guard, not evidence
that standalone Ndless scheduling is fixed.

The resulting local-only package is
`6430301d3edf5f214854c3f8be6b7c182ec520e832a11d095e7ac9d0e673ffc8` with
`alloc_size=52032`. All offline tests pass, but the host still enumerates only
the TS4 `ACE1` controller and the package has not been uploaded; the physical
`E022` and NavNet `CONNECTED -> request/RX -> same-page response` gates remain
open.
