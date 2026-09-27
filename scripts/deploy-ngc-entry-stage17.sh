#!/usr/bin/env bash
set -euo pipefail

EXPECTED_SHA="8fd7dacfaa9551e254e0595d21dfe23797f684c1cbb9894b72543a14388a9b94"
echo "Refusing NGC entry stage-17 upload: CX II Data Abort at raw TCT_Schedule call" >&2
exit 65
