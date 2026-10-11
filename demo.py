"""Walk through the library on the example from the original requirements.

    python3 demo.py

Output lands in ./demo_out/ and is left on disk so you can open the files the
overview points at.
"""

import json
import shutil
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path

from progressive_serializer import (
    CHINESE,
    Messages,
    SerializeConfig,
    StringTemplateRenderer,
    serialize,
)

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
    """300 tiny fields: no rule fires until max_object_keys is opted into."""
    data = {"metrics": {f"metric_{i}": i * 1.5 for i in range(300)}}

    print("=" * 70)
    print("4. Escalation: 300 small fields, no single rule fires")
    print("=" * 70)

    # Default config: dropping fields is opt-in, so this stays over budget.
    off = serialize(data, out_dir / "keys_kept", SerializeConfig(max_value_size=300))
    print(f"max_object_keys=None (default): {off.items[0].rendered_size:,} chars "
          f"-> over the 300 budget, every field name kept")

    # Opt in, and escalation can tighten it until the value fits.
    tight = serialize(
        data,
        out_dir / "tight",
        SerializeConfig(max_value_size=300, max_object_keys=20),
    )
    item = tight.items[0]
    print(f"max_object_keys=20:             {item.rendered_size:,} chars "
          f"-> fits, {item.original_size:,} chars spilled to disk")
    print()
    print(tight.overview_text)
    for key, path in tight.files.items():
        print(f"[full content] {key} -> {path}  ({path.stat().st_size:,} bytes)")
    print()


def sdk_mode(out_dir):
    """Show that generate_sdk=True writes a typed accessor alongside the JSON.

    The payload is deliberately deep and contains a list of objects, because
    those are the two shapes a flat "read the file" approach handles worst.
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
                "finance": {
                    "quarter": {
                        "metrics": {
                            "revenue": {
                                "total_usd": 4_820_000,
                                "by_region": {"amer": 3_100_000, "emea": 1_720_000},
                            },
                            "gross_margin_pct": 61.3,
                        }
                    }
                },
                "teams": [
                    {
                        "name": "Platform",
                        "lead": {"name": "Ann Lee", "email": "ann@example.com"},
                        "headcount": 48,
                    },
                    {
                        "name": "Growth",
                        "lead": {"name": "Bo Chen", "email": "bo@example.com"},
                        "headcount": 31,
                    },
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
        print(
            f"[full JSON]  {key} -> {json_path}"
            f"  ({json_path.stat().st_size:,} bytes)"
        )
        if sdk_path:
            print(f"[typed SDK]  {key} -> {sdk_path}")

    if not result.sdk_files:
        return

    sdk_path = next(iter(result.sdk_files.values()))
    src = sdk_path.read_text(encoding="utf-8")

    print()
    print("--- The SDK docstring: full schema, every level, no JSON read ---")
    print(src[: src.index('"""', 3) + 3])

    print()
    print("--- Classes generated (one per object, at every depth) ---")
    print(
        ", ".join(
            line[len("class ") : -1]
            for line in src.splitlines()
            if line.startswith("class ")
        )
    )

    # Load the generated module the way an agent would.
    import importlib.util

    spec = importlib.util.spec_from_file_location("_demo_sdk", sdk_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    report = getattr(mod, result.items[0].sdk_class)

    print()
    print("--- Agent pulls single deep values by path, never the 49k article ---")
    deep = "finance.quarter.metrics.revenue.by_region.emea"
    print(f"get({deep!r})")
    print(f"  -> {report.get(deep)}")
    print("get('teams.1.lead.email')")
    print(f"  -> {report.get('teams.1.lead.email')}")

    print()
    print("--- keys() explores a node without pulling its values ---")
    print(f"keys('finance.quarter.metrics') -> {report.keys('finance.quarter.metrics')}")
    print(f"keys('teams')                   -> {report.keys('teams')} items")
    print(f"keys('teams.0')                 -> {report.keys('teams.0')}")

    print()
    print("--- Or work with typed objects when the whole tree is wanted ---")
    obj = report.load()
    print(f"obj.finance.quarter.metrics.revenue.total_usd = "
          f"{obj.finance.quarter.metrics.revenue.total_usd:,}")
    for team in obj.teams:
        print(f"  team {team.name:9} lead={team.lead.name:9} hc={team.headcount}")
    print()


def chinese_prompt(out_dir):
    """Generate the whole prompt in Chinese via messages=CHINESE.

    Two things get localized: the trailing `//` fold notes inside the JSONC
    block, and the notice pointing at the full file / SDK. Everything else
    (keys, data, generated code) is untouched.
    """
    data = {
        "搜索结果": {
            "标题": "渐进式披露：为 Agent 压缩工具返回值",
            "正文": "大模型的上下文窗口是稀缺资源，不应被用不到的字段占满。" * 600,
            "作者": {"姓名": "李雷", "单位": "平台组"},
            "附件": [{"文件名": f"附件{i}.pdf", "大小": 1024 * i} for i in range(320)],
            "错误码": 0,
        }
    }

    plain = serialize(
        data,
        out_dir / "zh",
        SerializeConfig(max_value_size=400, messages=CHINESE),
    )

    print("=" * 70)
    print("6. 中文 prompt：messages=CHINESE")
    print("=" * 70)
    print(plain.overview_text)
    for key, path in plain.files.items():
        print(f"[完整内容] {key} -> {path}  ({path.stat().st_size:,} 字节)")

    # Same data, now with the SDK so the Chinese sdk_notice shows too.
    with_sdk = serialize(
        data,
        out_dir / "zh_sdk",
        SerializeConfig(max_value_size=400, messages=CHINESE, generate_sdk=True),
    )

    print()
    print("--- 开启 SDK 后，提示语改为中文的 sdk_notice ---")
    # The notice is the prose between the key line and the fenced block.
    notice = with_sdk.overview_text.split("```", 1)[0].split("\n", 2)[-1]
    print(notice.strip())
    print()
    print("SDK schema 里的字段名保留原始中文 key：")
    sdk_src = next(iter(with_sdk.sdk_files.values())).read_text(encoding="utf-8")
    print(sdk_src[: sdk_src.index("Usage:")].rstrip())

    print()
    print("--- 自定义中文文案：messages=Messages(...) ---")
    custom = serialize(
        {"文档": {"正文": "甲" * 5000, "错误码": 0}},
        out_dir / "zh_custom",
        SerializeConfig(
            max_value_size=200,
            messages=Messages(
                string_folded="此处省略 {length} 字",
                object_folded="共 {total} 个字段，此处仅列部分",
                folded_notice="【完整数据】`${full_path}`\n\n",
            ),
        ),
    )
    print(custom.overview_text)


def custom_template(out_dir):
    """Reshape overview.md itself, via template fragments or a whole Renderer.

    `messages` controls the *prose*; the renderer controls the *layout*. This
    is the knob for fitting the overview into an existing prompt format.
    """
    data = {
        "search": {"hits": 1284, "took_ms": 37},
        "document": {
            "title": "Progressive disclosure for agent tool results",
            "body": "Context windows are a scarce resource. " * 900,
            "tags": ["context", "serialization", "agents"],
        },
    }

    print("=" * 70)
    print("7. Custom templates: reshaping overview.md")
    print("=" * 70)

    # --- (a) Override individual fragments -----------------------------
    # Every fragment of the default renderer is replaceable. Placeholders
    # available: key, notice, json_block, fence, full_path, out_dir,
    # original_size, rendered_size.
    renderer = StringTemplateRenderer(
        header="# Tool results\n\n",
        item_template=(
            "## `${key}`\n\n"
            "> ${original_size} chars raw, ${rendered_size} shown\n\n"
            "${notice}${fence}json\n${json_block}\n${fence}"
        ),
        folded_notice="**Truncated.** Full payload: `${full_path}`\n\n",
        separator="\n\n---\n\n",
        footer="\n\n_End of results._\n",
    )
    frag = serialize(
        data,
        out_dir / "tpl_fragments",
        SerializeConfig(max_value_size=300, renderer=renderer),
    )
    print("--- (a) StringTemplateRenderer with every fragment overridden ---")
    print(frag.overview_text)

    # --- (b) A renderer of your own ------------------------------------
    # Any object with render(ctx) -> str works, so the output need not be
    # markdown at all. Here: a compact index an agent can scan in one pass,
    # with the bulky JSON blocks dropped entirely.
    class IndexRenderer:
        """Emits a table of contents instead of inline JSON."""

        def render(self, ctx):
            lines = [f"{len(ctx.items)} result(s) in {ctx.out_dir}", ""]
            width = max(len(i.key) for i in ctx.items) if ctx.items else 0
            for item in ctx.items:
                if item.folded:
                    where = f"-> {Path(item.full_path).name}"
                    state = "folded"
                else:
                    where = "(inline below)"
                    state = "whole"
                lines.append(
                    f"  {item.key:<{width}}  {item.original_size:>7,} chars  "
                    f"{state:6}  {where}"
                )
            lines.append("")
            # Only the small values are worth inlining in an index.
            for item in ctx.items:
                if not item.folded:
                    lines.append(f"{item.key} = {item.json_block}")
            return "\n".join(lines) + "\n"

    idx = serialize(
        data,
        out_dir / "tpl_renderer",
        SerializeConfig(max_value_size=300, renderer=IndexRenderer()),
    )
    print("--- (b) A custom Renderer: index instead of inline JSON ---")
    print(idx.overview_text)

    # A caller-supplied renderer owns the notice text, so `messages` no
    # longer governs it -- but it still governs the fold notes in the JSONC.
    zh = serialize(
        data,
        out_dir / "tpl_zh",
        SerializeConfig(
            max_value_size=300,
            messages=CHINESE,
            renderer=StringTemplateRenderer(
                item_template="${key}\n${notice}${fence}json\n${json_block}\n${fence}",
                folded_notice="FULL: ${full_path}\n\n",
            ),
        ),
    )
    print("--- (c) Custom renderer + messages=CHINESE ---")
    print("Notice comes from the renderer (English), fold notes from "
          "messages (Chinese):")
    print(zh.overview_text)


def main():
    # Start clean so stale files from a previous run can't confuse the output.
    if DEMO_OUT.exists():
        shutil.rmtree(DEMO_OUT)
    basic(DEMO_OUT)
    rules(DEMO_OUT)
    objects(DEMO_OUT)
    budgets(DEMO_OUT)
    sdk_mode(DEMO_OUT)
    chinese_prompt(DEMO_OUT)
    custom_template(DEMO_OUT)
    print("=" * 70)
    print(f"All output kept at: {DEMO_OUT}")
    print("Try: cat demo_out/basic/overview.md")
    print("     cat demo_out/sdk/report_sdk.py")
    print("     cat demo_out/zh/overview.md")
    print("     cat demo_out/tpl_fragments/overview.md")
    print("=" * 70)


if __name__ == "__main__":
    main()
