import json
import logging
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

try:
    from . import sessions
    from .sessions import DEFAULT_TITLE, Session, SessionStore, context_messages, make_title
except ImportError:  # direct `python bridge/test_sessions.py`
    import sessions
    from sessions import DEFAULT_TITLE, Session, SessionStore, context_messages, make_title


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.home = Path(self._directory.name)
        self.store = SessionStore(self.home)
        logging.disable(logging.WARNING)
        self.addCleanup(logging.disable, logging.NOTSET)

    def session_file(self, session_id: int) -> Path:
        return self.home / "sessions" / f"{session_id}.json"


class HomeTests(unittest.TestCase):
    def test_home_comes_from_the_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"NSPIREAI_HOME": directory}):
                store = SessionStore()
                self.assertEqual(store.home, Path(directory))
                store.active()
                self.assertTrue((Path(directory) / "sessions" / "1.json").exists())
                self.assertTrue((Path(directory) / "sessions" / "index.json").exists())

    def test_default_home(self):
        environment = {key: value for key, value in os.environ.items() if key != "NSPIREAI_HOME"}
        with mock.patch.dict(os.environ, environment, clear=True):
            self.assertEqual(sessions.default_home(), Path.home() / ".config" / "nspireai")


class SessionStoreTests(StoreTestCase):
    def test_active_creates_the_first_session(self):
        self.assertEqual(self.store.list(), [])
        session = self.store.active()
        self.assertEqual(session.id, 1)
        self.assertEqual(session.title, DEFAULT_TITLE)
        self.assertEqual(session.messages, [])
        self.assertEqual(self.store.active().id, 1)
        self.assertEqual([summary.id for summary in self.store.list()], [1])

    def test_timestamps_are_iso(self):
        session = self.store.create()
        created = datetime.fromisoformat(session.created)
        self.assertIsNotNone(created.tzinfo)
        updated = self.store.append(session.id, "user", "hi").updated
        self.assertGreater(datetime.fromisoformat(updated), created)
        self.assertEqual(self.store.get(session.id).created, session.created)

    def test_create_becomes_active_and_ids_increase(self):
        first = self.store.create()
        second = self.store.create("Homework")
        self.assertEqual((first.id, second.id), (1, 2))
        self.assertEqual(second.title, "Homework")
        self.assertEqual(self.store.active().id, 2)

    def test_the_lowest_free_id_is_reused(self):
        for _ in range(3):
            self.store.create()
        self.store.delete(2)
        self.assertEqual(self.store.create().id, 2)   # the gap is filled first
        self.assertEqual(self.store.create().id, 4)
        for session_id in (4, 3, 2):
            self.store.delete(session_id)
        self.store.delete(1)          # the store creates a fresh session here
        self.assertEqual([summary.id for summary in self.store.list()], [1])
        self.assertEqual(SessionStore(self.home).create().id, 2)

    def test_ids_come_from_the_files_not_the_index(self):
        for _ in range(3):
            self.store.create()
        (self.home / "sessions" / "index.json").unlink()
        self.assertEqual(SessionStore(self.home).create().id, 4)
        # A stale index cannot hand out an id that is still on disk.
        (self.home / "sessions" / "index.json").write_text('{"version": 1, "active": 1, "next_id": 2, "order": [1]}')
        self.assertEqual(SessionStore(self.home).create().id, 5)

    def test_select(self):
        first = self.store.create()
        self.store.create()
        self.assertEqual(self.store.select(first.id).id, first.id)
        self.assertEqual(self.store.active().id, first.id)
        self.assertEqual([s.id for s in self.store.list() if s.active], [first.id])
        with self.assertRaises(KeyError):
            self.store.select(99)
        self.assertEqual(self.store.active().id, first.id)

    def test_get_unknown_session(self):
        for bad in (1, 0, -1, "1", None, True):
            with self.assertRaises(KeyError):
                self.store.get(bad)

    def test_append_and_message_shape(self):
        session = self.store.create()
        self.store.append(session.id, "user", "x^2-1", cmd="factorize")
        updated = self.store.append(session.id, "assistant", "(x-1)(x+1)", think="difference of squares")
        self.assertEqual(updated.messages, [
            {"role": "user", "content": "x^2-1", "think": None, "cmd": "factorize"},
            {"role": "assistant", "content": "(x-1)(x+1)", "think": "difference of squares", "cmd": None},
        ])
        self.assertEqual(self.store.get(session.id).messages, updated.messages)
        with self.assertRaises(ValueError):
            self.store.append(session.id, "system", "nope")
        with self.assertRaises(ValueError):
            self.store.append(session.id, "user", None)
        with self.assertRaises(KeyError):
            self.store.append(42, "user", "hello")

    def test_list_is_sorted_by_update_time(self):
        first = self.store.create()
        second = self.store.create()
        third = self.store.create()
        self.assertEqual([s.id for s in self.store.list()], [third.id, second.id, first.id])
        self.store.append(first.id, "user", "hello")
        self.assertEqual([s.id for s in self.store.list()], [first.id, third.id, second.id])
        summary = self.store.list()[0]
        self.assertEqual((summary.title, summary.message_count, summary.active), ("hello", 1, False))
        index = json.loads((self.home / "sessions" / "index.json").read_text())
        self.assertEqual(index["order"], [first.id, third.id, second.id])
        self.assertEqual(index["active"], third.id)

    def test_auto_title(self):
        session = self.store.create()
        self.store.append(session.id, "assistant", "Hello, how can I help?")
        self.assertEqual(self.store.get(session.id).title, DEFAULT_TITLE)
        self.store.append(session.id, "user", "  What   is\n the\tderivative of sin(x) * cos(x)?  ")
        self.assertEqual(self.store.get(session.id).title, "What is the derivative o")
        self.assertEqual(len(self.store.get(session.id).title), 24)
        self.store.append(session.id, "user", "Another question")
        self.assertEqual(self.store.get(session.id).title, "What is the derivative o")

    def test_auto_title_counts_cjk_as_one(self):
        session = self.store.create()
        text = "求解一元二次方程的一般解法并给出详细的推导过程以及判别式的意义"
        self.store.append(session.id, "user", text)
        title = self.store.get(session.id).title
        self.assertEqual(title, text[:24])
        self.assertEqual(len(title), 24)
        self.assertEqual(make_title("短标题"), "短标题")
        self.assertEqual(make_title("   \n "), DEFAULT_TITLE)

    def test_rename_wins_over_auto_title(self):
        session = self.store.create()
        self.store.rename(session.id, "  Calculus   notes ")
        self.store.append(session.id, "user", "first question")
        self.assertEqual(self.store.get(session.id).title, "Calculus notes")
        self.store.rename(session.id, "")
        self.assertEqual(self.store.get(session.id).title, "first question")
        with self.assertRaises(KeyError):
            self.store.rename(77, "x")

    def test_explicit_title_is_kept(self):
        session = self.store.create("Physics")
        self.store.append(session.id, "user", "What is torque?")
        self.assertEqual(self.store.get(session.id).title, "Physics")
        self.store.clear(session.id)
        self.assertEqual(self.store.get(session.id).title, "Physics")

    def test_clear(self):
        session = self.store.create()
        self.store.append(session.id, "user", "hello")
        cleared = self.store.clear(session.id)
        self.assertEqual(cleared.messages, [])
        self.assertEqual(cleared.title, DEFAULT_TITLE)
        self.assertEqual(self.store.get(session.id).messages, [])
        self.store.append(session.id, "user", "second life")
        self.assertEqual(self.store.get(session.id).title, "second life")

    def test_delete_inactive_keeps_active(self):
        first = self.store.create()
        second = self.store.create()
        self.assertEqual(self.store.delete(first.id).id, second.id)
        self.assertEqual(self.store.active().id, second.id)
        self.assertFalse(self.session_file(first.id).exists())
        with self.assertRaises(KeyError):
            self.store.delete(first.id)

    def test_delete_active_selects_most_recently_updated(self):
        first = self.store.create()
        second = self.store.create()
        third = self.store.create()
        self.store.append(first.id, "user", "most recent activity")
        self.store.select(third.id)
        self.assertEqual(self.store.delete(third.id).id, first.id)
        self.assertEqual(self.store.active().id, first.id)
        self.assertEqual(self.store.delete(first.id).id, second.id)
        self.assertEqual(self.store.active().id, second.id)

    def test_delete_last_session_creates_a_fresh_one(self):
        session = self.store.create()
        self.store.append(session.id, "user", "hello")
        fresh = self.store.delete(session.id)
        self.assertEqual(fresh.id, 1)   # the numbering starts over
        self.assertEqual(fresh.messages, [])
        self.assertEqual(self.store.active().id, fresh.id)
        self.assertEqual(len(self.store.list()), 1)

    def test_persistence_across_instances(self):
        first = self.store.create()
        self.store.append(first.id, "user", "中文问题")
        self.store.append(first.id, "assistant", "回答 $x^2$", think="思考")
        second = self.store.create("Other")
        self.store.select(first.id)

        other = SessionStore(self.home)
        self.assertEqual(other.active().id, first.id)
        self.assertEqual([s.id for s in other.list()], [second.id, first.id])
        loaded = other.get(first.id)
        self.assertEqual(loaded.title, "中文问题")
        self.assertEqual([m["content"] for m in loaded.messages], ["中文问题", "回答 $x^2$"])
        self.assertEqual(loaded.messages[1]["think"], "思考")
        other.append(second.id, "user", "written by the second store")
        self.assertEqual(self.store.get(second.id).messages[0]["content"], "written by the second store")
        self.assertEqual(self.store.create().id, 3)
        self.assertEqual(other.create().id, 4)

    def test_files_are_utf8_json(self):
        session = self.store.create()
        self.store.append(session.id, "user", "中文")
        raw = self.session_file(session.id).read_bytes()
        self.assertIn("中文".encode("utf-8"), raw)
        self.assertEqual(json.loads(raw)["id"], session.id)

    def test_writes_are_atomic(self):
        session = self.store.create()
        self.store.append(session.id, "user", "kept")
        replaced = []
        real_replace = os.replace

        def spy(source, target):
            replaced.append((Path(source), Path(target)))
            self.assertEqual(Path(source).parent, Path(target).parent)
            self.assertTrue(Path(source).exists())
            return real_replace(source, target)

        with mock.patch.object(sessions.os, "replace", side_effect=spy):
            self.store.append(session.id, "assistant", "reply")
        self.assertIn(self.session_file(session.id), [target for _source, target in replaced])
        leftovers = [p.name for p in (self.home / "sessions").iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_failed_write_keeps_the_old_file(self):
        session = self.store.create()
        self.store.append(session.id, "user", "kept")
        with mock.patch.object(sessions.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.append(session.id, "assistant", "lost")
        self.assertEqual([m["content"] for m in self.store.get(session.id).messages], ["kept"])
        leftovers = [p.name for p in (self.home / "sessions").iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_corrupted_session_file_is_skipped(self):
        first = self.store.create()
        second = self.store.create()
        self.store.append(first.id, "user", "good")
        self.session_file(second.id).write_text("{ this is not json", encoding="utf-8")
        self.session_file(7).write_bytes(b"\xff\xfe\x00broken")
        self.session_file(8).write_text("[1, 2, 3]", encoding="utf-8")
        self.session_file(9).write_text(json.dumps({"id": 10, "messages": []}), encoding="utf-8")
        (self.home / "sessions" / "notes.txt").write_text("ignored", encoding="utf-8")

        store = SessionStore(self.home)
        self.assertEqual([s.id for s in store.list()], [first.id])
        # The index still points at the corrupted session: fall back, never raise.
        self.assertEqual(store.active().id, first.id)
        with self.assertRaises(KeyError):
            store.get(second.id)
        # Ids of unreadable files are not handed out again (3 is the first free one).
        self.assertEqual(store.create().id, 3)

    def test_corrupted_index_is_rebuilt(self):
        first = self.store.create()
        second = self.store.create()
        self.store.append(first.id, "user", "recent")
        index_path = self.home / "sessions" / "index.json"
        for garbage in ("", "null", "[]", "{broken", json.dumps({"active": "x", "next_id": -4})):
            index_path.write_text(garbage, encoding="utf-8")
            store = SessionStore(self.home)
            self.assertEqual([s.id for s in store.list()], [first.id, second.id])
            self.assertEqual(store.active().id, first.id)
        self.assertEqual(json.loads(index_path.read_text())["active"], first.id)

    def test_malformed_messages_are_dropped(self):
        session = self.store.create()
        data = json.loads(self.session_file(session.id).read_text())
        data["messages"] = [
            {"role": "user", "content": "ok"},
            {"role": "robot", "content": "bad role"},
            {"role": "assistant"},
            "not a message",
            {"role": "assistant", "content": "fine", "think": 5, "cmd": ["x"]},
        ]
        self.session_file(session.id).write_text(json.dumps(data), encoding="utf-8")
        loaded = self.store.get(session.id)
        self.assertEqual(loaded.messages, [
            {"role": "user", "content": "ok", "think": None, "cmd": None},
            {"role": "assistant", "content": "fine", "think": None, "cmd": None},
        ])

    def test_missing_directory_does_not_raise(self):
        store = SessionStore(self.home / "does" / "not" / "exist")
        self.assertEqual(store.list(), [])
        self.assertEqual(store.active().id, 1)


class InterfaceTests(StoreTestCase):
    """The names the page host relies on."""

    def test_context_messages_is_a_module_level_function(self):
        import importlib
        import inspect

        module = importlib.import_module(sessions.__name__)
        self.assertTrue(inspect.isfunction(module.context_messages))
        parameters = inspect.signature(module.context_messages).parameters
        self.assertEqual(list(parameters)[:2], ["session", "max_chars"])
        self.assertEqual(parameters["max_chars"].default, 24000)
        self.assertTrue(all(
            parameter.default is not inspect.Parameter.empty
            for name, parameter in parameters.items() if name != "session"
        ))

    def test_sessions_expose_id_title_and_messages(self):
        created = self.store.create()
        self.store.append(created.id, "user", "hello", think="high", cmd="solve")
        for session in (
            self.store.active(),
            self.store.get(created.id),
            self.store.select(created.id),
            self.store.create(),
        ):
            self.assertIsInstance(session.id, int)
            self.assertIsInstance(session.title, str)
            self.assertIsInstance(session.messages, list)
        message = self.store.get(created.id).messages[0]
        self.assertEqual(set(message), {"role", "content", "think", "cmd"})
        self.assertEqual(message, {"role": "user", "content": "hello", "think": "high", "cmd": "solve"})
        self.assertEqual(context_messages(self.store.get(created.id)), [{"role": "user", "content": "hello"}])

    def test_list_entries_expose_id_and_title(self):
        first = self.store.create("First")
        second = self.store.create()
        entries = self.store.list()
        self.assertEqual([(entry.id, entry.title) for entry in entries],
                         [(second.id, DEFAULT_TITLE), (first.id, "First")])


class ContextTests(unittest.TestCase):
    @staticmethod
    def session(*messages) -> Session:
        return Session(id=1, messages=[
            {"role": role, "content": content, "think": "hidden", "cmd": None} for role, content in messages
        ])

    def test_format(self):
        session = self.session(("user", "q1"), ("assistant", "a1"), ("user", "q2"))
        self.assertEqual(context_messages(session), [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ])
        self.assertIs(SessionStore.context_messages, context_messages)

    def test_empty(self):
        self.assertEqual(context_messages(self.session()), [])

    def test_trims_oldest_messages(self):
        session = self.session(
            ("user", "a" * 100), ("assistant", "b" * 100),
            ("user", "c" * 100), ("assistant", "d" * 100),
            ("user", "e" * 100),
        )
        self.assertEqual(len(context_messages(session, max_chars=500)), 5)
        self.assertEqual(len(context_messages(session, max_chars=499)), 3)
        result = context_messages(session, max_chars=300)
        self.assertEqual([m["content"][0] for m in result], ["c", "d", "e"])
        self.assertLessEqual(sum(len(m["content"]) for m in result), 300)

    def test_always_starts_with_a_user_message(self):
        session = self.session(
            ("user", "a" * 100), ("assistant", "b" * 100),
            ("user", "c" * 100), ("assistant", "d" * 100),
            ("user", "e" * 100),
        )
        # 250 characters fit "d" and "e"; the leading assistant message is dropped.
        result = context_messages(session, max_chars=250)
        self.assertEqual([m["role"] for m in result], ["user"])
        self.assertEqual(result[0]["content"], "e" * 100)
        only_assistant = self.session(("assistant", "hello"))
        self.assertEqual(context_messages(only_assistant), [])

    def test_oversized_last_message_is_cut(self):
        session = self.session(("user", "old"), ("assistant", "reply"), ("user", "x" * 50))
        self.assertEqual(context_messages(session, max_chars=20), [{"role": "user", "content": "x" * 20}])
        self.assertEqual(context_messages(session, max_chars=0), [])

    def test_counts_characters_not_bytes(self):
        session = self.session(("user", "中" * 10), ("assistant", "文" * 10), ("user", "字" * 10))
        self.assertEqual(len(context_messages(session, max_chars=30)), 3)
        self.assertEqual(len(context_messages(session, max_chars=29)), 1)

    def test_expands_commands(self):
        session = Session(id=1, messages=[
            {"role": "user", "content": "x^2-1", "think": None, "cmd": "factorize"},
            {"role": "assistant", "content": "(x-1)(x+1)", "think": None, "cmd": None},
            {"role": "user", "content": "plain", "think": None, "cmd": None},
        ])
        calls = []

        def expand(command_id, text):
            calls.append((command_id, text))
            return f"Factorize: {text}"

        result = context_messages(session, expand=expand)
        self.assertEqual(calls, [("factorize", "x^2-1")])
        self.assertEqual(result[0], {"role": "user", "content": "Factorize: x^2-1"})
        self.assertEqual(result[2], {"role": "user", "content": "plain"})

        def broken(command_id, text):
            raise RuntimeError("boom")

        logging.disable(logging.WARNING)
        try:
            self.assertEqual(context_messages(session, expand=broken)[0]["content"], "x^2-1")
        finally:
            logging.disable(logging.NOTSET)


if __name__ == "__main__":
    unittest.main()
