#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CALLER_CWD="$PWD"
REMOTE_ARGS=("$@")

# The NavNet runtime must run from TI's JAR directory so its native connector
# lookup works. Resolve every local path before changing directory, otherwise
# a normal relative `dist/nspire_ai.tns` is incorrectly looked up under the TI
# application bundle.
resolve_local_arg() {
  local value="$1"
  if [[ "$value" == /* ]]; then
    printf '%s' "$value"
  else
    printf '%s/%s' "$CALLER_CWD" "$value"
  fi
}
case "${REMOTE_ARGS[0]:-}" in
  upload|verify-program)
    if [[ "${#REMOTE_ARGS[@]}" -ge 2 ]]; then
      REMOTE_ARGS[1]="$(resolve_local_arg "${REMOTE_ARGS[1]}")"
    fi
    ;;
  download)
    if [[ "${#REMOTE_ARGS[@]}" -ge 3 ]]; then
      REMOTE_ARGS[2]="$(resolve_local_arg "${REMOTE_ARGS[2]}")"
    fi
    ;;
  screen)
    if [[ "${#REMOTE_ARGS[@]}" -ge 2 ]]; then
      REMOTE_ARGS[1]="$(resolve_local_arg "${REMOTE_ARGS[1]}")"
    fi
    ;;
esac
"$ROOT/scripts/build-nspire-navnet-helper.sh" >/dev/null
TI_APP="${TI_NSPIRE_APP:-/Applications/TI-Nspire CX CAS Student Software.app}"
JAVA_HOME_TI="$TI_APP/Contents/PlugIns/Java.runtime/Contents/Home"
JAR_DIR="$TI_APP/Contents/Java"
CP="$(printf '%s:' "$ROOT/bridge/nspire-navnet-helper/build" "$JAR_DIR"/*.jar)"
BUNDLED_JAVA="$JAVA_HOME_TI/bin/java"
if [[ ! -x "$BUNDLED_JAVA" ]]; then
  echo "TI-Nspire Java runtime not found: $BUNDLED_JAVA" >&2
  exit 2
fi

# The native NavNet server resolves its connector directory relative to cwd.
# Run beside navnet.jar so the bundled ./connectors directory is available.
if [[ -n "${NSPIRE_NAVNET_CWD:-}" ]]; then
  cd "$NSPIRE_NAVNET_CWD"
else
  cd "$JAR_DIR"
fi

# NavNetCommProxy can create a detached RMI server. Preserve a server that
# predates this diagnostic and remove only one spawned by this invocation.
PATTERN='com\.ti\.eps\.navnet\.server\.RemoteNavnetServer'
BEFORE="$(pgrep -f "$PATTERN" 2>/dev/null || true)"
TIMEOUT_SECONDS="${NSPIRE_REMOTE_TIMEOUT_SECONDS:-60}"
if [[ ! "$TIMEOUT_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]] || [[ "$TIMEOUT_SECONDS" == 0 || "$TIMEOUT_SECONDS" == 0.* ]]; then
  echo "NSPIRE_REMOTE_TIMEOUT_SECONDS must be a positive number" >&2
  exit 2
fi
CHILD_PID=""
TIMER_PID=""
SERVER_PID=""
cleanup() {
  local pid remaining
  if [[ -n "$TIMER_PID" ]] && kill -0 "$TIMER_PID" 2>/dev/null; then
    kill "$TIMER_PID" 2>/dev/null || true
  fi
  if [[ -n "$CHILD_PID" ]] && kill -0 "$CHILD_PID" 2>/dev/null; then
    kill -TERM "$CHILD_PID" 2>/dev/null || true
    for _ in {1..20}; do
      kill -0 "$CHILD_PID" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "$CHILD_PID" 2>/dev/null; then
      kill -KILL "$CHILD_PID" 2>/dev/null || true
    fi
    wait "$CHILD_PID" 2>/dev/null || true
  fi
  for pid in $(pgrep -f "$PATTERN" 2>/dev/null || true); do
    case " $BEFORE " in
      *" $pid "*) ;;
      *) kill -TERM "$pid" 2>/dev/null || true ;;
    esac
  done
  for _ in {1..20}; do
    remaining=""
    for pid in $(pgrep -f "$PATTERN" 2>/dev/null || true); do
      case " $BEFORE " in
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
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill -TERM "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

USE_ROSETTA=0
if [[ "$(uname -m)" == "arm64" && "$(file -b "$BUNDLED_JAVA")" != *"arm64"* ]]; then
  if arch -x86_64 true >/dev/null 2>&1; then USE_ROSETTA=1; else
    echo "Rosetta 2 is required for TI's Intel NavNet runtime" >&2
    exit 2
  fi
fi
if [[ "$USE_ROSETTA" == 1 ]]; then
  if ! lsof -nP -iTCP:1099 -sTCP:LISTEN -t >/dev/null 2>&1; then
    SERVER_LOG="${TMPDIR:-/tmp}/nspire-navnet-remote-server.$$.log"
    SERVER_ERR="${TMPDIR:-/tmp}/nspire-navnet-remote-server.$$.err"
    SERVER_CRASH="${TMPDIR:-/tmp}/nspire-navnet-remote-server.$$.crash.log"
    arch -x86_64 "$BUNDLED_JAVA" \
      "-XX:ErrorFile=$SERVER_CRASH" \
      -Djava.rmi.server.hostname=localhost \
      -Djava.rmi.server.useLocalHostname=true \
      -cp "$JAR_DIR/navnet.jar" \
      com.ti.eps.navnet.server.RemoteNavnetServer \
      -c 0 -d 0 -l "$SERVER_LOG" -r "$JAVA_HOME_TI/bin" \
      >"$SERVER_LOG.stdout" 2>"$SERVER_ERR" &
    SERVER_PID=$!
    for _ in {1..80}; do
      lsof -nP -iTCP:1099 -sTCP:LISTEN -t >/dev/null 2>&1 && break
      if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "RemoteNavnetServer exited before opening RMI port" >&2
        sed -n '1,120p' "$SERVER_ERR" >&2 || true
        exit 1
      fi
      sleep 0.1
    done
    if ! lsof -nP -iTCP:1099 -sTCP:LISTEN -t >/dev/null 2>&1; then
      echo "RemoteNavnetServer did not open RMI port 1099" >&2
      sed -n '1,120p' "$SERVER_ERR" >&2 || true
      exit 1
    fi
  fi
  arch -x86_64 "$BUNDLED_JAVA" -cp "$CP" NspireRemoteControl "${REMOTE_ARGS[@]}" &
else
  "$BUNDLED_JAVA" -cp "$CP" NspireRemoteControl "${REMOTE_ARGS[@]}" &
fi
CHILD_PID=$!
(
  sleep "$TIMEOUT_SECONDS"
  if [[ -n "$CHILD_PID" ]] && kill -0 "$CHILD_PID" 2>/dev/null; then
    echo "NspireRemoteControl timed out after ${TIMEOUT_SECONDS}s; cleaning up" >&2
    kill -TERM "$CHILD_PID" 2>/dev/null || true
    # TI's native USB call can ignore TERM indefinitely. Bound the parent's
    # wait on that Java child so the EXIT trap can reap the RMI server too.
    sleep 2
    if kill -0 "$CHILD_PID" 2>/dev/null; then
      echo "NspireRemoteControl did not stop after TERM; sending KILL" >&2
      kill -KILL "$CHILD_PID" 2>/dev/null || true
    fi
  fi
) &
TIMER_PID=$!
set +e
wait "$CHILD_PID"
status=$?
set -e
CHILD_PID=""
if [[ -n "$TIMER_PID" ]] && kill -0 "$TIMER_PID" 2>/dev/null; then
  kill "$TIMER_PID" 2>/dev/null || true
fi
TIMER_PID=""
exit "$status"
