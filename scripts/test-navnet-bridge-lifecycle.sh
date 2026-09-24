#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP_DIR="$(mktemp -d -t nspire-navnet-bridge.XXXXXX)"
LOG_FILE="$TMP_DIR/bridge.log"
FIFO_PATH="$TMP_DIR/stdin"
mkfifo "$FIFO_PATH"

BRIDGE_PID=""
HELPER_PATTERN='NspireNavnetHelper'
SERVER_PATTERN='com\.ti\.eps\.navnet\.server\.RemoteNavnetServer'
BRIDGES_BEFORE="$(pgrep -f 'run-navnet-bridge' 2>/dev/null || true)"
HELPERS_BEFORE="$(pgrep -f "$HELPER_PATTERN" 2>/dev/null || true)"
SERVERS_BEFORE="$(pgrep -f "$SERVER_PATTERN" 2>/dev/null || true)"

cleanup() {
  if [[ -n "$BRIDGE_PID" ]] && kill -0 "$BRIDGE_PID" 2>/dev/null; then
    kill -TERM "$BRIDGE_PID" 2>/dev/null || true
    wait "$BRIDGE_PID" 2>/dev/null || true
  fi
  exec 3>&- 2>/dev/null || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

# Keep stdin open so the Java child cannot confuse a test-shell EOF with a
# requested bridge shutdown. The actual stop is sent to the bridge wrapper.
NSPIRE_SKIP_USB_GATE=1 NSPIRE_SERVICE_ID=0x5001 \
  "$ROOT/scripts/run-navnet-bridge.sh" echo <"$FIFO_PATH" >"$LOG_FILE" 2>&1 &
BRIDGE_PID=$!
exec 3>"$FIFO_PATH"

for _ in {1..150}; do
  if grep -q 'helper: READY service=0x5001' "$LOG_FILE"; then
    break
  fi
  if ! kill -0 "$BRIDGE_PID" 2>/dev/null; then
    sed -n '1,220p' "$LOG_FILE" >&2
    echo 'bridge exited before helper READY' >&2
    exit 1
  fi
  sleep 0.1
done

if ! grep -q 'helper: READY service=0x5001' "$LOG_FILE"; then
  sed -n '1,220p' "$LOG_FILE" >&2
  echo 'bridge did not reach helper READY service=0x5001' >&2
  exit 1
fi

sleep 1
if ! kill -0 "$BRIDGE_PID" 2>/dev/null; then
  sed -n '1,220p' "$LOG_FILE" >&2
  echo 'bridge exited while stdin remained open' >&2
  exit 1
fi

kill -TERM "$BRIDGE_PID"
set +e
wait "$BRIDGE_PID"
RESULT=$?
set -e
BRIDGE_PID=""

if ! grep -q 'helper: STOPPED' "$LOG_FILE"; then
  sed -n '1,260p' "$LOG_FILE" >&2
  echo 'bridge cleanup did not emit helper STOPPED' >&2
  exit 1
fi

# A SIGTERM is expected to produce 143 at the outer wrapper, but any nonzero
# status is acceptable only after the explicit STOPPED marker was observed.
for spec in \
  "run-navnet-bridge:$BRIDGES_BEFORE" \
  "$HELPER_PATTERN:$HELPERS_BEFORE" \
  "$SERVER_PATTERN:$SERVERS_BEFORE"; do
  pattern="${spec%%:*}"
  before="${spec#*:}"
  for pid in $(pgrep -f "$pattern" 2>/dev/null || true); do
    case " $before " in
      *" $pid "*) ;;
      *)
        echo "lifecycle leak: pid=$pid pattern=$pattern" >&2
        exit 1
        ;;
    esac
  done
done

echo "PASS: bridge READY, STOPPED, no new child/RMI processes (outer status=$RESULT)"
