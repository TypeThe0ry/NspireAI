#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TI_APP="${TI_NSPIRE_APP:-/Applications/TI-Nspire CX CAS Student Software.app}"
JAVA_HOME_TI="$TI_APP/Contents/PlugIns/Java.runtime/Contents/Home"
JAR_DIR="$TI_APP/Contents/Java"
OUT_DIR="$ROOT/bridge/nspire-navnet-helper/build"

JAVAC_BIN="${JAVAC_BIN:-$(command -v javac || true)}"
if [[ -z "$JAVAC_BIN" || ! -x "$JAVA_HOME_TI/bin/java" || ! -d "$JAR_DIR" ]]; then
  echo "TI-Nspire Java runtime not found: $TI_APP" >&2
  exit 2
fi

mkdir -p "$OUT_DIR"
CP="$(printf '%s:' "$JAR_DIR"/*.jar)"
SOURCES=(
  "$ROOT/bridge/nspire-navnet-helper/NspireNavnetHelper.java"
  "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"
)
OUTPUTS=(
  "$OUT_DIR/NspireNavnetHelper.class"
  "$OUT_DIR/NspireRemoteControl.class"
)

# javac against TI's bundled Java 22-era jars is slow on this host (and emits
# a warning for every class-file version).  Do not pay that cost on every
# bridge start when neither source nor bundled dependency has changed.
needs_build=0
for output in "${OUTPUTS[@]}"; do
  if [[ ! -f "$output" ]]; then
    needs_build=1
    break
  fi
done
if [[ "$needs_build" -eq 0 ]]; then
  for input in "${SOURCES[@]}" "$JAR_DIR/navnet.jar" "$JAR_DIR/navnetcommproxy.jar" "$JAR_DIR/commproxy.jar"; do
    for output in "${OUTPUTS[@]}"; do
      if [[ "$input" -nt "$output" ]]; then
        needs_build=1
        break 2
      fi
    done
  done
fi
if [[ "$needs_build" -eq 0 ]]; then
  printf '%s\n' "$OUT_DIR"
  exit 0
fi

"$JAVAC_BIN" --release 8 -encoding UTF-8 -cp "$CP" \
  -d "$OUT_DIR" \
  "$ROOT/bridge/nspire-navnet-helper/NspireNavnetHelper.java" \
  "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"
printf '%s\n' "$OUT_DIR"
