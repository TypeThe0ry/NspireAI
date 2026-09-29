import logging
import os
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

try:
    from . import imagecodec, render, render_samples
    from .render import RenderConfig
except ImportError:  # direct `python bridge/test_render.py`
    import imagecodec
    import render
    import render_samples
    from render import RenderConfig

CJK_PARAGRAPH = (
    "一元二次方程的求根公式可以通过配方法推导出来：先把二次项系数化为一，"
    "再在等式两边同时加上一次项系数一半的平方，左边就成为完全平方式，"
    "最后两边开平方并整理，就得到了大家熟悉的求根公式。"
)


def ink_box(image: Image.Image):
    """Bounding box of everything that is not paper, or None."""
    return image.point(lambda value: 255 if value < 250 else 0).getbbox()


def ink_rows(image: Image.Image) -> list[bool]:
    width, height = image.size
    data = image.tobytes()
    return [min(data[y * width:(y + 1) * width]) < 128 for y in range(height)]


def count_of(image: Image.Image, value: int) -> int:
    """Number of pixels with exactly this gray value (filled areas, not anti-aliasing)."""
    return image.histogram()[value]


def text_lines(image: Image.Image) -> int:
    """Number of separate bands of dark rows."""
    rows = ink_rows(image)
    return sum(1 for y, dark in enumerate(rows) if dark and (y == 0 or not rows[y - 1]))


def latin_only_font() -> str:
    """A font file without CJK glyphs that exists on this machine."""
    candidates = [
        "/System/Library/Fonts/Menlo.ttc",
        str(Path(render._matplotlib_font_directory()) / "DejaVuSans.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    raise unittest.SkipTest("no Latin-only font file found")


def reset_font_caches() -> None:
    render._plan.cache_clear()
    render._chains.clear()
    render._math_cache.clear()
    render._warned.clear()


class RenderTestCase(unittest.TestCase):
    def assert_image(self, image: Image.Image, width: int = 320) -> None:
        self.assertIsInstance(image, Image.Image)
        self.assertEqual(image.mode, "L")
        self.assertEqual(image.width, width)
        self.assertGreaterEqual(image.height, 1)

    def assert_has_ink(self, image: Image.Image) -> None:
        self.assertLess(image.getextrema()[0], 128, "the image is blank")

    def assert_right_margin_is_clear(self, image: Image.Image, margin: int = 4) -> None:
        edge = image.crop((image.width - margin, 0, image.width, image.height))
        self.assertEqual(edge.getextrema(), (255, 255), "something is drawn into the right margin")


class ConfigTests(RenderTestCase):
    def test_defaults(self):
        cfg = RenderConfig()
        self.assertEqual(
            (cfg.width, cfg.margin, cfg.font_size, cfg.line_spacing, cfg.paragraph_spacing, cfg.max_height),
            (320, 4, 13, 2, 5, 6000),
        )
        self.assertIsNone(cfg.font_path)
        self.assertIsNone(cfg.bold_font_path)
        self.assertIsNone(cfg.mono_font_path)

    def test_from_env(self):
        environment = {
            "NSPIREAI_FONT": "/fonts/regular.ttf",
            "NSPIREAI_FONT_BOLD": "/fonts/bold.ttf",
            "NSPIREAI_FONT_MONO": "/fonts/mono.ttf",
            "NSPIREAI_FONT_SIZE": "15",
        }
        with mock.patch.dict(os.environ, environment):
            cfg = RenderConfig.from_env()
        self.assertEqual(
            (cfg.font_path, cfg.bold_font_path, cfg.mono_font_path, cfg.font_size),
            ("/fonts/regular.ttf", "/fonts/bold.ttf", "/fonts/mono.ttf", 15),
        )
        self.assertEqual(cfg.width, 320)

    def test_from_env_without_overrides(self):
        names = ("NSPIREAI_FONT", "NSPIREAI_FONT_BOLD", "NSPIREAI_FONT_MONO", "NSPIREAI_FONT_SIZE",
                 "NSPIREAI_FONT_LATIN", "NSPIREAI_MATH_FONTSET")
        environment = {key: value for key, value in os.environ.items() if key not in names}
        with mock.patch.dict(os.environ, environment, clear=True):
            self.assertEqual(RenderConfig.from_env(), RenderConfig())
            self.assertEqual(RenderConfig.from_env(width=312).width, 312)
        with mock.patch.dict(os.environ, {**environment, "NSPIREAI_FONT_SIZE": "huge"}, clear=True):
            logging.disable(logging.WARNING)
            try:
                self.assertEqual(RenderConfig.from_env().font_size, 13)
            finally:
                logging.disable(logging.NOTSET)

    def test_configuration_can_be_copied_through_its_dict(self):
        cfg = RenderConfig.from_env()
        render.render_markdown("warm up $x$", cfg)      # rendering must not add attributes
        copy = RenderConfig(**{**cfg.__dict__, "width": 312})
        self.assertEqual(copy.width, 312)
        self.assertEqual(copy.font_size, cfg.font_size)
        self.assertEqual(RenderConfig(**{**cfg.__dict__, "width": 312, "max_height": 220}).max_height, 220)
        self.assert_image(render.render_markdown("text", copy), 312)

    def test_strange_values_do_not_raise(self):
        for cfg in (
            RenderConfig(width=0), RenderConfig(width=1), RenderConfig(width=17, margin=100),
            RenderConfig(font_size=0), RenderConfig(font_size=500), RenderConfig(max_height=0),
            RenderConfig(line_spacing=-5, paragraph_spacing=-5), RenderConfig(math_fontset="nonsense"),
            RenderConfig(font_size="13", width="320"),      # type: ignore[arg-type]
        ):
            for function in (render.render_markdown, render.render_user_turn, render.render_info):
                image = function("文本 text $x^2$", cfg)
                self.assertEqual(image.mode, "L")
                self.assertGreaterEqual(image.height, 1)
            self.assertEqual(render.render_menu("t", [("1", "a")], cfg).mode, "L")


class FontTests(RenderTestCase):
    def test_describe_fonts(self):
        fonts = render.describe_fonts()
        for key in ("regular", "bold", "mono", "cjk", "cjk_bold", "fallbacks"):
            self.assertIn(key, fonts)
            self.assertIsInstance(fonts[key], str)

    def test_selected_cjk_font_really_has_the_glyphs(self):
        plan = render._plan_for(render._sane(None))
        if plan.cjk is None:
            self.skipTest("no CJK font on this machine")
        face = render._load_face(plan.cjk, 13)
        self.assertTrue(face.covers("中文字体测试"))
        self.assertFalse(face.has_glyph("\U0010FFFF"))

    def test_glyph_test_tells_latin_fonts_from_cjk_fonts(self):
        ref = render._pick_face(latin_only_font(), "regular")
        face = render._load_face(ref, 13)
        self.assertTrue(face.covers("Aag0"))
        self.assertFalse(face.has_glyph("中"))
        self.assertIsNone(render._pick_face(latin_only_font(), "regular", "中"))

    def test_latin_only_font_path_does_not_raise(self):
        logging.disable(logging.WARNING)
        self.addCleanup(logging.disable, logging.NOTSET)
        cfg = RenderConfig(font_path=latin_only_font())
        for function in (render.render_markdown, render.render_user_turn, render.render_info):
            image = function("中文 and English，**粗体** `代码` $x^2$\n\n$$\\text{面积} = \\pi r^2$$", cfg)
            self.assert_image(image)
            self.assert_has_ink(image)
        menu = render.render_menu("菜单", [("1", "中文"), ("2", "English")], cfg)
        self.assert_image(menu)

    def test_missing_font_file_does_not_raise(self):
        logging.disable(logging.WARNING)
        self.addCleanup(logging.disable, logging.NOTSET)
        cfg = RenderConfig(font_path="/does/not/exist.ttf", bold_font_path="/nope.ttf", mono_font_path="/nope.ttf")
        image = render.render_markdown("中文 **bold** `code`", cfg)
        self.assert_image(image)
        self.assert_has_ink(image)

    def test_no_cjk_font_at_all_falls_back_and_warns_once(self):
        self.addCleanup(reset_font_caches)
        with mock.patch.object(render, "_CJK_CANDIDATES", ()), \
                mock.patch.object(render, "_CJK_BOLD_CANDIDATES", ()), \
                mock.patch.object(render, "_SYMBOL_CANDIDATES", ()), \
                mock.patch.object(render, "_LATIN_CANDIDATES", ()), \
                mock.patch.object(render, "_MONO_CANDIDATES", ()), \
                mock.patch.object(render, "_user_fonts", lambda: []):
            reset_font_caches()
            with self.assertLogs(render.log, level="WARNING") as logs:
                first = render.render_markdown("中文 text **bold** `code` $x^2$")
                second = render.render_markdown("更多中文")
                menu = render.render_menu("菜单", [("1", "中文")])
            fonts = render.describe_fonts()
            self.assertEqual(render.wrap_text("中文 ok", 300), ["?? ok"])
        self.assertEqual(fonts["cjk"], "none")
        self.assertEqual(fonts["regular"], "PIL default font")
        self.assertEqual(len([line for line in logs.output if "CJK" in line]), 1)
        for image in (first, second, menu):
            self.assert_image(image)
            self.assert_has_ink(image)

    def test_characters_without_a_glyph_become_question_marks(self):
        self.assertEqual(render.wrap_text("a\U0001FAE8b", 300), ["a?b"])
        self.assertEqual(render.wrap_text("a\ufe0fb\u200d\u200bc", 300), ["abc"])
        self.assertEqual(render.wrap_text("ok\x00\x07", 300), ["ok"])


class LineBreakingTests(RenderTestCase):
    def setUp(self):
        self.cfg = RenderConfig()
        self.chain = render._chain(render._plan_for(render._sane(self.cfg)), "regular", 13)

    def width(self, line: str) -> float:
        return self.chain.length(line)

    def test_closing_punctuation_never_starts_a_line(self):
        text = (
            "他说：“你好，世界！”然后（微笑着）离开了《红楼梦》的书架，走向【出口】。"
            "第一，准备；第二，开始；第三，结束。真的吗？真的！好、很好、非常好。"
        ) * 3
        for width in range(60, 321, 3):
            lines = render.wrap_text(text, width, self.cfg)
            self.assertGreater(len(lines), 1)
            for number, line in enumerate(lines):
                self.assertTrue(line)
                if number:
                    self.assertNotIn(line[0], "，。！？；：、）】》”’", (width, lines))
                if number < len(lines) - 1:
                    self.assertNotIn(line[-1], "（【《“‘", (width, lines))
                self.assertLessEqual(self.width(line), width + 0.5, (width, line))
            self.assertEqual("".join(lines), text)

    def test_ascii_punctuation_stays_with_its_word(self):
        text = "alpha, beta; gamma: delta. (epsilon) [zeta] {eta}! theta? " * 4
        for width in range(50, 321, 9):
            lines = render.wrap_text(text, width, self.cfg)
            for number, line in enumerate(lines):
                if number:
                    self.assertNotIn(line[0], ",.;:!?)]}", (width, lines))
                self.assertLessEqual(self.width(line), width + 0.5)
            self.assertEqual("".join(lines).replace(" ", ""), text.replace(" ", ""))
        self.assertEqual(" ".join(render.wrap_text(text, 120, self.cfg)), text.strip())

    def test_latin_words_break_at_spaces(self):
        text = "The quick brown fox jumps over the lazy dog and keeps running through the forest"
        lines = render.wrap_text(text, 120, self.cfg)
        self.assertGreater(len(lines), 3)
        words = text.split()
        self.assertEqual(" ".join(lines).split(), words)
        for line in lines:
            self.assertEqual(line, line.strip())
            for word in line.split():
                self.assertIn(word, words)
            self.assertLessEqual(self.width(line), 120.5)

    def test_long_word_is_cut(self):
        word = "pneumonoultramicroscopicsilicovolcanoconiosis" * 4
        lines = render.wrap_text(word, 100, self.cfg)
        self.assertGreater(len(lines), 4)
        self.assertEqual("".join(lines), word)
        for line in lines:
            self.assertLessEqual(self.width(line), 100.5)
        # All lines but the last are filled.
        for line in lines[:-1]:
            self.assertGreater(self.width(line), 80)

    def test_cjk_breaks_between_any_two_characters(self):
        text = "中" * 100
        lines = render.wrap_text(text, 100, self.cfg)
        self.assertEqual("".join(lines), text)
        self.assertEqual(len({len(line) for line in lines[:-1]}), 1)
        for line in lines:
            self.assertLessEqual(self.width(line), 100.5)

    def test_mixed_text_measures_correctly(self):
        text = "混排English和中文以及numbers 12345，还有symbols和更多的文字内容mixed together再来一些"
        for width in (80, 131, 200, 312):
            lines = render.wrap_text(text, width, self.cfg)
            self.assertEqual("".join(lines).replace(" ", ""), text.replace(" ", ""))
            for line in lines:
                self.assertLessEqual(self.width(line), width + 0.5, (width, line))
            filled = sum(self.width(line) for line in lines[:-1]) / max(1, len(lines) - 1)
            self.assertGreater(filled, width * 0.7, (width, lines))
        self.assertGreater(self.width("中"), self.width("a"))
        self.assertAlmostEqual(self.width("中a文b"), 2 * self.width("中") + self.width("a") + self.width("b"),
                               delta=1.0)

    def test_newlines_and_empty_text(self):
        self.assertEqual(render.wrap_text("", 100), [""])
        self.assertEqual(render.wrap_text("one\ntwo\n\nfour", 300), ["one", "two", "", "four"])
        self.assertEqual(render.wrap_text("  padded   text  ", 300), ["padded text"])

    def test_styles(self):
        text = "The quick brown fox jumps over the lazy dog"
        self.assertGreaterEqual(len(render.wrap_text(text, 150, mono=True)), len(render.wrap_text(text, 150)))
        self.assertGreaterEqual(len(render.wrap_text(text, 150, size=20)), len(render.wrap_text(text, 150)))
        self.assertTrue(render.wrap_text(text, 150, bold=True))


class MarkdownParserTests(RenderTestCase):
    def kinds(self, text: str) -> list[str]:
        return [block.kind for block in render._parse_blocks(text.split("\n"))]

    def test_block_kinds(self):
        text = (
            "# Title\n\nparagraph\nsecond line\n\n- a\n- b\n\n1. one\n2. two\n\n```python\ncode\n```\n\n"
            "> quote\n\n---\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n$$x = 1$$\n\n\\[ y = 2 \\]\n"
        )
        self.assertEqual(
            self.kinds(text),
            ["heading", "paragraph", "list", "list", "code", "quote", "rule", "table", "math", "math"],
        )

    def test_headings(self):
        blocks = render._parse_blocks(["# one", "## two ##", "### three", "###### six", "#hashtag"])
        self.assertEqual([(b.kind, b.level, b.text) for b in blocks[:4]],
                         [("heading", 1, "one"), ("heading", 2, "two"), ("heading", 3, "three"),
                          ("heading", 6, "six")])
        self.assertEqual(blocks[4].kind, "paragraph")

    def test_nested_lists(self):
        text = "1. first\n   - nested a\n   - nested b\n     - deep\n2. second\n\n   continued\n3. third"
        blocks = render._parse_blocks(text.split("\n"))
        self.assertEqual(len(blocks), 1)
        outer = blocks[0]
        self.assertTrue(outer.ordered)
        self.assertEqual([marker for marker, _children in outer.children], ["1.", "2.", "3."])
        first = outer.children[0][1]
        self.assertEqual([block.kind for block in first], ["paragraph", "list"])
        self.assertFalse(first[1].ordered)
        self.assertEqual(len(first[1].children), 2)
        self.assertEqual([block.kind for block in first[1].children[1][1]], ["paragraph", "list"])
        self.assertEqual([block.kind for block in outer.children[1][1]], ["paragraph", "paragraph"])

    def test_code_block_keeps_its_text(self):
        blocks = render._parse_blocks("```\n# not a heading\n  indented $x$\n\n- not a list\n```\nafter".split("\n"))
        self.assertEqual([block.kind for block in blocks], ["code", "paragraph"])
        self.assertEqual(blocks[0].lines, ["# not a heading", "  indented $x$", "", "- not a list"])

    def test_unclosed_fence_and_math(self):
        self.assertEqual(self.kinds("```\ncode without end"), ["code"])
        self.assertEqual(self.kinds("$$\nx = 1"), ["paragraph"])

    def test_multi_line_display_math(self):
        blocks = render._parse_blocks("$$\n\\begin{aligned}\na &= b \\\\\nc &= d\n\\end{aligned}\n$$".split("\n"))
        self.assertEqual([block.kind for block in blocks], ["math"])
        self.assertIn("\\begin{aligned}", blocks[0].text)
        bare = render._parse_blocks("\\begin{align}\na &= b\n\\end{align}".split("\n"))
        self.assertEqual([block.kind for block in bare], ["math"])

    def test_table(self):
        blocks = render._parse_blocks("| a | b | c |\n|:--|:-:|--:|\n| 1 | $x|y$ | `p|q` |\n| 4 | 5 |".split("\n"))
        table = blocks[0]
        self.assertEqual(table.kind, "table")
        self.assertEqual(table.header, ["a", "b", "c"])
        self.assertEqual(table.alignments, ["l", "c", "r"])
        self.assertEqual(table.rows, [["1", "$x|y$", "`p|q`"], ["4", "5"]])

    def test_inline_spans(self):
        spans = render._parse_inline("plain **bold** *italic* `code` $x^2$ \\(y\\) ~~gone~~ ***both***")
        found = [(span.kind, span.text, span.style.bold, span.style.italic) for span in spans if span.text.strip()]
        self.assertEqual(found, [
            ("text", "plain ", False, False),
            ("text", "bold", True, False),
            ("text", "italic", False, True),
            ("code", "code", False, False),
            ("math", "x^2", False, False),
            ("math", "y", False, False),
            ("text", "gone", False, False),
            ("text", "both", True, True),
        ])
        self.assertTrue(next(span for span in spans if span.text == "gone").style.strike)

    def test_nested_emphasis_keeps_math_and_code(self):
        spans = render._parse_inline("**the value $x_1$ and `a*b` here**")
        self.assertEqual([(s.kind, s.text) for s in spans],
                         [("text", "the value "), ("math", "x_1"), ("text", " and "), ("code", "a*b"),
                          ("text", " here")])
        self.assertTrue(all(span.style.bold for span in spans))

    def test_things_that_are_not_markup(self):
        for text in ("costs $5 and $10 today", "2 * 3 * 4 = 24", "snake_case_name and other_name",
                     "a < b and b > c", "50% of $100", "use \\*stars\\* and \\$dollars\\$"):
            spans = render._parse_inline(text)
            self.assertTrue(all(span.kind == "text" for span in spans), (text, spans))
            self.assertFalse(any(span.style.bold or span.style.italic for span in spans), text)
        shown = "".join(span.text for span in render._parse_inline("use \\*stars\\* and \\$dollars\\$"))
        self.assertEqual(shown, "use *stars* and $dollars$")

    def test_display_math_inside_a_paragraph(self):
        spans = render._parse_inline("before $$x = 1$$ after \\[ y \\] end")
        self.assertEqual([(s.kind, s.text) for s in spans],
                         [("text", "before "), ("display", "x = 1"), ("text", " after "), ("display", "y"),
                          ("text", " end")])

    def test_links_images_and_entities(self):
        shown = "".join(span.text for span in render._parse_inline(
            "[text](http://example.com) ![alt](a.png) <http://x.org> &amp; &lt;"))
        self.assertEqual(shown, "text [alt] http://x.org & <")

    def test_dollar_signs_with_spaces(self):
        self.assertEqual([(s.kind, s.text) for s in render._parse_inline("let $ a = b $ then")],
                         [("text", "let "), ("math", "a = b"), ("text", " then")])
        self.assertEqual([(s.kind, s.text) for s in render._parse_inline("$ \\alpha $")], [("math", "\\alpha")])
        for text in ("cost $ 5 and $ 10", "$ hello world $", "a $ b"):
            self.assertEqual([s.kind for s in render._parse_inline(text)], ["text"], text)

    def test_setext_heading(self):
        blocks = render._parse_blocks(["Title", "=====", "text"])
        self.assertEqual([(b.kind, b.text, b.level) for b in blocks],
                         [("heading", "Title", 1), ("paragraph", "text", 0)])

    def test_user_turn_text_is_not_markdown(self):
        spans = render._parse_inline("**not bold** `not code` $x$ # no heading", markdown=False)
        self.assertEqual([(s.kind, s.text) for s in spans],
                         [("text", "**not bold** `not code` "), ("math", "x"), ("text", " # no heading")])


class MathTests(RenderTestCase):
    def setUp(self):
        self.cfg = render._sane(None)
        self.plan = render._plan_for(self.cfg)
        if render._mathtext_parser() is None:
            self.skipTest("matplotlib is not installed")

    def box(self, latex: str, display: bool = False, size: int = 13):
        return render._formula(latex, size, self.cfg, self.plan, display)

    def test_baseline(self):
        self.assertEqual(self.box("x").descent, 0)
        self.assertGreater(self.box("y").descent, 0)
        self.assertGreater(self.box("H").ascent, self.box("x").ascent)
        fraction = self.box("\\frac{a}{b}")
        self.assertGreater(fraction.ascent, 0)
        self.assertGreater(fraction.descent, 0)
        self.assertGreater(self.box("x^2").ascent, self.box("x").ascent)
        self.assertGreater(self.box("x_1").descent, 0)

    def test_inline_math_sits_on_the_text_baseline(self):
        # "x" in text and "x" in math end on the same row.
        def dark_box(text: str):
            image = render.render_markdown(text)
            return image.point(lambda value: 255 if value < 128 else 0).getbbox(), image.height

        for text_form, math_form in (("x", "$x$"), ("H", "$H$"), ("xg", "$x g$"), ("x. x", "$x$. $x$")):
            (_l, _t, _r, text_bottom), text_height = dark_box(text_form)
            (_l, _t, _r, math_bottom), math_height = dark_box(math_form)
            self.assertEqual(text_bottom, math_bottom, math_form)
            self.assertEqual(text_height, math_height, math_form)
        # Symbols that float above the baseline keep their place.
        self.assertGreaterEqual(self.box("-").ascent, 3)
        self.assertEqual(self.box("-").descent, 0)
        self.assertGreater(self.box("=").ascent, self.box("-").ascent)
        self.assertEqual(self.box("-x").descent, 0)

    def test_math_size_follows_the_font_size(self):
        small = self.box("x + y", size=10)
        large = self.box("x + y", size=20)
        self.assertGreater(large.width, small.width * 1.5)
        self.assertGreater(large.height, small.height * 1.5)
        chain = render._chain(self.plan, "regular", 13)
        self.assertLessEqual(abs(self.box("x").ascent - chain.x_height), 2)

    def test_display_fractions_are_larger(self):
        self.assertGreater(self.box("\\frac{a}{b}", display=True).height, self.box("\\frac{a}{b}").height)

    def test_constructs_that_need_normalization(self):
        for latex in (
            "a \\le b \\ge c", "\\tfrac{1}{2} + \\cfrac{1}{x}", "\\dfrac{a}{b}",
            "\\displaystyle \\sum_{i=1}^n i", "\\left( \\frac{a}{b} \\right)", "\\bigl( x \\bigr)",
            "\\left. \\frac{df}{dx} \\right|_{x=0}", "\\text{if } x > 0", "\\textbf{bold} \\mathrm{d}x",
            "\\operatorname*{argmax}_x f", "\\sum\\limits_{i=1}^{n} i", "{a \\over b} + {n \\choose k}",
            "\\color{red}{x}", "x \\bmod n \\pmod{n}", "\\hspace{1em} x", "\\xrightarrow{a}",
            "A \\implies B \\iff C", "\\lvert x \\rvert", "50\\% \\& \\#", "\\sgn(x) + \\arccot x",
            "x ≤ 5, α + β ≥ γ, x² × y", "\\boxed{x = 2}", "\\cancel{x}", "a \\tag{1}", "\\unknowncommand x",
            "\\text{速度} = \\frac{\\text{路程}}{\\text{时间}}", "面积 = \\pi r^2",
        ):
            for display in (False, True):
                box = self.box(latex, display)
                self.assertGreater(box.width, 1, latex)
                self.assertIsNotNone(box.mask.getbbox(), latex)

    def test_normalization_rules(self):
        self.assertEqual(render._normalize("a \\le b"), "a \\leq b")
        self.assertEqual(render._normalize("\\tfrac{1}{2}"), "\\frac{1}{2}")
        self.assertEqual(render._normalize("\\displaystyle\\frac{1}{2}"), "\\frac{1}{2}")
        self.assertEqual(render._normalize("\\frac{1}{\\frac{2}{3}}", display=True), "\\dfrac{1}{\\frac{2}{3}}")
        self.assertEqual(render._normalize("{a \\over b}").replace(" ", ""), "{\\frac{a}{b}}")
        self.assertEqual(render._normalize("\\left( x \\right)", strip_delimiters=True), "( x )")
        self.assertEqual(render._normalize("\\left. x \\right|", strip_delimiters=True), "x |")
        self.assertEqual(render._normalize("\\left( x \\right)"), "\\left( x \\right)")
        self.assertEqual(render._normalize("a &= b").split(), ["a", "=", "b"])
        self.assertNotIn("\\text", render._normalize("\\textbf{a b}"))

    def test_environments(self):
        single = self.box("x = 1", display=True)
        for latex in (
            "\\begin{aligned} a &= b + c \\\\ &= d \\end{aligned}",
            "\\begin{align*} a &= b \\\\ c &= d \\end{align*}",
            "\\begin{gather} a = b \\\\ c = d \\end{gather}",
            "\\begin{cases} x, & x \\ge 0 \\\\ -x, & x < 0 \\end{cases}",
            "a = b \\\\ c = d",
        ):
            box = self.box(latex, display=True)
            self.assertGreater(box.height, single.height * 1.8, latex)

    def test_matrices(self):
        plain = self.box("\\begin{matrix} a & b \\\\ c & d \\end{matrix}")
        self.assertGreater(plain.height, self.box("a").height * 2)
        self.assertGreater(plain.width, self.box("a").width * 2)
        self.assertGreater(plain.ascent, 0)
        self.assertGreater(plain.descent, 0)
        for name in ("pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix"):
            box = self.box("\\begin{%s} a & b \\\\ c & d \\end{%s}" % (name, name))
            self.assertGreater(box.width, plain.width, name)       # the brackets
            self.assertGreaterEqual(box.height, plain.height, name)
        wrapped = self.box("\\left(\\begin{array}{cc} a & b \\\\ c & d \\end{array}\\right)")
        self.assertGreater(wrapped.width, plain.width)
        composite = self.box("A = \\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}^{-1} + 1")
        self.assertGreater(composite.width, plain.width + self.box("A = ").width)

    def test_wide_display_formula_is_fitted(self):
        terms = " + ".join("a_{%d} x^{%d}" % (n, n) for n in range(14))
        for latex in (
            "f(x) = " + terms,
            "(a+b)^5 = a^5 + 5a^4b + 10a^3b^2 + 10a^2b^3 + 5ab^4 + b^5 = \\sum_{k=0}^{5} \\binom{5}{k} a^{5-k} b^{k}",
            "\\frac{" + terms + "}{2}",          # cannot be broken: it is scaled
            "\\begin{aligned} y &= " + terms + " \\\\ z &= 1 \\end{aligned}",
        ):
            whole = self.box(latex, display=True)
            self.assertGreater(whole.width, 312, latex)
            for width in (312, 200, 120):
                placed = render._display_math(latex, width, 13, self.cfg, self.plan)
                self.assertTrue(placed)
                for item in placed:
                    self.assertLessEqual(item.x + item.box.width, width, (latex, width))
                    self.assertGreaterEqual(item.x, 0)

    def test_breaking_is_preferred_to_tiny_type(self):
        terms = " + ".join("a_{%d} x^{%d}" % (n, n) for n in range(14))
        placed = render._display_math("f(x) = " + terms, 312, 13, self.cfg, self.plan)
        self.assertGreater(len(placed), 1)
        reference = self.box("a_{1} x^{1}", display=True)
        for item in placed:
            self.assertGreaterEqual(item.box.height, reference.height - 2)
        # A formula that is only a little too wide is set in smaller type on one line.
        wide = "x = " + " + ".join(["abc"] * 9)
        self.assertGreater(self.box(wide, display=True).width, 312)
        self.assertEqual(len(render._display_math(wide, 312, 13, self.cfg, self.plan)), 1)

    def test_display_math_is_centered(self):
        image = render.render_markdown("$$x = 1$$")
        left, _top, right, _bottom = ink_box(image)
        self.assertLessEqual(abs((left + right) / 2 - 160), 3)

    def test_bad_latex_does_not_raise(self):
        for latex in ("\\frac{1}{", "\\sqrt{", "x^", "}{", "\\left( x", "\\begin{pmatrix} 1 & 2", "\\end{x}",
                      "\\frac", "^", "_", "{", "}", "\\", "\\\\", "&", "\\begin{aligned}", "\\right)",
                      "{" * 300 + "x" + "}" * 300, "\\frac{1}{" * 80 + "x" + "}" * 80, "\\text{", "\\over"):
            if latex in ("\\frac{1}{", "\\sqrt{"):
                with self.assertRaises(render._MathError, msg=latex):
                    self.box(latex)
            for text in (f"inline ${latex}$ text", f"$$ {latex} $$", f"\\[ {latex} \\]", f"- item ${latex}$"):
                image = render.render_markdown(text)
                self.assert_image(image)
            self.assert_image(render.render_user_turn(f"what is ${latex}$?"))

    def test_rejected_formula_is_shown_as_source(self):
        good = render.render_markdown("value $x$ end")
        bad = render.render_markdown("value $\\frac{1}{$ end")
        self.assert_has_ink(bad)
        # The source is set in the monospace font on a gray background.
        self.assertGreater(count_of(bad, render.CODE_BACKGROUND), 200)
        self.assertLess(count_of(good, render.CODE_BACKGROUND), 20)

    def test_math_without_matplotlib(self):
        self.addCleanup(render._math_cache.clear)
        render._math_cache.clear()
        with mock.patch.dict(render._mathtext_state, {"parser": False}):
            with self.assertRaises(render._MathError):
                self.box("x^2 + 1")
            image = render.render_markdown("inline $x^2 + 1$ and\n\n$$\\frac{a}{b}$$")
        self.assert_image(image)
        self.assert_has_ink(image)
        self.assertGreater(count_of(image, render.CODE_BACKGROUND), 200)


class RenderMarkdownTests(RenderTestCase):
    def test_samples(self):
        cfg = RenderConfig()
        rendered = render_samples.samples(cfg)
        self.assertGreaterEqual(len(rendered), 6)
        for name, _kind, image in rendered:
            with self.subTest(sample=name):
                self.assert_image(image, 312 if name in ("menu_312", "menu_sessions") else 320)
                self.assert_has_ink(image)
                if not name.startswith("menu"):
                    self.assert_right_margin_is_clear(image)
                    left = image.crop((0, 0, 3, image.height))
                    self.assertEqual(left.getextrema(), (255, 255), "something is drawn into the left margin")

    def test_samples_survive_the_block_codec(self):
        for name, kind, image in render_samples.samples(RenderConfig()):
            with self.subTest(sample=name):
                payload = imagecodec.encode_block(kind, image, 7, bpp=4)
                self.assertLess(len(payload), imagecodec.row_bytes(image.width, 4) * image.height)
                decoded = imagecodec.decode_block(payload)
                self.assertEqual(decoded[:5], (kind, 4, image.width, image.height, 7))
                # 16 gray levels keep the picture: no pixel moves by more than half a level.
                difference = max(abs(a - b) for a, b in zip(image.tobytes(), decoded[5].tobytes()))
                self.assertLessEqual(difference, 9)

    def test_gray_values_are_exact_at_four_bits(self):
        for value in (render.INK, render.PAPER, render.GRAY_TEXT, render.GRAY_RULE, render.GRAY_BAR,
                      render.CODE_BACKGROUND, render.HEADER_BACKGROUND, render.KEYCAP_BACKGROUND,
                      render.KEYCAP_BORDER, render.TITLE_BACKGROUND, RenderConfig().info_ink):
            self.assertEqual(value % 17, 0, value)

    def test_empty_text(self):
        for text in ("", " ", "\n\n", None):
            image = render.render_markdown(text)
            self.assert_image(image)
            self.assertEqual(image.getextrema(), (255, 255))

    def test_width_follows_the_configuration(self):
        for width in (64, 160, 312, 320, 480):
            cfg = RenderConfig(width=width)
            self.assert_image(render.render_markdown(render_samples.CHINESE, cfg), width)
            self.assert_image(render.render_user_turn("hello 你好", cfg), width)
            self.assert_image(render.render_info("hello 你好", cfg), width)
            self.assert_image(render.render_menu("menu", [("1", "one")], cfg), width)

    def test_height_grows_with_the_text(self):
        heights = [render.render_markdown(CJK_PARAGRAPH * count).height for count in (1, 2, 4, 8)]
        self.assertEqual(heights, sorted(heights))
        self.assertEqual(len(set(heights)), 4)
        self.assertGreater(heights[3], heights[0] * 5)
        english = [render.render_markdown("word " * count).height for count in (10, 100, 400)]
        self.assertLess(english[0], english[1])
        self.assertLess(english[1], english[2])
        narrow = render.render_markdown(CJK_PARAGRAPH, RenderConfig(width=160)).height
        self.assertGreater(narrow, heights[0] * 1.6)

    def test_cjk_text_wraps_over_the_whole_width(self):
        image = render.render_markdown(CJK_PARAGRAPH)
        self.assertGreater(text_lines(image), 3)
        left, _top, right, _bottom = ink_box(image)
        self.assertLessEqual(left, 8)
        self.assertGreaterEqual(right, 320 - 4 - 16)       # at most one character short of the margin
        self.assertLessEqual(right, 320 - 4)
        self.assert_right_margin_is_clear(image)

    def test_nothing_is_clipped_at_the_right_edge(self):
        texts = [
            CJK_PARAGRAPH, "word " * 200, "x" * 500, "https://example.com/" + "path/" * 40,
            "`" + "code" * 60 + "`", "```\n" + "long code line " * 20 + "\n```",
            "- " + CJK_PARAGRAPH, "> " + CJK_PARAGRAPH, "1. a\n   - b\n     - " + CJK_PARAGRAPH,
            "# " + CJK_PARAGRAPH, "$" + "+".join(["x^2"] * 40) + "$", "$$" + "+".join(["x^2"] * 60) + "$$",
            "| a | b |\n|---|---|\n| " + CJK_PARAGRAPH + " | " + "word " * 30 + " |",
            "| a | b | c | d | e | f |\n|-|-|-|-|-|-|\n" + "| long cell content | 中文内容 | x | y | z | w |\n" * 3,
            "！" * 100, "（" * 100, "$\\begin{pmatrix}" + " & ".join(["a_{11}"] * 10) + "\\end{pmatrix}$",
        ]
        for width in (320, 200):
            cfg = RenderConfig(width=width)
            for text in texts:
                image = render.render_markdown(text, cfg)
                self.assert_image(image, width)
                self.assert_has_ink(image)
                self.assert_right_margin_is_clear(image)

    def test_max_height_truncates(self):
        text = (CJK_PARAGRAPH + "\n\n") * 40
        full = render.render_markdown(text)
        self.assertGreater(full.height, 1000)
        for limit in (1000, 300, 222, 100, 40):
            image = render.render_markdown(text, RenderConfig(max_height=limit))
            self.assert_image(image)
            self.assertLessEqual(image.height, limit)
            self.assertGreater(image.height, limit - 40)
            self.assert_has_ink(image)
            # The note at the bottom is gray, the text above it is black.
            note = image.crop((0, image.height - 16, 320, image.height))
            self.assertGreaterEqual(note.getextrema()[0], render.GRAY_TEXT - 40)
            self.assertLess(note.getextrema()[0], 200)
        default = render.render_markdown(text * 10)
        self.assertLessEqual(default.height, 6000)
        self.assertGreater(default.height, 5900)

    def test_text_that_fits_is_not_truncated(self):
        image = render.render_markdown("short", RenderConfig(max_height=100))
        self.assertLess(image.height, 30)
        exact = render.render_markdown(CJK_PARAGRAPH)
        same = render.render_markdown(CJK_PARAGRAPH, RenderConfig(max_height=exact.height))
        self.assertEqual(same.tobytes(), exact.tobytes())

    def test_tiny_max_height(self):
        for limit in (1, 2, 5, 17, 18, 19):
            image = render.render_markdown(CJK_PARAGRAPH * 5, RenderConfig(max_height=limit))
            self.assert_image(image)
            self.assertLessEqual(image.height, limit)

    def test_markdown_constructs_draw_something(self):
        plain = render.render_markdown("text")
        for text in (
            "# heading", "## heading", "### heading", "- item", "1. item", "- a\n  - b\n    - c",
            "**bold**", "*italic*", "`code`", "```\ncode\n```", "> quote", "text\n\n---\n\ntext",
            "| a | b |\n|---|---|\n| 1 | 2 |", "$x$", "$$x$$", "\\(x\\)", "\\[x\\]", "~~strike~~",
        ):
            image = render.render_markdown(text)
            self.assert_image(image)
            self.assert_has_ink(image)
        self.assertGreater(render.render_markdown("# text").height, plain.height)
        self.assertGreater(render.render_markdown("```\ntext\n```").height, plain.height)
        self.assertLess(count_of(plain, render.CODE_BACKGROUND), 20)
        self.assertGreater(count_of(render.render_markdown("`code`"), render.CODE_BACKGROUND), 100)
        self.assertGreater(count_of(render.render_markdown("```\ncode\n```"), render.CODE_BACKGROUND), 3000)
        self.assertGreaterEqual(count_of(render.render_markdown("a\n\n---\n\nb"), render.GRAY_RULE), 312)
        self.assertGreater(count_of(render.render_markdown("> quote"), render.GRAY_BAR), 20)

    def test_bold_is_darker_than_regular(self):
        def darkness(image: Image.Image) -> int:
            return sum(255 - value for value in image.tobytes())

        regular = render.render_markdown("The same words 同样的文字")
        bold = render.render_markdown("**The same words 同样的文字**")
        heading = render.render_markdown("# The same words 同样的文字")
        self.assertGreater(darkness(bold), darkness(regular) * 1.1)
        self.assertGreater(darkness(heading), darkness(bold))

    def test_list_items_are_indented(self):
        def left_edges(text: str) -> list[int]:
            """Leftmost ink of every line of text."""
            image = render.render_markdown(text)
            rows = ink_rows(image)
            edges = []
            start = None
            for y, dark in enumerate(rows + [False]):
                if dark and start is None:
                    start = y
                elif not dark and start is not None:
                    edges.append(ink_box(image.crop((0, start, 320, y)))[0])
                    start = None
            return edges

        plain = left_edges("text")[0]
        self.assertGreaterEqual(left_edges("- text")[0], plain)
        nested = left_edges("- first\n  - second\n    - third\n- fourth")
        self.assertEqual(len(nested), 4)
        self.assertLess(nested[0], nested[1])
        self.assertLess(nested[1], nested[2])
        self.assertEqual(nested[0], nested[3])
        ordered = left_edges("1. first\n2. second\n10. tenth")
        self.assertEqual(len(ordered), 3)
        # The text of all items starts in the same column: markers are right-aligned.
        wrapped = render.render_markdown("1. " + "word " * 60)
        self.assertGreater(text_lines(wrapped), 2)

    def test_table_grid_and_record_fallback(self):
        grid = render.render_markdown("| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |")
        self.assertGreater(count_of(grid, render.GRAY_RULE), 100)
        self.assertGreater(count_of(grid, render.HEADER_BACKGROUND), 100)
        self.assertEqual(text_lines(grid.point(lambda v: 255 if v > 100 else 0)), 3)
        wide = "| Function | Derivative | Integral | Domain | Notes |\n|---|---|---|---|---|\n" \
               "| sine of x | cosine of x | minus cosine of x | all real numbers | periodic with period two pi |"
        records = render.render_markdown(wide)
        self.assertLess(count_of(records, render.HEADER_BACKGROUND), 20)
        self.assertGreaterEqual(text_lines(records), 5)        # one "header: value" line per cell
        self.assert_right_margin_is_clear(records)

    def test_code_block_wraps_long_lines(self):
        short = render.render_markdown("```\nshort\n```")
        long = render.render_markdown("```\n" + "x = 1; " * 30 + "\n```")
        self.assertGreater(long.height, short.height * 2)
        self.assert_right_margin_is_clear(long)

    def test_unknown_syntax_degrades_to_text(self):
        for text in (
            "<div>html</div>", "[^1]: footnote", "==highlight==", ":::note\ntext\n:::", "{{template}}",
            "- [ ] task\n- [x] done", "Term\n: definition", "|||", "| a |\n| b |", "***", "* * * *",
            "#no space", "1) item", "\\begin{unknown} x \\end{unknown}", "$$", "$ $", "`", "``", "```",
            "[link](", "![image", "**unclosed", "_a_b_c_", "\\", "\\(", "\\[", "a\\", "> ", "- ", "1. ",
            "\t\ttabs", "\x00\x1b[31mcontrol\x1b[0m", "\ud800",
        ):
            image = render.render_markdown(text)
            self.assert_image(image)

    def test_rendering_is_deterministic(self):
        first = render.render_markdown(render_samples.CHINESE)
        second = render.render_markdown(render_samples.CHINESE)
        self.assertEqual(first.tobytes(), second.tobytes())

    def test_internal_error_falls_back_to_plain_text(self):
        logging.disable(logging.ERROR)
        self.addCleanup(logging.disable, logging.NOTSET)
        with mock.patch.object(render, "_parse_blocks", side_effect=RuntimeError("boom")):
            image = render.render_markdown("still **shown** 仍然显示")
        self.assert_image(image)
        self.assert_has_ink(image)
        with mock.patch.object(render, "_Layout", side_effect=RuntimeError("boom")):
            image = render.render_markdown("last resort")
        self.assert_image(image)
        self.assert_has_ink(image)


class UserTurnAndInfoTests(RenderTestCase):
    def test_user_turn_has_a_bar_at_the_left(self):
        image = render.render_user_turn("[solve] x^2 - 5x + 6 = 0 怎么解？")
        self.assert_image(image)
        self.assert_has_ink(image)
        bar = image.crop((4, 1, 6, image.height - 1))
        self.assertEqual(bar.getextrema(), (render.GRAY_BAR, render.GRAY_BAR))
        text = image.crop((6, 0, 320, image.height))
        self.assertGreater(ink_box(text)[0], 2)         # the text is set off the bar
        self.assert_right_margin_is_clear(image)
        # Small padding above and below.
        rows = ink_rows(text)
        self.assertFalse(rows[0] or rows[1])
        self.assertFalse(rows[-1] or rows[-2])

    def test_user_turn_is_plain_text_with_math(self):
        plain = render.render_user_turn("**bold** # heading `code`")
        self.assertLess(count_of(plain, render.CODE_BACKGROUND), 20)
        self.assertEqual(text_lines(plain.crop((8, 0, 320, plain.height))), 1)
        with_math = render.render_user_turn("simplify $\\frac{x^2}{x}$ please")
        without = render.render_user_turn("simplify please")
        self.assertGreater(with_math.height, without.height)
        self.assertGreater(render.render_user_turn("line one\nline two").height, without.height)

    def test_user_turn_wraps(self):
        short = render.render_user_turn("短")
        long = render.render_user_turn(CJK_PARAGRAPH)
        self.assertGreater(long.height, short.height * 3)
        self.assert_right_margin_is_clear(long)

    def test_empty_user_turn_and_info(self):
        for function in (render.render_user_turn, render.render_info):
            for text in ("", "\n", None):
                image = function(text)
                self.assert_image(image)
                self.assertGreater(image.height, 8)

    def test_info_is_small_and_gray(self):
        info = render.render_info("Session 3 已切换")
        body = render.render_markdown("Session 3 已切换")
        self.assert_image(info)
        self.assertGreaterEqual(info.getextrema()[0], RenderConfig().info_ink)
        self.assertLess(info.getextrema()[0], 160)
        self.assertLess(body.getextrema()[0], 30)
        box = ink_box(info)
        reference = ink_box(body)
        self.assertLess(box[2] - box[0], reference[2] - reference[0])
        self.assertLess(box[3] - box[1], reference[3] - reference[1] + 1)
        black = render.render_info("Session 3", RenderConfig(info_ink=0))
        self.assertLess(black.getextrema()[0], 30)

    def test_info_wraps(self):
        image = render.render_info(CJK_PARAGRAPH)
        self.assertGreater(text_lines(image), 2)
        self.assert_right_margin_is_clear(image)


class MenuTests(RenderTestCase):
    ITEMS = render_samples.MENU_ITEMS

    def test_menu(self):
        image = render.render_menu("Math 数学", self.ITEMS, footer="0 = next page")
        self.assert_image(image)
        self.assert_has_ink(image)
        self.assertLessEqual(image.height, 222)
        # A dark title bar over the whole width with light text on it.
        bar = image.crop((0, 0, 320, 8))
        self.assertEqual(bar.getpixel((0, 0)), render.TITLE_BACKGROUND)
        self.assertEqual(bar.getpixel((319, 0)), render.TITLE_BACKGROUND)
        title = image.crop((0, 0, 320, 18))
        self.assertGreater(title.getextrema()[1], 200)
        self.assertGreater(count_of(image, render.KEYCAP_BACKGROUND), 12 * 100)

    def test_keyword_arguments(self):
        cfg = RenderConfig(width=312)
        image = render.render_menu("Menu", [("1", "one")], cfg, footer="esc = close", height_limit=220)
        self.assert_image(image, 312)
        image = render.render_menu(title="Menu", items=[("1", "one")], cfg=cfg, footer=None, height_limit=100)
        self.assert_image(image, 312)

    def test_height_limit(self):
        many = [(str(n % 10), f"Item {n} 项目") for n in range(40)]
        logging.disable(logging.WARNING)
        self.addCleanup(logging.disable, logging.NOTSET)
        for limit in (222, 220, 160, 100, 60, 30):
            for items in (self.ITEMS, self.ITEMS[:3], many, []):
                for footer in (None, "footer text"):
                    image = render.render_menu("Title", items, footer=footer, height_limit=limit)
                    self.assert_image(image)
                    self.assertLessEqual(image.height, limit, (limit, len(items), footer))

    def test_height_follows_the_number_of_rows(self):
        heights = [render.render_menu("t", self.ITEMS[:count]).height for count in (1, 3, 6, 8)]
        self.assertEqual(heights, sorted(heights))
        self.assertEqual(len(set(heights)), 4)
        with_footer = render.render_menu("t", self.ITEMS[:3], footer="footer")
        self.assertGreater(with_footer.height, heights[1])

    def test_two_columns_for_more_than_eight_items(self):
        def right_half_has_items(image: Image.Image) -> bool:
            body = image.crop((170, 24, 320, image.height - 2))
            return body.getextrema()[0] < 128

        self.assertFalse(right_half_has_items(render.render_menu("t", self.ITEMS[:8])))
        self.assertTrue(right_half_has_items(render.render_menu("t", self.ITEMS[:9])))
        self.assertTrue(right_half_has_items(render.render_menu("t", self.ITEMS)))
        nine = render.render_menu("t", self.ITEMS[:9])
        eight = render.render_menu("t", self.ITEMS[:8])
        self.assertLess(nine.height, eight.height)

    def test_long_labels_use_one_column_when_it_fits(self):
        items = [(str(n), "一个很长的会话标题用来测试菜单的单列布局效果") for n in range(10)]
        image = render.render_menu("Chats", items, RenderConfig(width=312), footer="esc", height_limit=220)
        self.assertLessEqual(image.height, 220)
        keycaps = image.crop((156, 24, 176, image.height - 20))
        self.assertLess(count_of(keycaps, render.KEYCAP_BACKGROUND), 20)
        self.assertGreater(ink_box(image.crop((0, 24, 312, image.height - 20)))[2], 250)

    def test_labels_are_cut_to_fit(self):
        image = render.render_menu("标题" * 40, [("1", "很长的标签" * 30), ("2", "x" * 300)], footer="f" * 300)
        self.assert_image(image)
        body = image.crop((316, 24, 320, image.height))
        self.assertEqual(body.getextrema(), (255, 255))

    def test_odd_items(self):
        logging.disable(logging.WARNING)
        self.addCleanup(logging.disable, logging.NOTSET)
        for items in ([("", "empty key")], [("12", "long key")], [("中", "cjk key")], [(1, 2)], [("a", "")],
                      [("a",)], [None], "ab", [("a", "b", "c")]):
            self.assert_image(render.render_menu("t", items))
        for limit in (None, "tall", -5, 0, 10 ** 9):
            image = render.render_menu("t", [("1", "one")], height_limit=limit)
            self.assert_image(image)
            self.assertLessEqual(image.height, 222)

    def test_warm_up(self):
        fonts = render.warm_up(RenderConfig())
        self.assertEqual(fonts, render.describe_fonts(RenderConfig()))


class CandidateTests(RenderTestCase):
    WORDS = ["这个方程怎么解", "这个", "这歌"] + list("这着者折哲浙遮褶蔗辙")

    def test_pages_hold_what_fits_and_at_most_nine(self):
        pages = render.layout_candidates(self.WORDS)
        self.assertEqual(pages[0][0], 0)
        self.assertEqual(sum(count for _first, count in pages), len(self.WORDS))
        for index, (first, count) in enumerate(pages):
            self.assertTrue(1 <= count <= render.CANDIDATES_PER_PAGE)
            if index:
                self.assertEqual(first, sum(c for _f, c in pages[:index]))
        self.assertEqual(render.layout_candidates(list("一二三四五六七八九十")), [(0, 9), (9, 1)])
        self.assertEqual(render.layout_candidates([]), [])
        # One candidate wider than the bar still gets a page of its own.
        self.assertEqual(render.layout_candidates(["长" * 60, "短"]), [(0, 1), (1, 1)])
        # A narrower screen holds fewer.
        narrow = render.layout_candidates(self.WORDS, RenderConfig(width=160))
        self.assertGreater(len(narrow), len(pages))

    def test_a_page_is_drawn_completely(self):
        for first, count in render.layout_candidates(self.WORDS):
            shown = self.WORDS[first:first + count]
            image = render.render_candidates(shown, previous=first > 0, following=True)
            self.assert_image(image)
            self.assertLessEqual(image.height, 40)     # the page refuses a taller bar
            self.assert_has_ink(image)
            # Nothing is drawn under the arrows at the right edge ...
            plain = render.render_candidates(shown)
            self.assertEqual(plain.crop((306, 0, 320, plain.height)).getextrema(), (255, 255))
            # ... and the arrows are.
            self.assertLess(image.crop((306, 0, 320, image.height)).getextrema()[0], 200)

    def test_note_and_odd_input(self):
        note = render.render_candidates([], note="no match")
        self.assert_image(note)
        self.assert_has_ink(note)
        blank = render.render_candidates([])
        self.assert_image(blank)
        self.assertLessEqual(render.render_candidates(["长" * 200], RenderConfig(font_size=96)).height, 40)
        self.assert_image(render.render_candidates(["a\nb", "", None, 3]))


if __name__ == "__main__":
    unittest.main()
