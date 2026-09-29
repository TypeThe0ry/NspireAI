"""Quick commands offered by the calculator's command menu.

Commands are grouped in categories and described in JSON::

    {"version": 1, "categories": [
      {"id": "math", "label": "Math", "items": [
        {"id": "factorize", "label": "factorize", "type": "prompt",
         "template": "Factorize the following expression.\\n\\n{input}"},
        {"id": "frac", "label": "\\\\frac{}{}", "type": "insert", "text": "\\\\frac{"}
      ]}
    ]}

``type`` is ``"prompt"`` (the command is attached to the next user message and
its template wraps that message) or ``"insert"`` (``text`` is typed into the
calculator's input line).  ``label`` is what the calculator shows with its own
ASCII font (at most 12 characters); the optional ``title`` is a longer or
non-ASCII name for the rendered menu image.

The shipped defaults (``commands.default.json``) can be extended by a user
file, ``<NSPIREAI_HOME>/commands.json``: categories and items are matched by
id, a match replaces the default, anything new is appended, and
``"hidden": true`` removes a default category or item.  A category with
``"replace": true`` drops the default items of that category first.  Invalid
entries are skipped with a warning; loading never raises.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_LABEL_CHARS = 12
MAX_INSERT_CHARS = 60
MAX_ID_CHARS = 32
PER_PAGE = 9
PLACEHOLDER = "{input}"
TYPES = ("prompt", "insert")
DEFAULT_PATH = Path(__file__).resolve().with_name("commands.default.json")
USER_FILE_NAME = "commands.json"

_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def default_home() -> Path:
    """``$NSPIREAI_HOME``, or ``~/.config/nspireai``."""
    override = os.environ.get("NSPIREAI_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "nspireai"


def _is_printable_ascii(text: str) -> bool:
    return all(0x20 <= ord(char) <= 0x7E for char in text)


def _valid_id(value: object) -> bool:
    return isinstance(value, str) and len(value) <= MAX_ID_CHARS and _ID_PATTERN.fullmatch(value) is not None


def _valid_label(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= MAX_LABEL_CHARS
        and _is_printable_ascii(value)
        and value == value.strip()
        and "|" not in value
    )


@dataclass(frozen=True)
class Item:
    id: str
    label: str                      # ASCII, at most 12 characters
    title: Optional[str] = None     # optional display name, may be Chinese
    type: str = "prompt"            # "prompt" or "insert"
    template: Optional[str] = None  # prompt items
    text: Optional[str] = None      # insert items: ASCII, at most 60 characters
    category: str = ""              # id of the owning category

    @property
    def display(self) -> str:
        """Name for the rendered menu image."""
        return self.title or self.label

    @property
    def pending_arg(self) -> str:
        """The ``id|label`` argument of a "set pending command" screen key."""
        return f"{self.id}|{self.label}"

    def to_dict(self) -> dict:
        data: dict = {"id": self.id, "label": self.label}
        if self.title is not None:
            data["title"] = self.title
        data["type"] = self.type
        if self.type == "prompt":
            data["template"] = self.template
        else:
            data["text"] = self.text
        return data


Command = Item  # the two names are interchangeable


@dataclass
class Category:
    id: str
    label: str                      # ASCII, at most 12 characters
    title: Optional[str] = None     # optional display name, may be Chinese
    items: list[Item] = field(default_factory=list)

    @property
    def display(self) -> str:
        return self.title or self.label

    def to_dict(self) -> dict:
        data: dict = {"id": self.id, "label": self.label}
        if self.title is not None:
            data["title"] = self.title
        data["items"] = [item.to_dict() for item in self.items]
        return data


def _chunks(entries: list, per_page: int) -> list[list]:
    if isinstance(per_page, bool) or not isinstance(per_page, int) or per_page < 1:
        raise ValueError("per_page must be a positive integer")
    return [entries[start:start + per_page] for start in range(0, len(entries), per_page)]


class CommandSet:
    def __init__(self, categories: Optional[list[Category]] = None):
        self.categories: list[Category] = list(categories or [])

    # -- loading -----------------------------------------------------------

    @classmethod
    def load(
        cls,
        default_path: Union[str, Path, None] = None,
        user_path: Union[str, Path, bool, None] = None,
    ) -> "CommandSet":
        """Load the defaults and merge the user's file over them.

        ``default_path=None`` uses the shipped ``commands.default.json``;
        ``user_path=None`` uses ``<NSPIREAI_HOME>/commands.json`` when it
        exists; ``user_path=False`` loads the defaults only.
        """
        commands = cls()
        try:
            source = Path(default_path) if default_path is not None else DEFAULT_PATH
            commands._merge_file(source, required=True)
            if user_path is None:
                user_file: Optional[Path] = default_home() / USER_FILE_NAME
            elif user_path is False or user_path == "":
                user_file = None
            else:
                user_file = Path(user_path)
            if user_file is not None:
                # The user's file is optional: its absence is not worth a warning.
                commands._merge_file(user_file.expanduser(), required=False)
        except Exception as exc:  # defensive: a command file must never stop the bridge
            log.warning("cannot load commands: %s: %s", type(exc).__name__, exc)
        return commands

    def _merge_file(self, path: Path, required: bool) -> None:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            if required:
                log.warning("command file %s does not exist", path)
            return
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            log.warning("skipping unreadable command file %s: %s", path, exc)
            return
        try:
            self.merge(data, source=str(path))
        except Exception as exc:  # defensive: a command file must never stop the bridge
            log.warning("skipping command file %s: %s: %s", path, type(exc).__name__, exc)

    def merge(self, data: object, source: str = "<data>") -> None:
        """Merge decoded JSON into this set, skipping invalid entries."""
        if not isinstance(data, dict):
            log.warning("%s: not a JSON object, skipped", source)
            return
        version = data.get("version", SCHEMA_VERSION)
        if version != SCHEMA_VERSION or isinstance(version, bool):
            log.warning("%s: unsupported version %r, skipped", source, version)
            return
        categories = data.get("categories", [])
        if not isinstance(categories, list):
            log.warning("%s: categories is not a list, skipped", source)
            return
        for raw in categories:
            self._merge_category(raw, source)

    def _merge_category(self, raw: object, source: str) -> None:
        if not isinstance(raw, dict) or not _valid_id(raw.get("id")):
            log.warning("%s: skipping a category without a valid id", source)
            return
        category_id = raw["id"]
        existing = self.category(category_id)
        if raw.get("hidden") is True:
            if existing is not None:
                self.categories.remove(existing)
            return
        label = raw.get("label")
        if label is None and existing is not None:
            label = existing.label
        if not _valid_label(label):
            log.warning(
                "%s: skipping category %r: label must be 1..%d printable ASCII characters",
                source, category_id, MAX_LABEL_CHARS,
            )
            return
        title = raw.get("title", existing.title if existing is not None else None)
        if title is not None and (not isinstance(title, str) or not title.strip()):
            log.warning("%s: category %r: ignoring an invalid title", source, category_id)
            title = existing.title if existing is not None else None
        items = raw.get("items", [])
        if not isinstance(items, list):
            log.warning("%s: category %r: items is not a list", source, category_id)
            items = []
        if existing is None:
            existing = Category(id=category_id, label=label, title=title)
            self.categories.append(existing)
        else:
            existing.label = label
            existing.title = title
            if raw.get("replace") is True:
                existing.items = []
        for item in items:
            self._merge_item(existing, item, source)

    def _merge_item(self, category: Category, raw: object, source: str) -> None:
        if not isinstance(raw, dict) or not _valid_id(raw.get("id")):
            log.warning("%s: category %r: skipping an item without a valid id", source, category.id)
            return
        item_id = raw["id"]
        if raw.get("hidden") is True:
            self._remove(item_id)
            return
        problem = self._item_problem(raw)
        if problem is not None:
            log.warning("%s: skipping item %r: %s", source, item_id, problem)
            return
        title = raw.get("title")
        command = Item(
            id=item_id,
            label=raw["label"],
            title=title if isinstance(title, str) and title.strip() else None,
            type=raw["type"],
            template=raw["template"] if raw["type"] == "prompt" else None,
            text=raw["text"] if raw["type"] == "insert" else None,
            category=category.id,
        )
        for index, current in enumerate(category.items):
            if current.id == item_id:
                category.items[index] = command
                return
        # Ids are unique across categories: the newest definition wins.
        self._remove(item_id)
        category.items.append(command)

    @staticmethod
    def _item_problem(raw: dict) -> Optional[str]:
        if not _valid_label(raw.get("label")):
            return f"label must be 1..{MAX_LABEL_CHARS} printable ASCII characters without '|'"
        kind = raw.get("type")
        if kind == "prompt":
            template = raw.get("template")
            if not isinstance(template, str) or not template.strip():
                return "a prompt command needs a template"
        elif kind == "insert":
            text = raw.get("text")
            if not isinstance(text, str) or not text:
                return "an insert command needs text"
            if not _is_printable_ascii(text):
                return "insert text must be printable ASCII"
            if len(text) > MAX_INSERT_CHARS:
                return f"insert text is longer than {MAX_INSERT_CHARS} characters"
        else:
            return f"type must be one of {TYPES}"
        title = raw.get("title")
        if title is not None and not isinstance(title, str):
            return "title must be a string"
        return None

    def _remove(self, command_id: str) -> None:
        for category in self.categories:
            category.items = [item for item in category.items if item.id != command_id]

    # -- queries -----------------------------------------------------------

    def category(self, category_id: str) -> Optional[Category]:
        for category in self.categories:
            if category.id == category_id:
                return category
        return None

    def get(self, command_id: str) -> Optional[Item]:
        for category in self.categories:
            for item in category.items:
                if item.id == command_id:
                    return item
        return None

    def items(self) -> list[Item]:
        """Every item, in menu order."""
        return [item for category in self.categories for item in category.items]

    def label(self, command_id: str) -> Optional[str]:
        """The ASCII label of a prompt command (shown on the input line).

        None for an unknown id and for insert items, which cannot be attached
        to a message.
        """
        item = self.get(command_id) if isinstance(command_id, str) else None
        return item.label if item is not None and item.type == "prompt" else None

    def expand(self, command_id: Optional[str], user_text: str) -> str:
        """The text sent to the model for ``user_text`` with a command attached.

        ``{input}`` in the template is replaced by the text; a template
        without the placeholder gets the text appended after a blank line.
        An unknown id or an insert item leaves the text unchanged.  Never raises.
        """
        try:
            item = self.get(command_id) if command_id else None
            if item is None:
                if command_id:
                    log.warning("unknown command %r; sending the text unchanged", command_id)
                return user_text
            if item.type != "prompt" or not item.template:
                return user_text
            text = user_text if isinstance(user_text, str) else str(user_text)
            if PLACEHOLDER in item.template:
                return item.template.replace(PLACEHOLDER, text).strip()
            if not text.strip():
                return item.template.strip()
            return f"{item.template.rstrip()}\n\n{text}"
        except Exception as exc:  # defensive: a request must still reach the model
            log.warning("cannot expand command %r: %s: %s", command_id, type(exc).__name__, exc)
            return user_text

    def pages(self, category_id: str, per_page: int = PER_PAGE) -> list[list[Item]]:
        """The items of a category in pages of ``per_page`` (keys 1-9, 0 = next page).

        A known category always has at least one page (empty when it has no
        items); an unknown category has none.
        """
        category = self.category(category_id)
        if category is None:
            _chunks([], per_page)  # still validate per_page
            return []
        return _chunks(list(category.items), per_page) or [[]]

    def page(self, category_id: str, index: int, per_page: int = PER_PAGE) -> list[Item]:
        """One page of a category; the index wraps around so "next page" can cycle."""
        pages = self.pages(category_id, per_page)
        return pages[index % len(pages)] if pages else []

    def category_pages(self, per_page: int = PER_PAGE) -> list[list[Category]]:
        """The non-empty categories in pages of ``per_page``."""
        return _chunks([category for category in self.categories if category.items], per_page)

    def to_dict(self) -> dict:
        """JSON-serializable form of the merged set, in the schema of the files.

        The result is deterministic (menu order, fixed key order), so a hash
        of it can serve as a menu version.
        """
        return {
            "version": SCHEMA_VERSION,
            "categories": [category.to_dict() for category in self.categories],
        }
