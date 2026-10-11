"""Top-level orchestration: decide, fold, persist, render."""

from __future__ import annotations

import base64
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any, Mapping, Optional

from . import jsonc
from .config import SerializeConfig
from .fold import fold
from .render import ItemView, OverviewContext, StringTemplateRenderer
from .sdk_gen import generate_sdk

OVERVIEW_NAME = "overview.md"

# Targets macOS and Linux. "/" and NUL are the only bytes POSIX forbids in a
# filename, so Unicode letters are kept and a CJK key keeps a self-describing
# name. Beyond those two, this also replaces:
#   - control chars: legal on POSIX but make a path unprintable;
#   - whitespace and shell metacharacters: the overview prints these paths for
#     an agent to paste into a shell, where an unquoted space or "$" breaks.
_UNSAFE_CHARS = re.compile(r"[/\x00-\x1f\x7f\s\\:*?\"'<>|$`&;()\[\]{}!#]+")

# POSIX caps a filename at 255 *bytes*, not characters, and one CJK char is
# 3 bytes in UTF-8. Budget in bytes with room left for the "_NN.json" suffix.
_MAX_STEM_BYTES = 180


@dataclass
class SerializeResult:
    out_dir: Path
    overview_path: Path
    overview_text: str
    # Only keys that were folded and therefore spilled to disk.
    files: dict[str, Path] = field(default_factory=dict)
    items: list[ItemView] = field(default_factory=list)
    # SDK files generated alongside the JSON files (key → sdk .py path).
    # Only populated when SerializeConfig(generate_sdk=True).
    sdk_files: dict[str, Path] = field(default_factory=dict)


def serialize(
    data: Mapping[str, Any],
    out_dir: "str | Path",
    config: Optional[SerializeConfig] = None,
) -> SerializeResult:
    """Write a progressive-disclosure view of ``data`` into ``out_dir``.

    Each value is measured as full JSON. Small ones are inlined into
    ``overview.md`` verbatim; oversized ones are folded for the overview and
    written out in full as ``<key>.json`` so an agent can read them on demand.

    Existing files in ``out_dir`` are left alone apart from the ones written here.
    """
    config = config or SerializeConfig()
    out_path = Path(out_dir).expanduser()
    out_path.mkdir(parents=True, exist_ok=True)

    renderer = config.renderer or StringTemplateRenderer(
        folded_notice=config.messages.folded_notice,
        sdk_notice=config.messages.sdk_notice,
    )
    used_stems: dict[str, int] = {}
    items: list[ItemView] = []
    files: dict[str, Path] = {}
    sdk_files: dict[str, Path] = {}

    for key, value in data.items():
        # Dumped once: this same text is both what we measure and what we spill.
        full_text = json.dumps(
            value,
            indent=config.indent,
            ensure_ascii=config.ensure_ascii,
            default=_dump_default(config),
        )
        original_size = config.size_fn(full_text)

        if original_size <= config.max_value_size:
            items.append(
                ItemView(
                    key=str(key),
                    json_block=full_text,
                    folded=False,
                    full_path=None,
                    original_size=original_size,
                    rendered_size=original_size,
                )
            )
            continue

        # Fenced markdown block: nothing can be mistaken for a "//" comment's
        # continuation, so the compact style is the better default here.
        _, block, folded = _fold_to_budget(
            value, config, config.comment_style or jsonc.LINE
        )
        full_path: Optional[str] = None
        sdk_path: Optional[str] = None
        sdk_class: Optional[str] = None
        if folded:
            path = out_path / _unique_name(str(key), used_stems)
            path.write_text(full_text, encoding="utf-8")
            files[str(key)] = path
            full_path = str(path)

            if config.generate_sdk:
                import json as _json
                sdk_source, sdk_class = generate_sdk(
                    str(key), _json.loads(full_text), path
                )
                sdk_file = path.with_name(path.stem + "_sdk.py")
                sdk_file.write_text(sdk_source, encoding="utf-8")
                sdk_files[str(key)] = sdk_file
                sdk_path = str(sdk_file)

        items.append(
            ItemView(
                key=str(key),
                json_block=block,
                folded=folded,
                full_path=full_path,
                original_size=original_size,
                rendered_size=config.size_fn(block),
                sdk_path=sdk_path,
                sdk_class=sdk_class,
            )
        )

    ctx = OverviewContext(out_dir=str(out_path), items=items)
    overview_text = renderer.render(ctx)
    overview_path = out_path / OVERVIEW_NAME
    overview_path.write_text(overview_text, encoding="utf-8")

    return SerializeResult(
        out_dir=out_path,
        overview_path=overview_path,
        overview_text=overview_text,
        files=files,
        items=items,
        sdk_files=sdk_files,
    )


def serialize_value(
    value: Any,
    out_file: "str | Path",
    config: Optional[SerializeConfig] = None,
) -> str:
    """Fold one value and return it as a bare JSONC string.

    Unlike :func:`serialize`, which takes a dict of keys and writes an
    ``overview.md`` around them, this renders a *single* value — a JSON-ish
    structure or a class instance — and returns nothing but the JSONC block:
    no markdown, no code fence, no key heading. The caller can therefore splice
    the result straight into a larger JSON/JSONC document of its own.

    When the value exceeds ``max_value_size`` it is folded, its complete JSON
    is written to ``out_file``, and a notice pointing at that file is attached
    to the block's first line::

        {  /* Oversized fields were folded automatically; full content at
              `/tmp/out/report.json` */
          "title": "...",  /* folded, 21 chars originally */
          ...
        }

    Comments default to ``/* … */`` here, unlike :func:`serialize`: the result
    is spliced into a document this library does not control, and ``//`` has no
    terminator — a comment long enough to wrap, or one the reader's terminal
    soft-wraps, becomes indistinguishable from the next line of JSON. Pass
    ``SerializeConfig(comment_style="line")`` for ``//`` anyway, and
    ``comment_width=`` to change where block comments wrap.

    When the value already fits the budget nothing is folded, ``out_file`` is
    left untouched, and the return value is plain ``json.dumps`` output.

    ``out_file`` also fixes the naming of everything else: with
    ``SerializeConfig(generate_sdk=True)`` the SDK lands at
    ``<stem>_sdk.py`` beside it, and its root class is named after ``<stem>``
    (``report.json`` -> ``report_sdk.py``, class ``Report``).

    ``config.renderer`` is ignored — there is no template to render. The notice
    wording still comes from ``config.messages``.
    """
    config = config or SerializeConfig()
    out_path = Path(out_file).expanduser()
    # Self-delimiting comments by default: see the docstring.
    style = config.comment_style or jsonc.BLOCK

    full_text = json.dumps(
        value,
        indent=config.indent,
        ensure_ascii=config.ensure_ascii,
        default=_dump_default(config),
    )
    if config.size_fn(full_text) <= config.max_value_size:
        return full_text

    tree, block, folded = _fold_to_budget(value, config, style)
    if not folded:
        # Over budget yet no rule fired (e.g. a wide object with
        # max_object_keys disabled). Nothing was elided, so there is nothing to
        # spill and no notice to point anywhere.
        return block

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(full_text, encoding="utf-8")

    # The stem plays the role `key` plays in serialize(): it names the SDK file
    # and its root class, and fills ${key} in a custom notice.
    stem = out_path.stem
    sdk_path: Optional[str] = None
    sdk_class: Optional[str] = None
    if config.generate_sdk:
        sdk_source, sdk_class = generate_sdk(stem, json.loads(full_text), out_path)
        sdk_file = out_path.with_name(stem + "_sdk.py")
        sdk_file.write_text(sdk_source, encoding="utf-8")
        sdk_path = str(sdk_file)

    if sdk_path and sdk_class:
        notice = Template(config.messages.sdk_notice).substitute(
            key=stem,
            full_path=str(out_path),
            sdk_path=sdk_path,
            sdk_class=sdk_class,
            out_dir=str(out_path.parent),
        )
    else:
        notice = Template(config.messages.folded_notice).substitute(
            key=stem, full_path=str(out_path), out_dir=str(out_path.parent)
        )

    return jsonc.dumps(
        tree,
        indent=config.indent,
        ensure_ascii=config.ensure_ascii,
        root_comment=_one_line(notice),
        comment_width=config.comment_width,
        comment_style=style,
    )


def _one_line(text: str) -> str:
    """Normalize a notice's whitespace before it is re-wrapped as a comment.

    The message templates are written for markdown: hard line breaks and a
    trailing blank line. Collapsing that lets ``jsonc.dumps`` re-wrap the prose
    to the comment's own width rather than inheriting markdown's.
    """
    return " ".join(text.split())


def _fold_to_budget(
    value: Any, config: SerializeConfig, style: str = jsonc.LINE
) -> tuple[Any, str, bool]:
    """Fold ``value``, tightening thresholds until it fits the budget.

    One pass of the rules can miss — e.g. an object with hundreds of small
    fields trips no single rule yet still blows the budget — so retry with
    stepped-down thresholds up to ``escalate_rounds`` times.

    ``style`` is the comment style the result will be rendered in; it is applied
    here too, since ``/* … */`` costs more characters than ``//`` and the budget
    is measured on the rendered text.

    Returns the folded tree alongside its rendered JSONC, since a caller that
    wants to re-render with a root comment needs the tree, not the text.
    """
    current = config
    tree: Any = None
    block = ""
    folded = False
    for _ in range(max(1, config.escalate_rounds + 1)):
        tree, stats = fold(value, current)
        block = jsonc.dumps(
            tree,
            indent=config.indent,
            ensure_ascii=config.ensure_ascii,
            comment_width=config.comment_width,
            comment_style=style,
        )
        folded = stats.folded
        if config.size_fn(block) <= config.max_value_size:
            break
        current = current.tightened()
    return tree, block, folded


def _dump_default(config: SerializeConfig):
    """``json.dumps`` default hook: handle binary, then defer to the user's."""

    def _default(obj: Any) -> Any:
        if isinstance(obj, (bytes, bytearray, memoryview)):
            return base64.b64encode(bytes(obj)).decode("ascii")
        if config.default is not None:
            return config.default(obj)
        raise TypeError(
            f"Object of type {type(obj).__name__} is not JSON serializable; "
            "pass SerializeConfig(default=...) to convert it, same as "
            "json.dumps(default=...)."
        )

    return _default


def _safe_stem(key: str) -> str:
    """Turn an arbitrary key into a filename stem that stays inside out_dir.

    Unicode is preserved, so a CJK key keeps a self-describing filename; only
    separators, control characters and shell-hostile punctuation are replaced.
    """
    stem = _UNSAFE_CHARS.sub("_", key).strip("._-")
    # A leading dot would hide the file; "." and ".." would escape or clobber.
    if not stem or set(stem) <= {"."}:
        return "item"
    return _truncate_bytes(stem, _MAX_STEM_BYTES)


def _truncate_bytes(text: str, limit: int) -> str:
    """Clip ``text`` to ``limit`` UTF-8 bytes without splitting a character."""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    # errors="ignore" drops the partial character left at the cut point.
    return encoded[:limit].decode("utf-8", errors="ignore")


def _unique_name(key: str, used: dict[str, int]) -> str:
    """``<stem>.json``, suffixed on collision.

    Sanitizing is lossy ("a/b" and "a b" both become "a_b"), and macOS's
    default filesystem is case-insensitive, so dedupe case-insensitively.
    macOS also stores filenames as NFD while Linux keeps whatever it is given,
    so the collision key is NFC-normalized to keep behaviour identical on both.
    """
    stem = _safe_stem(key)
    marker = unicodedata.normalize("NFC", stem).casefold()
    count = used.get(marker, 0) + 1
    used[marker] = count
    return f"{stem}.json" if count == 1 else f"{stem}_{count}.json"
