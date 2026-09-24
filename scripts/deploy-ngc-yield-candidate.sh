#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXPECTED_SHA="a88bd700651bac3876a23e4b0e45428535102f1834524169219b04a249b499ef"
echo "Refusing NGC scheduler-yield candidate SHA=$EXPECTED_SHA: handheld rejected it as unsupported document format on 2026-09-24" >&2
exit 65
