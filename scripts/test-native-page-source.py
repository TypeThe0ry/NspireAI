#!/usr/bin/env python3
"""Static contract checks for the new TI document-page backend."""
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    page = (root / "src/native_page/nspire_ai_page.lua").read_text()
    module = (root / "src/native_page/nspire_ai_nav.c").read_text()
    makefile = (root / "src/native_page/Makefile").read_text()
    deploy = (root / "scripts/deploy-native-page-candidate.sh").read_text()

    required_page = (
        "D2Editor.newRichText()", "toolpalette.register", "timer.start",
        'nrequire "nspire_ai_nav"', "function on.timer()", "function on.destroy()", "CONNECTED",
    )
    for needle in required_page:
        if needle not in page:
            raise SystemExit(f"FAIL: native page missing {needle}")
    for forbidden in ("request.tns", "response.tns", "/documents/nspireai", "nspire_ai.luax"):
        if forbidden in page or forbidden in module:
            raise SystemExit(f"FAIL: new page restored removed file-exchange path: {forbidden}")
    required_module = (
        "SERVICE_ID 0x5001u", "TI_NN_NodeEnumInit", "TI_NN_Connect",
        "TI_NN_Write", "TI_NN_Read", "OP_FRAGMENT", "MAX_RESPONSE",
    )
    for needle in required_module:
        if needle not in module:
            raise SystemExit(f"FAIL: NavNet module missing {needle}")
    for forbidden in ("SDL_Init", "idle()", "TCT_Local_Control_Interrupts"):
        if forbidden in module:
            raise SystemExit(f"FAIL: resident Lua module contains standalone scheduler path: {forbidden}")
    if "$(EXE).tns" not in makefile or "nspire_ai.tns" not in makefile:
        raise SystemExit("FAIL: native page Makefile does not build both page and resident module")
    for needle in ("NSPIRE_ALLOW_NATIVE_PAGE_UPLOAD", "nspire_ai_nav.luax.tns", "nspire_ai.tns"):
        if needle not in deploy:
            raise SystemExit(f"FAIL: native page deploy gate missing {needle}")
    print("PASS: new TI document page uses native controls and resident NavNet module")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
