"""Pretty-print a folded tree as JSONC (JSON with comments).

``json.dumps`` cannot do this: a fold note has to land at the *end of a line*,
after any comma. So the printer flattens the tree into lines first, attaches
commas to the last line of each element, and comments to the first line.

Comments come out as ``//`` or as ``/* … */`` depending on ``comment_style``;
see :func:`dumps`.

With no :class:`~progressive_serializer.fold.Folded` nodes present the output is
byte-identical to ``json.dumps(obj, indent=2, ensure_ascii=False)``.
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass
from typing import Any

from .fold import Folded

# Default line budget for a wrapped comment.
DEFAULT_COMMENT_WIDTH = 88

LINE = "line"
BLOCK = "block"


@dataclass
class _Line:
    depth: int
    text: str
    comment: str = ""


def dumps(
    value: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    root_comment: str = "",
    comment_width: int = DEFAULT_COMMENT_WIDTH,
    comment_style: str = LINE,
) -> str:
    """Render ``value`` (possibly containing ``Folded`` nodes) as JSONC.

    ``root_comment`` labels the block as a whole, letting a caller annotate it
    without wrapping it in any surrounding text. It hangs off the very first
    line — the opening ``{`` of an object, say.

    ``comment_style`` picks the delimiters for *every* comment in the output:

    ``"line"`` (default)
        ``// note`` to end of line. Compact, and what a fenced markdown block
        in ``overview.md`` wants.

    ``"block"``
        ``/* note */``. Use this when the output is a bare string spliced into
        a larger document: ``//`` has no terminator, so a comment long enough
        to wrap — or one the reader's terminal soft-wraps — is
        indistinguishable from the next line of JSON. ``*/`` states where each
        note ends::

            {  /* Oversized fields were folded automatically; full content at
                  `/tmp/out/report.json` */
              "title": "...",  /* folded, 21 chars originally */
            }

    Comments are wrapped to ``comment_width`` columns, with continuation lines
    aligned under the opening delimiter. Pass ``comment_width=0`` to keep each
    on one line. Long unbroken runs (a filesystem path) are never split, since
    the point of printing a path is that the reader can copy it.

    Only ``"block"`` can wrap: a ``//`` comment's continuation would be read as
    JSON, so in ``"line"`` style ``comment_width`` is ignored and notes stay on
    one line however long they are.

    When the first line carries both a ``root_comment`` and a fold note, the
    root comment comes first. In ``"line"`` style the two are joined into the
    single ``//`` that the line can hold; in ``"block"`` style they stay two
    separate ``/* … */`` comments.
    """
    if comment_style not in (LINE, BLOCK):
        raise ValueError(
            f"comment_style must be {LINE!r} or {BLOCK!r}, not {comment_style!r}"
        )

    lines: list[_Line] = []
    _emit(value, 0, lines, ensure_ascii)
    pad = " " * indent

    # A "//" comment swallows the rest of the line, so a line can hold only one
    # of them: root comment and fold note have to merge. Block comments are
    # self-delimiting and can simply sit side by side.
    if lines and root_comment and comment_style == LINE:
        existing = lines[0].comment
        lines[0].comment = f"{root_comment} {existing}" if existing else root_comment
        root_comment = ""

    out: list[str] = []
    for i, line in enumerate(lines):
        text = pad * line.depth + line.text
        notes = [root_comment] if i == 0 and root_comment else []
        if line.comment:
            notes.append(line.comment)

        for note in notes:
            # Two spaces between the JSON (or a preceding comment) and the note.
            if comment_style == LINE:
                text += f"  // {note}"
                continue
            chunk = _block_comment(note, len(text) + 2, comment_width)
            text += "  " + chunk[0]
            if len(chunk) > 1:
                # Flush the lines the comment has now completed, and carry its
                # last line forward so a following note can append to it.
                out.append(text)
                out.extend(chunk[1:-1])
                text = chunk[-1]
        out.append(text)
    return "\n".join(out)


def _block_comment(text: str, column: int, width: int) -> list[str]:
    """Wrap ``text`` as a ``/* … */`` block opening at ``column``.

    Continuation lines are indented to sit under the opening delimiter's text,
    so the comment reads as one visual paragraph rather than as JSON.
    """
    # "*/" inside the text would end the comment early and spill the remainder
    # into the JSON. Nothing in this library produces one, but the text can
    # carry a caller-supplied path or message.
    text = text.replace("*/", "* /")

    opener = "/* "
    closer = " */"
    if width <= 0:
        return [opener + text + closer]

    body_column = column + len(opener)
    # At least some room to work with however deep the block is indented.
    room = max(24, width - body_column - len(closer))
    chunks = textwrap.wrap(
        text,
        room,
        # A path or URL must stay copy-pasteable, so it overruns the width
        # rather than being split across lines.
        break_long_words=False,
        break_on_hyphens=False,
    ) or [""]

    out = [opener + chunks[0]]
    out.extend(" " * body_column + chunk for chunk in chunks[1:])
    out[-1] += closer
    return out


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
