"""Configuration for progressive serialization."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from .messages import ENGLISH, Messages


@runtime_checkable
class Renderer(Protocol):
    """Turns an overview context into the text of ``overview.md``."""

    def render(self, ctx: Any) -> str:  # pragma: no cover - protocol
        ...


@dataclass
class SerializeConfig:
    """Thresholds and hooks controlling how aggressively values are folded.

    Sizes are measured by ``size_fn`` over the rendered JSON text; with the
    default ``len`` they are character counts.
    """

    # A value whose full JSON exceeds this budget gets folded and written to
    # its own file. Values at or below it are inlined verbatim.
    max_value_size: int = 2000

    # --- folding rules -------------------------------------------------
    # Strings longer than this are replaced by a "..." placeholder.
    max_string_len: int = 200
    # Lists longer than this are folded down to ``list_preview_items``.
    max_list_items: int = 10
    # How many leading items a folded list keeps.
    list_preview_items: int = 1
    # Objects with more keys than this keep only the first N.
    max_object_keys: int = 20
    # Containers at or below this nesting depth are walked; deeper ones are
    # folded whole.
    max_depth: int = 6
    # bytes/bytearray/memoryview are folded regardless of length.
    fold_binary: bool = True

    # --- hooks ---------------------------------------------------------
    # Measures the "size" of rendered JSON. Swap in a tokenizer to budget by
    # tokens, e.g. ``lambda s: len(enc.encode(s))``.
    size_fn: Callable[[str], int] = len
    # Converts objects json cannot serialize. Same contract as
    # ``json.dumps(default=...)``: return a JSON-serializable replacement.
    default: Optional[Callable[[Any], Any]] = None
    # ``None`` means the built-in string.Template renderer.
    renderer: Optional[Renderer] = None
    # Prose for fold notes and the overview notice. Use messages.CHINESE or your
    # own Messages instance to localize.
    messages: Messages = field(default_factory=lambda: ENGLISH)

    # --- output --------------------------------------------------------
    indent: int = 2
    ensure_ascii: bool = False
    # How many times to retry with tightened thresholds when one round of
    # folding still leaves the value over budget.
    escalate_rounds: int = 3

    def tightened(self) -> "SerializeConfig":
        """A copy with the folding thresholds stepped down one notch.

        Used by the escalation loop when a single folding pass is not enough
        to bring a value under ``max_value_size``.
        """
        return replace(
            self,
            max_string_len=max(16, self.max_string_len // 4),
            max_list_items=1,
            list_preview_items=1,
            max_object_keys=max(1, self.max_object_keys // 2),
            max_depth=max(1, self.max_depth - 1),
        )
