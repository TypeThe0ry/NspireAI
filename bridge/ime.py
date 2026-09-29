"""Pinyin input method for the calculator page.

The calculator only collects the letters the user types; this module turns
them into candidates.  The dictionary is built once from two pip packages and
cached on disk:

* ``jieba``    its word list with frequencies (``dict.txt``)
* ``pypinyin`` the reading of every word and character

::

    <cache>/ime-v<N>.json.gz    the index (rebuilt when missing or outdated)
    <home>/ime-user.json        what the user picked, to rank it first next time

Lookup offers, in this order: what the user picked before for these letters,
words that spell the whole input, a sentence composed from several words, words
that the input abbreviates (``zg`` -> initials) or begins (``zhongg``), and
finally the words and characters that spell the first syllables, so that a
long input can always be entered piece by piece.
"""
from __future__ import annotations

import bisect
import gzip
import json
import logging
import math
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)

INDEX_VERSION = 2
MAX_LETTERS = 40            # COMP_CAP in src/page/page.c
MAX_WORD_CHARS = 8
MAX_TEXT_BYTES = 47         # IME_TEXT_CAP - 1 in src/page/page.c
PER_KEY = 200               # words kept per spelling
PER_PREFIX = 24             # words kept per short prefix / abbreviation
SHORT_PREFIX = 4            # prefixes up to this length are precomputed
MAX_USER_ENTRIES = 5000

DOMAIN_BOOST = 20           # what calculator users ask about ranks higher
DOMAIN_FLOOR = 20000
DOMAIN_WORDS = tuple("""
方程 解方程 方程组 一元二次方程 不等式 函数 二次函数 一次函数 反函数 三角函数 指数函数
对数函数 奇函数 偶函数 定义域 值域 单调性 周期 导数 求导 偏导数 微分 积分 定积分
不定积分 微积分 极限 级数 泰勒展开 梯度 因式分解 化简 展开 多项式 矩阵 行列式 向量
特征值 特征向量 线性代数 概率 统计 方差 标准差 平均数 中位数 期望 正弦 余弦 正切
对数 指数 平方 立方 平方根 根号 分数 小数 整数 实数 复数 虚数 有理数 无理数 质数
素数 因数 倍数 最大公约数 最小公倍数 数列 等差数列 等比数列 通项公式 求和 几何
三角形 椭圆 抛物线 双曲线 面积 体积 周长 半径 直径 斜率 截距 坐标 证明 定理 公式
计算 求解 求值 解释 步骤 答案 怎么解 怎么算 怎么做 物理 化学 速度 加速度 能量 功率
电压 电流 电阻 质量 密度 摩尔 化学方程式 翻译 总结 代码
""".split())


@dataclass(frozen=True)
class Candidate:
    text: str
    consumed: int   # how many of the typed letters this candidate replaces


def _is_cjk(text: str) -> bool:
    return bool(text) and all("一" <= char <= "鿿" for char in text)


def _spellings(syllable: str) -> tuple[str, ...]:
    """The ways a syllable is typed: ü is "v", and "lue"/"nue" are common."""
    syllable = syllable.replace("ü", "v")
    if syllable in ("lve", "nve"):
        return (syllable, syllable[0] + "ue")
    return (syllable,)


class PinyinIME:
    """Candidates for pinyin letters.  Thread-safe once loaded."""

    def __init__(self, cache_dir: Optional[Path] = None, user_dir: Optional[Path] = None):
        cache = cache_dir if cache_dir is not None else os.environ.get(
            "NSPIREAI_CACHE", str(Path.home() / ".cache" / "nspireai"))
        home = user_dir if user_dir is not None else os.environ.get(
            "NSPIREAI_HOME", str(Path.home() / ".config" / "nspireai"))
        self.cache_path = Path(cache) / f"ime-v{INDEX_VERSION}.json.gz"
        self.user_path = Path(home) / "ime-user.json"
        self.ready = False
        self.error: Optional[str] = None
        self.words: dict[str, list[tuple[str, int]]] = {}     # spelling -> [(word, frequency)]
        self.prefixes: dict[str, list[tuple[str, str]]] = {}  # short prefix -> [(word, spelling)]
        self.initials: dict[str, list[str]] = {}              # "zg" -> words
        self.syllables: frozenset[str] = frozenset()
        self.keys: list[str] = []
        self.total = 1
        self.user: dict[str, dict[str, int]] = {}
        self._lock = threading.RLock()
        self._load_lock = threading.Lock()

    # ----- loading -------------------------------------------------------

    def load(self) -> bool:
        """Load the index, building it first when needed.  Never raises."""
        with self._load_lock:
            if self.ready:
                return True
            try:
                data = self._read_cache()
                if data is None:
                    data = build_index(*_sources(), domain=DOMAIN_WORDS)
                    self._write_cache(data)
                self._install(data)
                self._load_user()
                self.ready = True
                self.error = None
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                log.warning("pinyin input is unavailable: %s", self.error)
            return self.ready

    def _read_cache(self) -> Optional[dict]:
        try:
            with gzip.open(self.cache_path, "rt", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return None
        except Exception as exc:
            log.warning("ignoring unreadable %s: %s", self.cache_path, exc)
            return None
        if not isinstance(data, dict) or data.get("version") != INDEX_VERSION \
                or data.get("sources") != _source_stamp():
            return None
        return data

    def _write_cache(self, data: dict) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            handle, name = tempfile.mkstemp(dir=self.cache_path.parent, prefix=".ime-")
            with os.fdopen(handle, "wb") as raw, \
                    gzip.open(raw, "wt", encoding="utf-8", compresslevel=5) as out:
                json.dump(data, out, ensure_ascii=False, separators=(",", ":"))
            os.replace(name, self.cache_path)
        except OSError as exc:
            log.warning("cannot cache the pinyin index in %s: %s", self.cache_path, exc)

    def _install(self, data: dict) -> None:
        self.words = {spelling: [(str(word), int(frequency)) for word, frequency in bucket]
                      for spelling, bucket in data["words"].items()}
        self.prefixes = {prefix: [(str(word), str(spelling)) for word, spelling in items]
                         for prefix, items in data["prefixes"].items()}
        self.initials = {short: [str(word) for word in items]
                         for short, items in data["initials"].items()}
        self.syllables = frozenset(data["syllables"])
        self.total = max(1, int(data["total"]))
        self.keys = sorted(self.words)
        self._syllable_prefixes = frozenset(
            syllable[:length] for syllable in self.syllables
            for length in range(1, len(syllable) + 1))

    # ----- what the user picked ------------------------------------------

    def _load_user(self) -> None:
        try:
            raw = json.loads(self.user_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable %s: %s", self.user_path, exc)
            return
        if not isinstance(raw, dict):
            return
        for letters, picks in raw.items():
            if isinstance(letters, str) and isinstance(picks, dict):
                clean = {text: int(count) for text, count in picks.items()
                         if isinstance(text, str) and isinstance(count, int) and count > 0}
                if clean:
                    self.user[letters] = clean

    def _save_user(self) -> None:
        try:
            self.user_path.parent.mkdir(parents=True, exist_ok=True)
            handle, name = tempfile.mkstemp(dir=self.user_path.parent, prefix=".ime-user-")
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(self.user, out, ensure_ascii=False, sort_keys=True)
            os.replace(name, self.user_path)
        except OSError as exc:
            log.warning("cannot save %s: %s", self.user_path, exc)

    def learn(self, letters: str, text: str) -> None:
        """Remember that the user chose `text` for `letters`."""
        letters = _letters(letters)
        if not letters or not _is_cjk(text) or len(text) > 2 * MAX_WORD_CHARS:
            return
        with self._lock:
            picks = self.user.setdefault(letters, {})
            picks[text] = min(picks.get(text, 0) + 1, 1_000_000)
            if len(self.user) > MAX_USER_ENTRIES:
                weakest = sorted(self.user, key=lambda key: sum(self.user[key].values()))
                for key in weakest[:len(self.user) - MAX_USER_ENTRIES]:
                    del self.user[key]
            self._save_user()

    # ----- lookup --------------------------------------------------------

    def candidates(self, letters: str, limit: int = 60) -> list[Candidate]:
        letters = _letters(letters)
        if not self.ready or not letters or limit <= 0:
            return []
        found: list[Candidate] = []
        seen: set[str] = set()

        def offer(text: str, consumed: int) -> None:
            if text not in seen and len(text.encode("utf-8")) <= MAX_TEXT_BYTES:
                seen.add(text)
                found.append(Candidate(text, consumed))

        size = len(letters)
        with self._lock:
            picked = sorted(self.user.get(letters, {}).items(), key=lambda item: -item[1])
        for text, _count in picked[:5]:
            offer(text, size)

        whole = self.words.get(letters, ())
        for word, _frequency in whole[:3]:
            offer(word, size)
        sentence = self._sentence(letters)
        if sentence:
            offer(sentence, size)
        for word, _frequency in whole[3:]:
            offer(word, size)

        if not whole:
            for word in self.initials.get(letters, ()):
                offer(word, size)
        # Longer words that begin like this: a few when the letters already
        # spell something, all of them when the input is unfinished.
        for word in list(self._completions(letters))[:4 if whole else PER_PREFIX]:
            offer(word, size)

        # The first syllables on their own, longest first: the rest of the
        # letters stay in the composition for the next pick.
        boundaries = self._boundaries(letters)
        for end in sorted((end for end in boundaries if end < size), reverse=True):
            head = letters[:end]
            with self._lock:
                earlier = sorted(self.user.get(head, {}).items(), key=lambda item: -item[1])
            for text, _count in earlier[:3]:
                offer(text, end)
            for word, _frequency in self.words.get(head, ())[:PER_KEY]:
                offer(word, end)
            if len(found) >= limit * 3:
                break
        return found[:limit]

    def _boundaries(self, letters: str) -> set[int]:
        """Positions where a complete syllable ends and the rest still reads
        as pinyin (complete syllables, the last one possibly unfinished)."""
        size = len(letters)
        tail_ok = [False] * (size + 1)
        tail_ok[size] = True
        for start in range(size - 1, -1, -1):
            if letters[start:] in self._syllable_prefixes:
                tail_ok[start] = True
                continue
            for end in range(start + 1, min(size, start + 6) + 1):
                if tail_ok[end] and letters[start:end] in self.syllables:
                    tail_ok[start] = True
                    break
        reachable = [False] * (size + 1)
        reachable[0] = True
        for start in range(size):
            if not reachable[start]:
                continue
            for end in range(start + 1, min(size, start + 6) + 1):
                if letters[start:end] in self.syllables:
                    reachable[end] = True
        return {end for end in range(1, size + 1) if reachable[end] and tail_ok[end]}

    def _sentence(self, letters: str) -> str:
        """The most likely way to read all the letters as several words."""
        size = len(letters)
        best: list[Optional[tuple[float, str]]] = [None] * (size + 1)
        best[0] = (0.0, "")
        for start in range(size):
            here = best[start]
            if here is None:
                continue
            for end in range(start + 1, min(size, start + 6 * MAX_WORD_CHARS) + 1):
                entries = self.words.get(letters[start:end])
                if not entries:
                    continue
                with self._lock:
                    picks = self.user.get(letters[start:end])
                if picks:
                    word = max(picks, key=picks.get)
                    frequency = entries[0][1] + 1
                else:
                    word, frequency = entries[0]
                # One unit of cost per word on top of its rarity, so that
                # fewer, longer words win.
                score = here[0] + math.log(max(1, frequency) / self.total) - 1.0
                if best[end] is None or score > best[end][0]:
                    best[end] = (score, here[1] + word)
        result = best[size]
        if result is None or len(result[1]) > 2 * MAX_WORD_CHARS:
            return ""
        return result[1]

    def _completions(self, letters: str) -> Iterable[str]:
        """Words whose spelling begins with the letters (unfinished input)."""
        if len(letters) <= SHORT_PREFIX:
            return [word for word, _spelling in self.prefixes.get(letters, ())]
        low = bisect.bisect_left(self.keys, letters)
        high = bisect.bisect_left(self.keys, letters + "\x7f")
        matches: list[tuple[int, str]] = []
        for key in self.keys[low:min(high, low + 4000)]:
            if key == letters:
                continue
            word, frequency = self.words[key][0]
            matches.append((frequency, word))
        matches.sort(key=lambda item: -item[0])
        return [word for _frequency, word in matches[:PER_PREFIX]]


def _letters(text: str) -> str:
    return "".join(char for char in str(text).lower() if "a" <= char <= "z")[:MAX_LETTERS]


# ----- building the index ------------------------------------------------

def _sources() -> tuple[list[tuple[str, int]], "object"]:
    """The word list of jieba and the reading function of pypinyin."""
    import jieba
    import pypinyin

    entries: list[tuple[str, int]] = []
    path = Path(jieba.__file__).with_name("dict.txt")
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            parts = line.split()
            if len(parts) < 2 or not parts[1].isdigit():
                continue
            if _is_cjk(parts[0]) and len(parts[0]) <= MAX_WORD_CHARS:
                entries.append((parts[0], int(parts[1])))

    def readings(word: str) -> list[list[str]]:
        """Per character, the syllables it may be read as in this word."""
        if len(word) == 1:
            found = pypinyin.pinyin(word, style=pypinyin.Style.NORMAL, heteronym=True,
                                    errors="ignore")
            return [found[0]] if found else []
        return [[syllable] for syllable in
                pypinyin.lazy_pinyin(word, errors="ignore")]

    return entries, readings


def _source_stamp() -> str:
    try:
        import jieba
        import pypinyin
    except ImportError:
        return "missing"
    return f"jieba-{jieba.__version__};pypinyin-{pypinyin.__version__}"


def build_index(entries: Iterable[tuple[str, int]], readings, stamp: Optional[str] = None,
                domain: Iterable[str] = ()) -> dict:
    """Index `(word, frequency)` pairs by how they are typed.

    `readings(word)` gives, per character, the syllables it may be read as
    (for a single character: all its readings, the usual one first).
    `domain` words are what calculator users ask about; they rank higher.
    """
    known: dict[str, int] = {}
    for word, frequency in entries:
        known[word] = max(known.get(word, 0), int(frequency))
    for word in domain:
        known[word] = max(known.get(word, 0) * DOMAIN_BOOST, DOMAIN_FLOOR)

    words: dict[str, dict[str, int]] = {}
    initials: dict[str, dict[str, int]] = {}
    syllables: set[str] = set()
    evidence: dict[str, dict[str, int]] = {}   # character -> reading -> frequency in words
    total = 0

    def typed(syllable: str) -> tuple[str, ...]:
        options = _spellings(syllable)
        return options if all(item.isascii() and item.isalpha() for item in options) else ()

    for word, frequency in known.items():
        if len(word) == 1:
            continue
        per_char = readings(word)
        if len(per_char) != len(word) or not all(per_char):
            continue
        spelled = [""]
        for choices in per_char:
            options = typed(choices[0])
            if not options:
                spelled = []
                break
            spelled = [head + item for head in spelled for item in options][:4]
        if not spelled:
            continue
        total += frequency
        for char, choices in zip(word, per_char):
            bucket = evidence.setdefault(char, {})
            bucket[choices[0]] = bucket.get(choices[0], 0) + frequency
        for spelling in spelled:
            bucket = words.setdefault(spelling, {})
            bucket[word] = max(bucket.get(word, 0), frequency)
        if 2 <= len(word) <= 6:
            short = "".join(typed(choices[0])[0][0] for choices in per_char)
            bucket = initials.setdefault(short, {})
            bucket[word] = max(bucket.get(word, 0), frequency)

    # A character ranks by its use on its own and inside words.  jieba only
    # counts the former, and a reading that no word uses is a rare one.
    for char in sorted(set(evidence) | {word for word in known if len(word) == 1}):
        own = known.get(char, 0)
        found = readings(char)
        options = list(found[0]) if found else []
        seen = evidence.get(char, {})
        for syllable in seen:
            if syllable not in options:
                options.append(syllable)
        if not options:
            continue
        weight = {syllable: seen.get(syllable, 0) for syllable in options}
        weight[options[0]] += own + 1
        base = own + 0.3 * sum(seen.values())
        share = sum(weight.values())
        total += own
        for syllable in options:
            frequency = max(1, int(base * weight[syllable] / share))
            for spelling in typed(syllable):
                syllables.add(spelling)
                bucket = words.setdefault(spelling, {})
                bucket[char] = max(bucket.get(char, 0), frequency)

    ranked = {spelling: sorted(bucket.items(), key=lambda item: (-item[1], item[0]))[:PER_KEY]
              for spelling, bucket in words.items()}
    prefixes: dict[str, list[tuple[int, str, str]]] = {}
    for spelling, bucket in ranked.items():
        word, frequency = bucket[0]
        for length in range(1, min(SHORT_PREFIX, len(spelling) - 1) + 1):
            prefixes.setdefault(spelling[:length], []).append((frequency, word, spelling))
    return {
        "version": INDEX_VERSION,
        "sources": stamp if stamp is not None else _source_stamp(),
        "words": ranked,
        "prefixes": {prefix: [(word, spelling) for _f, word, spelling in
                              sorted(items, key=lambda item: (-item[0], item[1]))[:PER_PREFIX]]
                     for prefix, items in prefixes.items()},
        "initials": {short: [word for word, _f in
                             sorted(bucket.items(), key=lambda item: (-item[1], item[0]))[:PER_PREFIX]]
                     for short, bucket in initials.items()},
        "syllables": sorted(syllables),
        "total": max(1, total),
    }
