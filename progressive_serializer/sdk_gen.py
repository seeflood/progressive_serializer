"""Generate a typed Python accessor SDK for a folded JSON file.

The generated module defines dataclasses that mirror the top-level JSON
schema.  An agent can import the root class and call ``load_field("key")``
to fetch exactly what it needs, instead of reading the entire JSON file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_UNSAFE = re.compile(r"[^a-zA-Z0-9]+")
_KEYWORDS = frozenset(
    "False None True and as assert async await break class continue def del "
    "elif else except finally for from global if import in is lambda nonlocal "
    "not or pass raise return try while with yield".split()
)
_MAX_SCHEMA_DEPTH = 4

# Each field: (python_name, json_key, type_annotation, nested_class_name | None)
_Field = tuple[str, str, str, "str | None"]


def generate_sdk(key: str, value: Any, json_path: Path) -> tuple[str, str]:
    """Return ``(source_code, root_class_name)`` for a standalone read SDK.

    *source_code* is a self-contained ``.py`` file; *root_class_name* is the
    name of its entry-point dataclass so callers can reference it in prompts.
    """
    root_name = _to_class_name(key)
    classes: list[tuple[str, list[_Field]]] = []
    if isinstance(value, dict):
        _collect_classes(root_name, value, classes)
    else:
        classes.append((root_name, []))

    schema_lines = _schema_doc(value, root_name)
    schema_text = "\n".join(schema_lines)
    source = _emit_module(root_name, classes, json_path, schema_text)
    return source, root_name


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_class_name(key: str) -> str:
    parts = _UNSAFE.sub(" ", key).split()
    name = "".join(p.capitalize() for p in parts if p) or "Root"
    if name[0].isdigit():
        name = "V" + name
    return name


def _to_attr(key: str) -> str:
    name = _UNSAFE.sub("_", key).strip("_") or "field_"
    if name in _KEYWORDS or name[0].isdigit():
        name = "f_" + name
    return name


def _infer_type(value: Any, depth: int = 0) -> str:
    """Return a Python type annotation string for a JSON value."""
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        if not value or depth >= _MAX_SCHEMA_DEPTH:
            return "list"
        item_types = {_infer_type(v, depth + 1) for v in value[:5]}
        inner = next(iter(item_types)) if len(item_types) == 1 else "Any"
        return f"list[{inner}]"
    if isinstance(value, dict):
        return "dict"
    return "Any"


def _collect_classes(
    class_name: str,
    obj: dict,
    out: list[tuple[str, list[_Field]]],
    depth: int = 0,
) -> None:
    """Recursively build (class_name, fields) pairs for obj and its sub-dicts."""
    fields: list[_Field] = []
    for k, v in obj.items():
        attr = _to_attr(str(k))
        if isinstance(v, dict) and depth < _MAX_SCHEMA_DEPTH:
            nested_name = _to_class_name(str(k))
            # Avoid duplicate class names by suffixing.
            existing = {c[0] for c in out}
            candidate = nested_name
            counter = 2
            while candidate in existing:
                candidate = f"{nested_name}{counter}"
                counter += 1
            nested_name = candidate
            _collect_classes(nested_name, v, out, depth + 1)
            fields.append((attr, str(k), f"Optional[{nested_name}]", nested_name))
        else:
            fields.append((attr, str(k), _infer_type(v), None))
    out.append((class_name, fields))


def _schema_doc(value: Any, root_name: str, indent: str = "    ") -> list[str]:
    """Build a compact schema summary for the module docstring."""
    lines: list[str] = [f"Schema ({root_name}):"]
    if isinstance(value, dict):
        _schema_lines_for_dict(value, lines, indent, depth=0)
    else:
        lines.append(f"{indent}<{_infer_type(value)}>")
    return lines


def _schema_lines_for_dict(
    obj: dict, lines: list[str], prefix: str, depth: int
) -> None:
    for k, v in obj.items():
        type_str = _infer_type(v)
        lines.append(f"{prefix}{k}: {type_str}")
        if isinstance(v, dict) and depth < _MAX_SCHEMA_DEPTH - 1:
            _schema_lines_for_dict(v, lines, prefix + "    ", depth + 1)


def _emit_module(
    root_name: str,
    classes: list[tuple[str, list[_Field]]],
    json_path: Path,
    schema_text: str,
) -> str:
    """Emit the complete SDK source as a string."""
    lines: list[str] = []

    # Module docstring — schema overview so an agent reading just the top of
    # the file knows what fields exist without loading the JSON.
    lines.append('"""')
    lines.append(f"Read-only SDK for {json_path.name}")
    lines.append("")
    for schema_line in schema_text.splitlines():
        lines.append(schema_line)
    lines.append("")
    lines.append("Usage:")
    lines.append(f"    obj = {root_name}.load()")
    lines.append(f"    val = {root_name}.load_field('some_key')")
    lines.append('"""')
    lines.append("")
    lines.append("import json")
    lines.append("from dataclasses import dataclass")
    lines.append("from pathlib import Path")
    lines.append("from typing import Any, Optional")
    lines.append("")
    lines.append(f'_JSON_PATH = Path(__file__).with_name("{json_path.name}")')
    lines.append("")

    # Emit nested classes first (they appear before the root in *out* because
    # _collect_classes appends the current class after recursing into children).
    for class_name, fields in classes:
        lines.append("")
        lines.append("")
        lines.append("@dataclass")
        lines.append(f"class {class_name}:")
        if not fields:
            lines.append("    pass")
        else:
            for attr, json_key, type_ann, _ in fields:
                # Every field defaults to None: a dataclass cannot have a
                # non-default field after a defaulted one, and keys may be
                # absent from the JSON anyway.
                inner = type_ann[len("Optional[") : -1] if type_ann.startswith(
                    "Optional["
                ) else type_ann
                lines.append(
                    f"    {attr}: Optional[{inner}] = None  # JSON key: {json_key!r}"
                )

        # Every class needs _from_dict so parents can build their children.
        lines.append("")
        lines.append("    @classmethod")
        lines.append("    def _from_dict(cls, d: dict) -> " + f'"{class_name}":')
        if not fields:
            lines.append("        return cls()")
        else:
            lines.append("        return cls(")
            for attr, json_key, type_ann, nested_class in fields:
                if nested_class:
                    lines.append(
                        f"            {attr}={nested_class}._from_dict(d[{json_key!r}])"
                        f" if isinstance(d.get({json_key!r}), dict) else None,"
                    )
                else:
                    lines.append(f"            {attr}=d.get({json_key!r}),")
            lines.append("        )")

        # Only the root class is an entry point into the file on disk.
        if class_name == root_name:
            lines.append("")
            lines.append("    @classmethod")
            lines.append("    def load(cls) -> " + f'"{root_name}":')
            lines.append('        """Load and return the full object from disk."""')
            lines.append(
                "        return cls._from_dict("
                "json.loads(_JSON_PATH.read_text(encoding='utf-8')))"
            )
            lines.append("")
            lines.append("    @classmethod")
            lines.append("    def load_field(cls, key: str) -> Any:")
            lines.append(
                '        """Load one top-level field, keeping the rest '
                'out of context."""'
            )
            lines.append(
                "        return json.loads("
                "_JSON_PATH.read_text(encoding='utf-8'))[key]"
            )

    lines.append("")
    return "\n".join(lines)
