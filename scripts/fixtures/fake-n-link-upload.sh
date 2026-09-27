#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
  upload)
    # Model n-link's misleading zero exit even when the device rejected data.
    exit 0
    ;;
  download)
    DEST="$3/$(basename "$2")"
    case "${FAKE_N_LINK_MODE:-}" in
      match) cp "$FAKE_N_LINK_ARTIFACT" "$DEST" ;;
      mismatch) cp "$0" "$DEST" ;;
      missing) : ;;
      *) exit 2 ;;
    esac
    ;;
  *) exit 2 ;;
esac
