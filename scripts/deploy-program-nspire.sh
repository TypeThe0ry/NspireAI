#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
N_LINK_BIN="${N_LINK_BIN:-$ROOT/.deps/n-link/desktop/src-tauri/target/release/n-link}"
REMOTE_DEST="${REMOTE_DEST:-/}"
ARTIFACT="$ROOT/dist/nspire_ai.tns"
META="$ROOT/dist/nspire_ai.tns.meta"
UPLOAD_TIMEOUT_SECONDS="${NSPIRE_UPLOAD_TIMEOUT_SECONDS:-45}"

if [[ ! -x "$N_LINK_BIN" ]]; then
  echo "N-Link CLI not found: $N_LINK_BIN (run scripts/build-n-link.sh)" >&2
  exit 2
fi
if [[ ! -f "$ARTIFACT" ]]; then
  echo "Missing $ARTIFACT; run make program-docker first" >&2
  exit 2
fi
if [[ ! -f "$META" ]] || ! grep -q '^build_status=success$' "$META"; then
  echo "Missing successful-build manifest: $META; refusing stale artifact" >&2
  exit 65
fi
UI_BACKEND="$(sed -n 's/^ui_backend=//p' "$META")"
case "$UI_BACKEND" in
  FALSE|TRUE) ;;
  *) echo "Manifest is missing a valid ui_backend field; refusing upload" >&2; exit 65;;
esac
AUTO_TRANSPORT="$(sed -n 's/^ngc_auto_transport=//p' "$META")"
case "$AUTO_TRANSPORT" in
  FALSE) ;;
  TRUE)
    echo "Refusing auto-transport artifact: startup NavNet enumeration can wedge CX II USB; rebuild USB-idle/Menu-retry package" >&2
    exit 65
    ;;
  *)
    echo "Manifest is missing a valid ngc_auto_transport field; refusing upload" >&2
    exit 65
    ;;
esac
IRQ_WINDOW="$(sed -n 's/^ngc_irq_window=//p' "$META")"
case "$IRQ_WINDOW" in
  FALSE) ;;
  TRUE)
    if [[ "${NSPIRE_ALLOW_NGC_IRQ_WINDOW_UPLOAD:-}" != "1" ]]; then
      echo "Refusing opt-in IRQ-window candidate without explicit confirmation" >&2
      exit 65
    fi
    ;;
  *)
    echo "Manifest is missing a valid ngc_irq_window field; refusing upload" >&2
    exit 65
    ;;
esac
CPU_IRQ="$(sed -n 's/^ngc_cpu_irq=//p' "$META")"
case "$CPU_IRQ" in
  FALSE) ;;
  TRUE)
    echo "Refusing CPU-IRQ candidate: CX II flashed once and froze after launch on 2026-09-25; no override is permitted" >&2
    exit 65
    ;;
  *)
    echo "Manifest is missing a valid ngc_cpu_irq field; refusing upload" >&2
    exit 65
    ;;
esac
IRQ_MENU="$(sed -n 's/^ngc_irq_menu=//p' "$META")"
case "$IRQ_MENU" in
  FALSE) ;;
  TRUE)
    # This path was physically tested on the CX II and holding Menu froze
    # the handheld. Do not allow an environment override to re-enable it.
    echo "Refusing physically-crashing Menu-gated IRQ candidate; rebuild with ngc_irq_menu=FALSE" >&2
    exit 65
    ;;
  *)
    echo "Manifest is missing a valid ngc_irq_menu field; refusing upload" >&2
    exit 65
    ;;
esac
LOCAL_SERVICE="$(sed -n 's/^ngc_local_service=//p' "$META")"
case "$LOCAL_SERVICE" in
  FALSE) ;;
  TRUE)
    echo "Refusing local-service bootstrap candidate; this path stalled the CX II" >&2
    exit 65
    ;;
  *)
    echo "Manifest is missing a valid ngc_local_service field; refusing upload" >&2
    exit 65
    ;;
esac
ARTIFACT_DIGEST="$(shasum -a 256 "$ARTIFACT" | awk '{print $1}')"
if ! grep -qx "sha256=$ARTIFACT_DIGEST" "$META"; then
  echo "Artifact/manifest SHA mismatch; refusing upload" >&2
  exit 65
fi

# Reject candidates that failed a physical runtime test, even if their build
# manifest and readback are valid. A rebuild of unchanged source is not a fix.
ARTIFACT_SHA="$(shasum -a 256 "$ARTIFACT")"
case "${ARTIFACT_SHA%% *}" in
  ce92ae85e1d60cf9c3d4fea08ff1e897d35e13718cafd0ce23080fddd9e13c6c)
    echo "Refusing known-crashing IRQ-scope 0922 build; rebuild corrected source first" >&2; exit 65;;
  53f5f14965d3c4280f86f82565f22916db7eabbace8f6b7b9a4c410b4d94db2e)
    echo "Refusing SDL candidate that stalled the handheld on 2026-09-23" >&2; exit 65;;
  c8c564c2910a2f907fc792b47329a591cbc93dcbfc9e8f61327e73d2ac75aadf)
    echo "Refusing physically-crashing Menu-gated IRQ candidate tested on 2026-09-24" >&2; exit 65;;
  bc2c2099934f622cf0b3c137f7bb46416f04c1c45f9935f2351f03763bdc495f)
    echo "Refusing NGC candidate that stalled handheld startup on 2026-09-23" >&2; exit 65;;
  e0282e26c2d5017b76e893a15d51aa8c44a78f94cebcbf23a8d1ff651029d75c)
    echo "Refusing NGC lcd_blit candidate: launch returned handheld to Home and TI logged a CX II Data Abort on 2026-09-23" >&2; exit 65;;
  51ca73922afbc0ba2f0b488a98f4ff703eaa8081c64edac951ba1335d833a301)
    echo "Refusing NGC relocation candidate: handheld rejected it as unsupported document format on 2026-09-24" >&2; exit 65;;
  5f3d5213ccc3ff5ef60054981541df03565f69b943ac734f0ea73dafeea62cc9)
    echo "Refusing SDK-wrapper NGC candidate: handheld rejected it as unsupported document format on 2026-09-24" >&2; exit 65;;
  b821080614c2d3eb839b38f8a1f45105485f7a4bee26f49dea149bba82e42d20)
    echo "Refusing NGC lcd-order candidate: handheld rejected it as unsupported document format on 2026-09-24" >&2; exit 65;;
  6fbafc2c81be00e05baf62c898b895e3cbce4a0f6bddd58c5730254369759238)
    echo "Refusing CPU-IRQ auto-transport candidate: handheld flashed once and froze after launch on 2026-09-25" >&2; exit 65;;
  cc49702f6fa3aa182b0e8daf8ca1dd62eeee14136d17678b70c8bb35f823f14c)
    echo "Refusing local-service bootstrap candidate: launch attempt left CX II NavNet screen/info calls unresponsive on 2026-09-25" >&2; exit 65;;
  9cdf132883b0001259aaee62725ae5b0232cf8e7c477c3b879f5cf27cfffee5a|a88bd700651bac3876a23e4b0e45428535102f1834524169219b04a249b499ef|86b883f680a41f3c034167227ae3b0645d26652a8e8a299b2857b0d17a2f1ea1)
    echo "Refusing NGC candidate: handheld rejected this exact SHA as unsupported document format on 2026-09-24" >&2; exit 65;;
esac

# Never send the standalone artifact unless a normal CX II interface is
# enumerated. The checker is read-only and exits non-zero for dock controllers,
# unknown devices, or an absent handheld.
"$ROOT/scripts/check-nspire-usb-state.sh"

echo "Uploading one standalone Ndless program package; runtime chat uses NavNet, not file exchange."
exec python3 "$ROOT/scripts/run-with-timeout.py" "$UPLOAD_TIMEOUT_SECONDS" \
  "$N_LINK_BIN" upload "$ARTIFACT" "$REMOTE_DEST"
