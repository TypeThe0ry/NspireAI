#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$ROOT/scripts/build-nspire-navnet-helper.sh" >/dev/null
TI_APP="${TI_NSPIRE_APP:-/Applications/TI-Nspire CX CAS Student Software.app}"
JAVA_HOME_TI="$TI_APP/Contents/PlugIns/Java.runtime/Contents/Home"
JAR_DIR="$TI_APP/Contents/Java"
CP="$(printf '%s:' "$ROOT/bridge/nspire-navnet-helper/build" "$JAR_DIR"/*.jar)"
BUNDLED_JAVA="$JAVA_HOME_TI/bin/java"
# The TI application bundle ships an Intel-only Java runtime and Intel-only
# libnavnet/connectors. On Apple Silicon, use Rosetta for the complete TI
# stack; mixing the TI x86 native library with an arm64 JDK causes RMI EOF or
# a native crash during RemoteNavnetServer initialization.
USE_ROSETTA=0
if [[ "$(uname -m)" == "arm64" && "$(file -b "$BUNDLED_JAVA")" != *"arm64"* ]]; then
  if arch -x86_64 true >/dev/null 2>&1; then
    USE_ROSETTA=1
  else
    echo "Rosetta 2 is required for TI's Intel NavNet runtime" >&2
    exit 2
  fi
fi
TI_JAVA_BIN="${NSPIRE_HOST_JAVA:-$BUNDLED_JAVA}"
if [[ ! -x "$TI_JAVA_BIN" ]]; then
  echo "No usable Java runtime: $TI_JAVA_BIN" >&2
  exit 2
fi

# libnavnet loads its connector dylibs from a literal ./connectors path.  The
# TI app bundle keeps that directory beside navnet.jar; starting this helper
# from the repository root otherwise produces a healthy-looking READY service
# with no NODE callbacks because no USB connector was loaded.  Keep an
# explicit override for diagnostics, but default to the bundle's Java dir.
if [[ -n "${NSPIRE_NAVNET_CWD:-}" ]]; then
  cd "$NSPIRE_NAVNET_CWD"
else
  cd "$JAR_DIR"
fi

# NavNetCommProxy may spawn RemoteNavnetServer as a detached JVM. Keep a
# before-snapshot so this standalone helper can remove only a server created
# by this invocation; the parent bridge has the same guard for its own child.
NAVNET_SERVER_PATTERN='com\.ti\.eps\.navnet\.server\.RemoteNavnetServer'
NAVNET_SERVER_BEFORE="$(pgrep -f "$NAVNET_SERVER_PATTERN" 2>/dev/null || true)"
NAVNET_SERVER_PID=""

# NavNetCommProxy normally launches RemoteNavnetServer itself. On Apple
# Silicon with TI's Intel-only runtime that launch can race a stale RMI
# registry: the client sees the registry, then retries while no server is
# listening. Start the exact TI server command first when port 1099 is free;
# an already-running TI server remains untouched.
start_navnet_server_if_needed() {
  if lsof -nP -iTCP:1099 -sTCP:LISTEN -t >/dev/null 2>&1; then
    return 0
  fi
  local server_log server_err error_file
  server_log="${TMPDIR:-/tmp}/nspire-navnet-server.$$.log"
  server_err="${TMPDIR:-/tmp}/nspire-navnet-server.$$.err"
  error_file="${TMPDIR:-/tmp}/nspire-navnet-server.$$.crash.log"
  if [[ "$USE_ROSETTA" == 1 && -z "${NSPIRE_HOST_JAVA:-}" ]]; then
    arch -x86_64 "$TI_JAVA_BIN" \
      "-XX:ErrorFile=$error_file" \
      -Djava.rmi.server.hostname=localhost \
      -Djava.rmi.server.useLocalHostname=true \
      -cp "$JAR_DIR/navnet.jar" \
      com.ti.eps.navnet.server.RemoteNavnetServer \
      -c 0 -d 0 -l "$server_log" -r "$JAVA_HOME_TI/bin" \
      >"$server_log.stdout" 2>"$server_err" &
  else
    "$TI_JAVA_BIN" \
      "-XX:ErrorFile=$error_file" \
      -Djava.rmi.server.hostname=localhost \
      -Djava.rmi.server.useLocalHostname=true \
      -cp "$JAR_DIR/navnet.jar" \
      com.ti.eps.navnet.server.RemoteNavnetServer \
      -c 0 -d 0 -l "$server_log" -r "$JAVA_HOME_TI/bin" \
      >"$server_log.stdout" 2>"$server_err" &
  fi
  NAVNET_SERVER_PID=$!
  for _ in {1..80}; do
    if lsof -nP -iTCP:1099 -sTCP:LISTEN -t >/dev/null 2>&1; then
      return 0
    fi
    if ! kill -0 "$NAVNET_SERVER_PID" 2>/dev/null; then
      echo "RemoteNavnetServer exited before opening RMI port; stderr:" >&2
      sed -n '1,120p' "$server_err" >&2 || true
      return 1
    fi
    sleep 0.1
  done
  echo "RemoteNavnetServer did not open RMI port 1099" >&2
  sed -n '1,120p' "$server_err" >&2 || true
  return 1
}
CHILD_PID=""
cleanup_server() {
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
  if [[ -n "$NAVNET_SERVER_PID" ]] && kill -0 "$NAVNET_SERVER_PID" 2>/dev/null; then
    kill -TERM "$NAVNET_SERVER_PID" 2>/dev/null || true
    wait "$NAVNET_SERVER_PID" 2>/dev/null || true
  fi
}
cleanup() {
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
  cleanup_server
}
forward_signal() {
  kill -TERM "$CHILD_PID" 2>/dev/null || true
}
trap cleanup EXIT
trap 'forward_signal' INT TERM

if ! start_navnet_server_if_needed; then
  exit 1
fi

# Bash may give an asynchronous job /dev/null for stdin.  The Java adapter
# uses stdin as its lifetime/control pipe, so explicitly inherit the wrapper's
# descriptor or it will observe EOF immediately after READY and tear down the
# NavNet service before the Python bridge can use it.
if [[ "$USE_ROSETTA" == 1 && -z "${NSPIRE_HOST_JAVA:-}" ]]; then
  arch -x86_64 "$TI_JAVA_BIN" -cp "$CP" NspireNavnetHelper "$@" <&0 &
else
  "$TI_JAVA_BIN" -cp "$CP" NspireNavnetHelper "$@" <&0 &
fi
CHILD_PID=$!
set +e
wait "$CHILD_PID"
result=$?
set -e
CHILD_PID=""
exit "$result"
