import dataclasses
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from . import commands
    from .commands import Category, Command, CommandSet, Item
except ImportError:  # direct `python bridge/test_commands.py`
    import commands
    from commands import Category, Command, CommandSet, Item

DEFAULT_PATH = Path(__file__).resolve().with_name("commands.default.json")


class CommandTestCase(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.directory = Path(self._directory.name)

    def write(self, name: str, data) -> Path:
        path = self.directory / name
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        path.write_text(text, encoding="utf-8")
        return path

    def load(self, default, user=False) -> CommandSet:
        default_path = self.write("default.json", default)
        user_path = self.write("user.json", user) if user is not False else False
        return CommandSet.load(default_path, user_path)


def document(*categories) -> dict:
    return {"version": 1, "categories": list(categories)}


def prompt(item_id: str, label: str = "label", template: str = "Do it.\n\n{input}", **extra) -> dict:
    return {"id": item_id, "label": label, "type": "prompt", "template": template, **extra}


def insert(item_id: str, label: str = "label", text: str = "\\x", **extra) -> dict:
    return {"id": item_id, "label": label, "type": "insert", "text": text, **extra}


class DefaultFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = json.loads(DEFAULT_PATH.read_text(encoding="utf-8"))
        cls.commands = CommandSet.load(DEFAULT_PATH, False)

    def test_every_entry_of_the_file_is_valid(self):
        with self.assertNoLogs(commands.log, level="WARNING"):
            loaded = CommandSet.load(DEFAULT_PATH, False)
        self.assertEqual(self.raw["version"], 1)
        raw_items = [item["id"] for category in self.raw["categories"] for item in category["items"]]
        self.assertEqual([item.id for item in loaded.items()], raw_items)
        self.assertEqual([c.id for c in loaded.categories], [c["id"] for c in self.raw["categories"]])

    def test_default_path_is_the_shipped_file(self):
        self.assertEqual(commands.DEFAULT_PATH, DEFAULT_PATH)
        self.assertEqual(CommandSet.load(user_path=False).to_dict(), self.commands.to_dict())

    def test_labels_are_ascii_and_short(self):
        for category in self.commands.categories:
            self.assertIsInstance(category, Category)
            self.assertTrue(category.label.isascii(), category.id)
            self.assertTrue(1 <= len(category.label) <= 12, category.id)
            for item in category.items:
                self.assertIsInstance(item, Item)
                self.assertTrue(item.label.isascii() and item.label.isprintable(), item.id)
                self.assertTrue(1 <= len(item.label) <= 12, item.id)
                self.assertNotIn("|", item.label)
                self.assertNotIn("|", item.id)
                self.assertTrue(item.id.isascii())
                self.assertEqual(item.category, category.id)
                # "id|label" must fit the argument of a screen key.
                self.assertLessEqual(len(item.pending_arg), 255)

    def test_ids_are_unique(self):
        ids = [item.id for item in self.commands.items()]
        self.assertEqual(len(ids), len(set(ids)))
        categories = [category.id for category in self.commands.categories]
        self.assertEqual(len(categories), len(set(categories)))

    def test_item_types(self):
        for item in self.commands.items():
            self.assertIn(item.type, ("prompt", "insert"))
            if item.type == "prompt":
                self.assertIn("{input}", item.template)
                self.assertIsNone(item.text)
            else:
                self.assertIsNone(item.template)
                self.assertTrue(item.text.isascii() and item.text.isprintable(), item.id)
                self.assertTrue(1 <= len(item.text) <= 60, item.id)

    def test_required_defaults_are_present(self):
        categories = {category.id: category for category in self.commands.categories}
        self.assertEqual(list(categories), ["math", "language", "code", "study", "latex"])
        self.assertEqual(
            [item.id for item in categories["math"].items],
            ["explain", "solve", "simplify", "factorize", "expand", "differentiate", "integrate",
             "limit", "evaluate", "derive", "check"],
        )
        self.assertEqual(
            [item.id for item in categories["language"].items],
            ["to_chinese", "to_english", "summarize", "polish", "grammar", "define"],
        )
        self.assertEqual([item.id for item in categories["code"].items], ["code_explain", "code_write", "code_debug"])
        self.assertEqual([item.id for item in categories["study"].items], ["eli5", "example", "quiz", "compare"])
        self.assertTrue(all(item.type == "insert" for item in categories["latex"].items))
        self.assertTrue(all(item.type == "prompt" for c in "math language code study".split()
                            for item in categories[c].items))
        snippets = [item.text.strip() for item in categories["latex"].items]
        self.assertEqual(snippets, [
            "\\frac{", "\\sqrt{", "\\int", "\\sum", "\\lim", "^{", "_{", "\\pi", "\\infty",
            "\\cdot", "\\times", "\\leq", "\\geq", "\\neq",
        ])

    def test_example_from_the_specification(self):
        item = self.commands.get("factorize")
        self.assertEqual((item.label, item.type), ("factorize", "prompt"))
        self.assertEqual(
            item.template,
            "Factorize the following expression. Show the key steps briefly.\n\n{input}",
        )
        frac = self.commands.get("frac")
        self.assertEqual((frac.label, frac.type, frac.text), ("\\frac{}{}", "insert", "\\frac{"))


class InterfaceTests(CommandTestCase):
    """The names the page host relies on."""

    def test_categories_and_items(self):
        loaded = self.load(document({
            "id": "math", "label": "Math", "title": "数学",
            "items": [prompt("solve", "solve", title="求解"), insert("frac", "\\frac{}{}", "\\frac{")],
        }))
        self.assertIsInstance(loaded.categories, list)
        category = loaded.categories[0]
        self.assertTrue(dataclasses.is_dataclass(category))
        self.assertEqual((category.id, category.label, category.title), ("math", "Math", "数学"))
        solve, frac = category.items
        self.assertTrue(dataclasses.is_dataclass(solve))
        self.assertEqual(
            (solve.id, solve.label, solve.title, solve.type, solve.template, solve.text),
            ("solve", "solve", "求解", "prompt", "Do it.\n\n{input}", None),
        )
        self.assertEqual(
            (frac.id, frac.label, frac.title, frac.type, frac.template, frac.text),
            ("frac", "\\frac{}{}", None, "insert", None, "\\frac{"),
        )
        self.assertEqual(
            [f.name for f in dataclasses.fields(Item)][:6],
            ["id", "label", "title", "type", "template", "text"],
        )
        self.assertEqual([f.name for f in dataclasses.fields(Category)], ["id", "label", "title", "items"])
        self.assertIs(Command, Item)

    def test_label(self):
        loaded = self.load(document({"id": "math", "label": "Math", "items": [
            prompt("solve", "solve it", title="求解"), insert("frac", "\\frac{}{}", "\\frac{"),
        ]}))
        self.assertEqual(loaded.label("solve"), "solve it")
        self.assertIsNone(loaded.label("frac"))         # insert items are never attached to a message
        self.assertIsNone(loaded.label("unknown"))
        self.assertIsNone(loaded.label(""))
        self.assertIsNone(loaded.label(None))
        self.assertIsNone(loaded.label(["solve"]))
        defaults = CommandSet.load(DEFAULT_PATH, False)
        self.assertEqual(defaults.label("factorize"), "factorize")
        self.assertEqual(defaults.label("derive"), "step-by-step")

    def test_pages_interface(self):
        loaded = self.load(document(
            {"id": "math", "label": "Math", "items": [prompt(f"p{n}") for n in range(10)]},
            {"id": "empty", "label": "Empty", "items": []},
        ))
        pages = loaded.pages("math", per_page=9)
        self.assertEqual([len(page) for page in pages], [9, 1])
        self.assertTrue(all(isinstance(item, Item) for page in pages for item in page))
        self.assertEqual(loaded.pages("math"), pages)
        self.assertEqual(loaded.pages("empty", per_page=9), [[]])
        self.assertEqual(loaded.pages("missing", per_page=9), [])

    def test_expand_interface(self):
        loaded = self.load(document({"id": "math", "label": "Math", "items": [prompt("solve")]}))
        self.assertEqual(loaded.expand("solve", "x = 1"), "Do it.\n\nx = 1")
        logging.disable(logging.WARNING)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.assertEqual(loaded.expand("missing", "x = 1"), "x = 1")

    def test_to_dict_is_deterministic_and_serializable(self):
        first = CommandSet.load(DEFAULT_PATH, False).to_dict()
        second = CommandSet.load(DEFAULT_PATH, False).to_dict()
        self.assertEqual(repr(first), repr(second))
        self.assertEqual(json.loads(json.dumps(first)), first)
        self.assertEqual(first["version"], 1)
        self.assertEqual(first, json.loads(DEFAULT_PATH.read_text(encoding="utf-8")))
        # A changed command set changes the representation.
        changed = self.load(json.loads(json.dumps(first)), document(
            {"id": "math", "items": [prompt("solve", "solve", template="Different.\n\n{input}")]}
        ))
        self.assertNotEqual(repr(changed.to_dict()), repr(first))
        self.assertEqual(CommandSet().to_dict(), {"version": 1, "categories": []})

    def test_to_dict_round_trips_through_merge(self):
        first = CommandSet.load(DEFAULT_PATH, False)
        copy = CommandSet()
        copy.merge(first.to_dict())
        self.assertEqual(copy.to_dict(), first.to_dict())


class ExpandTests(CommandTestCase):
    def setUp(self):
        super().setUp()
        self.commands = self.load(document({"id": "c", "label": "C", "items": [
            prompt("factorize", template="Factorize the following expression.\n\n{input}"),
            prompt("twice", template="First {input}, then again {input}."),
            prompt("plain", template="Answer in one sentence.  "),
            prompt("latex", template="Typeset \\frac{a}{b} = {input} and {other}"),
            insert("frac", text="\\frac{"),
        ]}))

    def test_placeholder(self):
        self.assertEqual(
            self.commands.expand("factorize", "x^2-1"),
            "Factorize the following expression.\n\nx^2-1",
        )
        self.assertEqual(self.commands.expand("twice", "A"), "First A, then again A.")

    def test_template_without_placeholder(self):
        self.assertEqual(self.commands.expand("plain", "Why is the sky blue?"),
                         "Answer in one sentence.\n\nWhy is the sky blue?")
        self.assertEqual(self.commands.expand("plain", "  "), "Answer in one sentence.")

    def test_braces_are_not_format_fields(self):
        self.assertEqual(self.commands.expand("latex", "{x}"), "Typeset \\frac{a}{b} = {x} and {other}")
        self.assertEqual(self.commands.expand("factorize", "{input} {0} %s"),
                         "Factorize the following expression.\n\n{input} {0} %s")

    def test_empty_input(self):
        self.assertEqual(self.commands.expand("factorize", ""), "Factorize the following expression.")

    def test_unicode_input(self):
        self.assertEqual(self.commands.expand("factorize", "因式分解 x²−1"),
                         "Factorize the following expression.\n\n因式分解 x²−1")

    def test_unknown_or_insert_command_leaves_the_text(self):
        with self.assertLogs(commands.log, level="WARNING"):
            self.assertEqual(self.commands.expand("missing", "text"), "text")
        self.assertEqual(self.commands.expand("frac", "text"), "text")
        self.assertEqual(self.commands.expand(None, "text"), "text")
        self.assertEqual(self.commands.expand("", "text"), "text")

    def test_never_raises(self):
        logging.disable(logging.WARNING)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.assertEqual(self.commands.expand(["not", "hashable"], "text"), "text")
        self.assertEqual(self.commands.expand(12, "text"), "text")
        self.assertEqual(self.commands.expand("factorize", 5), "Factorize the following expression.\n\n5")


class MergeTests(CommandTestCase):
    def defaults(self) -> dict:
        return document(
            {"id": "math", "label": "Math", "items": [
                prompt("solve", "solve"), prompt("simplify", "simplify"), insert("frac", "\\frac{}{}", "\\frac{"),
            ]},
            {"id": "code", "label": "Code", "title": "Programming", "items": [prompt("debug", "find bug")]},
        )

    def test_user_file_overrides_and_extends(self):
        loaded = self.load(self.defaults(), document(
            {"id": "math", "title": "数学", "items": [
                prompt("simplify", "simpl.", template="Simplify, tersely: {input}", title="化简"),
                prompt("roots", "roots", template="Find the roots of {input}"),
            ]},
            {"id": "mine", "label": "Mine", "items": [insert("alpha", "\\alpha", "\\alpha ")]},
        ))
        self.assertEqual([c.id for c in loaded.categories], ["math", "code", "mine"])
        math = loaded.category("math")
        self.assertEqual((math.label, math.title), ("Math", "数学"))
        # An overridden item keeps its position, new items are appended.
        self.assertEqual([item.id for item in math.items], ["solve", "simplify", "frac", "roots"])
        simplify = loaded.get("simplify")
        self.assertEqual((simplify.label, simplify.title), ("simpl.", "化简"))
        self.assertEqual(loaded.expand("simplify", "2x+2x"), "Simplify, tersely: 2x+2x")
        self.assertEqual(loaded.expand("solve", "x=1"), "Do it.\n\nx=1")
        self.assertEqual(loaded.category("code").title, "Programming")
        self.assertEqual(loaded.get("alpha").category, "mine")

    def test_item_moves_to_the_users_category(self):
        loaded = self.load(self.defaults(), document(
            {"id": "code", "items": [prompt("solve", "solve", template="Solve with code: {input}")]},
        ))
        self.assertEqual([item.id for item in loaded.category("math").items], ["simplify", "frac"])
        self.assertEqual([item.id for item in loaded.category("code").items], ["debug", "solve"])
        self.assertEqual(len([item for item in loaded.items() if item.id == "solve"]), 1)

    def test_hidden_entries_are_removed(self):
        loaded = self.load(self.defaults(), document(
            {"id": "math", "items": [{"id": "frac", "hidden": True}]},
            {"id": "code", "hidden": True},
        ))
        self.assertEqual([c.id for c in loaded.categories], ["math"])
        self.assertEqual([item.id for item in loaded.items()], ["solve", "simplify"])
        self.assertIsNone(loaded.get("debug"))

    def test_replace_drops_the_default_items(self):
        loaded = self.load(self.defaults(), document(
            {"id": "math", "replace": True, "items": [prompt("only", "only")]},
        ))
        self.assertEqual([item.id for item in loaded.category("math").items], ["only"])

    def test_user_file_from_the_home_directory(self):
        default_path = self.write("default.json", self.defaults())
        home = self.directory / "home"
        home.mkdir()
        (home / "commands.json").write_text(json.dumps(document(
            {"id": "math", "items": [prompt("extra", "extra")]},
        )), encoding="utf-8")
        with mock.patch.dict(os.environ, {"NSPIREAI_HOME": str(home)}):
            self.assertIsNotNone(CommandSet.load(default_path).get("extra"))
            self.assertIsNone(CommandSet.load(default_path, False).get("extra"))
        with mock.patch.dict(os.environ, {"NSPIREAI_HOME": str(self.directory / "empty")}):
            self.assertIsNone(CommandSet.load(default_path).get("extra"))

    def test_missing_user_file_is_silent(self):
        default_path = self.write("default.json", self.defaults())
        with self.assertNoLogs(commands.log, level="WARNING"):
            loaded = CommandSet.load(default_path, self.directory / "missing.json")
        self.assertEqual(len(loaded.items()), 4)


class InvalidEntryTests(CommandTestCase):
    def setUp(self):
        super().setUp()
        logging.disable(logging.WARNING)
        self.addCleanup(logging.disable, logging.NOTSET)

    def test_invalid_items_are_skipped(self):
        loaded = self.load(document({"id": "c", "label": "C", "items": [
            prompt("good"),
            prompt("long_label", "thirteen_char"),
            prompt("twelve", "twelve_chars"),
            prompt("chinese_label", "因式分解"),
            prompt("pipe", "a|b"),
            prompt("padded", " pad"),
            prompt("empty_label", ""),
            prompt("no_template", template=""),
            prompt("bad id"),
            prompt("with|pipe"),
            prompt(""),
            {"id": "no_label", "type": "prompt", "template": "x"},
            {"id": "no_type", "label": "x", "template": "x"},
            {"id": "bad_type", "label": "x", "type": "macro", "template": "x"},
            {"id": "template_type", "label": "x", "type": "prompt", "template": 5},
            {"id": "title_type", "label": "x", "type": "prompt", "template": "x", "title": 5},
            {"label": "no id", "type": "prompt", "template": "x"},
            insert("ok_insert", text="x" * 60),
            insert("long_insert", text="x" * 61),
            insert("unicode_insert", text="π"),
            insert("newline_insert", text="a\nb"),
            insert("empty_insert", text=""),
            "not an object",
            None,
            42,
        ]}))
        self.assertEqual([item.id for item in loaded.items()], ["good", "twelve", "ok_insert"])

    def test_invalid_categories_are_skipped(self):
        loaded = self.load(document(
            {"id": "good", "label": "Good", "items": [prompt("a")]},
            {"id": "no_label", "items": [prompt("b")]},
            {"id": "long", "label": "a label that is too long", "items": [prompt("c")]},
            {"id": "unicode", "label": "数学", "items": [prompt("d")]},
            {"label": "No id", "items": [prompt("e")]},
            {"id": "bad_items", "label": "Bad", "items": "nope"},
            {"id": "bad_title", "label": "Title", "title": 7, "items": [prompt("f")]},
            "not an object",
        ))
        self.assertEqual([c.id for c in loaded.categories], ["good", "bad_items", "bad_title"])
        self.assertEqual([item.id for item in loaded.items()], ["a", "f"])
        self.assertIsNone(loaded.category("bad_title").title)

    def test_invalid_override_keeps_the_default(self):
        loaded = self.load(
            document({"id": "c", "label": "C", "items": [prompt("a", "first")]}),
            document({"id": "c", "label": "this label is far too long", "items": [prompt("a", "second")]},
                     {"id": "c", "items": [prompt("a", "label is too long")]}),
        )
        self.assertEqual(loaded.category("c").label, "C")
        self.assertEqual(loaded.get("a").label, "first")

    def test_duplicate_ids_keep_the_last_definition(self):
        loaded = self.load(document(
            {"id": "c", "label": "C", "items": [prompt("a", "first"), prompt("a", "second")]},
            {"id": "d", "label": "D", "items": [prompt("a", "third")]},
        ))
        self.assertEqual([(item.id, item.label, item.category) for item in loaded.items()], [("a", "third", "d")])

    def test_broken_files_never_raise(self):
        good = self.write("good.json", document({"id": "c", "label": "C", "items": [prompt("a")]}))
        for index, garbage in enumerate((
            "", "{", "null", "[]", "42", '"text"',
            json.dumps({"version": 2, "categories": []}),
            json.dumps({"version": 1, "categories": {"id": "x"}}),
            json.dumps({"version": 1}),
        )):
            path = self.write(f"garbage{index}.json", garbage)
            self.assertEqual(CommandSet.load(path, False).categories, [], garbage)
            self.assertEqual([item.id for item in CommandSet.load(good, path).items()], ["a"], garbage)
        binary = self.directory / "binary.json"
        binary.write_bytes(b"\xff\xfe\x00\x01")
        self.assertEqual(CommandSet.load(binary, False).categories, [])
        self.assertEqual(CommandSet.load(self.directory / "missing.json", False).categories, [])
        self.assertEqual(CommandSet.load(self.directory, False).categories, [])

    def test_unsupported_user_version_keeps_defaults(self):
        loaded = self.load(
            document({"id": "c", "label": "C", "items": [prompt("a")]}),
            {"version": 99, "categories": [{"id": "c", "hidden": True}]},
        )
        self.assertEqual([item.id for item in loaded.items()], ["a"])


class PagingTests(CommandTestCase):
    def setUp(self):
        super().setUp()
        self.commands = self.load(document(
            {"id": "big", "label": "Big", "items": [prompt(f"item{i}", f"item {i}") for i in range(20)]},
            {"id": "nine", "label": "Nine", "items": [prompt(f"n{i}") for i in range(9)]},
            {"id": "empty", "label": "Empty", "items": []},
        ))

    def test_pages(self):
        pages = self.commands.pages("big")
        self.assertEqual([len(page) for page in pages], [9, 9, 2])
        self.assertEqual([item.id for page in pages for item in page], [f"item{i}" for i in range(20)])
        self.assertEqual([len(page) for page in self.commands.pages("nine")], [9])
        self.assertEqual([len(page) for page in self.commands.pages("big", per_page=5)], [5, 5, 5, 5])
        self.assertEqual([len(page) for page in self.commands.pages("big", per_page=20)], [20])
        self.assertEqual([len(page) for page in self.commands.pages("big", 1)], [1] * 20)

    def test_known_category_always_has_a_page(self):
        self.assertEqual(self.commands.pages("empty"), [[]])
        self.assertEqual(self.commands.pages("unknown"), [])

    def test_page_wraps_around(self):
        self.assertEqual([item.id for item in self.commands.page("big", 2)], ["item18", "item19"])
        self.assertEqual([item.id for item in self.commands.page("big", 3)][0], "item0")
        self.assertEqual(self.commands.page("unknown", 0), [])
        self.assertEqual(self.commands.page("empty", 4), [])

    def test_invalid_page_size(self):
        for bad in (0, -1, 1.5, None, True):
            with self.assertRaises(ValueError):
                self.commands.pages("big", per_page=bad)
            with self.assertRaises(ValueError):
                self.commands.pages("unknown", per_page=bad)

    def test_category_pages_skip_empty_categories(self):
        self.assertEqual([[c.id for c in page] for page in self.commands.category_pages()], [["big", "nine"]])
        self.assertEqual([[c.id for c in page] for page in self.commands.category_pages(1)], [["big"], ["nine"]])

    def test_default_math_category_needs_two_pages(self):
        defaults = CommandSet.load(DEFAULT_PATH, False)
        self.assertEqual([len(page) for page in defaults.pages("math")], [9, 2])
        self.assertEqual([len(page) for page in defaults.pages("latex")], [9, 5])
        for category in defaults.categories:
            for page in defaults.pages(category.id):
                self.assertTrue(1 <= len(page) <= 9)


if __name__ == "__main__":
    unittest.main()
