#!/usr/bin/env bash
set -euo pipefail

# Exact path retained as a negative test for the relocation-correct NGC build.
# The calculator rejected this package as an unsupported document format, so
# it must never be uploaded again until a new package has a different SHA and
# an independently reviewed loader explanation.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACT="$ROOT/dist/nspire_ai.tns"
META="$ARTIFACT.meta"
EXPECTED_SHA="51ca73922afbc0ba2f0b488a98f4ff703eaa8081c64edac951ba1335d833a301"
REMOTE_DEST="${REMOTE_DEST:-/nspire_ai.tns}"
UPLOAD_TIMEOUT_SECONDS="${NSPIRE_UPLOAD_TIMEOUT_SECONDS:-60}"

echo "Refusing relocation-candidate upload: handheld rejected SHA $EXPECTED_SHA as unsupported document format on 2026-09-24" >&2
exit 65
