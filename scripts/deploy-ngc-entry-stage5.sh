#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACT="$ROOT/.build/ngc-entry-stage5/nspire_ai.tns"
META="$ARTIFACT.meta"
EXPECTED_SHA="e46e13c8ba6a00efea305321378a7014f596792d2b1c5cd1fa20ba5fd3b375f6"
echo "Refusing NGC entry stage-5 upload: this probe returned immediately on the CX II and did not open a visible page" >&2
exit 65
