"""Heuristic folding of large JSON-ish values into an annotated tree.

The walk produces native ``dict``/``list``/scalars for everything that stays,
and :class:`Folded` placeholders wherever a rule fired. Keeping the result a
tree (rather than text) lets :mod:`progressive_serializer.jsonc` decide where
the ``//`` comments go.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from .config import SerializeConfig

# Guard against a ``default`` hook that keeps handing back non-native objects.
_MAX_COERCE_CHAIN = 4

_BINARY_TYPES = (bytes, bytearray, memoryview)

# Stands in for elided content. A JSON string rather than a bare ``...`` so the
# block still parses once the ``//`` comments are stripped.
ELLIPSIS = "..."


def _plural(n: int) -> str:
    """``"s"`` unless ``n`` is 1, for messages that want "1 field"/"2 fields"."""
    return "" if n == 1 else "s"


@dataclass(frozen=True)
class Folded:
    """A placeholder standing in for content that was elided.

    ``preview`` is what remains visible (``"..."`` for strings, the leading
    items of a list, ``{}`` for a pruned subtree); ``note`` becomes a trailing
    ``//`` comment.
    """

    preview: Any
    note: str
    kind: str


@dataclass
class FoldStats:
    """What a folding pass did, for the caller to report on."""

    folded: bool = False
    notes: int = 0

    def record(self) -> None:
        self.folded = True
        self.notes += 1


def fold(value: Any, config: SerializeConfig) -> tuple[Any, FoldStats]:
    """Fold ``value`` per ``config``, returning the tree and what was elided."""
    stats = FoldStats()
    tree = _walk(value, config, stats, depth=0)
    return tree, stats


def _walk(value: Any, config: SerializeConfig, stats: FoldStats, depth: int) -> Any:
    value = _coerce(value, config)

    if isinstance(value, _BINARY_TYPES):
        return _fold_binary(value, config, stats)
    if isinstance(value, str):
        return _fold_string(value, config, stats)
    if isinstance(value, dict):
        return _fold_dict(value, config, stats, depth)
    if isinstance(value, (list, tuple)):
        return _fold_list(list(value), config, stats, depth)
    # bool/int/float/None render as-is; everything else was coerced above.
    return value


def _coerce(value: Any, config: SerializeConfig) -> Any:
    """Map non-JSON-native objects through the ``default`` hook.

    Loops because ``default`` may itself return something non-native (e.g. a
    dataclass whose field is another custom object).
    """
    for _ in range(_MAX_COERCE_CHAIN):
        if _is_native(value):
            return value
        if config.default is None:
            raise TypeError(
                f"Object of type {type(value).__name__} is not JSON serializable; "
                "pass SerializeConfig(default=...) to convert it, same as "
                "json.dumps(default=...)."
            )
        value = config.default(value)
    if not _is_native(value):
        raise TypeError(
            f"SerializeConfig.default kept returning non-serializable objects "
            f"(last was {type(value).__name__}) after {_MAX_COERCE_CHAIN} attempts."
        )
    return value


def _is_native(value: Any) -> bool:
    return isinstance(
        value, (dict, list, tuple, str, int, float, bool, type(None)) + _BINARY_TYPES
    )


def _fold_binary(value: Any, config: SerializeConfig, stats: FoldStats) -> Any:
    raw = bytes(value)
    if not config.fold_binary:
        # Still not valid JSON as-is, so base64 it and let the string rule decide.
        return _fold_string(base64.b64encode(raw).decode("ascii"), config, stats)
    stats.record()
    return Folded(
        preview=ELLIPSIS,
        note=config.messages.binary_folded.format(
            type_name=type(value).__name__, size=len(raw), s=_plural(len(raw))
        ),
        kind="binary",
    )


def _fold_string(value: str, config: SerializeConfig, stats: FoldStats) -> Any:
    if len(value) <= config.max_string_len:
        return value
    stats.record()
    return Folded(
        preview=ELLIPSIS,
        note=config.messages.string_folded.format(
            length=len(value), s=_plural(len(value))
        ),
        kind="string",
    )


def _fold_dict(
    value: dict, config: SerializeConfig, stats: FoldStats, depth: int
) -> Any:
    if depth >= config.max_depth:
        stats.record()
        return Folded(
            preview={ELLIPSIS: ELLIPSIS} if value else {},
            note=config.messages.depth_object_folded.format(
                total=len(value), s=_plural(len(value))
            ),
            kind="depth",
        )

    keys = list(value.keys())
    dropped = 0
    if config.max_object_keys is not None and len(keys) > config.max_object_keys:
        dropped = len(keys) - config.max_object_keys
        keys = keys[: config.max_object_keys]

    preview = {
        _dict_key(k): _walk(value[k], config, stats, depth + 1) for k in keys
    }
    if not dropped:
        return preview

    # Sentinel member marking where the remaining fields were cut, unless the
    # data legitimately has a "..." key of its own.
    if ELLIPSIS not in preview:
        preview[ELLIPSIS] = ELLIPSIS
    stats.record()
    return Folded(
        preview=preview,
        note=config.messages.object_folded.format(
            total=len(value), dropped=dropped, s=_plural(len(value))
        ),
        kind="object",
    )


def _dict_key(key: Any) -> str:
    """JSON object keys must be strings; match json.dumps' coercion."""
    if isinstance(key, str):
        return key
    if isinstance(key, bool):
        return "true" if key else "false"
    if key is None:
        return "null"
    if isinstance(key, (int, float)):
        return repr(key) if isinstance(key, float) else str(key)
    raise TypeError(f"keys must be str, int, float, bool or None, not {type(key).__name__}")


def _fold_list(
    value: list, config: SerializeConfig, stats: FoldStats, depth: int
) -> Any:
    if depth >= config.max_depth:
        stats.record()
        return Folded(
            preview=[ELLIPSIS] if value else [],
            note=config.messages.depth_list_folded.format(
                total=len(value), s=_plural(len(value))
            ),
            kind="depth",
        )

    if len(value) <= config.max_list_items:
        return [_walk(item, config, stats, depth + 1) for item in value]

    keep = max(1, min(config.list_preview_items, len(value)))
    preview = [_walk(item, config, stats, depth + 1) for item in value[:keep]]
    # Trailing sentinel so the elision is visible in the structure itself, not
    # only in the comment.
    preview.append(ELLIPSIS)
    stats.record()
    return Folded(
        preview=preview,
        note=config.messages.list_folded.format(
            total=len(value), shown=keep, s=_plural(len(value))
        ),
        kind="list",
    )
