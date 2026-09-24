#!/usr/bin/env bash
set -euo pipefail

# Negative-test record for the SDK-wrapper NGC build. The calculator rejected
# this exact SHA as an unsupported document; fail before any USB operation.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACT="$ROOT/dist/nspire_ai.tns"
META="$ARTIFACT.meta"
EXPECTED_SHA="5f3d5213ccc3ff5ef60054981541df03565f69b943ac734f0ea73dafeea62cc9"
REMOTE_DEST="${REMOTE_DEST:-/nspire_ai.tns}"
UPLOAD_TIMEOUT_SECONDS="${NSPIRE_UPLOAD_TIMEOUT_SECONDS:-60}"

echo "Refusing SDK-wrapper candidate upload: handheld rejected SHA $EXPECTED_SHA as unsupported document format on 2026-09-24" >&2
exit 65
