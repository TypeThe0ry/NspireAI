#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$(mktemp -t nspire-java-helper.XXXXXX)"
FIFO_PATH="$(mktemp -u -t nspire-java-helper.stdin.XXXXXX)"
mkfifo "$FIFO_PATH"
HELPER_PATTERN='NspireNavnetHelper'
SERVER_PATTERN='com\.ti\.eps\.navnet\.server\.RemoteNavnetServer'
HELPERS_BEFORE="$(pgrep -f "$HELPER_PATTERN" 2>/dev/null || true)"
SERVERS_BEFORE="$(pgrep -f "$SERVER_PATTERN" 2>/dev/null || true)"
HELPER_PID=""

cleanup() {
  if [[ -n "$HELPER_PID" ]] && kill -0 "$HELPER_PID" 2>/dev/null; then
    kill -TERM "$HELPER_PID" 2>/dev/null || true
    wait "$HELPER_PID" 2>/dev/null || true
  fi
  exec 3>&- 2>/dev/null || true
  rm -f "$LOG_FILE"
  rm -f "$FIFO_PATH"
}
trap cleanup EXIT

NSPIRE_SERVICE_ID=0x5001 \
  "$ROOT/scripts/run-nspire-java-helper.sh" <"$FIFO_PATH" >"$LOG_FILE" 2>&1 &
HELPER_PID=$!
# Keep the control pipe open while READY is produced. This catches the
# regression where Bash gives an asynchronous Java child /dev/null and the
# helper observes EOF immediately after registering the service.
exec 3>"$FIFO_PATH"

for _ in {1..100}; do
  if grep -qx 'READY service=0x5001' "$LOG_FILE"; then
    break
  fi
  if ! kill -0 "$HELPER_PID" 2>/dev/null; then
    sed -n '1,160p' "$LOG_FILE" >&2
    echo 'Java helper exited before registering service 0x5001' >&2
    exit 1
  fi
  sleep 0.1
done

if ! grep -qx 'READY service=0x5001' "$LOG_FILE"; then
  sed -n '1,160p' "$LOG_FILE" >&2
  echo 'Java helper did not register service 0x5001' >&2
  exit 1
fi

sleep 1
if ! kill -0 "$HELPER_PID" 2>/dev/null; then
  sed -n '1,160p' "$LOG_FILE" >&2
  echo 'Java helper exited before the control pipe was closed' >&2
  exit 1
fi

printf 'QUIT\n' >&3
exec 3>&-
wait "$HELPER_PID"
HELPER_PID=""

if ! grep -qx 'STOPPED' "$LOG_FILE"; then
  sed -n '1,160p' "$LOG_FILE" >&2
  echo 'Java helper did not finish its shutdown path' >&2
  exit 1
fi

sleep 1
for spec in "$HELPER_PATTERN:$HELPERS_BEFORE" "$SERVER_PATTERN:$SERVERS_BEFORE"; do
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

echo 'PASS: READY service=0x5001, STOPPED, no new helper/RMI processes'
