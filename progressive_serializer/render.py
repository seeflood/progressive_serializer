"""Rendering ``overview.md`` from the per-key results.

The default renderer is built on :class:`string.Template` so the library stays
dependency-free. Any object with ``render(ctx) -> str`` can be substituted via
``SerializeConfig(renderer=...)`` — e.g. a Jinja2-backed one if you want loops
and conditionals in your template.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from string import Template
from typing import Optional

from .messages import ENGLISH

DEFAULT_HEADER = ""

DEFAULT_ITEM_TEMPLATE = "- ${key}\n\n${notice}${fence}json\n${json_block}\n${fence}"

DEFAULT_FOLDED_NOTICE = ENGLISH.folded_notice

DEFAULT_SEPARATOR = "\n\n"

DEFAULT_FOOTER = ""

_BACKTICK_RUN = re.compile(r"`+")


@dataclass
class ItemView:
    """One key's rendered result, as handed to the renderer."""

    key: str
    json_block: str
    folded: bool
    # Path to the complete JSON file, or None when the value was inlined whole.
    full_path: Optional[str]
    # Size of the untruncated JSON, per ``size_fn``.
    original_size: int
    # Size of what actually goes into overview.md.
    rendered_size: int


@dataclass
class OverviewContext:
    out_dir: str
    items: list[ItemView] = field(default_factory=list)


class StringTemplateRenderer:
    """Assembles ``overview.md`` from overridable template fragments.

    ``string.Template`` has no loops, so the per-item template is applied once
    per key and the results joined by ``separator``.
    """

    def __init__(
        self,
        *,
        item_template: str = DEFAULT_ITEM_TEMPLATE,
        folded_notice: Optional[str] = None,
        header: str = DEFAULT_HEADER,
        footer: str = DEFAULT_FOOTER,
        separator: str = DEFAULT_SEPARATOR,
    ) -> None:
        self.item_template = Template(item_template)
        self.folded_notice = Template(
            DEFAULT_FOLDED_NOTICE if folded_notice is None else folded_notice
        )
        self.header = header
        self.footer = footer
        self.separator = separator

    def render(self, ctx: OverviewContext) -> str:
        blocks = [self.render_item(item, ctx) for item in ctx.items]
        body = self.separator.join(blocks)
        return (self.header + body + self.footer).rstrip("\n") + "\n"

    def render_item(self, item: ItemView, ctx: OverviewContext) -> str:
        notice = ""
        if item.folded and item.full_path:
            notice = self.folded_notice.substitute(
                key=item.key, full_path=item.full_path, out_dir=ctx.out_dir
            )
        # Values are not rescanned by substitute(), so a "$" inside json_block
        # is safe; only the template itself needs "$$".
        return self.item_template.substitute(
            key=item.key,
            notice=notice,
            json_block=item.json_block,
            fence=_fence_for(item.json_block),
            full_path=item.full_path or "",
            out_dir=ctx.out_dir,
            original_size=item.original_size,
            rendered_size=item.rendered_size,
        )


def _fence_for(text: str) -> str:
    """A code fence long enough to not be closed by backticks inside ``text``.

    JSON does not escape backticks, so content containing ``` would otherwise
    break out of the markdown block.
    """
    longest = max((len(m.group()) for m in _BACKTICK_RUN.finditer(text)), default=0)
    return "`" * max(3, longest + 1)
