#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="${1:-${NSPIRE_AI_BACKEND:-echo}}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/bridge/.venv/bin/python}"
CUSTOM_USB_HELPER="${NSPIRE_USB_HELPER:-}"
HELPER_BIN="${CUSTOM_USB_HELPER:-$ROOT/bridge/nspire-helper/target/debug/nspireai-usb-helper}"
JAVA_HELPER="${NSPIRE_NAVNET_JAVA_HELPER:-$ROOT/bridge/nspire-navnet-helper/build}"
LOCK_DIR="${NSPIRE_BRIDGE_LOCK_DIR:-${TMPDIR:-/tmp}/nspireai-navnet-bridge.lock}"

# USB ownership is exclusive. Keep the whole NavNet entrypoint single-instance
# so a second click cannot start another helper while the first one still owns
# the transport (or leave a stale Java/RMI child behind).
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  if [[ -f "$LOCK_DIR/pid" ]]; then
    owner="$(<"$LOCK_DIR/pid")"
    owner_alive=0
    if [[ "$owner" =~ ^[0-9]+$ ]] && kill -0 "$owner" 2>/dev/null; then
      owner_state="$(ps -p "$owner" -o stat= 2>/dev/null | tr -d '[:space:]')"
      # A killed shell can leave a zombie briefly; it cannot own the bridge.
      if [[ -n "$owner_state" && "$owner_state" != Z* ]]; then
        owner_alive=1
      fi
    fi
    if [[ "$owner" =~ ^[0-9]+$ ]] && [[ "$owner_alive" -eq 0 ]]; then
      rm -f "$LOCK_DIR/pid"
      rmdir "$LOCK_DIR" 2>/dev/null || true
    fi
  fi
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "another NavNet bridge already owns the USB session (lock: $LOCK_DIR)" >&2
    exit 75
  fi
fi
printf '%s\n' "$$" >"$LOCK_DIR/pid"
release_lock() {
  if [[ -f "$LOCK_DIR/pid" ]] && [[ "$(<"$LOCK_DIR/pid")" == "$$" ]]; then
    rm -f "$LOCK_DIR/pid"
    rmdir "$LOCK_DIR" 2>/dev/null || true
  fi
}
trap release_lock EXIT

if [[ "$BACKEND" != "echo" && "$BACKEND" != "openai" ]]; then
  echo "usage: $0 [echo|openai]" >&2
  exit 2
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "bridge Python is missing; run ./scripts/setup-bridge-python.sh" >&2
  exit 2
fi
if [[ "$BACKEND" == "openai" && -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is required for the OpenAI backend" >&2
  exit 2
fi

cd "$ROOT"
TRANSPORT="${NSPIRE_NAVNET_TRANSPORT:-java}"
if [[ "$TRANSPORT" != "java" && "$TRANSPORT" != "raw" ]]; then
  echo "NSPIRE_NAVNET_TRANSPORT must be java or raw" >&2
  exit 2
fi
if [[ "$TRANSPORT" == "java" ]]; then
  export NSPIRE_USB_HELPER="$ROOT/scripts/run-nspire-java-helper.sh"
else
  export NSPIRE_USB_HELPER="$HELPER_BIN"
fi
if [[ "$TRANSPORT" == "raw" && -z "$CUSTOM_USB_HELPER" ]]; then
  # The raw helper is a source-built binary, not a stable system install.
  # Always run Cargo's incremental freshness check so a stale target/debug
  # executable cannot silently use an old service ID or frame implementation.
  if ! command -v cargo >/dev/null 2>&1; then
    echo "Cargo is required to refresh the default raw USB helper" >&2
    exit 2
  fi
  RAW_TARGET_DIR="${NSPIRE_RAW_TARGET_DIR:-${TMPDIR:-/tmp}/nspireai-raw-target}"
  # Homebrew's Cargo can inherit an x86_64 `cc` selection from the TI/Rosetta
  # toolchain. Pin the native Apple clang defaults unless the caller supplied
  # an explicit compiler, otherwise a fresh isolated target fails in xcrun.
  # Use absolute native Apple tools and pin Cargo's target linker.  On this
  # host a bare `cc` can be resolved through an x86_64 xcrun shim even though
  # rustc is native arm64; isolated fresh targets then fail before the helper
  # can refresh its service ID.
  CC="${CC:-/usr/bin/clang}" CXX="${CXX:-/usr/bin/clang++}" \
    CARGO_TARGET_AARCH64_APPLE_DARWIN_LINKER="${CARGO_TARGET_AARCH64_APPLE_DARWIN_LINKER:-/usr/bin/clang}" \
    cargo build --manifest-path "$ROOT/bridge/nspire-helper/Cargo.toml" \
      --target-dir "$RAW_TARGET_DIR" --quiet
  HELPER_BIN="$RAW_TARGET_DIR/debug/nspireai-usb-helper"
  export NSPIRE_USB_HELPER="$HELPER_BIN"
fi
if [[ "$TRANSPORT" != "java" && ! -x "$HELPER_BIN" ]]; then
  echo "USB helper is missing; run: cargo build --manifest-path bridge/nspire-helper/Cargo.toml" >&2
  exit 2
fi

# Do not start a host service that can only report READY while the handheld is
# absent. Tests may bypass this physical gate explicitly; normal runs may not.
if [[ "${NSPIRE_SKIP_USB_GATE:-0}" != "1" ]]; then
  # Do not use `tee | grep -q` under pipefail: grep exits as soon as it sees
  # the state line, which makes tee return SIGPIPE and falsely rejects a real
  # TI-Nspire device. Capture once, print once, then inspect the complete text.
  # macOS can publish the IORegistry node a few hundred milliseconds before
  # NavNet/libusb can open it (especially after a calculator app exits).  A
  # one-shot check made a perfectly present E022 look like a missing cable and
  # forced users into needless unplug/replug cycles.  Wait briefly on the host
  # for the same device; this is read-only and never resets USB hardware.
  USB_STATE=""
  USB_STATE_STATUS=1
  USB_WAIT_ATTEMPTS="${NSPIRE_USB_GATE_ATTEMPTS:-20}"
  USB_WAIT_DELAY="${NSPIRE_USB_GATE_DELAY:-0.25}"
  for _ in $(seq 1 "$USB_WAIT_ATTEMPTS"); do
    USB_STATE_STATUS=0
    USB_STATE="$($ROOT/scripts/check-nspire-usb-state.sh 2>&1)" || USB_STATE_STATUS=$?
    if [[ "$USB_STATE_STATUS" -eq 0 ]] && grep -q '^STATE=CX2_USB_CANDIDATE ' <<<"$USB_STATE"; then
      break
    fi
    sleep "$USB_WAIT_DELAY"
  done
  printf '%s\n' "$USB_STATE" >&2
  if [[ "$USB_STATE_STATUS" -ne 0 ]] || ! grep -q '^STATE=CX2_USB_CANDIDATE ' <<<"$USB_STATE"; then
    echo "TI-Nspire CX II handheld is not enumerated; bridge not started" >&2
    exit 69
  fi
fi

# TI's NavNetCommProxy may launch RemoteNavnetServer as a detached JVM.  It
# survives the helper's normal shutdown and can keep RMI/USB state busy.  Take
# a PID snapshot before this run, then remove only servers created afterwards;
# an already-running TI application server is left untouched.
NAVNET_SERVER_PATTERN='com\.ti\.eps\.navnet\.server\.RemoteNavnetServer'
NAVNET_SERVER_BEFORE="$(pgrep -f "$NAVNET_SERVER_PATTERN" 2>/dev/null || true)"
cleanup_navnet_server() {
  [[ "$TRANSPORT" == "java" ]] || return 0
  local pid remaining
  for pid in $(pgrep -f "$NAVNET_SERVER_PATTERN" 2>/dev/null || true); do
    case " $NAVNET_SERVER_BEFORE " in
      *" $pid "*) ;;
      *) kill -TERM "$pid" 2>/dev/null || true ;;
    esac
  done
  for _ in {1..20}; do
    remaining=""
    for pid in $(pgrep -f "$NAVNET_SERVER_PATTERN" 2>/dev/null || true); do
      case " $NAVNET_SERVER_BEFORE " in
        *" $pid "*) ;;
        *) remaining="$remaining $pid" ;;
      esac
    done
    [[ -z "$remaining" ]] && break
    sleep 0.1
  done
  for pid in $remaining; do
    kill -KILL "$pid" 2>/dev/null || true
  done
}
BRIDGE_PID=""
cleanup_all() {
  if [[ -n "$BRIDGE_PID" ]] && kill -0 "$BRIDGE_PID" 2>/dev/null; then
    kill -TERM "$BRIDGE_PID" 2>/dev/null || true
    wait "$BRIDGE_PID" 2>/dev/null || true
  fi
  cleanup_navnet_server
  release_lock
}
on_interrupt() {
  local status="$1"
  cleanup_all
  exit "$status"
}
trap cleanup_all EXIT
trap 'on_interrupt 130' INT
trap 'on_interrupt 143' TERM

"$PYTHON_BIN" -m bridge.navnet_bridge --backend "$BACKEND" &
BRIDGE_PID=$!
set +e
wait "$BRIDGE_PID"
status=$?
set -e
BRIDGE_PID=""
exit "$status"
