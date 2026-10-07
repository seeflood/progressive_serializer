"""Pretty-print a folded tree as JSONC (JSON with trailing ``//`` comments).

``json.dumps`` cannot do this: a fold note has to land at the *end of a line*,
after any comma. So the printer flattens the tree into lines first, attaches
commas to the last line of each element, and comments to the first line.

With no :class:`~progressive_serializer.fold.Folded` nodes present the output is
byte-identical to ``json.dumps(obj, indent=2, ensure_ascii=False)``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .fold import Folded


@dataclass
class _Line:
    depth: int
    text: str
    comment: str = ""


def dumps(value: Any, *, indent: int = 2, ensure_ascii: bool = False) -> str:
    """Render ``value`` (possibly containing ``Folded`` nodes) as JSONC."""
    lines: list[_Line] = []
    _emit(value, 0, lines, ensure_ascii)
    pad = " " * indent
    return "\n".join(
        pad * line.depth + line.text + (f"  // {line.comment}" if line.comment else "")
        for line in lines
    )


def _emit(
    value: Any, depth: int, lines: list[_Line], ensure_ascii: bool, comment: str = ""
) -> None:
    """Append the lines for ``value``, hanging ``comment`` off its first line."""
    if isinstance(value, Folded):
        # The note describes this slot, so it wins over any inherited comment.
        _emit(value.preview, depth, lines, ensure_ascii, value.note)
        return

    if isinstance(value, dict):
        _emit_container(value, depth, lines, ensure_ascii, comment, "{", "}")
        return
    if isinstance(value, (list, tuple)):
        _emit_container(value, depth, lines, ensure_ascii, comment, "[", "]")
        return

    lines.append(_Line(depth, json.dumps(value, ensure_ascii=ensure_ascii), comment))


def _emit_container(
    value: Any,
    depth: int,
    lines: list[_Line],
    ensure_ascii: bool,
    comment: str,
    open_tok: str,
    close_tok: str,
) -> None:
    if not value:
        # json.dumps renders empty containers on one line; match that.
        lines.append(_Line(depth, open_tok + close_tok, comment))
        return

    lines.append(_Line(depth, open_tok, comment))
    items = value.items() if isinstance(value, dict) else ((None, v) for v in value)
    first = True
    for key, item in items:
        if not first:
            # Comma belongs to the *previous* element's final line, which for a
            # scalar is also the line carrying its comment.
            lines[-1].text += ","
        first = False
        if key is None:
            _emit(item, depth + 1, lines, ensure_ascii)
        else:
            _emit_member(key, item, depth + 1, lines, ensure_ascii)
    lines.append(_Line(depth, close_tok))


def _emit_member(
    key: str, item: Any, depth: int, lines: list[_Line], ensure_ascii: bool
) -> None:
    """Emit ``"key": value``, splicing the key onto the value's first line."""
    before = len(lines)
    _emit(item, depth, lines, ensure_ascii)
    prefix = json.dumps(key, ensure_ascii=ensure_ascii) + ": "
    lines[before].text = prefix + lines[before].text
