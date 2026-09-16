#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="${1:-${NSPIRE_AI_BACKEND:-echo}}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/bridge/.venv/bin/python}"
HELPER_BIN="${NSPIRE_USB_HELPER:-$ROOT/bridge/nspire-helper/target/debug/nspireai-usb-helper}"
JAVA_HELPER="${NSPIRE_NAVNET_JAVA_HELPER:-$ROOT/bridge/nspire-navnet-helper/build}"

if [[ "$BACKEND" != "echo" && "$BACKEND" != "openai" ]]; then
  echo "usage: $0 [echo|openai]" >&2
  exit 2
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "bridge Python is missing; run ./scripts/setup-bridge-python.sh" >&2
  exit 2
fi
if [[ "$BACKEND" == "openai" && -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is required for the OpenAI backend" >&2
  exit 2
fi

cd "$ROOT"
if [[ "${NSPIRE_NAVNET_TRANSPORT:-java}" == "java" ]]; then
  export NSPIRE_USB_HELPER="$ROOT/scripts/run-nspire-java-helper.sh"
else
  export NSPIRE_USB_HELPER="$HELPER_BIN"
fi
if [[ "${NSPIRE_NAVNET_TRANSPORT:-java}" != "java" && ! -x "$HELPER_BIN" ]]; then
  echo "USB helper is missing; run: cargo build --manifest-path bridge/nspire-helper/Cargo.toml" >&2
  exit 2
fi
exec "$PYTHON_BIN" -m bridge.navnet_bridge --backend "$BACKEND"
