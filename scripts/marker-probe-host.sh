#!/usr/bin/env bash
# Host driver for src/probes/marker.  Usage:
#   marker-probe-host.sh screen OUT.png        capture the calculator screen
#   marker-probe-host.sh keys KEY...           send remote keys (e.g. '~down~')
#   marker-probe-host.sh launch [SECONDS]      Enter on the selected file, then
#                                              poll raw USB + marker files
# The first remote call after a bridge start often times out, so every session
# begins with a throwaway screen capture.  The Java bridge is stopped before
# polling so only the raw helper touches USB.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
H=bridge/nspire-helper/target/debug/nspireai-usb-helper
R=./scripts/run-nspire-remote.sh
TMP="$(mktemp -d)"
export NSPIRE_REMOTE_TIMEOUT_SECONDS="${NSPIRE_REMOTE_TIMEOUT_SECONDS:-12}"
MARKERS=(marker_a marker_l marker_g marker_b marker_c)

with_bridge() {
  ./scripts/run-navnet-bridge.sh echo >"$TMP/bridge.log" 2>&1 &
  local bp=$!
  sleep 8
  $R screen "$TMP/warm.png" >/dev/null 2>&1
  "$@"
  kill -TERM "$bp" 2>/dev/null
  sleep 2
}

remote() { $R "$@" 2>&1 | grep -E '^(KEY|SCREEN)|timed out' ; }

cmd="${1:-}"; shift || true
case "$cmd" in
  screen) with_bridge remote screen "$1" ;;
  keys) do_keys() { for k in "$@"; do remote key "$k"; done; }; with_bridge do_keys "$@" ;;
  launch)
    secs="${1:-60}"
    for m in "${MARKERS[@]}"; do $H --delete-file "/$m.tns" >/dev/null 2>&1; done
    echo "launch $(date +%T)"
    with_bridge remote key '~enter~'
    end=$(( $(date +%s) + secs ))
    while [ "$(date +%s)" -lt "$end" ]; do
      info=$(python3 scripts/run-with-timeout.py 10 $H --info 2>&1 | grep -oE 'ready=true|Busy|NoDevice|Invalid|timed out' | head -1)
      seen=""
      for m in "${MARKERS[@]}"; do
        [ -f "$TMP/$m" ] && { seen="$seen $m"; continue; }
        out=$(python3 scripts/run-with-timeout.py 10 $H --download "/$m.tns" "$TMP/$m" 2>&1)
        if [ -f "$TMP/$m" ]; then seen="$seen $m($(tr -d '\n' <"$TMP/$m"))"
        else err=$(echo "$out" | grep -oE 'Invalid|Busy|NoDevice|timed out' | head -1); [ "$err" != Invalid ] && seen="$seen $m?${err:-err}"; fi
      done
      echo "$(date +%T) usb=${info:-none} markers:$seen"
      sleep 2
    done ;;
  *) echo "usage: $0 screen OUT | keys KEY... | launch [SECONDS]" >&2; exit 2 ;;
esac
rm -rf "$TMP"
