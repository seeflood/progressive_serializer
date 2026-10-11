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
    # Objects with more keys than this keep only the first N. ``None`` (the
    # default) disables the rule: dropping fields removes the agent's
    # navigation information — it can no longer tell the field is there at all,
    # whereas every other rule leaves a visible placeholder in its place. Set
    # an int to opt in.
    max_object_keys: Optional[int] = None
    # Containers at or below this nesting depth are walked; deeper ones are
    # folded whole.
    max_depth: int = 10
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
    # Comment delimiters: "line" for ``// note``, "block" for ``/* note */``.
    # ``None`` lets each API pick its own default: ``serialize`` uses "line",
    # since its JSON sits in a fenced markdown block where nothing can be
    # mistaken for a comment's continuation; ``serialize_value`` uses "block",
    # since its return value is spliced into a document the library does not
    # control and ``//`` has no terminator. Set a string to force one.
    comment_style: Optional[str] = None
    # Column budget for wrapping block comments. 0 keeps each on one line.
    # Ignored in "line" style, which cannot wrap.
    comment_width: int = 88
    # How many times to retry with tightened thresholds when one round of
    # folding still leaves the value over budget.
    escalate_rounds: int = 3
    # When True, write a ``<key>_sdk.py`` beside each ``<key>.json`` that
    # spills to disk.  The SDK exposes typed dataclasses mirroring the JSON
    # schema and ``get("a.b.c")`` so an agent can fetch only the fields it
    # needs rather than loading the whole file.
    generate_sdk: bool = False

    def tightened(self) -> "SerializeConfig":
        """A copy with the folding thresholds stepped down one notch.

        Used by the escalation loop when a single folding pass is not enough
        to bring a value under ``max_value_size``. A disabled
        ``max_object_keys`` stays disabled — escalation tightens rules the
        caller opted into, it does not enable new ones.

        Depth is halved rather than decremented so that the rounds needed to
        converge do not depend on where ``max_depth`` started: stepping by one
        would make a deep default (10) take four times as many rounds as a
        shallow one to reach the same place.
        """
        keys = self.max_object_keys
        return replace(
            self,
            max_string_len=max(16, self.max_string_len // 4),
            max_list_items=1,
            list_preview_items=1,
            max_object_keys=None if keys is None else max(1, keys // 2),
            max_depth=max(1, self.max_depth // 2),
        )
