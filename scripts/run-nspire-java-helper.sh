#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$ROOT/scripts/build-nspire-navnet-helper.sh" >/dev/null
TI_APP="${TI_NSPIRE_APP:-/Applications/TI-Nspire CX CAS Student Software.app}"
JAVA_HOME_TI="$TI_APP/Contents/PlugIns/Java.runtime/Contents/Home"
JAR_DIR="$TI_APP/Contents/Java"
CP="$(printf '%s:' "$ROOT/bridge/nspire-navnet-helper/build" "$JAR_DIR"/*.jar)"
exec "$JAVA_HOME_TI/bin/java" -cp "$CP" NspireNavnetHelper "$@"
