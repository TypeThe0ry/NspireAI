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
"$JAVAC_BIN" --release 8 -encoding UTF-8 -cp "$CP" \
  -d "$OUT_DIR" \
  "$ROOT/bridge/nspire-navnet-helper/NspireNavnetHelper.java" \
  "$ROOT/bridge/nspire-navnet-helper/NspireRemoteControl.java"
printf '%s\n' "$OUT_DIR"
