"""Generate a typed Python accessor SDK for a folded JSON file.

The generated module defines dataclasses mirroring the JSON structure at every
depth, including the element type of lists of objects.  An agent reads the
schema from the module docstring, then pulls just the field it wants with
``get("a.b.c")`` instead of loading the whole payload into its context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Separators between identifier words. Python 3 identifiers allow Unicode
# letters, so CJK/Cyrillic/etc. keys become usable attribute names rather than
# being stripped to nothing: "标题" -> 标题, not field_. Underscore counts as a
# separator (so "search_result" -> SearchResult) even though \w would match it.
_UNSAFE = re.compile(r"[\W_]+", re.UNICODE)
_KEYWORDS = frozenset(
    "False None True and as assert async await break class continue def del "
    "elif else except finally for from global if import in is lambda nonlocal "
    "not or pass raise return try while with yield".split()
)

# How many list elements to sample when inferring an element type. Scanning a
# 320k-item list buys nothing: the annotation is a hint for the agent, not a
# contract, and sampling keeps generation time independent of data size.
_LIST_SAMPLE = 5

# Hard stop on class generation, purely to bound output on pathological input
# (e.g. a linked list a thousand levels deep). Real payloads nest far less.
_MAX_CLASS_DEPTH = 24

# Schema lines past this depth are summarized rather than expanded, so the
# docstring itself cannot become the context hog this library exists to avoid.
_SCHEMA_DOC_DEPTH = 6

# Nouns whose plural equals their singular, or that would be mangled by the
# trailing-"s" rule (series -> Sery, status -> Statu).
_INVARIANT_NOUNS = frozenset(
    "series species data media metadata status news children people".split()
)


@dataclass
class _Field:
    """One field of a generated dataclass."""

    attr: str
    json_key: str
    type_ann: str
    # Set when the value is an object: the dataclass to hydrate it into.
    nested: Optional[str] = None
    # Set when the value is a list of objects: the element dataclass. The
    # nesting depth tells _from_dict how many list levels to map over.
    item_class: Optional[str] = None
    list_depth: int = 0


@dataclass
class _Class:
    name: str
    fields: list[_Field] = field(default_factory=list)

    def add(self, f: _Field) -> None:
        """Append ``f``, renaming it if its attribute name is already used.

        Sanitizing is lossy — "a b", "a-b" and "a.b" all normalize to a_b —
        and duplicate attributes would emit `cls(a_b=..., a_b=...)`, which is
        a SyntaxError rather than a merely odd name.
        """
        taken = {existing.attr for existing in self.fields}
        if f.attr in taken:
            base, n = f.attr, 2
            while f.attr in taken:
                f.attr = f"{base}_{n}"
                n += 1
        self.fields.append(f)


class _Builder:
    """Walks a JSON value and accumulates the dataclasses describing it."""

    def __init__(self) -> None:
        self.classes: list[_Class] = []
        self._taken: set[str] = set()

    def reserve(self, base: str) -> str:
        """Claim a unique class name derived from ``base``.

        Reserved *before* recursing into children, so an inner object sharing
        its parent's key cannot steal the parent's name.
        """
        name = base
        n = 2
        while name in self._taken:
            name = f"{base}{n}"
            n += 1
        self._taken.add(name)
        return name

    def build(self, name: str, obj: dict, depth: int) -> None:
        """Emit a class for ``obj``, recursing into nested objects first."""
        cls = _Class(name)
        for k, v in obj.items():
            cls.add(self._field_for(str(k), v, depth))
        # Children are appended by the recursive calls above, so appending the
        # parent here leaves every class defined after the ones it references.
        self.classes.append(cls)

    def _field_for(self, key: str, value: Any, depth: int) -> _Field:
        attr = _to_attr(key)

        if isinstance(value, dict) and value and depth < _MAX_CLASS_DEPTH:
            nested = self.reserve(_to_class_name(key))
            self.build(nested, value, depth + 1)
            return _Field(attr, key, f"Optional[{nested}]", nested=nested)

        item_class, list_depth = self._element_class(key, value, depth)
        if item_class:
            ann = item_class
            for _ in range(list_depth):
                ann = f"list[{ann}]"
            return _Field(
                attr,
                key,
                f"Optional[{ann}]",
                item_class=item_class,
                list_depth=list_depth,
            )

        return _Field(attr, key, f"Optional[{_infer_type(value)}]")

    def _element_class(
        self, key: str, value: Any, depth: int
    ) -> tuple[Optional[str], int]:
        """Build a class for the objects inside a (possibly nested) list.

        Returns ``(class_name, list_depth)``, or ``(None, 0)`` when the value
        is not a list that bottoms out in objects.
        """
        list_depth = 0
        probe = value
        while isinstance(probe, list) and probe:
            list_depth += 1
            probe = probe[0]

        if not list_depth or not isinstance(probe, dict) or not probe:
            return None, 0
        if depth >= _MAX_CLASS_DEPTH:
            return None, 0

        # Union the keys across sampled elements: JSON lists are often ragged,
        # and a field present on only some elements still belongs in the schema.
        merged = _merge_objects(value, list_depth)
        if not merged:
            return None, 0

        name = self.reserve(_singular(_to_class_name(key)))
        self.build(name, merged, depth + 1)
        return name, list_depth


def generate_sdk(key: str, value: Any, json_path: Path) -> tuple[str, str]:
    """Return ``(source_code, root_class_name)`` for a standalone read SDK.

    *source_code* is a self-contained ``.py`` file; *root_class_name* is the
    name of its entry-point dataclass so callers can cite it in prompts.
    """
    builder = _Builder()
    root_name = builder.reserve(_to_class_name(key))

    if isinstance(value, dict) and value:
        builder.build(root_name, value, depth=1)
    else:
        # A list or scalar at the top level has no fields to describe; the
        # root class still carries load()/get() so access stays uniform.
        builder.classes.append(_Class(root_name))

    schema = "\n".join(_schema_doc(value, root_name))
    return _emit_module(root_name, builder.classes, json_path, schema), root_name


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

def _to_class_name(key: str) -> str:
    parts = _UNSAFE.sub(" ", key).split()
    name = "".join(p[:1].upper() + p[1:] for p in parts if p)
    if name[:1].isdigit():
        name = "V" + name
    return name if name.isidentifier() else "Root"


def _to_attr(key: str) -> str:
    """A valid, non-reserved Python attribute name for ``key``.

    The result is only guaranteed to be a legal identifier; it is not
    guaranteed unique. _Class.add() resolves collisions, since distinct keys
    can still normalize to the same name ("a b" and "a-b" both -> a_b).
    """
    name = _UNSAFE.sub("_", key).strip("_")
    if name in _KEYWORDS or not name.isidentifier():
        # Covers keywords, empty results, and leading digits alike.
        name = "f_" + name
    return name if name.isidentifier() else "field_"


def _singular(name: str) -> str:
    """``Users`` -> ``User``, so a list's element class reads naturally.

    Best-effort English heuristics on a cosmetic concern: a wrong guess costs
    a slightly odd class name, nothing more. Data access never depends on it.
    """
    low = name.lower()
    # Already singular, or the same in both numbers.
    if low in _INVARIANT_NOUNS or low.endswith(("ss", "us", "is")):
        return name
    if low.endswith("ies") and len(name) > 4:
        return name[:-3] + "y"
    # Sibilant stems take "-es": boxes, dishes, addresses.
    if low.endswith(("sses", "xes", "zes", "ches", "shes")):
        return name[:-2]
    if low.endswith("s") and len(name) > 2:
        return name[:-1]
    return name


# ---------------------------------------------------------------------------
# Type inference
# ---------------------------------------------------------------------------

def _merge_objects(value: Any, list_depth: int) -> dict:
    """Union the keys of the sampled objects at the bottom of a nested list."""
    items: list[Any] = [value]
    for _ in range(list_depth):
        flat: list[Any] = []
        for it in items:
            if isinstance(it, list):
                flat.extend(it[:_LIST_SAMPLE])
        items = flat

    merged: dict = {}
    for it in items[:_LIST_SAMPLE]:
        if not isinstance(it, dict):
            continue
        for k, v in it.items():
            # First non-null wins, so a null in element 0 does not mask the
            # real type visible in element 1.
            if k not in merged or merged[k] is None:
                merged[k] = v
    return merged


def _infer_type(value: Any, depth: int = 0) -> str:
    """A Python annotation string for a JSON value."""
    if value is None:
        return "Any"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        if not value or depth >= 3:
            return "list"
        inner = {_infer_type(v, depth + 1) for v in value[:_LIST_SAMPLE]}
        return f"list[{inner.pop() if len(inner) == 1 else 'Any'}]"
    if isinstance(value, dict):
        return "dict"
    return "Any"


# ---------------------------------------------------------------------------
# Schema docstring
# ---------------------------------------------------------------------------

def _schema_doc(value: Any, root_name: str) -> list[str]:
    lines = [f"Schema ({root_name}):"]
    if isinstance(value, dict) and value:
        _schema_walk(value, lines, "    ", 0)
    else:
        lines.append(f"    <{_infer_type(value)}>")
    return lines


def _schema_walk(obj: dict, lines: list[str], pad: str, depth: int) -> None:
    for k, v in obj.items():
        lines.append(f"{pad}{k}: {_infer_type(v)}")
        if depth >= _SCHEMA_DOC_DEPTH:
            continue
        if isinstance(v, dict) and v:
            _schema_walk(v, lines, pad + "    ", depth + 1)
        else:
            # Describe list elements once, using the merged key set.
            probe, n = v, 0
            while isinstance(probe, list) and probe:
                n += 1
                probe = probe[0]
            if n and isinstance(probe, dict) and probe:
                merged = _merge_objects(v, n)
                if merged:
                    lines.append(f"{pad}    [each item]")
                    _schema_walk(merged, lines, pad + "        ", depth + 2)


# ---------------------------------------------------------------------------
# Code emission
# ---------------------------------------------------------------------------

def _emit_module(
    root_name: str,
    classes: list[_Class],
    json_path: Path,
    schema_text: str,
) -> str:
    out: list[str] = []
    w = out.append

    # Docstring first: an agent that only reads the head of this file still
    # learns the full schema, and the schema's size tracks field count rather
    # than payload size.
    w('"""')
    w(f"Read-only SDK for {json_path.name}")
    w("")
    for line in schema_text.splitlines():
        w(line)
    w("")
    w("Usage:")
    w(f"    {root_name}.get('a.b.c')        # one field, dotted path")
    w(f"    {root_name}.get('items.0.id')   # list index as a path segment")
    w(f"    {root_name}.keys('a.b')         # what is under a node")
    w(f"    {root_name}.load()              # whole payload, typed")
    w('"""')
    w("")
    w("import json")
    w("from dataclasses import dataclass")
    w("from pathlib import Path")
    w("from typing import Any, Optional")
    w("")
    w(f'_JSON_PATH = Path(__file__).with_name("{json_path.name}")')
    w("")
    w("_MISSING = object()")
    w("")
    w("")
    w("def _read() -> Any:")
    w("    return json.loads(_JSON_PATH.read_text(encoding='utf-8'))")

    for cls in classes:
        w("")
        w("")
        w("@dataclass")
        w(f"class {cls.name}:")
        if not cls.fields:
            w("    pass")
        for f in cls.fields:
            # Every field defaults to None: dataclass forbids a non-default
            # field after a defaulted one, and field order comes from the
            # JSON's key order, which the caller does not control.
            w(f"    {f.attr}: {f.type_ann} = None  # JSON key: {f.json_key!r}")

        # _from_dict on every class, so parents can hydrate their children.
        w("")
        w("    @classmethod")
        w(f'    def _from_dict(cls, d: dict) -> "{cls.name}":')
        if not cls.fields:
            w("        return cls()")
        else:
            w("        return cls(")
            for f in cls.fields:
                w(f"            {f.attr}={_hydrate(f)},")
            w("        )")

        if cls.name == root_name:
            _emit_root_methods(w, root_name)

    w("")
    return "\n".join(out)


def _hydrate(f: _Field) -> str:
    """The expression that builds one field's value inside ``_from_dict``."""
    key = repr(f.json_key)
    if f.nested:
        return (
            f"{f.nested}._from_dict(d[{key}]) "
            f"if isinstance(d.get({key}), dict) else None"
        )
    if f.item_class:
        # Build the comprehension from the inside out so nested lists keep
        # their shape: a list[list[Item]] maps twice.
        expr = f"{f.item_class}._from_dict(_x0) if isinstance(_x0, dict) else None"
        for lvl in range(f.list_depth):
            outer = (
                f"d.get({key}) or []"
                if lvl == f.list_depth - 1
                else f"(_x{lvl + 1} or [])"
            )
            expr = f"[{expr} for _x{lvl} in {outer}]"
        return expr
    return f"d.get({key})"


def _emit_root_methods(w, root_name: str) -> None:
    """Emit the file-level entry points, which only the root class carries."""
    w("")
    w("    @classmethod")
    w(f'    def load(cls) -> "{root_name}":')
    w('        """The whole payload as typed dataclasses."""')
    w("        return cls._from_dict(_read())")
    w("")
    w("    @classmethod")
    w("    def get(cls, path: str = '', default: Any = _MISSING) -> Any:")
    w('        """One value, addressed by dotted path.')
    w("")
    w("        Use this instead of reading the JSON file: only the value at")
    w("        ``path`` is returned, so the rest never reaches your context.")
    w("        Integer segments index into lists.")
    w("")
    w("            get('metrics.revenue_usd')")
    w("            get('users.0.address.city')")
    w("")
    w("        Raises KeyError/IndexError when the path does not exist,")
    w("        unless a ``default`` is given.")
    w('        """')
    w("        node: Any = _read()")
    w("        if not path:")
    w("            return node")
    w("        for i, seg in enumerate(path.split('.')):")
    w("            try:")
    w("                if isinstance(node, list):")
    w("                    node = node[int(seg)]")
    w("                else:")
    w("                    node = node[seg]")
    w("            except (KeyError, IndexError, TypeError, ValueError):")
    w("                if default is not _MISSING:")
    w("                    return default")
    w("                where = '.'.join(path.split('.')[:i + 1])")
    w("                raise KeyError(")
    w("                    f'no such path: {where!r} (in {path!r})'")
    w("                ) from None")
    w("        return node")
    w("")
    w("    @classmethod")
    w("    def keys(cls, path: str = '') -> Any:")
    w('        """What is under ``path``, without pulling its values.')
    w("")
    w("        Object -> its key names. List -> its length. Scalar -> its")
    w("        type name. Use it to explore before committing to a get().")
    w('        """')
    w("        node = cls.get(path)")
    w("        if isinstance(node, dict):")
    w("            return list(node)")
    w("        if isinstance(node, list):")
    w("            return len(node)")
    w("        return type(node).__name__")
