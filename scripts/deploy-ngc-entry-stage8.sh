#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXPECTED_SHA="9cdf132883b0001259aaee62725ae5b0232cf8e7c477c3b879f5cf27cfffee5a"
echo "Refusing NGC entry stage-8 SHA=$EXPECTED_SHA: handheld rejected it as unsupported document format on 2026-09-24" >&2
exit 65
