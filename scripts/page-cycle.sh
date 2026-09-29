#!/usr/bin/env bash
# Unattended redeploy cycle for the chat page:
#   close the page (remote Esc) -> stop the bridge -> upload and verify ->
#   start the bridge -> reopen the page (remote Enter) -> wait for HELLO.
# Usage: page-cycle.sh [echo|deepseek|openai] [LOGFILE]
# Assumes nspire_ai is the selected entry in the calculator's file browser,
# which is where the browser returns to after the page closes.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BACKEND="${1:-echo}"
LOG="${2:-${TMPDIR:-/tmp}/nspireai-bridge.log}"
CTL="python3 scripts/pagectl.py"
export NSPIRE_REMOTE_TIMEOUT_SECONDS=20

if $CTL status >/dev/null 2>&1; then
  $CTL type '\e' >/dev/null 2>&1 || true   # the page closes on Esc
  sleep 4
fi
pkill -f navnet_bridge.py 2>/dev/null
pkill -f NspireNavnetHelper 2>/dev/null
sleep 4
python3 scripts/run-with-timeout.py 90 ./scripts/deploy-page.sh page | tail -1 || {
  echo "deploy failed: is the page still open on the calculator?" >&2
  exit 1
}
sleep 8   # let the TI runtime settle before the next helper
(./scripts/run-navnet-bridge.sh "$BACKEND" 2>&1 |
  while IFS= read -r line; do printf '%s %s\n' "$(date +%T)" "$line"; done >"$LOG") &
for _ in $(seq 1 24); do
  sleep 5
  grep -q 'CONNECTED' "$LOG" 2>/dev/null && break
done
grep -q 'CONNECTED' "$LOG" || { echo "bridge did not connect; see $LOG" >&2; exit 1; }
./scripts/run-nspire-remote.sh key '~esc~' 2>&1 | grep -E 'timed' || true   # "Document Sent"
./scripts/run-nspire-remote.sh key '~enter~' 2>&1 | grep -E 'timed' || true
for _ in $(seq 1 20); do
  sleep 1
  if $CTL status 2>/dev/null | grep -q '"page": true'; then
    echo "PAGE_OPEN log=$LOG"
    exit 0
  fi
done
echo "page did not say HELLO; see $LOG" >&2
exit 1
