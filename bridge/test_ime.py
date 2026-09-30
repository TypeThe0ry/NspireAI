from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bridge import ime
from bridge.ime import Candidate, PinyinIME, build_index

# A dictionary small enough to reason about: word, frequency, syllables.
WORDS = [
    ("你", 900, ["ni"]), ("尼", 50, ["ni"]), ("好", 800, ["hao", "hao"]), ("号", 300, ["hao"]),
    ("你好", 500, ["ni", "hao"]), ("解", 100, ["jie", "xie"]), ("她", 5000, ["ta", "jie"]),
    ("方程", 400, ["fang", "cheng"]), ("解放", 300, ["jie", "fang"]),
    ("中国", 900, ["zhong", "guo"]), ("中", 700, ["zhong"]), ("国", 600, ["guo"]),
    ("这个", 800, ["zhe", "ge"]), ("西安", 200, ["xi", "an"]), ("先", 600, ["xian"]),
    ("略", 100, ["lüe"]), ("女", 300, ["nü"]), ("的", 9000, ["de"]), ("导数", 3, ["dao", "shu"]),
    ("得到", 700, ["de", "dao"]), ("属", 500, ["shu"]), ("程", 80, ["cheng"]), ("方", 90, ["fang"]),
]
SYLLABLES = {word: syllables for word, _frequency, syllables in WORDS}


def readings(word: str) -> list[list[str]]:
    if word in SYLLABLES and len(word) == 1:
        return [SYLLABLES[word]]
    if word in SYLLABLES:
        return [[syllable] for syllable in SYLLABLES[word]]
    return [[SYLLABLES[char][0]] for char in word if char in SYLLABLES]


def engine(directory: str, domain=()) -> PinyinIME:
    found = PinyinIME(Path(directory) / "cache", Path(directory) / "home")
    found._install(build_index([(word, frequency) for word, frequency, _s in WORDS],
                               readings, stamp="test", domain=domain))
    found.ready = True
    return found


def texts(candidates: list[Candidate]) -> list[str]:
    return [candidate.text for candidate in candidates]


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.ime = engine(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_whole_word_first_then_its_first_syllable(self):
        found = self.ime.candidates("nihao")
        self.assertEqual(found[0], Candidate("你好", 5))
        self.assertIn(Candidate("你", 2), found)
        self.assertLess(texts(found).index("你"), texts(found).index("尼"))

    def test_a_reading_that_no_word_uses_ranks_last(self):
        # 她 is far more frequent than 解, but "jie" is not how it is read.
        found = texts(self.ime.candidates("jie"))
        self.assertEqual(found[0], "解")
        self.assertLess(found.index("解"), found.index("她"))

    def test_sentence_is_composed_from_words(self):
        found = self.ime.candidates("jiefangcheng")
        self.assertEqual(found[0], Candidate("解方程", 12))
        self.assertIn(Candidate("解放", 7), found)

    def test_syllable_boundaries_are_ambiguous(self):
        found = texts(self.ime.candidates("xian"))
        self.assertIn("先", found)
        self.assertIn("西安", found)

    def test_unfinished_and_abbreviated_input(self):
        self.assertEqual(self.ime.candidates("zhongg")[0], Candidate("中国", 6))
        self.assertEqual(self.ime.candidates("zg")[0], Candidate("中国", 2))
        self.assertEqual(self.ime.candidates("z")[0].consumed, 1)

    def test_u_umlaut_spellings(self):
        self.assertEqual(texts(self.ime.candidates("lve"))[0], "略")
        self.assertEqual(texts(self.ime.candidates("lue"))[0], "略")
        self.assertEqual(texts(self.ime.candidates("nv"))[0], "女")

    def test_consumed_never_exceeds_the_input(self):
        for letters in ("nihao", "jiefangcheng", "zhongg", "xq", "nihaox"):
            for candidate in self.ime.candidates(letters):
                self.assertTrue(0 < candidate.consumed <= len(letters), (letters, candidate))
                self.assertLessEqual(len(candidate.text.encode("utf-8")), ime.MAX_TEXT_BYTES)

    def test_input_is_cleaned_and_bounded(self):
        self.assertEqual(self.ime.candidates(""), [])
        self.assertEqual(self.ime.candidates("123"), [])
        self.assertEqual(self.ime.candidates("NiHao")[0].text, "你好")
        self.assertEqual(self.ime.candidates("nihao", limit=1), [Candidate("你好", 5)])
        self.ime.candidates("ni" * 200)  # longer than the page can send: no error

    def test_no_duplicates(self):
        found = texts(self.ime.candidates("nihao"))
        self.assertEqual(len(found), len(set(found)))

    def test_not_ready(self):
        blank = PinyinIME(Path(self.directory.name) / "c", Path(self.directory.name) / "h")
        self.assertEqual(blank.candidates("ni"), [])

    def test_domain_words_rank_higher(self):
        self.assertEqual(self.ime.candidates("dedaoshu")[0].text, "得到属")
        boosted = engine(self.directory.name, domain=("导数",))
        self.assertEqual(boosted.candidates("dedaoshu")[0].text, "的导数")


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.ime = engine(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_a_pick_ranks_first_next_time_and_survives_a_restart(self):
        self.assertEqual(self.ime.candidates("ni")[0].text, "你")
        self.ime.learn("ni", "尼")
        self.assertEqual(self.ime.candidates("ni")[0], Candidate("尼", 2))
        # ... also as the first part of a longer input
        self.assertLess(texts(self.ime.candidates("nihao")).index("尼"),
                        texts(self.ime.candidates("nihao")).index("你"))
        again = engine(self.directory.name)
        again._load_user()
        self.assertEqual(again.candidates("ni")[0].text, "尼")

    def test_a_learned_phrase(self):
        self.ime.learn("dedaoshu", "的导数")
        self.assertEqual(self.ime.candidates("dedaoshu")[0], Candidate("的导数", 8))

    def test_only_chinese_text_is_learned(self):
        self.ime.learn("ni", "<script>")
        self.ime.learn("", "你")
        self.ime.learn("ni", "")
        self.assertEqual(self.ime.user, {})

    def test_a_damaged_user_file_is_ignored(self):
        path = Path(self.directory.name) / "home" / "ime-user.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        for content in ("{not json", "[1, 2]", json.dumps({"ni": {"尼": "many"}, "x": 3})):
            path.write_text(content, encoding="utf-8")
            fresh = engine(self.directory.name)
            fresh._load_user()
            self.assertEqual(fresh.user, {})


class CacheTests(unittest.TestCase):
    def test_the_index_is_cached_and_an_outdated_cache_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as directory:
            first = PinyinIME(Path(directory), Path(directory))
            data = build_index([("你", 9)], readings, stamp=ime._source_stamp())
            first._write_cache(data)
            self.assertEqual(first._read_cache()["words"], {"ni": [["你", 9]]})
            first._write_cache({**data, "version": -1})
            self.assertIsNone(first._read_cache())
            first._write_cache({**data, "sources": "other versions"})
            self.assertIsNone(first._read_cache())
            first.cache_path.write_bytes(b"not gzip")
            self.assertIsNone(first._read_cache())

    def test_load_reports_failure_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as directory:
            broken = PinyinIME(Path(directory), Path(directory))
            original = ime._sources
            ime._sources = lambda: (_ for _ in ()).throw(ImportError("no jieba"))
            try:
                self.assertFalse(broken.load())
            finally:
                ime._sources = original
            self.assertFalse(broken.ready)
            self.assertIn("no jieba", broken.error)
            self.assertEqual(broken.candidates("ni"), [])


try:
    import jieba  # noqa: F401
    import pypinyin  # noqa: F401
    HAVE_DICTIONARY = True
except ImportError:
    HAVE_DICTIONARY = False


@unittest.skipUnless(HAVE_DICTIONARY, "jieba and pypinyin are not installed")
class DictionaryTests(unittest.TestCase):
    """The real dictionary; its index is kept between runs (seconds to build)."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.TemporaryDirectory()
        cache = Path(tempfile.gettempdir()) / "nspireai-test-cache"
        cls.ime = PinyinIME(cache, Path(cls.home.name))
        assert cls.ime.load(), cls.ime.error

    @classmethod
    def tearDownClass(cls):
        cls.home.cleanup()

    def test_everyday_and_math_input(self):
        for letters, expected in {
            "nihao": "你好", "zhongguo": "中国", "women": "我们", "shenme": "什么",
            "hanshu": "函数", "jiefangcheng": "解方程", "yinshifenjie": "因式分解",
            "zhegefangchengzenmejie": "这个方程怎么解",
            "qiuzhegehanshudedaoshu": "求这个函数的导数",
            "zg": "中国", "jf": "积分",
        }.items():
            found = self.ime.candidates(letters)
            self.assertEqual((found[0].text, found[0].consumed), (expected, len(letters)), letters)

    def test_math_words_and_sentence_particles(self):
        for letters, expected in {
            "zhegehanshudejizhishiduoshao": "这个函数的极值是多少",
            "yiyuanerci": "一元二次",
            "mingtianxiayuma": "明天下雨吗",
            "nizaiganshenmene": "你在干什么呢",
        }.items():
            self.assertEqual(self.ime.candidates(letters)[0].text, expected, letters)
        # Only at the end: "ma" inside a sentence keeps its own reading.
        self.assertNotIn("吗", self.ime.candidates("mashang")[0].text)

    def test_first_characters(self):
        for letters, expected in {"ni": "你", "jie": "解", "shu": "数", "de": "的"}.items():
            self.assertEqual(self.ime.candidates(letters)[0].text, expected, letters)

    def test_every_candidate_fits_the_page(self):
        for letters in ("zhongguorenmin", "a", "xianzai", "qingbawofanyichengyingwen"):
            found = self.ime.candidates(letters, 60)
            self.assertTrue(found, letters)
            for candidate in found:
                self.assertTrue(0 < candidate.consumed <= len(letters))
                self.assertLessEqual(len(candidate.text.encode("utf-8")), ime.MAX_TEXT_BYTES)


if __name__ == "__main__":
    unittest.main()
