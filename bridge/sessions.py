"""Persistent chat sessions for the bridge.

Conversation history lives on the host, never on the calculator::

    <home>/sessions/index.json    active session id, ordering, next free id
    <home>/sessions/<id>.json     one session

``<home>`` is ``$NSPIREAI_HOME`` and defaults to ``~/.config/nspireai``.  Every
write goes to a temporary file in the same directory and is moved into place
with ``os.replace``, so a crash never leaves a half-written session behind.
Unreadable files are skipped with a warning; loading never raises.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

DEFAULT_TITLE = "New chat"
MAX_TITLE_CHARS = 24
ROLES = ("user", "assistant")
INDEX_VERSION = 1
SESSION_VERSION = 1


def default_home() -> Path:
    """``$NSPIREAI_HOME``, or ``~/.config/nspireai``."""
    override = os.environ.get("NSPIREAI_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "nspireai"


def make_title(text: str, limit: int = MAX_TITLE_CHARS) -> str:
    """Whitespace-collapsed ``text`` cut to ``limit`` characters (a CJK character counts as one)."""
    collapsed = " ".join(str(text).split())
    return collapsed[:limit].rstrip() or DEFAULT_TITLE


@dataclass
class Session:
    id: int
    title: str = DEFAULT_TITLE
    created: str = ""
    updated: str = ""
    messages: list[dict] = field(default_factory=list)
    # False once the title was chosen by the user (create(title=...) or rename()).
    auto_title: bool = True

    def to_json(self) -> dict:
        return {
            "version": SESSION_VERSION,
            "id": self.id,
            "title": self.title,
            "auto_title": self.auto_title,
            "created": self.created,
            "updated": self.updated,
            "messages": self.messages,
        }

    @classmethod
    def from_json(cls, data: object, expected_id: Optional[int] = None) -> "Session":
        """Build a session from decoded JSON; raises ValueError when it is malformed."""
        if not isinstance(data, dict):
            raise ValueError("session is not a JSON object")
        session_id = data.get("id")
        if isinstance(session_id, bool) or not isinstance(session_id, int) or session_id < 1:
            raise ValueError("session id is missing or invalid")
        if expected_id is not None and session_id != expected_id:
            raise ValueError(f"session id {session_id} does not match its file name")
        raw_messages = data.get("messages", [])
        if not isinstance(raw_messages, list):
            raise ValueError("session messages are not a list")
        messages = []
        for raw in raw_messages:
            if not isinstance(raw, dict) or raw.get("role") not in ROLES or not isinstance(raw.get("content"), str):
                log.warning("session %s: skipping a malformed message", session_id)
                continue
            think = raw.get("think")
            cmd = raw.get("cmd")
            messages.append({
                "role": raw["role"],
                "content": raw["content"],
                "think": think if isinstance(think, str) else None,
                "cmd": cmd if isinstance(cmd, str) else None,
            })
        title = data.get("title")
        created = data.get("created")
        updated = data.get("updated")
        created = created if isinstance(created, str) else ""
        return cls(
            id=session_id,
            title=title if isinstance(title, str) and title else DEFAULT_TITLE,
            created=created,
            updated=updated if isinstance(updated, str) and updated else created,
            messages=messages,
            auto_title=bool(data.get("auto_title", True)),
        )


@dataclass(frozen=True)
class SessionSummary:
    id: int
    title: str
    created: str
    updated: str
    message_count: int
    active: bool = False


def context_messages(
    session: Session,
    max_chars: int = 24000,
    expand: Optional[Callable[[str, str], str]] = None,
) -> list[dict]:
    """The most recent messages that fit ``max_chars``, for a chat-completions API.

    The result holds ``{"role", "content"}`` entries and always starts with a
    user message.  ``expand(cmd, content)`` (for example ``CommandSet.expand``)
    turns a user message with an attached quick command into the text the
    model should see.  A final user message that alone exceeds ``max_chars``
    is cut to fit rather than dropped.
    """
    selected: list[dict] = []
    total = 0
    for message in reversed(session.messages):
        role = message.get("role")
        content = message.get("content")
        if role not in ROLES or not isinstance(content, str):
            continue
        cmd = message.get("cmd")
        if expand is not None and role == "user" and cmd:
            try:
                content = str(expand(cmd, content))
            except Exception as exc:  # a bad command must not lose the message
                log.warning("cannot expand command %r: %s", cmd, exc)
        if total + len(content) > max_chars:
            if not selected and role == "user" and max_chars > 0:
                selected.append({"role": role, "content": content[:max_chars]})
            break
        total += len(content)
        selected.append({"role": role, "content": content})
    selected.reverse()
    while selected and selected[0]["role"] != "user":
        selected.pop(0)
    return selected


class SessionStore:
    """Multi-session store; every call reads and writes through to disk."""

    def __init__(self, home: Optional[Path] = None):
        self.home = Path(home).expanduser() if home is not None else default_home()
        self.directory = self.home / "sessions"
        self.index_path = self.directory / "index.json"
        self._lock = threading.RLock()
        self._last_moment: Optional[datetime] = None

    # -- public API --------------------------------------------------------

    def list(self) -> list[SessionSummary]:
        """Summaries of all sessions, most recently updated first."""
        with self._lock:
            index = self._load_index()
            return [
                SessionSummary(
                    id=session.id,
                    title=session.title,
                    created=session.created,
                    updated=session.updated,
                    message_count=len(session.messages),
                    active=session.id == index["active"],
                )
                for session in self._load_all()
            ]

    def get(self, session_id: int) -> Session:
        """The session with this id; KeyError when it does not exist or is unreadable."""
        with self._lock:
            session = self._load_session(session_id)
            if session is None:
                raise KeyError(session_id)
            return session

    def active(self) -> Session:
        """The active session; the first session is created when none exists."""
        with self._lock:
            index = self._load_index()
            active_id = index["active"]
            if active_id is not None:
                session = self._load_session(active_id)
                if session is not None:
                    return session
            sessions = self._load_all()
            if sessions:
                self._save_index(index, sessions, active=sessions[0].id)
                return sessions[0]
            return self._create(index, None)

    def create(self, title: Optional[str] = None) -> Session:
        """Create a session and make it the active one."""
        with self._lock:
            return self._create(self._load_index(), title)

    def select(self, session_id: int) -> Session:
        """Make an existing session the active one."""
        with self._lock:
            session = self.get(session_id)
            index = self._load_index()
            self._save_index(index, self._load_all(), active=session.id)
            return session

    def delete(self, session_id: int) -> Session:
        """Delete a session and return the session that is active afterwards.

        When the active session is deleted, the most recently updated remaining
        one takes over; when none remains, a fresh session is created.
        """
        with self._lock:
            path = self._session_path(session_id)
            if not path.exists():
                raise KeyError(session_id)
            index = self._load_index()
            path.unlink()
            remaining = self._load_all()
            active_id = index["active"]
            if active_id == session_id or all(session.id != active_id for session in remaining):
                active_id = remaining[0].id if remaining else None
            if active_id is None:
                return self._create(index, None)
            self._save_index(index, remaining, active=active_id)
            return next(session for session in remaining if session.id == active_id)

    def rename(self, session_id: int, title: str) -> Session:
        """Set a title chosen by the user; an empty title returns to automatic titles."""
        with self._lock:
            session = self.get(session_id)
            cleaned = " ".join(str(title).split())
            if cleaned:
                session.title = cleaned
                session.auto_title = False
            else:
                session.auto_title = True
                session.title = self._auto_title(session)
            return self._touch(session)

    def append(
        self,
        session_id: int,
        role: str,
        content: str,
        think: Optional[str] = None,
        cmd: Optional[str] = None,
    ) -> Session:
        """Append one message; the first user message names an untitled session."""
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}, not {role!r}")
        if not isinstance(content, str):
            raise ValueError("message content must be a string")
        with self._lock:
            session = self.get(session_id)
            session.messages.append({"role": role, "content": content, "think": think, "cmd": cmd})
            if session.auto_title:
                session.title = self._auto_title(session)
            return self._touch(session)

    def clear(self, session_id: int) -> Session:
        """Remove all messages of a session (an automatic title starts over)."""
        with self._lock:
            session = self.get(session_id)
            session.messages = []
            if session.auto_title:
                session.title = DEFAULT_TITLE
            return self._touch(session)

    context_messages = staticmethod(context_messages)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _auto_title(session: Session) -> str:
        for message in session.messages:
            if message.get("role") == "user" and str(message.get("content", "")).strip():
                return make_title(message["content"])
        return DEFAULT_TITLE

    def _now(self) -> str:
        """UTC ISO timestamp, strictly increasing within this store."""
        moment = datetime.now(timezone.utc)
        if self._last_moment is not None and moment <= self._last_moment:
            moment = self._last_moment + timedelta(microseconds=1)
        self._last_moment = moment
        return moment.isoformat(timespec="microseconds")

    def _session_path(self, session_id: int) -> Path:
        if isinstance(session_id, bool) or not isinstance(session_id, int) or session_id < 1:
            raise KeyError(session_id)
        return self.directory / f"{session_id}.json"

    def _session_ids(self) -> list[int]:
        try:
            names = [entry.name for entry in self.directory.iterdir()]
        except OSError:
            return []
        ids = []
        for name in names:
            match = re.fullmatch(r"([1-9][0-9]*)\.json", name)
            if match:
                ids.append(int(match.group(1)))
        return sorted(ids)

    def _load_session(self, session_id: int) -> Optional[Session]:
        try:
            path = self._session_path(session_id)
        except KeyError:
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return Session.from_json(json.load(handle), expected_id=session_id)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            log.warning("skipping unreadable session file %s: %s", path, exc)
            return None

    def _load_all(self) -> list[Session]:
        """All readable sessions, most recently updated first."""
        sessions = [self._load_session(session_id) for session_id in self._session_ids()]
        found = [session for session in sessions if session is not None]
        found.sort(key=lambda session: (session.updated, session.id), reverse=True)
        return found

    def _load_index(self) -> dict:
        index = {"active": None, "next_id": 1, "order": []}
        try:
            with open(self.index_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError("index is not a JSON object")
            active = data.get("active")
            if isinstance(active, int) and not isinstance(active, bool) and active >= 1:
                index["active"] = active
            next_id = data.get("next_id")
            if isinstance(next_id, int) and not isinstance(next_id, bool) and next_id >= 1:
                index["next_id"] = next_id
        except FileNotFoundError:
            pass
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            log.warning("rebuilding unreadable session index %s: %s", self.index_path, exc)
        # Never hand out an id that is still on disk, even if the index was lost.
        ids = self._session_ids()
        if ids:
            index["next_id"] = max(index["next_id"], ids[-1] + 1)
        return index

    def _save_index(self, index: dict, sessions: list[Session], active: Optional[int]) -> None:
        index["active"] = active
        index["order"] = [session.id for session in sessions]
        self._write_json(self.index_path, {
            "version": INDEX_VERSION,
            "active": index["active"],
            "next_id": index["next_id"],
            "order": index["order"],
        })

    def _create(self, index: dict, title: Optional[str]) -> Session:
        session_id = index["next_id"]
        index["next_id"] = session_id + 1
        stamp = self._now()
        cleaned = " ".join(str(title).split()) if title is not None else ""
        session = Session(
            id=session_id,
            title=cleaned or DEFAULT_TITLE,
            created=stamp,
            updated=stamp,
            messages=[],
            auto_title=not cleaned,
        )
        self._write_json(self._session_path(session_id), session.to_json())
        self._save_index(index, self._load_all(), active=session_id)
        return session

    def _touch(self, session: Session) -> Session:
        session.updated = self._now()
        self._write_json(self._session_path(session.id), session.to_json())
        index = self._load_index()
        self._save_index(index, self._load_all(), active=index["active"])
        return session

    def _write_json(self, path: Path, data: dict) -> None:
        """Atomic write: temporary file in the same directory, then os.replace."""
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=1)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
