"""Top-level orchestration: decide, fold, persist, render."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from . import jsonc
from .config import SerializeConfig
from .fold import fold
from .render import ItemView, OverviewContext, StringTemplateRenderer
from .sdk_gen import generate_sdk

OVERVIEW_NAME = "overview.md"

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_STEM_LEN = 80


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

        block, folded = _fold_to_budget(value, config)
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


def _fold_to_budget(value: Any, config: SerializeConfig) -> tuple[str, bool]:
    """Fold ``value``, tightening thresholds until it fits the budget.

    One pass of the rules can miss — e.g. an object with hundreds of small
    fields trips no single rule yet still blows the budget — so retry with
    stepped-down thresholds up to ``escalate_rounds`` times.
    """
    current = config
    block = ""
    folded = False
    for _ in range(max(1, config.escalate_rounds + 1)):
        tree, stats = fold(value, current)
        block = jsonc.dumps(
            tree, indent=config.indent, ensure_ascii=config.ensure_ascii
        )
        folded = stats.folded
        if config.size_fn(block) <= config.max_value_size:
            break
        current = current.tightened()
    return block, folded


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
    """Turn an arbitrary key into a filename stem that stays inside out_dir."""
    stem = _UNSAFE_CHARS.sub("_", key).strip("._-")
    if not stem:
        return "item"
    return stem[:_MAX_STEM_LEN]


def _unique_name(key: str, used: dict[str, int]) -> str:
    """``<stem>.json``, suffixed on collision.

    Sanitizing is lossy ("a/b" and "a b" both become "a_b"), and macOS/Windows
    filesystems are case-insensitive, so dedupe case-insensitively.
    """
    stem = _safe_stem(key)
    marker = stem.lower()
    count = used.get(marker, 0) + 1
    used[marker] = count
    return f"{stem}.json" if count == 1 else f"{stem}_{count}.json"
