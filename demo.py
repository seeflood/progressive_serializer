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


def sdk_mode(out_dir):
    """Show that generate_sdk=True writes a typed accessor alongside the JSON.

    The generated <key>_sdk.py lets an agent call load_field("key") to fetch
    only one field, rather than cat-ing the whole JSON into its context.
    """
    result = serialize(
        {
            "report": {
                "title": "Q3 Performance Report",
                "executive_summary": (
                    "Revenue grew 18 % year-over-year driven by the enterprise "
                    "segment.  Margin expansion of 2.4 pp reflects operating "
                    "leverage. " * 400
                ),
                "metrics": {
                    "revenue_usd": 4_820_000,
                    "gross_margin_pct": 61.3,
                    "headcount": 312,
                },
                "recommendations": [
                    "Accelerate enterprise sales hiring",
                    "Reduce cloud spend via reserved instances",
                    "Launch APAC expansion in Q1",
                ],
            }
        },
        out_dir / "sdk",
        SerializeConfig(max_value_size=300, generate_sdk=True),
    )

    print("=" * 70)
    print("5. SDK mode: typed accessor generated alongside the JSON file")
    print("=" * 70)
    print(result.overview_text)

    for key, json_path in result.files.items():
        sdk_path = result.sdk_files.get(key)
        print(f"[full JSON]  {key} -> {json_path}  ({json_path.stat().st_size:,} bytes)")
        if sdk_path:
            print(f"[typed SDK]  {key} -> {sdk_path}")

    print()
    print("--- What the SDK docstring looks like (schema at a glance) ---")
    if result.sdk_files:
        sdk_path = next(iter(result.sdk_files.values()))
        # Print just the module docstring so the demo stays readable.
        src = sdk_path.read_text(encoding="utf-8")
        doc_end = src.index('"""', 3) + 3
        print(src[:doc_end])

    print()
    print("--- Agent fetches only 'metrics' via load_field, not the whole file ---")
    import importlib.util
    if result.sdk_files:
        sdk_path = next(iter(result.sdk_files.values()))
        root_class = result.items[0].sdk_class
        spec = importlib.util.spec_from_file_location("_demo_sdk", sdk_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls = getattr(mod, root_class)
        metrics = cls.load_field("metrics")
        print(f"metrics = {metrics}")
    print()


def main():
    # Start clean so stale files from a previous run can't confuse the output.
    if DEMO_OUT.exists():
        shutil.rmtree(DEMO_OUT)
    basic(DEMO_OUT)
    rules(DEMO_OUT)
    objects(DEMO_OUT)
    budgets(DEMO_OUT)
    sdk_mode(DEMO_OUT)
    print("=" * 70)
    print(f"All output kept at: {DEMO_OUT}")
    print("Try: cat demo_out/basic/overview.md")
    print("     cat demo_out/sdk/report_sdk.py")
    print("=" * 70)


if __name__ == "__main__":
    main()
