#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXPECTED_SHA="86b883f680a41f3c034167227ae3b0645d26652a8e8a299b2857b0d17a2f1ea1"
echo "Refusing NGC size-reduced candidate SHA=$EXPECTED_SHA: handheld rejected it as unsupported document format on 2026-09-24" >&2
exit 65
