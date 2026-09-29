"""Render Markdown, LaTeX math and CJK text to grayscale images.

The calculator page is a thin terminal with a 320x240 screen and a tiny ASCII
font, so the bridge turns model answers and menus into images.  Everything
here produces mode "L" PIL images with a white background (255) and black ink
(0), rendered at the target size so that text stays crisp after quantization
to 16 gray levels (see ``imagecodec``).

Public API::

    RenderConfig, RenderConfig.from_env()
    render_markdown(text, cfg=None)      assistant answers
    render_user_turn(text, cfg=None)     the user's message, plain text + $math$
    render_info(text, cfg=None)          small gray system notes
    render_menu(title, items, cfg=None, footer=None, height_limit=222)
    wrap_text(text, max_width, cfg=None) the line breaker, for tests and tools
    describe_fonts(cfg=None)             which font files were selected
    warm_up(cfg=None)                    load fonts and the math engine ahead of time

Fonts are searched in this order: the paths of the configuration, font files
in ``<NSPIREAI_HOME>/fonts`` (default ``~/.config/nspireai/fonts``), then the
usual system locations of macOS, Linux and Windows.  A font counts as a CJK
font only if it really draws a sample of Chinese characters.

Math is typeset with matplotlib's mathtext (no TeX installation).  Formulas
that mathtext cannot parse are normalized first and, as a last resort, shown
as LaTeX source in the monospace font; rendering never raises.
"""
from __future__ import annotations

import glob
import html
import importlib.util
import logging
import math
import os
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

# Gray values that survive 4 bpp quantization unchanged (multiples of 17).
INK = 0
PAPER = 255
GRAY_TEXT = 102         # secondary text: truncation note, menu footer
GRAY_RULE = 136         # horizontal rules, table borders
GRAY_BAR = 119          # block quote and user turn bars
CODE_BACKGROUND = 221
HEADER_BACKGROUND = 221
KEYCAP_BACKGROUND = 238
KEYCAP_BORDER = 85
TITLE_BACKGROUND = 51

TRUNCATED_NOTE = "... (truncated)"
MIN_MATH_SCALE = 0.6    # display math is never typeset below this share of the body size
SOFT_MATH_SCALE = 0.8   # below this, breaking a formula is preferred to shrinking it


# ==========================================================================
# Configuration
# ==========================================================================

@dataclass
class RenderConfig:
    width: int = 320                        # final image width in pixels, exactly
    margin: int = 4                         # left/right margin
    font_size: int = 13                     # body text size in px
    font_path: Optional[str] = None         # regular font (must cover CJK); None = auto-discover
    bold_font_path: Optional[str] = None
    mono_font_path: Optional[str] = None
    line_spacing: int = 2
    paragraph_spacing: int = 5
    max_height: int = 6000                  # truncate with a "... (truncated)" line beyond this
    # Extensions.  A font path may end in "#<n>" to select face n of a collection.
    latin_font_path: Optional[str] = None   # font for non-CJK text; None = auto, "" = the regular font
    math_fontset: str = "dejavusans"        # mathtext font set: dejavusans, stix, stixsans, cm, dejavuserif
    info_ink: int = 51                      # gray value of render_info text (0 = black)

    @classmethod
    def from_env(cls, **overrides) -> "RenderConfig":
        """Defaults overridden by NSPIREAI_FONT, NSPIREAI_FONT_BOLD,
        NSPIREAI_FONT_MONO, NSPIREAI_FONT_LATIN, NSPIREAI_FONT_SIZE and
        NSPIREAI_MATH_FONTSET, then by keyword arguments."""
        values: dict = {}
        for key, name in (
            ("font_path", "NSPIREAI_FONT"),
            ("bold_font_path", "NSPIREAI_FONT_BOLD"),
            ("mono_font_path", "NSPIREAI_FONT_MONO"),
        ):
            value = os.environ.get(name, "").strip()
            if value:
                values[key] = value
        if "NSPIREAI_FONT_LATIN" in os.environ:
            values["latin_font_path"] = os.environ["NSPIREAI_FONT_LATIN"].strip()
        fontset = os.environ.get("NSPIREAI_MATH_FONTSET", "").strip()
        if fontset:
            values["math_fontset"] = fontset
        size = os.environ.get("NSPIREAI_FONT_SIZE", "").strip()
        if size:
            try:
                values["font_size"] = int(float(size))
            except ValueError:
                log.warning("ignoring NSPIREAI_FONT_SIZE=%r: not a number", size)
        values.update(overrides)
        return cls(**values)


def _sane(cfg: Optional[RenderConfig]) -> RenderConfig:
    """A copy of the configuration with every value forced into a usable range."""
    cfg = cfg if cfg is not None else RenderConfig()

    def number(value, default, low, high):
        try:
            return max(low, min(high, int(value)))
        except (TypeError, ValueError):
            return default

    width = number(cfg.width, 320, 16, 4096)
    fontset = cfg.math_fontset if cfg.math_fontset in _MATH_FONTSETS else "dejavusans"
    return RenderConfig(
        width=width,
        margin=number(cfg.margin, 4, 0, max(0, (width - 12) // 2)),
        font_size=number(cfg.font_size, 13, 6, 96),
        font_path=cfg.font_path or None,
        bold_font_path=cfg.bold_font_path or None,
        mono_font_path=cfg.mono_font_path or None,
        line_spacing=number(cfg.line_spacing, 2, 0, 64),
        paragraph_spacing=number(cfg.paragraph_spacing, 5, 0, 64),
        max_height=number(cfg.max_height, 6000, 1, 65535),
        latin_font_path=cfg.latin_font_path,
        math_fontset=fontset,
        info_ink=number(cfg.info_ink, 51, 0, 221),
    )


_MATH_FONTSETS = ("dejavusans", "dejavuserif", "cm", "stix", "stixsans")


# ==========================================================================
# Characters
# ==========================================================================

# Never at the start of a line.
_CLOSING_CJK = frozenset("，。！？；：、）】》」』〉〕］｝〗〙〛”’％‰．")
_CLOSING_ASCII = frozenset(",.!?;:)]}%")
_CLOSING = _CLOSING_CJK | _CLOSING_ASCII
# Never at the end of a line.
_OPENING = frozenset("（【《「『〈〔［｛〖〘〚“‘([{")

_DROPPED = frozenset(
    "\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff\u00ad"
    "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)
_SPACES = frozenset("\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f")

# Private use characters mark protected spans while inline markup is parsed.
_MARK_OPEN = "\ue000"
_MARK_CLOSE = "\ue001"


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return (
        0x2E80 <= code <= 0x9FFF
        or 0xAC00 <= code <= 0xD7AF
        or 0xF900 <= code <= 0xFAFF
        or 0xFE30 <= code <= 0xFE4F
        or 0xFF00 <= code <= 0xFFEF
        or 0x20000 <= code <= 0x3FFFF
    )


def _has_cjk(text: str) -> bool:
    return any(_is_cjk(char) for char in text)


def _clean(text: str) -> str:
    """Normalize line ends and drop characters that can never be drawn."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for char in text:
        code = ord(char)
        if char == "\n" or char == "\t":
            out.append(char)
        elif code < 0x20 or 0x7F <= code < 0xA0:
            continue
        elif char in _DROPPED or 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF:
            continue        # zero width characters and (emoji) variation selectors
        elif 0xE000 <= code <= 0xF8FF or 0xD800 <= code <= 0xDFFF:
            continue        # private use (our own markers) and lone surrogates
        elif char in _SPACES:
            out.append(" ")
        elif char == "\u3000":
            out.append("  ")
        elif char in "\u2028\u2029":
            out.append("\n")
        else:
            out.append(char)
    return "".join(out)


# ==========================================================================
# Fonts
# ==========================================================================

_FONT_SUFFIXES = (".ttf", ".ttc", ".otf", ".otc")
_CJK_SAMPLE = "中文的一是数"
_LATIN_SAMPLE = "Aag0"
_LINUX_ROOTS = ("/usr/share/fonts", "/usr/local/share/fonts", "~/.local/share/fonts", "~/.fonts")

# (glob pattern, the font's own Latin glyphs are good enough for body text)
_CJK_CANDIDATES: tuple[tuple[str, bool], ...] = (
    ("/System/Library/Fonts/PingFang.ttc", True),
    ("/System/Library/AssetsV2/com_apple_MobileAsset_Font*/*.asset/AssetData/PingFang.ttc", True),
    ("/System/Library/Fonts/STHeiti Medium.ttc", False),
    ("/System/Library/Fonts/STHeiti Light.ttc", False),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", True),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", False),
    ("/Library/Fonts/Arial Unicode.ttf", True),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", True),
    ("{linux}/**/NotoSansCJK-Regular.ttc", True),
    ("{linux}/**/NotoSansCJK*-Regular.otf", True),
    ("{linux}/**/NotoSansSC-Regular.[ot]tf", True),
    ("{linux}/**/NotoSansSC*.ttf", True),
    ("{linux}/**/SourceHanSans*-Regular.[ot]t[fc]", True),
    ("{linux}/**/wqy-microhei.tt[cf]", True),
    ("{linux}/**/wqy-zenhei.tt[cf]", True),
    ("{linux}/**/DroidSansFallback*.ttf", False),
    ("C:/Windows/Fonts/msyh.ttc", True),
    ("C:/Windows/Fonts/simhei.ttf", False),
    ("C:/Windows/Fonts/simsun.ttc", False),
)
# Fonts searched for a bold CJK face when the regular font's file has none.
_CJK_BOLD_CANDIDATES = (
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "{linux}/**/NotoSansCJK-Bold.ttc",
    "{linux}/**/NotoSansCJK*-Bold.otf",
    "{linux}/**/NotoSansSC-Bold.[ot]tf",
    "{linux}/**/SourceHanSans*-Bold.[ot]t[fc]",
    "C:/Windows/Fonts/msyhbd.ttc",
)
_LATIN_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Tahoma.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "{linux}/**/DejaVuSans.ttf",
    "{linux}/**/LiberationSans-Regular.ttf",
    "{linux}/**/NotoSans-Regular.ttf",
    "C:/Windows/Fonts/tahoma.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "{matplotlib}/DejaVuSans.ttf",
)
_MONO_CANDIDATES = (
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Monaco.ttf",
    "{linux}/**/DejaVuSansMono.ttf",
    "{linux}/**/LiberationMono-Regular.ttf",
    "{linux}/**/NotoSansMono-Regular.ttf",
    "{linux}/**/UbuntuMono-R.ttf",
    "C:/Windows/Fonts/consola.ttf",
    "{matplotlib}/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
)
# Tried, in order, for characters that the main fonts lack (arrows, symbols...).
_SYMBOL_CANDIDATES = (
    "{matplotlib}/DejaVuSans.ttf",
    "{linux}/**/DejaVuSans.ttf",
    "/System/Library/Fonts/Apple Symbols.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "{linux}/**/NotoSansSymbols-Regular.ttf",
    "{linux}/**/NotoSansSymbols2-Regular.ttf",
    "{linux}/**/NotoSansMath-Regular.ttf",
    "/System/Library/Fonts/Supplemental/STIXTwoMath.otf",
    "{matplotlib}/STIXGeneral.ttf",
)

_REGULAR_STYLES = ("regular", "w3", "book", "normal", "roman", "medium", "w4", "light", "")
_BOLD_STYLES = ("semibold", "bold", "w6", "demibold", "demi", "w5", "heavy", "black", "medium")
_SIMPLIFIED_HINTS = (" sc", " gb", " cn", "simplified")

_warned: set[str] = set()


def _warn_once(key: str, message: str, *args) -> None:
    if key not in _warned:
        _warned.add(key)
        log.warning(message, *args)


def _user_font_directory() -> Path:
    home = os.environ.get("NSPIREAI_HOME")
    base = Path(home).expanduser() if home else Path.home() / ".config" / "nspireai"
    return base / "fonts"


@lru_cache(maxsize=1)
def _matplotlib_font_directory() -> str:
    try:
        spec = importlib.util.find_spec("matplotlib")
    except (ImportError, ValueError):
        spec = None
    if spec is None or not spec.submodule_search_locations:
        return ""
    return str(Path(list(spec.submodule_search_locations)[0]) / "mpl-data" / "fonts" / "ttf")


def _expand(pattern: str) -> list[str]:
    """Existing files that match a candidate pattern."""
    if "{matplotlib}" in pattern:
        directory = _matplotlib_font_directory()
        patterns = [pattern.replace("{matplotlib}", directory)] if directory else []
    elif "{linux}" in pattern:
        patterns = [pattern.replace("{linux}", os.path.expanduser(root)) for root in _LINUX_ROOTS]
    else:
        patterns = [os.path.expanduser(pattern)]
    found: list[str] = []
    for entry in patterns:
        if glob.has_magic(entry):
            root = entry.split("*", 1)[0].rsplit("/", 1)[0]
            if root and not os.path.isdir(root):
                continue
            found.extend(sorted(glob.glob(entry, recursive=True)))
        elif os.path.isfile(entry):
            found.append(entry)
    return found


@dataclass(frozen=True)
class _FontRef:
    path: str               # "" is PIL's built-in font
    index: int = 0
    faux_bold: bool = False
    name: str = ""

    def describe(self) -> str:
        if not self.path:
            return "PIL default font"
        text = f"{self.path}#{self.index}" if self.index else self.path
        if self.name:
            text += f" ({self.name})"
        if self.faux_bold:
            text += " [synthetic bold]"
        return text


class _Face:
    """One font face at one size, with a per-character glyph test."""

    def __init__(self, font: ImageFont.ImageFont, ref: _FontRef):
        self.font = font
        self.ref = ref
        self.faux_bold = ref.faux_bold
        self._known: dict[str, bool] = {}
        self._missing = self._signature("\U0010FFFF")

    def _signature(self, char: str):
        try:
            mask = self.font.getmask(char, mode="L")
            return mask.size, bytes(mask)
        except Exception:
            return None

    def has_glyph(self, char: str) -> bool:
        """True when the face draws `char` with something else than .notdef."""
        known = self._known.get(char)
        if known is None:
            signature = self._signature(char)
            known = bool(
                signature is not None
                and signature != self._missing
                and signature[0][0] > 0
                and signature[0][1] > 0
                and any(signature[1])
            )
            self._known[char] = known
        return known

    def covers(self, text: str) -> bool:
        return all(self.has_glyph(char) for char in text)

    def length(self, text: str) -> float:
        try:
            width = float(self.font.getlength(text))
        except Exception:
            width = float(len(text) * 6)
        return width + (1.0 if self.faux_bold and text else 0.0)

    def extent(self, text: str) -> tuple[int, int]:
        """Ink rows above and below the baseline for `text`."""
        try:
            _left, top, _right, bottom = self.font.getbbox(text, anchor="ls")
        except Exception:
            return 0, 0
        return max(0, -int(math.floor(top))), max(0, int(math.ceil(bottom)))


@lru_cache(maxsize=256)
def _load_face(ref: _FontRef, size: int) -> Optional[_Face]:
    try:
        if not ref.path:
            try:
                font = ImageFont.load_default(size)
            except TypeError:       # Pillow < 10.1 has a fixed-size bitmap font only
                font = ImageFont.load_default()
        else:
            font = ImageFont.truetype(ref.path, size, index=ref.index, layout_engine=ImageFont.Layout.BASIC)
    except Exception as exc:
        _warn_once(f"load:{ref.path}#{ref.index}", "cannot load font %s: %s", ref.describe(), exc)
        return None
    return _Face(font, ref)


@lru_cache(maxsize=256)
def _faces_in(path: str) -> tuple[tuple[int, str, str], ...]:
    """(index, family, style) of every face in a font file."""
    faces = []
    for index in range(64):
        try:
            font = ImageFont.truetype(path, 12, index=index, layout_engine=ImageFont.Layout.BASIC)
        except Exception:
            break
        family, style = font.getname()
        faces.append((index, family or "", style or ""))
        if not path.lower().endswith((".ttc", ".otc")):
            break
    return tuple(faces)


def _split_face_index(path: str) -> tuple[str, Optional[int]]:
    head, separator, tail = path.rpartition("#")
    if separator and tail.isdigit() and head:
        return head, int(tail)
    return path, None


def _rank(value: str, preferences: Sequence[str]) -> int:
    value = value.lower()
    for position, preference in enumerate(preferences):
        if preference and preference in value:
            return position
        if not preference and not value:
            return position
    return len(preferences)


def _pick_face(path: str, role: str, sample: str = "", family: Optional[str] = None) -> Optional[_FontRef]:
    """The best face of a font file for a role ("regular" or "bold") that covers `sample`."""
    path, forced = _split_face_index(path)
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return None
    candidates = []
    for index, face_family, style in _faces_in(path):
        if forced is not None and index != forced:
            continue
        if face_family.startswith("."):
            continue                    # hidden system interface variants
        lowered = style.lower()
        if forced is None and ("italic" in lowered or "oblique" in lowered):
            continue
        bold_rank = _rank(style, _BOLD_STYLES)
        if role == "bold" and forced is None and bold_rank == len(_BOLD_STYLES):
            continue
        if family is not None and forced is None and face_family != family:
            continue
        score = (
            bold_rank if role == "bold" else _rank(style, _REGULAR_STYLES),
            0 if any(hint in f" {face_family.lower()}" for hint in _SIMPLIFIED_HINTS) else 1,
            index,
        )
        candidates.append((score, index, face_family, style))
    for _score, index, face_family, style in sorted(candidates):
        ref = _FontRef(path, index, False, f"{face_family} {style}".strip())
        face = _load_face(ref, 13)
        if face is not None and face.covers(sample):
            return ref
    return None


def _first_face(patterns: Iterable[str], role: str, sample: str) -> Optional[_FontRef]:
    for pattern in patterns:
        for path in _expand(pattern):
            ref = _pick_face(path, role, sample)
            if ref is not None:
                return ref
    return None


def _user_fonts() -> list[str]:
    directory = _user_font_directory()
    try:
        entries = sorted(entry for entry in directory.iterdir() if entry.suffix.lower() in _FONT_SUFFIXES)
    except OSError:
        return []
    return [str(entry) for entry in entries]


def _looks_mono(path: str) -> bool:
    name = os.path.basename(path).lower()
    if any(word in name for word in ("mono", "courier", "consol", "menlo", "code")):
        return True
    return any("mono" in family.lower() for _index, family, _style in _faces_in(path))


def _bold_sibling(path: str) -> list[str]:
    """File names that usually hold the bold cut of a regular font file."""
    directory, name = os.path.split(path)
    stem, suffix = os.path.splitext(name)
    names = []
    for old, new in (("-Regular", "-Bold"), ("Regular", "Bold"), (" Light", " Medium"), ("-R", "-B")):
        if old in stem:
            names.append(stem.replace(old, new) + suffix)
    names += [f"{stem} Bold{suffix}", f"{stem}-Bold{suffix}", f"{stem}bd{suffix}"]
    return [os.path.join(directory, candidate) for candidate in names]


@dataclass(frozen=True)
class _FontPlan:
    regular: tuple[_FontRef, ...]
    bold: tuple[_FontRef, ...]
    mono: tuple[_FontRef, ...]
    cjk: Optional[_FontRef]             # the font that covers CJK, None when there is none

    def chain(self, style: str) -> tuple[_FontRef, ...]:
        return {"bold": self.bold, "mono": self.mono}.get(style, self.regular)


def _bold_for(ref: _FontRef, sample: str, extra: Iterable[str] = ()) -> _FontRef:
    """A bold companion of `ref`: same file, sibling file, other candidates, synthetic."""
    family = ref.name.rsplit(" ", 1)[0] if " " in ref.name else None
    for index, face_family, _style in _faces_in(ref.path) if ref.path else ():
        if index == ref.index:
            family = face_family
    if ref.path:
        found = _pick_face(ref.path, "bold", sample, family)
        if found is not None and found != ref:
            return found
        for sibling in _bold_sibling(ref.path):
            found = _pick_face(sibling, "bold", sample)
            if found is not None and found != ref:
                return found
        found = _first_face(extra, "bold", sample)
        if found is not None and found != ref:
            return found
    return _FontRef(ref.path, ref.index, True, ref.name)


def _unique(refs: Iterable[Optional[_FontRef]]) -> tuple[_FontRef, ...]:
    seen = set()
    out = []
    for ref in refs:
        if ref is None:
            continue
        key = (ref.path, ref.index, ref.faux_bold)
        if key not in seen:
            seen.add(key)
            out.append(ref)
    return tuple(out)


@lru_cache(maxsize=32)
def _plan(font_path: Optional[str], bold_path: Optional[str], mono_path: Optional[str],
          latin_path: Optional[str], user_fonts: tuple[str, ...]) -> _FontPlan:
    cjk: Optional[_FontRef] = None
    own_latin = True
    explicit = None
    if font_path:
        explicit = _pick_face(font_path, "regular")
        if explicit is None:
            _warn_once(f"font:{font_path}", "cannot use font %s; searching for another one", font_path)
        else:
            face = _load_face(explicit, 13)
            if face is not None and face.covers(_CJK_SAMPLE):
                cjk = explicit
            else:
                _warn_once(f"nocjk:{font_path}", "font %s has no CJK glyphs", font_path)
    if cjk is None:
        for path in user_fonts:
            if not _looks_mono(path):
                cjk = _pick_face(path, "regular", _CJK_SAMPLE)
                if cjk is not None:
                    break
    if cjk is None:
        for pattern, latin_ok in _CJK_CANDIDATES:
            for path in _expand(pattern):
                cjk = _pick_face(path, "regular", _CJK_SAMPLE)
                if cjk is not None:
                    own_latin = latin_ok
                    break
            if cjk is not None:
                break
    if cjk is None:
        _warn_once("nocjk", "no font with CJK glyphs was found; Chinese text will show as '?'. "
                            "Set NSPIREAI_FONT or put a font into %s", _user_font_directory())

    # Non-CJK text: an explicit regular font is used for everything, as is an
    # automatically found font with good Latin glyphs.
    latin: Optional[_FontRef] = None
    if latin_path:
        latin = _pick_face(latin_path, "regular", _LATIN_SAMPLE)
        if latin is None:
            _warn_once(f"latin:{latin_path}", "cannot use Latin font %s", latin_path)
    elif latin_path is None and explicit is None and (cjk is None or not own_latin):
        latin = _first_face(_LATIN_CANDIDATES, "regular", _LATIN_SAMPLE)
    primary = explicit if explicit is not None else cjk
    if primary is None and latin is None:
        latin = _first_face(_LATIN_CANDIDATES, "regular", _LATIN_SAMPLE) or _FontRef("", 0, False, "default")

    symbols = [_pick_face(path, "regular") for pattern in _SYMBOL_CANDIDATES for path in _expand(pattern)]
    default = _FontRef("", 0, False, "default")
    regular = _unique([latin, primary, cjk, *symbols, default])

    bold_primary: Optional[_FontRef] = None
    if bold_path:
        bold_primary = _pick_face(bold_path, "bold") or _pick_face(bold_path, "regular")
        if bold_primary is None:
            _warn_once(f"bold:{bold_path}", "cannot use bold font %s", bold_path)
    bold_refs: list[Optional[_FontRef]] = []
    if latin is not None:
        bold_refs.append(_bold_for(latin, _LATIN_SAMPLE))
    if bold_primary is not None:
        bold_refs.append(bold_primary)
    for ref in (primary, cjk):
        if ref is not None:
            sample = _CJK_SAMPLE if ref == cjk else _LATIN_SAMPLE
            extra = _CJK_BOLD_CANDIDATES if ref == cjk else ()
            user_bold = [path for path in user_fonts if not _looks_mono(path)] if ref == cjk else []
            bold_refs.append(_bold_for(ref, sample, [*user_bold, *extra]))
    bold_refs += [_FontRef(ref.path, ref.index, True, ref.name) for ref in symbols if ref is not None]
    bold_refs.append(_FontRef("", 0, True, "default"))
    bold = _unique(bold_refs)

    mono: Optional[_FontRef] = None
    if mono_path:
        mono = _pick_face(mono_path, "regular", _LATIN_SAMPLE)
        if mono is None:
            _warn_once(f"mono:{mono_path}", "cannot use monospace font %s", mono_path)
    if mono is None:
        for path in user_fonts:
            if _looks_mono(path):
                mono = _pick_face(path, "regular", _LATIN_SAMPLE)
                if mono is not None:
                    break
    if mono is None:
        mono = _first_face(_MONO_CANDIDATES, "regular", _LATIN_SAMPLE)
    mono_chain = _unique([mono, cjk, primary, latin, *symbols, default])
    return _FontPlan(regular, bold, mono_chain, cjk)


def _plan_for(cfg: RenderConfig) -> _FontPlan:
    return _plan(cfg.font_path, cfg.bold_font_path, cfg.mono_font_path, cfg.latin_font_path,
                 tuple(_user_fonts()))


def describe_fonts(cfg: Optional[RenderConfig] = None) -> dict[str, str]:
    """The font files selected for a configuration (first of each chain, and the CJK font)."""
    plan = _plan_for(_sane(cfg))
    return {
        "regular": plan.regular[0].describe(),
        "bold": plan.bold[0].describe(),
        "mono": plan.mono[0].describe(),
        "cjk": plan.cjk.describe() if plan.cjk is not None else "none",
        "cjk_bold": next(
            (ref.describe() for ref in plan.bold
             if (face := _load_face(ref, 13)) is not None and face.covers(_CJK_SAMPLE)),
            "none",
        ),
        "fallbacks": ", ".join(ref.describe() for ref in plan.regular[1:]),
    }


class _Chain:
    """Fonts tried in order for every character, all at one size."""

    def __init__(self, faces: Sequence[_Face], size: int):
        self.faces = list(faces)
        self.size = size
        self._face_of: dict[str, Optional[_Face]] = {}
        self._advance: dict[str, float] = {}
        self.primary = self.faces[0]
        # Ink extents of ordinary letters rather than the font's own line
        # metrics, which are generous: lines can sit closer on a small screen.
        # Brackets and accents may reach one row into the line spacing.
        ascent = descent = 0
        for face in self.faces[:3]:
            for sample in ("Hhdfkl", "gjpqy", "中国语"):
                if face.covers(sample):
                    up, down = face.extent(sample)
                    ascent, descent = max(ascent, up), max(descent, down)
        if ascent == 0:
            ascent, descent = max(1, round(size * 0.8)), max(1, round(size * 0.2))
        self.ascent = ascent
        self.descent = descent
        self.x_height = self.primary.extent("x")[0] or max(1, round(size * 0.5))
        self.space = max(1.0, self.length(" "))

    def face_for(self, char: str) -> Optional[_Face]:
        if char == " ":
            return self.primary
        if char not in self._face_of:
            self._face_of[char] = next((face for face in self.faces if face.has_glyph(char)), None)
        return self._face_of[char]

    def runs(self, text: str) -> list[tuple[_Face, str]]:
        """Split text into runs of one face; characters without a glyph become "?"."""
        runs: list[tuple[_Face, str]] = []
        for char in text:
            face = self.face_for(char)
            if face is None:
                if unicodedata.category(char) in ("Mn", "Me", "Cf", "Cc", "Cs", "Co"):
                    continue
                char = "?"
                face = self.face_for(char) or self.primary
            if runs and runs[-1][0] is face:
                runs[-1] = (face, runs[-1][1] + char)
            else:
                runs.append((face, char))
        return runs

    def length(self, text: str) -> float:
        return sum(face.length(run) for face, run in self.runs(text))

    def advance(self, char: str) -> float:
        """Width of a single character (cached; kerning is not considered)."""
        width = self._advance.get(char)
        if width is None:
            width = self._advance[char] = self.length(char)
        return width


_chains: dict[tuple, _Chain] = {}
_chain_lock = threading.RLock()


def _chain(plan: _FontPlan, style: str, size: int) -> _Chain:
    key = (plan, style, size)
    with _chain_lock:
        chain = _chains.get(key)
        if chain is None:
            faces = [face for ref in plan.chain(style) if (face := _load_face(ref, size)) is not None]
            if not faces:
                raise RuntimeError("no usable font")
            chain = _Chain(faces, size)
            if len(_chains) > 256:
                _chains.clear()
            _chains[key] = chain
        return chain


# ==========================================================================
# Boxes (math and other pre-rendered pieces)
# ==========================================================================

@dataclass
class _Box:
    """An ink mask (0 = paper, 255 = full ink) positioned on a baseline."""

    mask: Image.Image
    ascent: int

    @property
    def width(self) -> int:
        return self.mask.width

    @property
    def height(self) -> int:
        return self.mask.height

    @property
    def descent(self) -> int:
        return self.mask.height - self.ascent


def _empty_box(width: int = 1, ascent: int = 1, descent: int = 0) -> _Box:
    return _Box(Image.new("L", (max(1, width), max(1, ascent + descent)), 0), ascent)


def _hcat(boxes: Sequence[_Box], gaps: Sequence[int] = ()) -> _Box:
    """Boxes side by side on one baseline; gaps[i] follows boxes[i]."""
    boxes = list(boxes)
    if not boxes:
        return _empty_box()
    if len(boxes) == 1:
        return boxes[0]
    spaces = [gaps[index] if index < len(gaps) else 0 for index in range(len(boxes) - 1)]
    ascent = max(box.ascent for box in boxes)
    descent = max(box.descent for box in boxes)
    width = sum(box.width for box in boxes) + sum(spaces)
    mask = Image.new("L", (width, max(1, ascent + descent)), 0)
    x = 0
    for index, box in enumerate(boxes):
        mask.paste(box.mask, (x, ascent - box.ascent))
        x += box.width + (spaces[index] if index < len(spaces) else 0)
    return _Box(mask, ascent)


def _attach_scripts(base: _Box, upper: Optional[_Box], lower: Optional[_Box]) -> _Box:
    """Put a superscript and a subscript at the right corners of a box."""
    extra = max(upper.width if upper else 0, lower.width if lower else 0) + 1
    above = max(0, upper.height // 2) if upper else 0
    below = max(0, lower.height // 2) if lower else 0
    mask = Image.new("L", (base.width + extra, base.height + above + below), 0)
    mask.paste(base.mask, (0, above))
    if upper is not None:
        mask.paste(upper.mask, (base.width + 1, 0))
    if lower is not None:
        mask.paste(lower.mask, (base.width + 1, mask.height - lower.height))
    return _Box(mask, base.ascent + above)


def _pad(box: _Box, left: int = 0, top: int = 0, right: int = 0, bottom: int = 0) -> _Box:
    mask = Image.new("L", (box.width + left + right, box.height + top + bottom), 0)
    mask.paste(box.mask, (left, top))
    return _Box(mask, box.ascent + top)


def _squeeze(box: _Box, width: int) -> _Box:
    """Scale a box down to `width`; the last resort for formulas that never fit."""
    if box.width <= width or width < 1:
        return box
    factor = width / box.width
    height = max(1, round(box.height * factor))
    mask = box.mask.resize((width, height), Image.LANCZOS)
    return _Box(mask, min(height, max(0, round(box.ascent * factor))))


def _text_box(text: str, chain: _Chain) -> _Box:
    runs = chain.runs(text)
    width = int(math.ceil(sum(face.length(run) for face, run in runs))) + 1
    mask = Image.new("L", (max(1, width), chain.ascent + chain.descent), 0)
    draw = ImageDraw.Draw(mask)
    x = 0.0
    for face, run in runs:
        _draw_run(draw, x, chain.ascent, run, face, 255)
        x += face.length(run)
    return _Box(mask, chain.ascent)


def _draw_run(draw: ImageDraw.ImageDraw, x: float, baseline: int, text: str, face: _Face, fill: int) -> None:
    position = (int(round(x)), baseline)
    try:
        draw.text(position, text, font=face.font, fill=fill, anchor="ls")
        if face.faux_bold:
            draw.text((position[0] + 1, baseline), text, font=face.font, fill=fill, anchor="ls")
    except Exception:
        # Bitmap fonts have no anchors: place the text by its top left corner.
        try:
            top = baseline - face.extent("Hg")[0]
            draw.text((position[0], top), text, font=face.font, fill=fill)
        except Exception:
            log.debug("cannot draw %r", text, exc_info=True)


# ==========================================================================
# LaTeX
# ==========================================================================

class _MathError(Exception):
    pass


_TOKEN = re.compile(r"\\[A-Za-z]+|\\.|\s+|.", re.S)

_DROP_COMMANDS = frozenset((
    "\\displaystyle", "\\textstyle", "\\scriptstyle", "\\scriptscriptstyle",
    "\\limits", "\\nolimits", "\\nonumber", "\\notag", "\\middle", "\\hline", "\\centering",
    "\\big", "\\Big", "\\bigg", "\\Bigg", "\\bigl", "\\Bigl", "\\biggl", "\\Biggl",
    "\\bigr", "\\Bigr", "\\biggr", "\\Biggr", "\\bigm", "\\Bigm", "\\biggm", "\\Biggm",
    "\\mathop", "\\mathbin", "\\mathrel", "\\mathord", "\\mathpunct", "\\mathinner",
    "\\normalsize", "\\small", "\\large", "\\Large", "\\allowbreak", "\\relax", "\\strut",
))
_DROP_WITH_ARGUMENT = frozenset(("\\label", "\\vspace", "\\vphantom", "\\color", "\\rule", "\\vskip"))
_UNWRAP = frozenset(("\\cancel", "\\bcancel", "\\xcancel", "\\boxed", "\\fbox", "\\smash", "\\ensuremath",
                     "\\mathstrut", "\\mathclap", "\\mathllap", "\\mathrlap"))
_RENAME = {
    "\\le": "\\leq", "\\ge": "\\geq", "\\lt": "<", "\\gt": ">",
    "\\tfrac": "\\frac", "\\cfrac": "\\frac", "\\dbinom": "\\binom", "\\tbinom": "\\binom",
    "\\implies": "\\Rightarrow", "\\impliedby": "\\Leftarrow", "\\iff": "\\Leftrightarrow",
    "\\lvert": "|", "\\rvert": "|", "\\lVert": "\\|", "\\rVert": "\\|",
    "\\lnot": "\\neg", "\\land": "\\wedge", "\\lor": "\\vee",
    "\\bm": "\\boldsymbol", "\\pmb": "\\boldsymbol", "\\bold": "\\mathbf",
    "\\stackrel": "\\overset", "\\overbrace": "\\overline", "\\underbrace": "\\underline",
    "\\varDelta": "\\Delta", "\\varGamma": "\\Gamma", "\\varTheta": "\\Theta", "\\varLambda": "\\Lambda",
    "\\varXi": "\\Xi", "\\varPi": "\\Pi", "\\varSigma": "\\Sigma", "\\varPhi": "\\Phi",
    "\\varPsi": "\\Psi", "\\varOmega": "\\Omega", "\\varUpsilon": "\\Upsilon",
    "\\square": "\\boxdot", "\\Box": "\\boxdot",
    "\\hphantom": "\\phantom", "\\mbox": "\\text", "\\hbox": "\\text", "\\textrm": "\\text",
    "\\textnormal": "\\text", "\\textup": "\\text", "\\textsf": "\\mathsf", "\\texttt": "\\mathtt",
    "\\textbf": "\\mathbf", "\\textit": "\\mathit", "\\emph": "\\mathit",
    "\\R": "\\mathbb{R}", "\\N": "\\mathbb{N}", "\\Z": "\\mathbb{Z}", "\\Q": "\\mathbb{Q}",
    "\\dots": "\\ldots", "\\dotsc": "\\ldots", "\\dotsb": "\\cdots", "\\dotsm": "\\cdots",
    "\\>": "\\:", "\\bmod": "\\ \\mathrm{mod}\\ ", "\\mod": "\\ \\mathrm{mod}\\ ",
    "\\thinspace": "\\,", "\\medspace": "\\:", "\\thickspace": "\\;", "\\enspace": "\\;",
    "\\negthinspace": "\\!", "\\nobreakspace": "\\ ", "\\space": "\\ ",
    "\\longmapsto": "\\mapsto", "\\dagger": "\\dagger", "\\part": "\\partial",
    "\\geqq": "\\geq", "\\leqq": "\\leq", "\\colon": ":",
    "\\&": "&",       # mathtext draws a bare "&" and rejects the escaped one
}
# Spelled-out names mathtext does not know; shown upright like \sin.
_OPERATOR_NAMES = frozenset((
    "arccot", "arcsec", "arccsc", "sech", "csch", "arsinh", "arcosh", "artanh", "sgn", "sign",
    "tr", "rank", "lcm", "diag", "argmax", "argmin", "span", "Var", "Cov", "erf", "cis", "adj",
    "grad", "curl", "rot", "res", "Res", "card", "vol", "mod", "const", "Im", "Re", "lb",
))
_UNICODE_MATH = {
    "≤": "\\leq ", "≥": "\\geq ", "≠": "\\neq ", "≈": "\\approx ", "≡": "\\equiv ", "×": "\\times ",
    "÷": "\\div ", "±": "\\pm ", "∓": "\\mp ", "·": "\\cdot ", "∙": "\\cdot ", "⋅": "\\cdot ",
    "→": "\\to ", "←": "\\leftarrow ", "↔": "\\leftrightarrow ", "⇒": "\\Rightarrow ", "⇐": "\\Leftarrow ",
    "⇔": "\\Leftrightarrow ", "∞": "\\infty ", "∂": "\\partial ", "∇": "\\nabla ", "∈": "\\in ",
    "∉": "\\notin ", "⊂": "\\subset ", "⊆": "\\subseteq ", "∪": "\\cup ", "∩": "\\cap ", "∅": "\\emptyset ",
    "∀": "\\forall ", "∃": "\\exists ", "∑": "\\sum ", "∏": "\\prod ", "∫": "\\int ", "√": "\\surd ",
    "∠": "\\angle ", "⊥": "\\perp ", "∥": "\\parallel ", "∝": "\\propto ", "∴": "\\therefore ",
    "∵": "\\because ", "°": "^{\\circ}", "′": "'", "″": "''", "−": "-", "–": "-", "—": "-",
    "²": "^{2}", "³": "^{3}", "¹": "^{1}", "⁰": "^{0}", "⁴": "^{4}", "⁵": "^{5}", "⁶": "^{6}",
    "⁷": "^{7}", "⁸": "^{8}", "⁹": "^{9}", "ⁿ": "^{n}", "₀": "_{0}", "₁": "_{1}", "₂": "_{2}",
    "₃": "_{3}", "₄": "_{4}", "½": "\\frac{1}{2}", "…": "\\ldots ", "⋯": "\\cdots ",
    "α": "\\alpha ", "β": "\\beta ", "γ": "\\gamma ", "δ": "\\delta ", "ε": "\\epsilon ",
    "ζ": "\\zeta ", "η": "\\eta ", "θ": "\\theta ", "ι": "\\iota ", "κ": "\\kappa ", "λ": "\\lambda ",
    "μ": "\\mu ", "ν": "\\nu ", "ξ": "\\xi ", "π": "\\pi ", "ρ": "\\rho ", "σ": "\\sigma ",
    "τ": "\\tau ", "υ": "\\upsilon ", "φ": "\\phi ", "ϕ": "\\phi ", "χ": "\\chi ", "ψ": "\\psi ",
    "ω": "\\omega ", "Γ": "\\Gamma ", "Δ": "\\Delta ", "Θ": "\\Theta ", "Λ": "\\Lambda ",
    "Ξ": "\\Xi ", "Π": "\\Pi ", "Σ": "\\Sigma ", "Φ": "\\Phi ", "Ψ": "\\Psi ", "Ω": "\\Omega ",
    "ℝ": "\\mathbb{R}", "ℕ": "\\mathbb{N}", "ℤ": "\\mathbb{Z}", "ℚ": "\\mathbb{Q}", "ℂ": "\\mathbb{C}",
    "（": "(", "）": ")", "，": ",", "＝": "=", "＋": "+", "＜": "<", "＞": ">",
}
_TEXT_ESCAPES = {"\\%": "%", "\\&": "&", "\\#": "#", "\\_": "_", "\\$": "$", "\\{": "{", "\\}": "}",
                 "\\ ": " ", "\\,": " ", "\\;": " ", "\\:": " ", "\\!": "", "~": " "}

_GRID_ENVIRONMENTS = {
    # name: (left delimiter, right delimiter, column alignment, column gap in em)
    "matrix": ("", "", "c", 0.9), "smallmatrix": ("", "", "c", 0.7),
    "pmatrix": ("(", ")", "c", 0.9), "bmatrix": ("[", "]", "c", 0.9),
    "Bmatrix": ("{", "}", "c", 0.9), "vmatrix": ("|", "|", "c", 0.9),
    "Vmatrix": ("‖", "‖", "c", 0.9), "cases": ("{", "", "l", 0.9),
    "dcases": ("{", "", "l", 0.9), "rcases": ("", "}", "l", 0.9),
    "array": ("", "", "c", 0.9), "subarray": ("", "", "c", 0.5),
}
_ROW_ENVIRONMENTS = frozenset((
    "aligned", "align", "align*", "alignat", "alignat*", "alignedat", "flalign", "flalign*",
    "gather", "gather*", "gathered", "split", "eqnarray", "eqnarray*", "equation", "equation*",
    "multline", "multline*", "displaymath", "math",
))
_TEXT_COMMANDS = frozenset(("\\text", "\\mbox", "\\hbox", "\\textrm", "\\textnormal", "\\textup",
                            "\\textbf", "\\textit", "\\textsf", "\\texttt", "\\mathrm", "\\emph"))
_DELIMITERS = {
    "(": "(", ")": ")", "[": "[", "]": "]", "\\{": "{", "\\}": "}", "\\lbrace": "{", "\\rbrace": "}",
    "|": "|", "\\|": "‖", "\\vert": "|", "\\Vert": "‖", "\\lvert": "|", "\\rvert": "|",
    "\\lVert": "‖", "\\rVert": "‖", "\\lbrack": "[", "\\rbrack": "]", ".": "",
    "\\langle": "<", "\\rangle": ">", "<": "<", ">": ">",
}
_RELATIONS = frozenset((
    "=", "<", ">", "\\leq", "\\geq", "\\le", "\\ge", "\\neq", "\\ne", "\\approx", "\\equiv", "\\sim",
    "\\simeq", "\\cong", "\\propto", "\\to", "\\rightarrow", "\\Rightarrow", "\\implies", "\\iff",
    "\\Leftrightarrow", "\\leftarrow", "\\Leftarrow", "\\leqslant", "\\geqslant", "\\ll", "\\gg",
))
_BINARY = frozenset(("+", "-", "\\pm", "\\mp"))


def _tokens(latex: str) -> list[str]:
    return _TOKEN.findall(latex)


def _group_end(tokens: Sequence[str], start: int) -> int:
    """Index just past the group that starts at tokens[start] == "{"."""
    depth = 0
    for index in range(start, len(tokens)):
        if tokens[index] == "{":
            depth += 1
        elif tokens[index] == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return len(tokens)


def _argument(tokens: Sequence[str], start: int) -> tuple[list[str], int]:
    """The argument that starts at or after tokens[start]: a group's content or one token."""
    index = start
    while index < len(tokens) and tokens[index].isspace():
        index += 1
    if index >= len(tokens):
        return [], index
    if tokens[index] == "{":
        end = _group_end(tokens, index)
        inner_end = end - 1 if end <= len(tokens) and tokens[end - 1] == "}" else end
        return list(tokens[index + 1:inner_end]), end
    return [tokens[index]], index + 1


def _optional_argument(tokens: Sequence[str], start: int) -> int:
    """Skip a [...] argument at tokens[start], if there is one."""
    index = start
    while index < len(tokens) and tokens[index].isspace():
        index += 1
    if index < len(tokens) and tokens[index] == "[":
        depth = 0
        for end in range(index, len(tokens)):
            if tokens[end] == "[":
                depth += 1
            elif tokens[end] == "]":
                depth -= 1
                if depth == 0:
                    return end + 1
    return start


def _split_top(tokens: Sequence[str], separators: Iterable[str]) -> list[list[str]]:
    """Split a token list at separators that are outside of groups and environments."""
    separators = frozenset(separators)
    parts: list[list[str]] = [[]]
    depth = 0
    environment = 0
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "{":
            depth += 1
        elif token == "}":
            depth = max(0, depth - 1)
        elif token == "\\begin":
            environment += 1
        elif token == "\\end":
            environment = max(0, environment - 1)
        if depth == 0 and environment == 0 and token in separators:
            parts.append([])
        else:
            parts[-1].append(token)
        index += 1
    return parts


def _plain_text(tokens: Sequence[str]) -> str:
    """The text of a \\text{...} argument."""
    out = []
    for token in tokens:
        if token in _TEXT_ESCAPES:
            out.append(_TEXT_ESCAPES[token])
        elif token in ("{", "}", "$"):
            continue
        elif token.startswith("\\") and len(token) > 1 and token[1].isalpha():
            if token not in _TEXT_COMMANDS:
                out.append(token[1:])
        elif token.isspace():
            out.append(" ")
        else:
            out.append(token)
    return "".join(out)


def _math_text(text: str, command: str = "\\mathrm") -> str:
    """Plain text as a mathtext expression."""
    escaped = []
    for char in text:
        if char == " ":
            escaped.append("\\ ")
        elif char in "%#_$":
            escaped.append("\\" + char)
        elif char in "{}":
            escaped.append("\\" + char)
        elif char in "\\^~":
            escaped.append(" ")
        else:
            escaped.append(char)
    return command + "{" + "".join(escaped) + "}"


def _normalize(latex: str, display: bool = False, strip_delimiters: bool = False) -> str:
    """Rewrite LaTeX that mathtext does not understand into something it does."""
    return _normalize_tokens(_tokens(latex), display, strip_delimiters, 0).strip()


def _normalize_tokens(tokens: Sequence[str], display: bool, strip: bool, depth: int) -> str:
    # TeX primitives that split their whole group: {a \over b}, {n \choose k}.
    for primitive, command in (("\\over", "\\frac"), ("\\choose", "\\binom"), ("\\atop", "\\genfrac{}{}{0}{}")):
        if primitive in tokens:
            halves = _split_top(tokens, (primitive,))
            if len(halves) == 2:
                upper = _normalize_tokens(halves[0], False, strip, depth + 1)
                lower = _normalize_tokens(halves[1], False, strip, depth + 1)
                if command == "\\genfrac{}{}{0}{}":
                    return "\\genfrac{}{}{0}{}{" + upper + "}{" + lower + "}"
                return command + "{" + upper + "}{" + lower + "}"
    out: list[str] = []
    index = 0
    count = len(tokens)
    while index < count:
        token = tokens[index]
        index += 1
        if token == "{":
            end = _group_end(tokens, index - 1)
            closed = end <= count and tokens[end - 1] == "}" and end - 1 >= index
            inner = tokens[index:end - 1] if closed else tokens[index:end]
            out.append("{" + _normalize_tokens(inner, False, strip, depth + 1) + "}")
            index = end
        elif token == "}":
            continue                        # unbalanced
        elif token in ("\\left", "\\right"):
            following = tokens[index] if index < count else ""
            if strip or not following or following.isspace():
                if following == ".":
                    index += 1
                continue
            out.append(token)
            out.append(_RENAME.get(following, following) if following.endswith(("vert", "Vert")) else following)
            index += 1
        elif token in _DROP_COMMANDS:
            continue
        elif token in _DROP_WITH_ARGUMENT:
            if index < count and tokens[index] == "*":
                index += 1
            _argument_tokens, index = _argument(tokens, _optional_argument(tokens, index))
        elif token == "\\textcolor":
            _color, index = _argument(tokens, index)
        elif token in _UNWRAP:
            inner, index = _argument(tokens, index)
            out.append("{" + _normalize_tokens(inner, False, strip, depth + 1) + "}")
        elif token in ("\\hspace", "\\hskip", "\\kern", "\\mkern", "\\mskip"):
            if index < count and tokens[index] == "*":
                index += 1
            if index < count and tokens[index] == "{":
                _width, index = _argument(tokens, index)
            out.append("\\;")
        elif token == "\\tag":
            if index < count and tokens[index] == "*":
                index += 1
            inner, index = _argument(tokens, index)
            out.append("\\qquad " + _math_text("(" + _plain_text(inner) + ")"))
        elif token == "\\pmod":
            inner, index = _argument(tokens, index)
            out.append("\\ (\\mathrm{mod}\\ " + _normalize_tokens(inner, False, strip, depth + 1) + ")")
        elif token == "\\operatorname":
            if index < count and tokens[index] == "*":
                index += 1
            inner, index = _argument(tokens, index)
            out.append("\\operatorname{" + _plain_text(inner).replace(" ", "") + "}")
        elif token in ("\\xrightarrow", "\\xleftarrow", "\\xRightarrow", "\\xLeftarrow"):
            index = _optional_argument(tokens, index)
            inner, index = _argument(tokens, index)
            arrow = {"\\xrightarrow": "\\longrightarrow", "\\xleftarrow": "\\longleftarrow",
                     "\\xRightarrow": "\\Longrightarrow", "\\xLeftarrow": "\\Longleftarrow"}[token]
            out.append("\\overset{" + _normalize_tokens(inner, False, strip, depth + 1) + "}{" + arrow + "}")
        elif token in _TEXT_COMMANDS:
            inner, index = _argument(tokens, index)
            command = {"\\textbf": "\\mathbf", "\\textit": "\\mathit", "\\emph": "\\mathit",
                       "\\textsf": "\\mathsf", "\\texttt": "\\mathtt"}.get(token, "\\mathrm")
            if token == "\\mathrm":
                out.append("\\mathrm{" + _normalize_tokens(inner, False, strip, depth + 1) + "}")
            else:
                out.append(_math_text(_plain_text(inner), command))
        elif token == "\\frac" and display and depth == 0:
            out.append("\\dfrac")
        elif token == "\\dfrac" and strip:
            out.append("\\frac")
        elif token == "\\not":
            following = tokens[index] if index < count else ""
            replacement = {"=": "\\neq ", "\\in": "\\notin ", "<": "\\nless ", ">": "\\ngtr ",
                           "\\leq": "\\nleq ", "\\geq": "\\ngeq ", "\\subset": "\\nsubset ",
                           "\\equiv": "\\not\\equiv "}.get(following)
            if replacement:
                out.append(replacement)
                index += 1
            else:
                out.append(token)
        elif token in _RENAME:
            out.append(_RENAME[token])
            if _RENAME[token][-1:].isalpha() and index < count and tokens[index][:1].isalnum():
                out.append(" ")
        elif token == "\\\\":
            out.append("\\quad ")           # a line break that was not handled as a row
            index = _optional_argument(tokens, index)
        elif token == "&":
            out.append(" ")
        elif token.startswith("\\") and token[1:] in _OPERATOR_NAMES:
            out.append("\\operatorname{" + token[1:] + "}")
        elif token in _UNICODE_MATH:
            out.append(_UNICODE_MATH[token])
        elif token == "%":
            out.append("\\%")
        elif token == "\\begin" or token == "\\end":
            _name, index = _argument(tokens, index)     # an environment that was not handled
        elif token.startswith("\\") and len(token) > 1 and token[1].isalpha():
            out.append(token)
            if index < count and tokens[index][:1].isalnum():
                out.append(" ")
        else:
            out.append(token)
    return "".join(out)


_math_lock = threading.RLock()
_math_cache: dict[tuple, object] = {}
_mathtext_state: dict[str, object] = {}


class _GlyphWatch(logging.Handler):
    """Collects mathtext's "does not have a glyph" warnings."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.missing = False

    def emit(self, record: logging.LogRecord) -> None:
        self.missing = True


def _mathtext_parser():
    parser = _mathtext_state.get("parser")
    if parser is None:
        try:
            import matplotlib
            from matplotlib.font_manager import FontProperties
            from matplotlib.mathtext import MathTextParser
        except Exception as exc:    # not installed, or broken
            _warn_once("matplotlib", "matplotlib is not available, math is shown as source: %s", exc)
            _mathtext_state["parser"] = False
            return None
        _mathtext_state["matplotlib"] = matplotlib
        _mathtext_state["properties"] = FontProperties
        parser = MathTextParser("agg")
        _mathtext_state["parser"] = parser
    return parser or None


def _cjk_math_family(plan: _FontPlan) -> Optional[str]:
    """Register the CJK font with matplotlib and return its family name."""
    if plan.cjk is None or not plan.cjk.path:
        return None
    key = f"family:{plan.cjk.path}"
    if key not in _mathtext_state:
        family = None
        try:
            from matplotlib import font_manager

            entries = [entry for entry in font_manager.fontManager.ttflist if entry.fname == plan.cjk.path]
            if not entries:
                font_manager.fontManager.addfont(plan.cjk.path)
                entries = [entry for entry in font_manager.fontManager.ttflist if entry.fname == plan.cjk.path]
            wanted = plan.cjk.name.lower()
            entries.sort(key=lambda entry: (not wanted.startswith(entry.name.lower()), entry.name))
            if entries:
                family = entries[0].name
        except Exception:
            log.debug("cannot register %s with matplotlib", plan.cjk.path, exc_info=True)
        _mathtext_state[key] = family
    return _mathtext_state[key]     # type: ignore[return-value]


# Appended to every expression: the flat bottom of this upright "x" marks the
# baseline in the rendered image, which mathtext itself does not report in a
# usable way (its depth includes padding, and is relative to the ink).
_MARKER = "\\quad\\mathrm{x}"


def _mathtext_raster(parser, source: str, size: int, fontset: str) -> Image.Image:
    properties = _mathtext_state["properties"]
    result = parser.parse("$" + source + "$", dpi=72,
                          prop=properties(size=size, math_fontfamily=fontset))     # type: ignore[operator]
    import numpy

    array = numpy.asarray(result.image, dtype=numpy.uint8)
    height, width = array.shape[:2]
    return Image.frombytes("L", (width, height), array.tobytes())


def _mathtext_box(latex: str, size: int, fontset: str, plan: _FontPlan) -> _Box:
    """Typeset one expression with mathtext; raises _MathError when it is rejected."""
    parser = _mathtext_parser()
    if parser is None:
        raise _MathError("matplotlib is not available")
    if not latex.strip():
        return _empty_box(1, max(1, size // 2), 0)
    matplotlib = _mathtext_state["matplotlib"]
    settings: dict = {"text.usetex": False}
    family = None
    if any(ord(char) > 0x2E7F for char in latex):
        family = _cjk_math_family(plan)
        if family is None:
            raise _MathError("no font for CJK text in math")
        fontset = "custom"
        settings.update({"mathtext.fontset": "custom", "mathtext.rm": family, "mathtext.fallback": "stixsans"})
    watch = _GlyphWatch()
    quiet = logging.NullHandler()
    logger = logging.getLogger("matplotlib.mathtext")
    font_logger = logging.getLogger("matplotlib.font_manager")
    previous = (logger.propagate, font_logger.propagate)
    current = latex
    try:
        # mathtext reports missing glyphs and font substitutions through
        # logging; they are handled here and must not reach the console.
        logger.addHandler(watch)
        font_logger.addHandler(quiet)
        logger.propagate = False
        font_logger.propagate = False
        with matplotlib.rc_context(settings):       # type: ignore[attr-defined]
            marker_key = ("marker", size, fontset, family)
            marker_width = _mathtext_state.get(marker_key)      # type: ignore[call-overload]
            if marker_width is None:
                bounds = _mathtext_raster(parser, "\\mathrm{x}", size, fontset).getbbox()
                marker_width = (bounds[2] - bounds[0]) if bounds else max(1, size // 2)
                _mathtext_state[marker_key] = marker_width      # type: ignore[index]
            for _attempt in range(8):
                try:
                    mask = _mathtext_raster(parser, current + _MARKER, size, fontset)
                    break
                except Exception as exc:
                    unknown = re.search(r"Unknown symbol: (\\[A-Za-z]+)", str(exc))
                    if unknown is None:
                        message = str(exc).strip()
                        raise _MathError(message.splitlines()[-1] if message else repr(exc)) from exc
                    name = unknown.group(1)
                    replaced = re.sub(re.escape(name) + r"(?![A-Za-z])",
                                      lambda _m: "\\operatorname{" + name[1:] + "}", current)
                    if replaced == current:
                        raise _MathError(f"unknown symbol {name}")
                    current = replaced
            else:
                raise _MathError("too many unknown symbols")
    except _MathError:
        raise
    except Exception as exc:
        raise _MathError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        logger.removeHandler(watch)
        font_logger.removeHandler(quiet)
        logger.propagate, font_logger.propagate = previous
    if watch.missing:
        raise _MathError("the math font lacks a glyph")
    everything = mask.getbbox()
    if everything is None:
        raise _MathError("mathtext drew nothing")
    # The marker is the last thing on the line, one quad after the expression.
    cut = max(0, everything[2] - int(marker_width) - max(1, size // 2))
    marker = mask.crop((cut, 0, mask.width, mask.height)).point(lambda value: 255 if value >= 64 else 0).getbbox()
    if marker is None:
        raise _MathError("the baseline marker is missing")
    baseline = marker[3]
    body = mask.crop((0, 0, cut, mask.height))
    bounds = body.getbbox()
    if bounds is None:
        return _empty_box(max(1, cut - size // 2), max(1, size // 2), 0)
    left, top, right, bottom = bounds
    top = min(top, baseline)
    bottom = max(bottom, baseline)
    if bottom <= top:
        bottom = top + 1
    return _Box(body.crop((left, top, right, bottom)), baseline - top)


def _math_segment(latex: str, size: int, cfg: RenderConfig, plan: _FontPlan, display: bool) -> _Box:
    attempts = []
    for candidate in (
        _normalize(latex, display=display),
        latex.strip(),
        _normalize(latex, display=False, strip_delimiters=True),
    ):
        if candidate not in attempts:
            attempts.append(candidate)
    error: Optional[Exception] = None
    for candidate in attempts:
        try:
            return _mathtext_box(candidate, size, cfg.math_fontset, plan)
        except _MathError as exc:
            error = exc
    raise _MathError(str(error))


def _strip_outer(tokens: list[str]) -> list[str]:
    start, end = 0, len(tokens)
    while start < end and tokens[start].isspace():
        start += 1
    while end > start and tokens[end - 1].isspace():
        end -= 1
    return tokens[start:end]


def _find_environment(tokens: Sequence[str], start: int) -> Optional[tuple[str, int, int, int]]:
    """For tokens[start] == "\\begin": (name, body start, body end, index after \\end{name})."""
    name_tokens, body_start = _argument(tokens, start + 1)
    name = "".join(name_tokens).strip()
    depth = 1
    index = body_start
    while index < len(tokens):
        if tokens[index] == "\\begin":
            depth += 1
        elif tokens[index] == "\\end":
            depth -= 1
            if depth == 0:
                _end_name, after = _argument(tokens, index + 1)
                return name, body_start, index, after
        index += 1
    return None


def _segments(tokens: Sequence[str]) -> list[tuple]:
    """Split a formula into pieces that are typeset in different ways.

    ("math", tokens), ("grid", name, body, left, right), ("rows", body),
    ("boxed", tokens) and ("text", string, bold).
    """
    segments: list[tuple] = []
    pending: list[str] = []
    depth = 0
    index = 0
    count = len(tokens)

    def flush() -> None:
        if any(not token.isspace() for token in pending):
            segments.append(("math", list(pending)))
        pending.clear()

    while index < count:
        token = tokens[index]
        if token == "{":
            end = _group_end(tokens, index)
            pending.extend(tokens[index:end])
            index = end
            continue
        if token == "\\begin" and depth == 0:
            found = _find_environment(tokens, index)
            if found is not None:
                name, body_start, body_end, after = found
                body = list(tokens[body_start:body_end])
                left = right = None
                # \left( \begin{array}...\end{array} \right) : the delimiters belong to the grid.
                tail = len(pending)
                while tail > 0 and pending[tail - 1].isspace():
                    tail -= 1
                if tail >= 2 and pending[tail - 2] == "\\left" and pending[tail - 1] in _DELIMITERS:
                    probe = after
                    while probe < count and tokens[probe].isspace():
                        probe += 1
                    if probe + 1 < count and tokens[probe] == "\\right" and tokens[probe + 1] in _DELIMITERS:
                        left = _DELIMITERS[pending[tail - 1]]
                        right = _DELIMITERS[tokens[probe + 1]]
                        del pending[tail - 2:]
                        after = probe + 2
                flush()
                if name in _GRID_ENVIRONMENTS or left is not None or right is not None:
                    segments.append(("grid", name, body, left, right))
                elif name in _ROW_ENVIRONMENTS:
                    segments.append(("rows", name, body))
                else:
                    segments.append(("math", body))
                index = after
                continue
        if token in ("\\boxed", "\\fbox") and depth == 0:
            inner, after = _argument(tokens, index + 1)
            flush()
            segments.append(("boxed", inner))
            index = after
            continue
        if token in _TEXT_COMMANDS and depth == 0:
            inner, after = _argument(tokens, index + 1)
            text = _plain_text(inner)
            if _has_cjk(text):
                flush()
                segments.append(("text", text, token == "\\textbf"))
                index = after
                continue
        if depth == 0 and len(token) == 1 and _is_cjk(token):
            end = index
            while end < count and len(tokens[end]) == 1 and _is_cjk(tokens[end]):
                end += 1
            flush()
            segments.append(("text", "".join(tokens[index:end]), False))
            index = end
            continue
        pending.append(token)
        index += 1
    flush()
    return segments


def _formula(latex: str, size: int, cfg: RenderConfig, plan: _FontPlan, display: bool = False) -> _Box:
    """Typeset a formula, environments included; raises _MathError."""
    key = (latex, size, cfg.math_fontset, plan, display)
    with _math_lock:
        cached = _math_cache.get(key)
        if cached is None:
            try:
                cached = _formula_tokens(_tokens(latex), size, cfg, plan, display, 0)
            except _MathError as exc:
                cached = exc
            except RecursionError:
                cached = _MathError("formula is nested too deeply")
            except Exception as exc:        # a bug here must not take the answer down
                log.debug("formula %r failed", latex, exc_info=True)
                cached = _MathError(f"{type(exc).__name__}: {exc}")
            if len(_math_cache) > 2048:
                _math_cache.clear()
            _math_cache[key] = cached
    if isinstance(cached, Exception):
        raise _MathError(str(cached))
    return cached       # type: ignore[return-value]


def _formula_tokens(tokens: Sequence[str], size: int, cfg: RenderConfig, plan: _FontPlan,
                    display: bool, level: int) -> _Box:
    if level > 6:
        raise _MathError("formula is nested too deeply")
    tokens = _strip_outer(list(tokens))
    segments = _segments(tokens)
    if not segments:
        return _empty_box(1, max(1, size // 2), 0)
    if len(segments) == 1 and segments[0][0] == "math" and "\\\\" in segments[0][1]:
        rows = [row for row in _split_top(segments[0][1], ("\\\\",)) if any(not t.isspace() for t in row)]
        if len(rows) > 1:
            return _grid(rows_of_cells=[_cells(row) for row in rows], size=size, cfg=cfg, plan=plan,
                         level=level, alignment="rl", gap=0.0, left="", right="", display=display)
    boxes: list[_Box] = []
    gaps: list[int] = []
    narrow = max(1, round(size * 0.15))
    wide = max(3, round(size * 0.4))
    for position, segment in enumerate(segments):
        kind = segment[0]
        if kind == "math" and boxes and segments[position - 1][0] != "math":
            # "\\end{pmatrix}^T": scripts that belong to the box before them.
            rest = _strip_outer(list(segment[1]))
            scripts: dict[str, _Box] = {}
            index = 0
            while index < len(rest) and rest[index] in ("^", "_") and rest[index] not in scripts:
                argument, after = _argument(rest, index + 1)
                small = max(6, round(size * 0.7))
                scripts[rest[index]] = _formula_tokens(argument, small, cfg, plan, False, level + 1)
                index = after
                while index < len(rest) and rest[index].isspace():
                    index += 1
            if scripts:
                boxes[-1] = _attach_scripts(boxes[-1], scripts.get("^"), scripts.get("_"))
                segment = ("math", rest[index:])
                if not any(not token.isspace() for token in segment[1]):
                    continue
        if kind == "math":
            words = [token for token in segment[1] if not token.isspace()]
            source = "".join(segment[1]).strip()
            # Boxes are cropped to their ink: an operator next to another
            # segment gets its space back as a gap.
            leading = bool(words) and (words[0] in _RELATIONS or words[0] in _BINARY)
            trailing = bool(words) and (words[-1] in _RELATIONS or words[-1] in _BINARY)
            if position > 0:
                if leading:
                    source = "{}" + source
                    gaps[-1] = wide
                elif words and words[0] in (",", ".", ";", ":", ")", "]", "^", "_", "'", "!"):
                    gaps[-1] = 1
                elif words and words[0] in ("\\quad", "\\qquad"):
                    gaps[-1] = size if words[0] == "\\quad" else 2 * size
            boxes.append(_math_segment(source, size, cfg, plan, display))
            gaps.append(wide if trailing else narrow)
            if words and words[-1] in ("\\quad", "\\qquad", "\\,", "\\;", "\\:", "\\ ", "~"):
                gaps[-1] = {"\\quad": size, "\\qquad": 2 * size}.get(words[-1], wide)
            continue
        gaps.append(narrow)
        if kind == "text":
            chain = _chain(plan, "bold" if segment[2] else "regular", size)
            boxes.append(_text_box(segment[1], chain))
        elif kind == "boxed":
            inner = _formula_tokens(segment[1], size, cfg, plan, display, level + 1)
            boxes.append(_boxed(inner))
        elif kind == "rows":
            body = segment[2]
            rows = [row for row in _split_top(_strip_environment_arguments(segment[1], body), ("\\\\",))
                    if any(not t.isspace() for t in row)]
            boxes.append(_grid([_cells(row) for row in rows], size, cfg, plan, level, "rl", 0.0, "", "", display))
        else:
            _kind, name, body, left, right = segment
            default_left, default_right, alignment, column_gap = _GRID_ENVIRONMENTS.get(name, ("", "", "rl", 0.0))
            if name in _ROW_ENVIRONMENTS:
                alignment, column_gap = "rl", 0.0
            body = _strip_environment_arguments(name, body)
            rows = [row for row in _split_top(body, ("\\\\",)) if any(not t.isspace() for t in row)]
            boxes.append(_grid(
                [_cells(row) for row in rows], size, cfg, plan, level, alignment, column_gap,
                default_left if left is None else left, default_right if right is None else right,
                display and name in _ROW_ENVIRONMENTS,
            ))
    return _hcat(boxes, gaps)


def _strip_environment_arguments(name: str, body: Sequence[str]) -> list[str]:
    """Drop the column specification of array-like environments and \\hline."""
    body = list(body)
    index = 0
    while index < len(body) and body[index].isspace():
        index += 1
    if name in ("array", "subarray", "alignat", "alignat*", "alignedat", "tabular"):
        index = _optional_argument(body, index)
        if index < len(body) and body[index] == "{":
            index = _group_end(body, index)
    body = body[index:]
    return [token for token in body if token not in ("\\hline", "\\nonumber", "\\notag")]


def _cells(row: Sequence[str]) -> list[list[str]]:
    return [_strip_outer(cell) for cell in _split_top(row, ("&",))]


def _grid(rows_of_cells: list[list[list[str]]], size: int, cfg: RenderConfig, plan: _FontPlan, level: int,
          alignment: str, gap: float, left: str, right: str, display: bool = False) -> _Box:
    """Lay cells out in rows and columns, with delimiters drawn around them."""
    rows_of_cells = [row for row in rows_of_cells if row]
    if not rows_of_cells:
        return _empty_box(1, max(1, size // 2), 0)
    chain = _chain(plan, "regular", size)
    columns = max(len(row) for row in rows_of_cells)
    aligned = alignment == "rl"
    boxes: list[list[Optional[_Box]]] = []
    for row in rows_of_cells:
        rendered: list[Optional[_Box]] = []
        for column, cell in enumerate(row):
            if not cell:
                rendered.append(None)
                continue
            source = list(cell)
            text = "".join(source).strip()
            # "x &= 1": the relation keeps its spacing although its left side is in another cell.
            if aligned and column % 2 == 1 and (text[:1] in "=+-<>" or text.startswith(tuple(_RELATIONS))):
                source = ["{", "}"] + source
            rendered.append(_formula_tokens(source, size, cfg, plan, display, level + 1))
        rendered += [None] * (columns - len(rendered))
        boxes.append(rendered)
    widths = [max((row[column].width for row in boxes if row[column] is not None), default=0)
              for column in range(columns)]
    column_gap = max(0, round(gap * size))
    pair_gap = round(size * 1.2)
    # Boxes are cropped to their ink, so the space around the relation at an
    # alignment point ("x &= 1") has to be added back.
    relation_gap = max(2, round(size * 0.3))
    ascents = [max([chain.x_height] + [box.ascent for box in row if box is not None]) for row in boxes]
    descents = [max([0] + [box.descent for box in row if box is not None]) for row in boxes]
    row_gap = max(3, round(size * 0.35))
    inner_height = sum(ascents) + sum(descents) + row_gap * (len(boxes) - 1)
    offsets = []
    x = 0
    for column in range(columns):
        offsets.append(x)
        x += widths[column]
        if column < columns - 1:
            if aligned:
                x += pair_gap if column % 2 == 1 else relation_gap
            else:
                x += column_gap
    inner_width = max(1, x)
    pad = 2 if (left or right) else 0
    height = inner_height + 2 * pad
    left_mask = _delimiter(left, height, size) if left else None
    right_mask = _delimiter(right, height, size) if right else None
    left_width = left_mask.width + 2 if left_mask is not None else 0
    right_width = right_mask.width + 2 if right_mask is not None else 0
    mask = Image.new("L", (left_width + inner_width + right_width, height), 0)
    if left_mask is not None:
        mask.paste(left_mask, (0, 0))
    if right_mask is not None:
        mask.paste(right_mask, (left_width + inner_width + 2, 0))
    y = pad
    for row, up, down in zip(boxes, ascents, descents):
        for column, box in enumerate(row):
            if box is None:
                continue
            if aligned:
                mode = "r" if column % 2 == 0 else "l"
            else:
                mode = alignment[column] if column < len(alignment) else alignment[-1]
            if mode == "r":
                dx = widths[column] - box.width
            elif mode == "c":
                dx = (widths[column] - box.width) // 2
            else:
                dx = 0
            mask.paste(box.mask, (left_width + offsets[column] + dx, y + up - box.ascent))
        y += up + down + row_gap
    if len(boxes) == 1:
        return _Box(mask, pad + ascents[0])
    # Several rows are centered on the math axis.
    axis = max(1, round(chain.x_height * 0.5))
    return _Box(mask, min(height, height // 2 + axis))


def _delimiter(kind: str, height: int, size: int) -> Image.Image:
    """A tall bracket drawn with PIL (mathtext cannot stretch one around a grid)."""
    scale = 4
    stroke = max(1.0, size / 11.0)
    width = {"(": 0.42, ")": 0.42, "{": 0.5, "}": 0.5, "[": 0.32, "]": 0.32, "<": 0.4, ">": 0.4,
             "|": 0.12, "‖": 0.36}.get(kind, 0.3)
    width = max(3, round(size * width))
    if kind in ("|", "‖"):
        width = max(1, round(stroke)) if kind == "|" else max(3, round(stroke) * 2 + 2)
    big = Image.new("L", (width * scale, height * scale), 0)
    draw = ImageDraw.Draw(big)
    w, h = width * scale, height * scale
    line = max(scale, round(stroke * scale))
    inset = line / 2
    mirrored = kind in (")", "}", "]", ">")
    if kind in ("(", ")"):
        points = []
        for step in range(33):
            t = step / 32
            y = inset + (h - 2 * inset) * t
            bulge = math.sin(math.pi * t) ** 0.8
            points.append((w - inset - (w - 2 * inset) * bulge, y))
        draw.line(points, fill=255, width=line, joint="curve")
    elif kind in ("[", "]"):
        draw.line([(w - inset, inset), (inset, inset), (inset, h - inset), (w - inset, h - inset)],
                  fill=255, width=line, joint="curve")
    elif kind in ("<", ">"):
        draw.line([(w - inset, inset), (inset, h / 2), (w - inset, h - inset)], fill=255, width=line, joint="curve")
    elif kind in ("{", "}"):
        middle = w / 2
        radius = min(h / 4, w * 0.9)
        points = []

        def curve(p0, p1, p2):
            for step in range(13):
                t = step / 12
                points.append((
                    (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
                    (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1],
                ))

        curve((w - inset, inset), (middle, inset), (middle, inset + radius))
        curve((middle, h / 2 - radius), (middle, h / 2), (inset, h / 2))
        curve((inset, h / 2), (middle, h / 2), (middle, h / 2 + radius))
        curve((middle, h - inset - radius), (middle, h - inset), (w - inset, h - inset))
        draw.line(points, fill=255, width=line, joint="curve")
    elif kind == "|":
        draw.rectangle((0, 0, w - 1, h - 1), fill=255)
    elif kind == "‖":
        bar = max(scale, round(stroke) * scale)
        draw.rectangle((0, 0, bar - 1, h - 1), fill=255)
        draw.rectangle((w - bar, 0, w - 1, h - 1), fill=255)
    if mirrored:
        big = big.transpose(Image.FLIP_LEFT_RIGHT)
    return big.resize((width, height), Image.LANCZOS)


def _boxed(inner: _Box) -> _Box:
    pad = 3
    mask = Image.new("L", (inner.width + 2 * pad + 2, inner.height + 2 * pad + 2), 0)
    draw = ImageDraw.Draw(mask)
    draw.rectangle((0, 0, mask.width - 1, mask.height - 1), outline=255, width=1)
    mask.paste(inner.mask, (pad + 1, pad + 1))
    return _Box(mask, inner.ascent + pad + 1)


def _break_pieces(tokens: Sequence[str], operators: frozenset) -> list[list[str]]:
    """Cut a formula before top-level operators; every later piece starts with one."""
    pieces: list[list[str]] = [[]]
    depth = 0
    paired = 0
    previous = ""
    index = 0
    count = len(tokens)
    while index < count:
        token = tokens[index]
        if token == "{":
            end = _group_end(tokens, index)
            pieces[-1].extend(tokens[index:end])
            previous = "}"
            index = end
            continue
        if token in ("\\left", "\\begin"):
            paired += 1
        elif token in ("\\right", "\\end"):
            paired = max(0, paired - 1)
        elif token in ("(", "["):
            depth += 1
        elif token in (")", "]"):
            depth = max(0, depth - 1)
        unary = previous in ("", "^", "_", "(", "[", ",", "&", "\\left") or previous in _RELATIONS \
            or previous in _BINARY or previous in ("\\cdot", "\\times", "\\div")
        if token in operators and depth == 0 and paired == 0 and not unary \
                and any(not t.isspace() for t in pieces[-1]):
            pieces.append([])
        pieces[-1].append(token)
        if not token.isspace():
            previous = token
        index += 1
    return [piece for piece in pieces if any(not t.isspace() for t in piece)]


@dataclass
class _Placed:
    """A box and its horizontal offset inside the available width."""

    box: _Box
    x: int


def _fit_size(latex: str, width: int, sizes: Iterable[int], cfg: RenderConfig, plan: _FontPlan,
              display: bool) -> Optional[_Box]:
    for size in sizes:
        box = _formula(latex, size, cfg, plan, display)
        if box.width <= width:
            return box
    return None


def _math_sizes(size: int) -> tuple[list[int], list[int]]:
    """Font sizes to try before and after attempting to break a formula."""
    floor = max(5, min(size, math.ceil(size * MIN_MATH_SCALE)))
    soft = max(floor, min(size, math.ceil(size * SOFT_MATH_SCALE)))
    return list(range(size, soft - 1, -1)), list(range(soft - 1, floor - 1, -1))


def _fit_row(latex: str, width: int, size: int, cfg: RenderConfig, plan: _FontPlan) -> list[_Placed]:
    """One row of display math as one or more lines that fit `width`.

    In order of preference: shrink a little (to SOFT_MATH_SCALE), break at
    top-level relations, break at top-level "+" and "-", shrink down to
    MIN_MATH_SCALE, and finally scale the rendered formula.
    """
    first, last = _math_sizes(size)
    box = _fit_size(latex, width, first, cfg, plan, True)
    if box is not None:
        return [_Placed(box, max(0, (width - box.width) // 2))]
    indent = max(4, size)

    def measure(source: str) -> int:
        try:
            return _formula(source, size, cfg, plan, True).width
        except _MathError:
            return 0            # pieces that only parse together stay together

    def joined(pieces: Sequence[Sequence[str]]) -> str:
        text = " ".join("".join(piece).strip() for piece in pieces)
        return "{}" + text if pieces and pieces[0] and _leading_operator(pieces[0]) else text

    # Relations start a new group; a group that fits a line is never split.
    groups: list[list[list[str]]] = []
    for piece in _break_pieces(_tokens(latex), _RELATIONS | _BINARY):
        if not groups or _leading_operator(piece) in _RELATIONS:
            groups.append([piece])
        else:
            groups[-1].append(piece)
    lines: list[list[list[str]]] = []
    current: list[list[str]] = []

    def limit_for(pieces: Sequence[Sequence[str]]) -> int:
        if not lines and (not current or pieces is current):
            return width
        operator = _leading_operator(pieces[0]) if pieces else ""
        return max(size, width - indent * (1 if operator in _RELATIONS else 2))

    for group in groups:
        whole = measure(joined(group)) <= max(size, width - indent)
        for unit in ([group] if whole else [[piece] for piece in group]):
            if not current:
                current = list(unit)
                continue
            candidate = current + list(unit)
            if measure(joined(candidate)) <= limit_for(current):
                current = candidate
            else:
                lines.append(current)
                current = list(unit)
    if current:
        lines.append(current)

    placed: list[tuple[_Box, int]] = []
    for number, pieces in enumerate(lines):
        operator = _leading_operator(pieces[0])
        level = 0 if number == 0 else 1 if operator in _RELATIONS else 2
        limit = max(size, width - level * indent)
        source = joined(pieces)
        try:
            fitted = _fit_size(source, limit, first + last, cfg, plan, True)
            if fitted is None:
                fitted = _squeeze(_formula(source, (last or first)[-1], cfg, plan, True), limit)
        except _MathError:
            if len(lines) == 1:
                raise
            # A piece that does not parse on its own: give up on breaking.
            lines = []
            break
        placed.append((fitted, level))
    if not lines:
        fitted = _fit_size(latex, width, last, cfg, plan, True)
        if fitted is None:
            fitted = _squeeze(_formula(latex, (last or first)[-1], cfg, plan, True), width)
        placed = [(fitted, 0)]
    if len(placed) == 1:
        box = placed[0][0]
        return [_Placed(box, max(0, (width - box.width) // 2))]
    span = max(box.width + level * indent for box, level in placed)
    origin = max(0, (width - span) // 2)
    return [_Placed(box, min(origin + level * indent, max(0, width - box.width))) for box, level in placed]


def _leading_operator(piece: Sequence[str]) -> str:
    for token in piece:
        if not token.isspace():
            return token if token in _RELATIONS or token in _BINARY else ""
    return ""


def _display_math(latex: str, width: int, size: int, cfg: RenderConfig, plan: _FontPlan) -> list[_Placed]:
    """Lines of a display formula, each at most `width` wide; raises _MathError."""
    latex = latex.strip()
    first, _last = _math_sizes(size)
    whole = _fit_size(latex, width, first, cfg, plan, True)
    if whole is not None:
        return [_Placed(whole, max(0, (width - whole.width) // 2))]
    # Too wide as a whole: rows of an environment (or of "\\") are fitted one by one.
    tokens = _strip_outer(_tokens(latex))
    segments = _segments(tokens)
    rows: list[list[str]] = []
    if len(segments) == 1 and segments[0][0] == "rows":
        body = _strip_environment_arguments(segments[0][1], segments[0][2])
        rows = _split_top(body, ("\\\\",))
    elif len(segments) == 1 and segments[0][0] == "math":
        rows = _split_top(segments[0][1], ("\\\\",))
    rows = [row for row in rows if any(not token.isspace() for token in row)]
    if len(rows) < 2:
        return _fit_row(latex, width, size, cfg, plan)
    placed: list[_Placed] = []
    for row in rows:
        source = "".join(" " if token == "&" else token for token in row).strip()
        placed.extend(_fit_row(source, width, size, cfg, plan))
    return placed


def _inline_math(latex: str, width: int, size: int, cfg: RenderConfig, plan: _FontPlan) -> _Box:
    """An inline formula, shrunk when it is wider than a whole line; raises _MathError."""
    first, last = _math_sizes(size)
    box = _fit_size(latex, width, first + last, cfg, plan, False)
    if box is None:
        box = _squeeze(_formula(latex, (last or first)[-1], cfg, plan, False), width)
    return box


# ==========================================================================
# Inline content
# ==========================================================================

@dataclass(frozen=True)
class _Style:
    bold: bool = False
    italic: bool = False
    code: bool = False
    strike: bool = False


@dataclass
class _Span:
    kind: str               # "text", "code", "math", "display" or "break"
    text: str = ""
    style: _Style = _Style()


_EMPHASIS = re.compile(
    r"(?P<bi>\*\*\*(?=\S)(?P<bi_text>.+?)(?<=\S)\*\*\*)"
    r"|(?P<b>\*\*(?=\S)(?P<b_text>.+?)(?<=\S)\*\*)"
    r"|(?P<u>(?<![\w\\])__(?=\S)(?P<u_text>.+?)(?<=\S)__(?!\w))"
    r"|(?P<s>~~(?=\S)(?P<s_text>.+?)(?<=\S)~~)"
    r"|(?P<i>(?<![*\\])\*(?=[^\s*])(?P<i_text>[^*\n]+?)(?<=[^\s*])\*(?!\*))"
    r"|(?P<e>(?<![\w\\])_(?=[^\s_])(?P<e_text>[^_\n]+?)(?<=[^\s_])_(?!\w))",
    re.S,
)
_LINK = re.compile(r"(!?)\[([^\]\n]*)\]\(\s*<?([^)\s>]*)>?(?:\s+\"[^\"]*\")?\s*\)")
_AUTOLINK = re.compile(r"<((?:https?|mailto|ftp):[^>\s]+)>")
_BREAK_TAG = re.compile(r"<br\s*/?>", re.I)
_MARK = re.compile(_MARK_OPEN + r"(\d+)" + _MARK_CLOSE)
_ENTITY = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]{1,15}|#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6});")
_SPACED_MATH = re.compile(r"[\\^_={}]|\d\s*[-+*/<>]|[-+*/<>]\s*\d")
_ESCAPABLE = frozenset("\\`*_{}[]()#+-.!|~<>$&%^=\"'/:;,?@")
_MAX_INLINE_MATH = 600


def _find_closing_dollar(text: str, start: int) -> int:
    """Index of the "$" that closes inline math opened before `start`, or -1."""
    index = start
    limit = min(len(text), start + _MAX_INLINE_MATH)
    while index < limit:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "\n" and text[index + 1:index + 2] == "\n":
            return -1
        if char == "$":
            if text[index - 1] in " \t\n" or index == start:
                return -1
            if text[index + 1:index + 2].isdigit():
                return -1
            return index
        index += 1
    return -1


def _protect(text: str, markdown: bool) -> tuple[str, list[_Span]]:
    """Replace code, math and escapes by markers so that emphasis cannot touch them."""
    spans: list[_Span] = []
    out: list[str] = []

    def mark(span: _Span) -> None:
        spans.append(span)
        out.append(f"{_MARK_OPEN}{len(spans) - 1}{_MARK_CLOSE}")

    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\\" and index + 1 < length:
            following = text[index + 1]
            if following in "([":
                closing = "\\)" if following == "(" else "\\]"
                end = text.find(closing, index + 2)
                if end != -1 and text[index + 2:end].strip():
                    mark(_Span("math" if following == "(" else "display", text[index + 2:end].strip()))
                    index = end + 2
                    continue
            if markdown and following in _ESCAPABLE:
                mark(_Span("text", following))
                index += 2
                continue
            if markdown and following == "\n":
                mark(_Span("break"))
                index += 2
                continue
            out.append(char)
            index += 1
        elif char == "`" and markdown:
            run = 1
            while index + run < length and text[index + run] == "`":
                run += 1
            fence = "`" * run
            end = text.find(fence, index + run)
            while end != -1 and text[end + run:end + run + 1] == "`":
                # A longer run of backticks does not close this span.
                skip = end
                while skip < length and text[skip] == "`":
                    skip += 1
                end = text.find(fence, skip)
            if end == -1:
                out.append(fence)
                index += run
                continue
            code = text[index + run:end].replace("\n", " ")
            if len(code) > 1 and code[0] == " " and code[-1] == " " and code.strip():
                code = code[1:-1]
            mark(_Span("code", code))
            index = end + run
        elif char == "$":
            if text.startswith("$$", index):
                end = text.find("$$", index + 2)
                if end != -1 and text[index + 2:end].strip():
                    mark(_Span("display", text[index + 2:end].strip()))
                    index = end + 2
                    continue
                out.append("$$")
                index += 2
                continue
            if index + 1 < length and text[index + 1] not in " \t\n":
                end = _find_closing_dollar(text, index + 1)
                if end != -1:
                    mark(_Span("math", text[index + 1:end].strip()))
                    index = end + 1
                    continue
            elif index + 1 < length and text[index + 1] in " \t":
                # "$ x^2 $": accepted when the content can only be a formula.
                end = text.find("$", index + 1, index + _MAX_INLINE_MATH)
                inner = text[index + 1:end] if end != -1 else ""
                if inner.strip() and "\n" not in inner and _SPACED_MATH.search(inner) \
                        and not text[end + 1:end + 2].isdigit():
                    mark(_Span("math", inner.strip()))
                    index = end + 1
                    continue
            out.append(char)
            index += 1
        elif char == "\n":
            mark(_Span("break"))
            index += 1
        elif char == "<" and markdown:
            tag = _BREAK_TAG.match(text, index)
            if tag:
                mark(_Span("break"))
                index = tag.end()
                continue
            link = _AUTOLINK.match(text, index)
            if link:
                mark(_Span("text", link.group(1)))
                index = link.end()
                continue
            out.append(char)
            index += 1
        else:
            out.append(char)
            index += 1
    return "".join(out), spans


def _link_text(match: "re.Match[str]") -> str:
    image, label, target = match.group(1), match.group(2), match.group(3)
    if image:
        return f"[{label or 'image'}]"
    return label or target


def _emphasis(text: str, style: _Style, out: list[tuple[str, _Style]], depth: int = 0) -> None:
    position = 0
    while position < len(text):
        match = _EMPHASIS.search(text, position) if depth < 8 else None
        if match is None:
            out.append((text[position:], style))
            return
        if match.start() > position:
            out.append((text[position:match.start()], style))
        kind = next(name for name in ("bi", "b", "u", "s", "i", "e") if match.group(name) is not None)
        inner = match.group(f"{kind}_text")
        if kind == "bi":
            nested = _Style(True, True, style.code, style.strike)
        elif kind in ("b", "u"):
            nested = _Style(True, style.italic, style.code, style.strike)
        elif kind == "s":
            nested = _Style(style.bold, style.italic, style.code, True)
        else:
            nested = _Style(style.bold, True, style.code, style.strike)
        _emphasis(inner, nested, out, depth + 1)
        position = match.end()


def _parse_inline(text: str, markdown: bool = True, style: _Style = _Style()) -> list[_Span]:
    protected, spans = _protect(text, markdown)
    pieces: list[tuple[str, _Style]] = []
    if markdown:
        protected = _LINK.sub(_link_text, protected)
        protected = _ENTITY.sub(lambda match: _clean(html.unescape(match.group(0))), protected)
        _emphasis(protected, style, pieces)
    else:
        pieces.append((protected, style))
    out: list[_Span] = []
    for piece, piece_style in pieces:
        position = 0
        for match in _MARK.finditer(piece):
            if match.start() > position:
                out.append(_Span("text", piece[position:match.start()], piece_style))
            number = int(match.group(1))
            if number < len(spans):
                span = spans[number]
                if span.kind == "code":
                    out.append(_Span("code", span.text, _Style(piece_style.bold, False, True, piece_style.strike)))
                else:
                    out.append(_Span(span.kind, span.text, piece_style))
            position = match.end()
        if position < len(piece):
            out.append(_Span("text", piece[position:], piece_style))
    return out


# ==========================================================================
# Line breaking
# ==========================================================================

@dataclass
class _Atom:
    kind: str                           # "text", "space", "box" or "break"
    width: float = 0.0
    ascent: int = 0
    descent: int = 0
    text: str = ""
    runs: list = field(default_factory=list)       # (face, text) pairs
    chain: Optional[_Chain] = None
    box: Optional[_Box] = None
    cjk: bool = False
    no_break_before: bool = False       # closing punctuation
    hard_no_break_before: bool = False  # ... of the CJK kind: not even after a space
    no_break_after: bool = False        # opening punctuation
    chunk: bool = False                 # a later part of a word that was too long for a line
    ink: int = INK
    background: Optional[int] = None
    strike: bool = False


def _word_atom(text: str, chain: _Chain, cjk: bool, template: Optional[_Atom] = None) -> _Atom:
    runs = chain.runs(text)
    shown = "".join(run for _face, run in runs)
    atom = _Atom(
        "text",
        width=sum(face.length(run) for face, run in runs),
        ascent=chain.ascent,
        descent=chain.descent,
        text=shown,
        runs=runs,
        chain=chain,
        cjk=cjk,
        no_break_before=text[:1] in _CLOSING,
        hard_no_break_before=text[:1] in _CLOSING_CJK,
        no_break_after=text[-1:] in _OPENING,
    )
    if template is not None:
        atom.ink, atom.background, atom.strike = template.ink, template.background, template.strike
    return atom


def _text_atoms(text: str, chain: _Chain, ink: int = INK, background: Optional[int] = None,
                strike: bool = False) -> list[_Atom]:
    """Words, spaces and single CJK characters."""
    atoms: list[_Atom] = []
    word: list[str] = []
    template = _Atom("text", ink=ink, background=background, strike=strike)

    def flush() -> None:
        if word:
            atoms.append(_word_atom("".join(word), chain, False, template))
            word.clear()

    index = 0
    while index < len(text):
        char = text[index]
        if char in " \t":
            flush()
            end = index
            while end < len(text) and text[end] in " \t":
                end += 1
            count = sum(4 if c == "\t" else 1 for c in text[index:end])
            # Runs of spaces collapse, except in code where they carry meaning.
            width = chain.space * (count if background is not None else 1)
            atoms.append(_Atom("space", width=width, ascent=chain.ascent, descent=chain.descent, text=" ",
                               chain=chain, ink=ink, background=background, strike=strike))
            index = end
            continue
        if _is_cjk(char):
            flush()
            atoms.append(_word_atom(char, chain, True, template))
        else:
            word.append(char)
        index += 1
    flush()
    return atoms


def _split_atom(atom: _Atom, first: float, width: float) -> list[_Atom]:
    """Cut a word that is wider than a line into chunks (character level)."""
    chain = atom.chain
    if chain is None or atom.kind != "text" or len(atom.text) < 2:
        return [atom]
    chunks: list[_Atom] = []
    current = ""
    used = 0.0
    limit = first if first >= chain.size else width
    for char in atom.text:
        step = chain.advance(char)
        if current and used + step > limit:
            # Keep punctuation away from the ends of the chunks, if possible.
            carried = ""
            while len(current) > 1 and (char in _CLOSING or current[-1] in _OPENING) and len(carried) < 3:
                char, current, carried = current[-1], current[:-1], carried + char
            chunks.append(_word_atom(current, chain, False, atom))
            current = char + carried[::-1]
            used = sum(chain.advance(item) for item in current)
            limit = width
        else:
            current += char
            used += step
    if current:
        chunks.append(_word_atom(current, chain, False, atom))
    for chunk in chunks[1:]:
        chunk.chunk = True
        chunk.no_break_before = chunk.hard_no_break_before = False
    for chunk in chunks[:-1]:
        chunk.no_break_after = False
    return chunks


def _can_break(previous: _Atom, following: _Atom) -> bool:
    """May a line end after `previous` and start with `following`?"""
    if following.kind == "space":
        return False
    if previous.kind == "space":
        return not following.hard_no_break_before
    if following.no_break_before or previous.no_break_after:
        return False
    return previous.cjk or following.cjk or following.chunk


def _line_width(atoms: Sequence[_Atom]) -> float:
    end = len(atoms)
    while end > 0 and atoms[end - 1].kind == "space":
        end -= 1
    return sum(atom.width for atom in atoms[:end])


def _break_atoms(atoms: Sequence[_Atom], width: float, limit: Optional[int] = None) -> list[list[_Atom]]:
    """Greedy line breaking; closing punctuation never starts a line.

    With `limit`, breaking stops once that many lines exist (the rest of the
    text would be cut off by the height limit anyway).
    """
    width = max(1.0, float(width))
    lines: list[list[_Atom]] = []
    current: list[_Atom] = []
    queue = list(atoms)
    queue.reverse()
    while queue:
        atom = queue.pop()
        if limit is not None and len(lines) >= limit:
            current = []
            break
        if atom.kind == "break":
            lines.append(current)
            current = []
            continue
        if atom.kind == "space":
            if current:
                current.append(atom)
            continue
        used = sum(item.width for item in current)
        if used + atom.width <= width + 0.01:
            current.append(atom)
            continue
        if not current:
            if atom.kind == "text" and len(atom.text) > 1:
                text = atom.text
                if limit is not None and len(text) > 4096:
                    # Do not cut up megabytes of text that can never be shown.
                    room = int((limit - len(lines) + 1) * (width + 1)) + 1
                    if len(text) > room:
                        atom = _word_atom(text[:room], atom.chain, False, atom) if atom.chain else atom
                chunks = _split_atom(atom, width, width)
                if len(chunks) > 1:
                    queue.extend(reversed(chunks))
                    continue
            current.append(atom)        # a single unbreakable item: it has to overflow
            continue
        if _can_break(current[-1], atom):
            lines.append(current)
            current = []
            queue.append(atom)
            continue
        # Move the end of the line down together with the item that does not fit.
        cut = None
        for position in range(len(current) - 1, 0, -1):
            if _can_break(current[position - 1], current[position]):
                cut = position
                break
        if cut is not None:
            carried = current[cut:]
            lines.append(current[:cut])
            current = []
            queue.append(atom)
            queue.extend(reversed(carried))
            continue
        # Nothing in this line can be broken.
        remaining = width - used
        if atom.kind == "text" and len(atom.text) > 1 and not atom.no_break_before:
            chunks = _split_atom(atom, remaining, width)
            if len(chunks) > 1:
                if chunks[0].width <= remaining + 0.01:
                    current.append(chunks[0])
                    chunks = chunks[1:]
                lines.append(current)
                current = []
                queue.extend(reversed(chunks))
                continue
        if len(current) == 1 and current[0].kind == "text" and len(current[0].text) > 1 \
                and current[0].chain is not None:
            # "aaaaaaaaaaaa," : the last letter goes down with the comma, so
            # that the comma neither overflows nor starts a line.
            head = current[0]
            kept = _word_atom(head.text[:-1], head.chain, False, head)
            kept.chunk, kept.no_break_before = head.chunk, head.no_break_before
            kept.hard_no_break_before = head.hard_no_break_before
            moved = _word_atom(head.text[-1:], head.chain, False, head)
            moved.chunk = True
            moved.no_break_before = moved.hard_no_break_before = False
            lines.append([kept])
            current = []
            queue.append(atom)
            queue.append(moved)
            continue
        if atom.no_break_before and len(current) >= 1:
            # Give up the last item of the line rather than start a line with punctuation.
            carried = current[-1:]
            if len(current) > 1:
                lines.append(current[:-1])
                current = []
                queue.append(atom)
                queue.extend(reversed(carried))
                continue
            current.append(atom)
            continue
        lines.append(current)
        current = []
        queue.append(atom)
    lines.append(current)
    for line in lines:
        while line and line[-1].kind == "space":
            line.pop()
    while len(lines) > 1 and not lines[-1]:
        lines.pop()
    return lines


def wrap_text(text: str, max_width: int, cfg: Optional[RenderConfig] = None, *,
              bold: bool = False, mono: bool = False, size: Optional[int] = None) -> list[str]:
    """Break plain text into lines of at most `max_width` pixels.

    This is the line breaker used for all rendered text: Latin words break at
    spaces (words longer than a line are cut), CJK text breaks between any two
    characters, closing punctuation never starts a line and opening
    punctuation never ends one.
    """
    cfg = _sane(cfg)
    chain = _chain(_plan_for(cfg), "mono" if mono else "bold" if bold else "regular", size or cfg.font_size)
    atoms: list[_Atom] = []
    for number, line in enumerate(_clean(text).split("\n")):
        if number:
            atoms.append(_Atom("break"))
        atoms.extend(_text_atoms(line, chain))
    return ["".join(atom.text for atom in line) for line in _break_atoms(atoms, max_width)]


# ==========================================================================
# Canvas
# ==========================================================================

class _Canvas:
    """Records drawing operations until the final height is known."""

    def __init__(self, width: int):
        self.width = width
        self.operations: list[tuple] = []

    def rectangle(self, left: int, top: int, right: int, bottom: int, fill: int) -> None:
        """Fill [left, right) x [top, bottom)."""
        if right > left and bottom > top:
            self.operations.append(("rectangle", top, bottom, (left, top, right, bottom), fill))

    def outline(self, left: int, top: int, right: int, bottom: int, fill: int, radius: int = 0,
                background: Optional[int] = None) -> None:
        self.operations.append(("outline", top, bottom, (left, top, right, bottom), fill, radius, background))

    def text(self, x: float, baseline: int, text: str, face: _Face, fill: int, top: int, bottom: int) -> None:
        self.operations.append(("text", top, bottom, x, baseline, text, face, fill))

    def mask(self, x: int, y: int, mask: Image.Image, fill: int) -> None:
        self.operations.append(("mask", y, y + mask.height, x, mask, fill))

    def paint(self, height: int, cut: Optional[int] = None) -> Image.Image:
        """Draw everything; with `cut`, only what lies completely above that row."""
        image = Image.new("L", (self.width, max(1, height)), PAPER)
        draw = ImageDraw.Draw(image)
        for operation in self.operations:
            kind, top, bottom = operation[0], operation[1], operation[2]
            if cut is not None:
                if top >= cut:
                    continue
                if bottom > cut and kind != "rectangle":
                    continue
            if kind == "rectangle":
                left, top, right, bottom = operation[3]
                if cut is not None:
                    bottom = min(bottom, cut)
                draw.rectangle((left, top, right - 1, bottom - 1), fill=operation[4])
            elif kind == "outline":
                left, top, right, bottom = operation[3]
                draw.rounded_rectangle((left, top, right - 1, bottom - 1), radius=operation[5],
                                       outline=operation[4], fill=operation[6], width=1)
            elif kind == "text":
                _draw_run(draw, operation[3], operation[4], operation[5], operation[6], operation[7])
            elif kind == "mask":
                image.paste(operation[5], (operation[3], top), operation[4])
        return image


# ==========================================================================
# Markdown blocks
# ==========================================================================

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*([^`]*)$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_RULE = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_QUOTE = re.compile(r"^ {0,3}>[ \t]?(.*)$")
_ITEM = re.compile(r"^([ \t]*)(?:([-*+•])|(\d{1,9})([.)]))(?:[ \t]+(.*)|[ \t]*)$")
_TABLE_RULE = re.compile(r"^ {0,3}\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}={3,}[ \t]*$")
_MATH_OPEN = re.compile(r"^ {0,3}(\$\$|\\\[)")
_ENVIRONMENT_OPEN = re.compile(r"^ {0,3}\\begin\{([A-Za-z*]+)\}")


@dataclass
class _Block:
    kind: str
    text: str = ""
    level: int = 0
    lines: list = field(default_factory=list)
    children: list = field(default_factory=list)       # quote: blocks; list: (marker, blocks)
    ordered: bool = False
    header: list = field(default_factory=list)
    alignments: list = field(default_factory=list)
    rows: list = field(default_factory=list)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _table_cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells: list[str] = []
    current: list[str] = []
    index = 0
    code = False
    math_open = False
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line):
            if line[index + 1] == "|":
                current.append("|")
            else:
                current.append(line[index:index + 2])
            index += 2
            continue
        if char == "`":
            code = not code
        elif char == "$" and not code:
            math_open = not math_open
        if char == "|" and not code and not math_open:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    cells.append("".join(current).strip())
    return cells


def _starts_block(lines: Sequence[str], index: int) -> bool:
    line = lines[index]
    if _FENCE.match(line) or _HEADING.match(line) or _RULE.match(line) or _QUOTE.match(line):
        return True
    if _MATH_OPEN.match(line):
        return True
    item = _ITEM.match(line)
    if item and (item.group(2) or item.group(3) == "1" or _indent(line) == 0) and item.group(5):
        return True
    if "|" in line and index + 1 < len(lines) and _TABLE_RULE.match(lines[index + 1]) and "-" in lines[index + 1]:
        return True
    return False


def _parse_blocks(lines: Sequence[str], depth: int = 0) -> list[_Block]:
    blocks: list[_Block] = []
    index = 0
    count = len(lines)
    while index < count:
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)
            indent = _indent(line)
            body = []
            index += 1
            while index < count:
                closing = lines[index].strip()
                if closing.startswith(marker[0] * len(marker)) and not closing.strip(marker[0]):
                    index += 1
                    break
                body.append(lines[index][min(indent, _indent(lines[index])):])
                index += 1
            while body and not body[-1].strip():
                body.pop()
            blocks.append(_Block("code", lines=body))
            continue
        opening = _MATH_OPEN.match(line)
        if opening:
            closing = "$$" if opening.group(1) == "$$" else "\\]"
            rest = line[opening.end():]
            end = rest.find(closing)
            if end != -1 and not rest[end + len(closing):].strip(" \t.,;:。，；"):
                blocks.append(_Block("math", text=rest[:end]))
                index += 1
                continue
            if end == -1:
                body = [rest]
                probe = index + 1
                while probe < count and closing not in lines[probe]:
                    body.append(lines[probe])
                    probe += 1
                if probe < count:
                    last = lines[probe]
                    body.append(last[:last.find(closing)])
                    blocks.append(_Block("math", text="\n".join(body)))
                    trailing = last[last.find(closing) + len(closing):].strip()
                    index = probe + 1
                    if trailing.strip(" \t.,;:。，；"):
                        blocks.append(_Block("paragraph", text=trailing))
                    continue
        environment = _ENVIRONMENT_OPEN.match(line)
        if environment and environment.group(1) in _ROW_ENVIRONMENTS | set(_GRID_ENVIRONMENTS):
            closing = "\\end{" + environment.group(1) + "}"
            probe = index
            body = []
            while probe < count:
                body.append(lines[probe])
                if closing in lines[probe]:
                    break
                probe += 1
            if probe < count:
                blocks.append(_Block("math", text="\n".join(body)))
                index = probe + 1
                continue
        heading = _HEADING.match(line)
        if heading:
            blocks.append(_Block("heading", text=(heading.group(2) or "").strip(), level=len(heading.group(1))))
            index += 1
            continue
        if _RULE.match(line):
            blocks.append(_Block("rule"))
            index += 1
            continue
        if "|" in line and index + 1 < count and _TABLE_RULE.match(lines[index + 1]) and "-" in lines[index + 1]:
            header = _table_cells(line)
            rule = _table_cells(lines[index + 1])
            if len(rule) >= 1 and len(header) >= 1:
                alignments = []
                for cell in rule:
                    cell = cell.strip()
                    if cell.startswith(":") and cell.endswith(":"):
                        alignments.append("c")
                    elif cell.endswith(":"):
                        alignments.append("r")
                    else:
                        alignments.append("l")
                rows = []
                index += 2
                while index < count and lines[index].strip() and "|" in lines[index]:
                    rows.append(_table_cells(lines[index]))
                    index += 1
                blocks.append(_Block("table", header=header, alignments=alignments, rows=rows))
                continue
        if _QUOTE.match(line) and depth < 8:
            body = []
            while index < count:
                quoted = _QUOTE.match(lines[index])
                if quoted:
                    body.append(quoted.group(1))
                elif lines[index].strip() and body and body[-1].strip() and not _starts_block(lines, index):
                    body.append(lines[index])       # lazy continuation
                else:
                    break
                index += 1
            blocks.append(_Block("quote", children=_parse_blocks(body, depth + 1)))
            continue
        item = _ITEM.match(line)
        if item and depth < 8 and not (item.group(5) is None and index + 1 < count
                                       and not lines[index + 1].strip() and not item.group(3)):
            block, index = _parse_list(lines, index, depth)
            blocks.append(block)
            continue
        body = [line.strip()]
        index += 1
        while index < count and lines[index].strip() and not _starts_block(lines, index):
            if _SETEXT.match(lines[index]):
                break
            body.append(lines[index].strip())
            index += 1
        if index < count and _SETEXT.match(lines[index]):
            blocks.append(_Block("heading", text=" ".join(body), level=1))
            index += 1
            continue
        blocks.append(_Block("paragraph", text="\n".join(body)))
    return blocks


def _parse_list(lines: Sequence[str], index: int, depth: int) -> tuple[_Block, int]:
    first = _ITEM.match(lines[index])
    assert first is not None
    base = len(first.group(1).expandtabs(4))
    ordered = first.group(3) is not None
    block = _Block("list", ordered=ordered)
    count = len(lines)
    while index < count:
        line = lines[index]
        item = _ITEM.match(line)
        if item is None or (item.group(3) is not None) != ordered:
            break
        indent = len(item.group(1).expandtabs(4))
        if abs(indent - base) > 1:
            break
        marker = f"{item.group(3)}{item.group(4)}" if ordered else "•"
        content = item.group(5) or ""
        offset = len(line) - len(content) if content else indent + len(marker) + 1
        if offset - indent > 6:
            offset = indent + 2
        body = [content]
        index += 1
        while index < count:
            following = lines[index]
            if not following.strip():
                probe = index
                while probe < count and not lines[probe].strip():
                    probe += 1
                if probe < count and _indent(lines[probe]) > indent + 1:
                    body.extend([""] * (probe - index))
                    index = probe
                    continue
                break
            following_indent = _indent(following)
            if following_indent <= indent + 1:
                break
            body.append(following[min(offset, following_indent):])
            index += 1
        block.children.append((marker, _parse_blocks(body, depth + 1)))
        # Blank lines between the items of one list.
        probe = index
        while probe < count and not lines[probe].strip():
            probe += 1
        if probe < count and probe != index:
            following_item = _ITEM.match(lines[probe])
            if following_item is not None and (following_item.group(3) is not None) == ordered \
                    and abs(len(following_item.group(1).expandtabs(4)) - base) <= 1:
                index = probe
    return block, index


# ==========================================================================
# Layout
# ==========================================================================

class _Layout:
    def __init__(self, cfg: RenderConfig, top: int = 2):
        self.cfg = cfg
        self.plan = _plan_for(cfg)
        self.canvas = _Canvas(cfg.width)
        self.y = top
        self.stops = [top]
        self.full = False
        self.limit = cfg.max_height + 4 * cfg.font_size + 64

    # -- helpers -----------------------------------------------------------

    def chain(self, style: str, size: Optional[int] = None) -> _Chain:
        return _chain(self.plan, style, size or self.cfg.font_size)

    def mark(self) -> None:
        self.stops.append(self.y)
        if self.y > self.limit:
            self.full = True

    def skip(self, rows: int) -> None:
        self.y += max(0, rows)

    def atoms(self, spans: Sequence[_Span], width: int, size: Optional[int] = None,
              bold: bool = False, ink: int = INK, mono: bool = False, promote: bool = True) -> list[_Atom]:
        """Inline spans as atoms; display math stays in the list as a marker atom.

        With `promote`, inline math that is wider than a line becomes display
        math, which can be broken at operators; otherwise it is scaled down.
        """
        size = size or self.cfg.font_size
        atoms: list[_Atom] = []
        for span in spans:
            style = span.style
            if span.kind == "break":
                atoms.append(_Atom("break"))
            elif span.kind == "text":
                name = "mono" if mono else "bold" if (bold or style.bold) else "regular"
                atoms.extend(_text_atoms(span.text, self.chain(name, size), ink, None, style.strike))
            elif span.kind == "code":
                chain = self.chain("mono", max(6, size - 1))
                code = _text_atoms(span.text, chain, ink, CODE_BACKGROUND, style.strike)
                if code:
                    # Two pixels of background on either side of the code.
                    atoms.append(_Atom("text", width=2, ascent=chain.ascent, descent=chain.descent, chain=chain,
                                       background=CODE_BACKGROUND, no_break_after=True))
                    atoms.extend(code)
                    atoms.append(_Atom("text", width=2, ascent=chain.ascent, descent=chain.descent, chain=chain,
                                       background=CODE_BACKGROUND, no_break_before=True))
            elif span.kind == "math":
                atoms.extend(self.math_atoms(span.text, width, size, ink, promote))
            elif span.kind == "display":
                if promote:
                    atoms.append(_Atom("display", text=span.text, ink=ink))
                else:
                    atoms.extend(self.math_atoms(span.text, width, size, ink, False))
        return atoms

    def math_atoms(self, latex: str, width: int, size: int, ink: int, promote: bool = True) -> list[_Atom]:
        room = max(8, width - 2)
        try:
            if promote:
                box = _fit_size(latex, room, _math_sizes(size)[0], self.cfg, self.plan, False)
                if box is None:
                    return [_Atom("display", text=latex, ink=ink)]
            else:
                box = _inline_math(latex, room, size, self.cfg, self.plan)
        except _MathError as exc:
            log.debug("inline math %r is shown as source: %s", latex, exc)
            return self.source_atoms(latex, size, ink)
        box = _pad(box, 1, 0, 1, 0)
        return [_Atom("box", width=box.width, ascent=box.ascent, descent=box.descent, box=box, ink=ink)]

    def source_atoms(self, latex: str, size: int, ink: int) -> list[_Atom]:
        chain = self.chain("mono", max(6, size - 1))
        return _text_atoms(" ".join(latex.split()), chain, ink, CODE_BACKGROUND)

    def paint_lines(self, lines: Sequence[Sequence[_Atom]], x: int, y: int, width: int, align: str,
                    base: _Chain, advance: bool = False) -> tuple[int, Optional[int]]:
        """Draw broken lines at (x, y); returns the height used and the first baseline."""
        spacing = self.cfg.line_spacing
        start = y
        first: Optional[int] = None
        for line in lines:
            ascent = max([base.ascent] + [atom.ascent for atom in line])
            descent = max([base.descent] + [atom.descent for atom in line])
            baseline = y + ascent
            if first is None:
                first = baseline
            used = _line_width(line)
            if align == "c":
                cursor = x + max(0.0, (width - used) / 2)
            elif align == "r":
                cursor = x + max(0.0, width - used)
            else:
                cursor = float(x)
            for atom in line:
                left = int(round(cursor))
                right = int(round(cursor + atom.width))
                if atom.background is not None and right > left:
                    self.canvas.rectangle(left, baseline - atom.ascent - 1, right, baseline + atom.descent,
                                          atom.background)
                cursor += atom.width
            cursor -= sum(atom.width for atom in line)
            for atom in line:
                if atom.kind == "text":
                    offset = cursor
                    for face, run in atom.runs:
                        self.canvas.text(offset, baseline, run, face, atom.ink, baseline - atom.ascent,
                                         baseline + atom.descent)
                        offset += face.length(run)
                elif atom.kind == "box" and atom.box is not None:
                    self.canvas.mask(int(round(cursor)), baseline - atom.box.ascent, atom.box.mask, atom.ink)
                if atom.strike and atom.chain is not None and atom.width > 0:
                    middle = baseline - max(1, atom.chain.x_height // 2) - 1
                    self.canvas.rectangle(int(round(cursor)), middle, int(round(cursor + atom.width)), middle + 1,
                                          atom.ink)
                cursor += atom.width
            y = baseline + descent + spacing
            if advance:
                self.y = y
                self.mark()
                if self.full:
                    break
        return y - start, first

    def flow(self, atoms: Sequence[_Atom], x: int, width: int, base: _Chain, align: str = "l") -> Optional[int]:
        """Lay atoms out as a paragraph at the current position; returns the first baseline."""
        first: Optional[int] = None
        pending: list[_Atom] = []

        def flush() -> None:
            nonlocal first
            if pending:
                pitch = max(1, base.ascent + base.descent + self.cfg.line_spacing)
                lines = _break_atoms(pending, width, max(1, (self.limit - self.y) // pitch + 2))
                _height, baseline = self.paint_lines(lines, x, self.y, width, align, base, advance=True)
                if first is None:
                    first = baseline
                pending.clear()

        for atom in atoms:
            if atom.kind == "display":
                while pending and pending[-1].kind in ("space", "break"):
                    pending.pop()
                flush()
                self.display(atom.text, x, width, base.size, atom.ink)
            else:
                if not pending and atom.kind in ("space", "break") and first is not None:
                    continue
                pending.append(atom)
        flush()
        return first

    def display(self, latex: str, x: int, width: int, size: int, ink: int = INK) -> None:
        """A display formula: centered, shrunk or broken into lines when it is too wide."""
        self.skip(2)
        try:
            placed = _display_math(latex, width, size, self.cfg, self.plan)
        except _MathError as exc:
            log.debug("display math %r is shown as source: %s", latex, exc)
            chain = self.chain("mono", max(6, size - 1))
            atoms: list[_Atom] = []
            for number, line in enumerate(latex.strip().split("\n")):
                if number:
                    atoms.append(_Atom("break"))
                atoms.extend(_text_atoms(line.strip(), chain, ink, CODE_BACKGROUND))
            lines = _break_atoms(atoms, width)
            self.paint_lines(lines, x, self.y, width, "l", chain, advance=True)
            self.skip(2)
            return
        for item in placed:
            self.canvas.mask(x + item.x, self.y, item.box.mask, ink)
            self.y += item.box.height + self.cfg.line_spacing + 2
            self.mark()
            if self.full:
                break
        self.skip(1)

    # -- blocks ------------------------------------------------------------

    def blocks(self, blocks: Sequence[_Block], x: int, width: int, depth: int = 0,
               tight: bool = False) -> Optional[int]:
        """Draw blocks below each other; returns the baseline of the first line of text."""
        first: Optional[int] = None
        previous: Optional[_Block] = None
        for number, block in enumerate(blocks):
            if self.full:
                break
            if previous is not None:
                gap = self.cfg.paragraph_spacing
                if tight:
                    gap = min(gap, 3)
                if block.kind == "heading":
                    gap += 3
                elif previous.kind == "heading":
                    gap = min(gap, 3)
                elif block.kind == "math" or previous.kind == "math":
                    gap = min(gap, 3)
                self.skip(gap)
            baseline = self.block(block, x, width, depth)
            if number == 0:
                first = baseline
            previous = block
        return first

    def block(self, block: _Block, x: int, width: int, depth: int) -> Optional[int]:
        cfg = self.cfg
        kind = block.kind
        if kind == "paragraph":
            base = self.chain("regular")
            return self.flow(self.atoms(_parse_inline(block.text), width), x, width, base)
        if kind == "heading":
            size = cfg.font_size + {1: 3, 2: 2}.get(block.level, 0)
            base = self.chain("bold", size)
            atoms = self.atoms(_parse_inline(block.text), width, size, bold=True)
            return self.flow(atoms, x, width, base)
        if kind == "math":
            self.display(block.text, x, width, cfg.font_size)
            return None
        if kind == "code":
            self.code(block.lines, x, width)
            return None
        if kind == "rule":
            self.skip(2)
            self.canvas.rectangle(x, self.y, x + width, self.y + 1, GRAY_RULE)
            self.skip(3)
            self.mark()
            return None
        if kind == "quote":
            top = self.y
            inset = 8 if width > 60 else 4
            first = self.blocks(block.children, x + inset, width - inset, depth + 1, tight=True)
            bottom = max(top + 1, self.y - cfg.line_spacing)
            self.canvas.rectangle(x + 1, top, x + 3, bottom, GRAY_BAR)
            return first
        if kind == "list":
            return self.list(block, x, width, depth)
        if kind == "table":
            self.table(block, x, width)
            return None
        return None

    def code(self, lines: Sequence[str], x: int, width: int) -> None:
        cfg = self.cfg
        chain = self.chain("mono", max(6, cfg.font_size - 2))
        pad = 3
        inner = max(chain.size, width - 2 * pad)
        visual: list[list[tuple[_Face, str]]] = []
        for line in lines or [""]:
            line = line.expandtabs(4).rstrip()
            if not line:
                visual.append([])
                continue
            current = ""
            used = 0.0
            for char in line:
                char_width = chain.length(char)
                if current and used + char_width > inner:
                    visual.append(chain.runs(current))
                    current, used = "", 0.0
                current += char
                used += char_width
            visual.append(chain.runs(current))
        pitch = chain.ascent + chain.descent + 1
        top = self.y
        self.canvas.rectangle(x, top, x + width, top + len(visual) * pitch + 2 * pad - 1, CODE_BACKGROUND)
        self.y += pad
        for runs in visual:
            baseline = self.y + chain.ascent
            cursor = float(x + pad)
            for face, run in runs:
                self.canvas.text(cursor, baseline, run, face, INK, self.y, self.y + pitch)
                cursor += face.length(run)
            self.y += pitch
            self.mark()
            if self.full:
                return
        self.y += pad - 1 + cfg.line_spacing
        self.mark()

    def list(self, block: _Block, x: int, width: int, depth: int) -> Optional[int]:
        cfg = self.cfg
        base = self.chain("regular")
        if block.ordered:
            gutter = int(math.ceil(max(base.length(marker) for marker, _children in block.children))) + 5
        else:
            gutter = max(9, round(cfg.font_size * 0.85))
        gutter = min(gutter, max(0, width - 4 * cfg.font_size))
        first: Optional[int] = None
        for number, (marker, children) in enumerate(block.children):
            if self.full:
                break
            if number:
                self.skip(1)
            top = self.y
            baseline = self.blocks(children, x + gutter, width - gutter, depth + 1, tight=True)
            if not children:
                self.y += base.ascent + base.descent + cfg.line_spacing
                self.mark()
            if baseline is None:
                baseline = top + base.ascent
            if first is None:
                first = baseline
            if gutter <= 0:
                continue
            if block.ordered:
                runs = base.runs(marker)
                cursor = x + gutter - 5 - sum(face.length(run) for face, run in runs)
                for face, run in runs:
                    self.canvas.text(cursor, baseline, run, face, INK, baseline - base.ascent,
                                     baseline + base.descent)
                    cursor += face.length(run)
            else:
                middle = baseline - max(2, round(base.x_height / 2)) - 1
                left = x + max(1, gutter // 2 - 3)
                if depth % 3 == 0:
                    self.canvas.outline(left, middle - 1, left + 4, middle + 3, INK, 1, INK)
                elif depth % 3 == 1:
                    self.canvas.outline(left, middle - 1, left + 4, middle + 3, INK, 1, PAPER)
                else:
                    self.canvas.rectangle(left, middle, left + 4, middle + 2, INK)
        return first

    # -- tables ------------------------------------------------------------

    def table(self, block: _Block, x: int, width: int) -> None:
        cfg = self.cfg
        columns = max([len(block.header)] + [len(row) for row in block.rows])
        header = list(block.header) + [""] * (columns - len(block.header))
        rows = [list(row) + [""] * (columns - len(row)) for row in block.rows]
        alignments = list(block.alignments) + ["l"] * (columns - len(block.alignments))
        pad = 3
        frame = columns * 2 * pad + columns + 1
        inner = width - frame
        minimum = max(24, round(cfg.font_size * 2.6))

        def measure(size: int):
            cells = []
            for number, row in enumerate([header] + rows):
                cells.append([
                    self.atoms(_parse_inline(cell), max(minimum, inner), size, bold=(number == 0), promote=False)
                    for cell in row
                ])
            natural = [0] * columns
            for row in cells:
                for column, atoms in enumerate(row):
                    natural[column] = max(natural[column], self.natural_width(atoms, max(minimum, inner)))
            return cells, natural

        for size in (cfg.font_size, max(6, cfg.font_size - 1), max(6, cfg.font_size - 2)):
            cells, natural = measure(size)
            if sum(natural) <= inner:
                self.grid(cells, natural, alignments, x, size, pad)
                return
        if columns * minimum <= inner and columns <= 4:
            size = max(6, cfg.font_size - 1)
            cells, natural = measure(size)
            self.grid(cells, _share(natural, inner, minimum), alignments, x, size, pad)
            return
        self.records(header, rows, x, width)

    @staticmethod
    def natural_width(atoms: Sequence[_Atom], limit: int) -> int:
        widest = 0.0
        current: list[_Atom] = []
        for atom in list(atoms) + [_Atom("break")]:
            if atom.kind in ("break", "display"):
                widest = max(widest, _line_width(current))
                current = []
            else:
                current.append(atom)
        return min(limit, int(math.ceil(widest)))

    def grid(self, cells, widths: Sequence[int], alignments: Sequence[str], x: int, size: int, pad: int) -> None:
        cfg = self.cfg
        regular = self.chain("regular", size)
        bold = self.chain("bold", size)
        total = sum(widths) + len(widths) * 2 * pad + len(widths) + 1
        top = self.y
        edges = [top]
        for number, row in enumerate(cells):
            base = bold if number == 0 else regular
            broken = []
            for column, atoms in enumerate(row):
                atoms = [atom for atom in atoms if atom.kind != "display"]
                broken.append(_break_atoms(atoms, widths[column]))
            heights = []
            for lines in broken:
                height = 0
                for line in lines:
                    height += max([base.ascent] + [a.ascent for a in line]) \
                        + max([base.descent] + [a.descent for a in line]) + cfg.line_spacing
                heights.append(height - cfg.line_spacing)
            row_height = max(heights + [base.ascent + base.descent]) + 4
            row_top = self.y + 1
            if number == 0:
                self.canvas.rectangle(x, row_top, x + total, row_top + row_height, HEADER_BACKGROUND)
            cursor = x + 1
            for column, lines in enumerate(broken):
                self.paint_lines(lines, cursor + pad, row_top + 2, widths[column], alignments[column], base)
                cursor += widths[column] + 2 * pad + 1
            self.y = row_top + row_height
            edges.append(self.y)
            self.mark()
            if self.full:
                break
        bottom = self.y
        for edge in edges:
            self.canvas.rectangle(x, edge, x + total, edge + 1, GRAY_RULE)
        cursor = x
        for column in range(len(widths) + 1):
            self.canvas.rectangle(cursor, top, cursor + 1, bottom + 1, GRAY_RULE)
            if column < len(widths):
                cursor += widths[column] + 2 * pad + 1
        self.y = bottom + 1 + cfg.line_spacing
        self.mark()

    def records(self, header: Sequence[str], rows: Sequence[Sequence[str]], x: int, width: int) -> None:
        """A table that is too wide: one "header: value" line per cell."""
        base = self.chain("regular")
        for number, row in enumerate(rows or [[""] * len(header)]):
            if self.full:
                break
            if number:
                self.skip(2)
                self.canvas.rectangle(x, self.y, x + width, self.y + 1, GRAY_RULE)
                self.skip(3)
            for column, cell in enumerate(row):
                if not cell.strip() and column >= len(header):
                    continue
                name = header[column].strip() if column < len(header) else ""
                atoms: list[_Atom] = []
                if name:
                    atoms += self.atoms(_parse_inline(name), width, bold=True, promote=False)
                    atoms += _text_atoms(": ", self.chain("bold"))
                atoms += self.atoms(_parse_inline(cell), width)
                self.flow(atoms, x, width, base)
        self.mark()

    # -- result ------------------------------------------------------------

    def finish(self, bottom: int = 2) -> Image.Image:
        cfg = self.cfg
        content = max(self.stops)
        content = max(1, content - cfg.line_spacing) if len(self.stops) > 1 else content
        height = content + bottom
        if height <= cfg.max_height:
            return self.canvas.paint(max(1, height))
        chain = self.chain("regular")
        note = chain.ascent + chain.descent + 2
        room = cfg.max_height - note - 1
        cut = max([stop for stop in self.stops if stop <= room] + [0])
        height = max(1, min(cfg.max_height, cut + note + 1))
        image = self.canvas.paint(height, cut=cut)
        draw = ImageDraw.Draw(image)
        baseline = min(height - chain.descent - 1, cut + 1 + chain.ascent)
        cursor = float(cfg.margin)
        for face, run in chain.runs(TRUNCATED_NOTE):
            _draw_run(draw, cursor, baseline, run, face, GRAY_TEXT)
            cursor += face.length(run)
        return image


def _share(natural: Sequence[int], total: int, minimum: int) -> list[int]:
    """Column widths for a table that needs wrapping: narrow columns keep their width."""
    widths = [0] * len(natural)
    pending = sorted(range(len(natural)), key=lambda column: natural[column])
    remaining = total
    while pending:
        share = remaining // len(pending)
        column = pending[0]
        if natural[column] <= share:
            widths[column] = max(1, natural[column])
            remaining -= widths[column]
            pending.pop(0)
            continue
        for position, column in enumerate(pending):
            widths[column] = max(minimum, share + (1 if position < remaining - share * len(pending) else 0))
        break
    return widths


# ==========================================================================
# Public API
# ==========================================================================

def _fallback_image(text: str, cfg: RenderConfig) -> Image.Image:
    """Plain text with PIL's own font: used when everything else failed."""
    try:
        font = ImageFont.load_default()
        lines = []
        for line in (text or "").split("\n"):
            line = line.encode("ascii", "replace").decode("ascii")
            while len(line) > 50:
                lines.append(line[:50])
                line = line[50:]
            lines.append(line)
        lines = lines[:max(1, min(len(lines), cfg.max_height // 12))]
        image = Image.new("L", (cfg.width, max(1, min(cfg.max_height, 12 * len(lines) + 4))), PAPER)
        draw = ImageDraw.Draw(image)
        for number, line in enumerate(lines):
            draw.text((cfg.margin, 2 + 12 * number), line, font=font, fill=INK)
        return image
    except Exception:
        return Image.new("L", (cfg.width, 1), PAPER)


def _guarded(render: Callable[[RenderConfig], Image.Image], text: str, cfg: Optional[RenderConfig]) -> Image.Image:
    try:
        safe = _sane(cfg)
    except Exception:
        safe = RenderConfig()
    try:
        image = render(safe)
        if image.mode != "L":
            image = image.convert("L")
        return image
    except Exception:
        log.exception("rendering failed; falling back to plain text")
    try:
        return _render_plain(text, safe)
    except Exception:
        log.exception("plain text rendering failed")
        return _fallback_image(text if isinstance(text, str) else "", safe)


def _render_plain(text: str, cfg: RenderConfig) -> Image.Image:
    layout = _Layout(cfg)
    chain = layout.chain("regular")
    atoms: list[_Atom] = []
    for number, line in enumerate(_clean(text).split("\n")):
        if number:
            atoms.append(_Atom("break"))
        atoms.extend(_text_atoms(line, chain))
    layout.flow(atoms, cfg.margin, cfg.width - 2 * cfg.margin, chain)
    return layout.finish()


def render_markdown(text: str, cfg: Optional[RenderConfig] = None) -> Image.Image:
    """Render an assistant answer (Markdown with LaTeX math) to a mode "L" image."""

    def render(safe: RenderConfig) -> Image.Image:
        layout = _Layout(safe)
        lines = [line.expandtabs(4) for line in _clean(text).split("\n")]
        layout.blocks(_parse_blocks(lines), safe.margin, safe.width - 2 * safe.margin)
        return layout.finish()

    return _guarded(render, text, cfg)


def render_user_turn(text: str, cfg: Optional[RenderConfig] = None) -> Image.Image:
    """Render the user's message: plain text with $...$ math and a bar at the left."""

    def render(safe: RenderConfig) -> Image.Image:
        layout = _Layout(safe, top=3)
        inset = safe.margin + 5
        width = max(safe.font_size, safe.width - inset - safe.margin)
        chain = layout.chain("regular")
        spans = _parse_inline(_clean(text).strip("\n"), markdown=False)
        layout.flow(layout.atoms(spans, width), inset, width, chain)
        if len(layout.stops) == 1:
            layout.y += chain.ascent + chain.descent + safe.line_spacing
            layout.mark()
        image = layout.finish(bottom=3)
        inset = 1 if image.height >= 4 else 0
        ImageDraw.Draw(image).rectangle(
            (safe.margin, inset, safe.margin + 1, image.height - 1 - inset), fill=GRAY_BAR)
        return image

    return _guarded(render, text, cfg)


def render_info(text: str, cfg: Optional[RenderConfig] = None) -> Image.Image:
    """Render a system note as small gray text."""

    def render(safe: RenderConfig) -> Image.Image:
        layout = _Layout(safe, top=1)
        size = max(6, safe.font_size - 2)
        chain = layout.chain("regular", size)
        width = safe.width - 2 * safe.margin
        spans = _parse_inline(_clean(text).strip("\n"), markdown=False)
        layout.flow(layout.atoms(spans, width, size, ink=safe.info_ink), safe.margin, width, chain)
        if len(layout.stops) == 1:
            layout.y += chain.ascent + chain.descent + safe.line_spacing
            layout.mark()
        return layout.finish(bottom=1)

    return _guarded(render, text, cfg)


def _ellipsize(text: str, chain: _Chain, width: float) -> str:
    text = " ".join(_clean(text).split())
    if chain.length(text) <= width:
        return text
    ellipsis = "…" if chain.face_for("…") is not None else "..."
    room = width - chain.length(ellipsis)
    kept = ""
    for char in text:
        if chain.length(kept + char) > room:
            break
        kept += char
    return kept.rstrip() + ellipsis


def render_menu(title: str, items: Sequence[tuple[str, str]], cfg: Optional[RenderConfig] = None,
                footer: Optional[str] = None, height_limit: int = 222) -> Image.Image:
    """Render a menu overlay: a title bar, then rows of "<key> label".

    ``items`` holds ``(key, label)`` pairs; the key is one character shown in
    a key cap.  More than 8 items are set in two columns (unless the labels
    only fit a single column and that column fits the height).  The image is
    never taller than ``height_limit``.
    """
    try:
        tallest = max(1, min(65535, int(height_limit)))
    except (TypeError, ValueError):
        tallest = 222
    try:
        entries = [(str(item[0])[:1] or " ", str(item[1])) for item in items]
    except Exception:
        log.warning("menu %r: items must be (key, label) pairs", title)
        entries = []

    def render(safe: RenderConfig) -> Image.Image:
        nonlocal entries
        limit = max(24, tallest)
        plan = _plan_for(safe)
        width = safe.width
        margin = max(2, safe.margin)

        title_chain = _chain(plan, "bold", safe.font_size)
        title_height = title_chain.ascent + title_chain.descent + 5
        small = _chain(plan, "regular", max(6, safe.font_size - 2))
        footer_text = " ".join(_clean(footer).split()) if footer else ""
        footer_height = small.ascent + small.descent + 5 if footer_text else 2
        room = max(1, limit - title_height - footer_height - 4)

        def columns_for(count: int) -> int:
            return 2 if count > 8 else 1

        size = safe.font_size
        columns = columns_for(len(entries))
        while True:
            chain = _chain(plan, "regular", size)
            cap = chain.ascent + chain.descent + 2
            natural = cap + 3
            rows = max(1, -(-len(entries) // columns))
            pitch = min(natural + 2, room // rows) if rows else natural
            if pitch >= cap + 1 or size <= 8:
                break
            size -= 1
        if len(entries) > 8 and columns == 2:
            # Long labels read better in one column when that column fits.
            chain = _chain(plan, "regular", safe.font_size)
            cap = chain.ascent + chain.descent + 2
            column_room = (width - 2 * margin) / 2 - cap - 10
            if any(chain.length(label) > column_room for _key, label in entries) \
                    and len(entries) * (cap + 1) <= room:
                columns, size = 1, safe.font_size
                rows = len(entries)
                pitch = min(cap + 5, room // rows)
        if pitch < cap + 1:
            rows = max(1, room // (cap + 1))
            pitch = cap + 1
            if len(entries) > rows * columns:
                log.warning("menu %r: %d items do not fit, showing %d", title, len(entries), rows * columns)
                entries = entries[:rows * columns]
        rows = max(1, -(-len(entries) // columns)) if entries else 0
        body = rows * pitch
        height = min(limit, title_height + 3 + body + 1 + footer_height)

        canvas = _Canvas(width)
        canvas.rectangle(0, 0, width, title_height, TITLE_BACKGROUND)
        shown = _ellipsize(title, title_chain, width - 2 * margin - 4)
        baseline = 2 + title_chain.ascent
        cursor = float(margin + 2)
        for face, run in title_chain.runs(shown):
            canvas.text(cursor, baseline, run, face, PAPER, 0, title_height)
            cursor += face.length(run)

        key_chain = _chain(plan, "bold", max(6, size - 1))
        column_width = (width - 2 * margin) // columns
        for number, (key, label) in enumerate(entries):
            column, row = divmod(number, rows) if rows else (0, 0)
            left = margin + column * column_width
            top = title_height + 3 + row * pitch
            cap_top = top + (pitch - cap) // 2
            canvas.outline(left + 1, cap_top, left + 1 + cap + 1, cap_top + cap, KEYCAP_BORDER, 3,
                           KEYCAP_BACKGROUND)
            key_runs = key_chain.runs(key)
            key_width = sum(face.length(run) for face, run in key_runs)
            key_ascent = key_chain.primary.extent("0")[0] or key_chain.ascent
            key_baseline = cap_top + (cap + key_ascent) // 2
            cursor = left + 1 + (cap + 1 - key_width) / 2
            for face, run in key_runs:
                canvas.text(cursor, key_baseline, run, face, INK, cap_top, cap_top + cap)
                cursor += face.length(run)
            text_left = left + cap + 8
            text_width = column_width - cap - 10
            baseline = top + (pitch - chain.ascent - chain.descent) // 2 + chain.ascent
            cursor = float(text_left)
            for face, run in chain.runs(_ellipsize(label, chain, text_width)):
                canvas.text(cursor, baseline, run, face, INK, top, top + pitch)
                cursor += face.length(run)

        if footer_text:
            rule = height - footer_height
            canvas.rectangle(margin, rule, width - margin, rule + 1, GRAY_RULE)
            baseline = rule + 3 + small.ascent
            cursor = float(margin + 2)
            for face, run in small.runs(_ellipsize(footer_text, small, width - 2 * margin - 4)):
                canvas.text(cursor, baseline, run, face, GRAY_TEXT, rule, height)
                cursor += face.length(run)
        return canvas.paint(height)

    summary = str(title) + "\n" + "\n".join(f"{key} {label}" for key, label in entries)
    image = _guarded(render, summary, cfg)
    if image.height > tallest:
        image = image.crop((0, 0, image.width, tallest))
    return image


def warm_up(cfg: Optional[RenderConfig] = None) -> dict[str, str]:
    """Load the fonts and the math engine now instead of during the first answer.

    The very first use of matplotlib on a machine scans the installed fonts,
    which can take several seconds.  Returns :func:`describe_fonts`.
    """
    render_markdown("warm up 预热 **bold** `code` $x^2$", cfg)
    render_menu("warm up", [("1", "预热")], cfg)
    return describe_fonts(cfg)
