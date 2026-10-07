"""Walk through the library on the example from the original requirements.

    python3 demo.py

Output lands in ./demo_out/ and is left on disk so you can open the files the
overview points at.
"""

import json
import shutil
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path

from progressive_serializer import SerializeConfig, serialize

DEMO_OUT = Path(__file__).parent / "demo_out"


def show(title, result):
    print("=" * 70)
    print(title)
    print("=" * 70)
    print(result.overview_text)
    for key, path in result.files.items():
        size = path.stat().st_size
        print(f"[full content] {key} -> {path}  ({size:,} bytes)")
    print()


def basic(out_dir):
    result = serialize(
        {
            "key1": {"foo": "bar"},
            "key2": {
                "article": "Progressive disclosure keeps prompts small. " * 2500,
                "summary": (
                    "Fold oversized JSON into an overview; keep the full "
                    "payload in a file the agent reads only when needed."
                ),
                "err_code": 0,
            },
        },
        out_dir / "basic",
    )
    show("1. Basic: one small value inlined, one large value folded", result)

    article = json.loads(result.files["key2"].read_text(encoding="utf-8"))["article"]
    print(f"Article recovered from disk: {len(article):,} chars\n")


def rules(out_dir):
    result = serialize(
        {
            "mixed": {
                "long_text": "lorem ipsum dolor sit amet " * 120,
                "thumbnail": b"\x89PNG\r\n\x1a\n" + b"\x00" * 4000,
                "attachments": [{"name": f"file{i}.pdf"} for i in range(320)],
                "deep": {"a": {"b": {"c": {"d": {"e": {"f": "too deep"}}}}}},
                "err_code": 0,
            }
        },
        out_dir / "rules",
        SerializeConfig(max_value_size=400, max_depth=4),
    )
    show("2. Folding rules: long string, binary, big list, deep nesting", result)


def objects(out_dir):
    @dataclass
    class Report:
        body: str
        score: float

    result = serialize(
        {"report": Report("the quick brown fox jumps over the lazy dog " * 50, 0.93)},
        out_dir / "objects",
        SerializeConfig(
            max_value_size=200,
            default=lambda o: asdict(o) if is_dataclass(o) else vars(o),
        ),
    )
    show("3. Class objects via the `default` hook", result)


def budgets(out_dir):
    data = {"metrics": {f"metric_{i}": i * 1.5 for i in range(300)}}
    tight = serialize(data, out_dir / "tight", SerializeConfig(max_value_size=300))
    print("=" * 70)
    print("4. Escalation: 300 small fields, no single rule fires")
    print("=" * 70)
    item = tight.items[0]
    print(f"full JSON: {item.original_size:,} chars")
    print(f"overview:  {item.rendered_size:,} chars (budget 300)")
    print(tight.overview_text)
    for key, path in tight.files.items():
        print(f"[full content] {key} -> {path}  ({path.stat().st_size:,} bytes)")
    print()


def main():
    # Start clean so stale files from a previous run can't confuse the output.
    if DEMO_OUT.exists():
        shutil.rmtree(DEMO_OUT)
    basic(DEMO_OUT)
    rules(DEMO_OUT)
    objects(DEMO_OUT)
    budgets(DEMO_OUT)
    print("=" * 70)
    print(f"All output kept at: {DEMO_OUT}")
    print("Try: cat demo_out/basic/overview.md")
    print("=" * 70)


if __name__ == "__main__":
    main()
