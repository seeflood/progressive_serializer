"""Progressive disclosure of large JSON payloads for LLM agents.

Turn a dict of oversized values into a compact ``overview.md`` plus full
``<key>.json`` files an agent can read on demand:

    >>> from progressive_serializer import serialize
    >>> result = serialize({"doc": {...}}, "/tmp/out")
    >>> print(result.overview_text)
"""

from .config import Renderer, SerializeConfig
from .fold import ELLIPSIS, Folded, FoldStats, fold
from .jsonc import dumps as jsonc_dumps
from .messages import CHINESE, ENGLISH, Messages
from .render import (
    DEFAULT_FOLDED_NOTICE,
    DEFAULT_ITEM_TEMPLATE,
    ItemView,
    OverviewContext,
    StringTemplateRenderer,
)
from .serializer import OVERVIEW_NAME, SerializeResult, serialize

__all__ = [
    "serialize",
    "SerializeConfig",
    "SerializeResult",
    "Renderer",
    "StringTemplateRenderer",
    "OverviewContext",
    "ItemView",
    "Folded",
    "FoldStats",
    "fold",
    "ELLIPSIS",
    "Messages",
    "ENGLISH",
    "CHINESE",
    "jsonc_dumps",
    "OVERVIEW_NAME",
    "DEFAULT_ITEM_TEMPLATE",
    "DEFAULT_FOLDED_NOTICE",
]

__version__ = "0.1.0"
