"""Walk through `serialize_value`: one value in, a bare JSONC string out.

    python3 demo_serialize_value.py

`serialize` owns the whole output -- a dict of keys in, an `overview.md` out.
`serialize_value` owns nothing but the value: it folds a single structure,
spills the full copy to the file you name, and hands back the JSONC block with
no markdown, no fence and no key heading around it. The caller assembles the
prompt.

Output lands in ./demo_out/single_value/ and is left on disk.
"""

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from progressive_serializer import (
    CHINESE,
    Messages,
    SerializeConfig,
    serialize,
    serialize_value,
)

DEMO_OUT = Path(__file__).parent / "demo_out" / "single_value"


def head(n, title):
    print("=" * 72)
    print(f"{n}. {title}")
    print("=" * 72)


def sub(title):
    print(f"\n--- {title} ---")


def report_payload():
    """Deep, with a list of objects and one oversized string."""
    return {
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


# ---------------------------------------------------------------------------


def basic(out_dir):
    """The core shape: a string, and only a string."""
    head(1, "Basic: a single value -> a bare JSONC string")

    out_file = out_dir / "basic" / "report.json"
    block = serialize_value(
        report_payload(), out_file, SerializeConfig(max_value_size=300)
    )
    print(block)

    sub("The return value really is just that string")
    print(f"type        : {type(block).__name__}")
    print(f"first char  : {block[:1]!r}")
    print(f"last char   : {block[-1:]!r}")
    print(f"has a ``` fence : {'```' in block}")
    print(f"has a '- key' heading : {block.lstrip().startswith('-')}")
    print(f"an overview.md was written : {(out_file.parent / 'overview.md').exists()}")

    sub("The full payload is on disk, untouched")
    restored = json.loads(out_file.read_text(encoding="utf-8"))
    print(f"{out_file}  ({out_file.stat().st_size:,} bytes)")
    print(f"executive_summary recovered: {len(restored['executive_summary']):,} chars")
    print(f"round-trips exactly: {restored == report_payload()}")


def splicing(out_dir):
    """The reason the API exists: paste the block into a bigger document."""
    head(2, "Splicing: the block drops into a larger JSON string as-is")

    tools = {
        "get_report": report_payload(),
        "search": {"hits": 1284, "took_ms": 37},
    }

    # Build a JSONC document by hand, one folded value per tool result.
    parts = []
    for name, payload in tools.items():
        block = serialize_value(
            payload,
            out_dir / "splice" / f"{name}.json",
            SerializeConfig(max_value_size=300),
        )
        # Indent the block to sit under its key, skipping line 1 (the key line
        # supplies that indent already).
        lines = block.splitlines()
        body = "\n".join([lines[0]] + ["  " + ln for ln in lines[1:]])
        parts.append(f'  "{name}": {body}')

    document = "{\n" + ",\n".join(parts) + "\n}"
    print(document)

    sub("Strip the comments and it is valid JSON again")
    parsed = json.loads(_strip_comments(document))
    print(f"parsed keys      : {list(parsed)}")
    print(f"folded placeholder: {parsed['get_report']['title']!r}")
    print(f"small value kept : {parsed['search']}")
    print("\nNote: as returned, the text is JSONC, not JSON -- json.loads()")
    print("rejects the comments. Fine for an LLM reader; a machine consumer")
    print("downstream needs a JSONC parser or a strip pass like the one above.")


def under_budget(out_dir):
    """Small values are passthrough: no fold, no file, no comment."""
    head(3, "Under budget: returned verbatim, nothing written")

    out_file = out_dir / "small" / "search.json"
    small = {"hits": 1284, "took_ms": 37, "query": "progressive disclosure"}
    block = serialize_value(small, out_file)

    print(block)
    sub("Compared with plain json.dumps")
    plain = json.dumps(small, indent=2, ensure_ascii=False)
    print(f"identical to json.dumps(indent=2): {block == plain}")
    print(f"contains any comment             : {'//' in block or '/*' in block}")
    print(f"{out_file} written : {out_file.exists()}")
    print("\nSo you can call it unconditionally -- it only costs a file when")
    print("something actually had to be folded away.")


def sdk(out_dir):
    """generate_sdk=True: naming comes from the out-file stem."""
    head(4, "SDK mode: the out-file stem names the SDK and its root class")

    out_file = out_dir / "sdk" / "quarterly_report.json"
    block = serialize_value(
        report_payload(),
        out_file,
        SerializeConfig(max_value_size=300, generate_sdk=True),
    )
    # The notice wraps, so it is the whole /* … */ span, not just line 1.
    notice = block[: block.index("*/") + 2]
    print(notice)
    print("  ... (fields omitted here, same as section 1)")

    sdk_file = out_file.with_name(out_file.stem + "_sdk.py")
    root_class = _root_class(notice)
    sub("Files produced")
    print(f"[full JSON] {out_file}  ({out_file.stat().st_size:,} bytes)")
    print(f"[typed SDK] {sdk_file}")
    print()
    print("`serialize` derives these names from the dict key; there is no key")
    print("here, so the file stem plays that role instead:")
    print(f"  quarterly_report.json  ->  quarterly_report_sdk.py  ->  class "
          f"{root_class}")

    sub("An agent uses the SDK exactly as in serialize()'s SDK mode")
    root = _import_root(sdk_file, root_class)
    deep = "finance.quarter.metrics.revenue.by_region.emea"
    print(f"get({deep!r}) -> {root.get(deep)}")
    print(f"get('teams.1.lead.email')  -> {root.get('teams.1.lead.email')}")
    print(f"keys('teams.0')            -> {root.keys('teams.0')}")
    print("\nThe 49k-char executive_summary was never read.")


def class_objects(out_dir):
    """A class instance, not a dict, via the `default` hook."""
    head(5, "Class objects: dataclasses and friends via SerializeConfig(default=)")

    @dataclass
    class Author:
        name: str
        email: str

    @dataclass
    class Article:
        title: str
        body: str
        author: Author
        tags: list = field(default_factory=list)

    article = Article(
        title="Progressive disclosure for agent tool results",
        body="Context windows are a scarce resource. " * 1200,
        author=Author("Ann Lee", "ann@example.com"),
        tags=["context", "serialization", "agents"],
    )

    # vars() per object rather than asdict(): asdict() recurses eagerly and
    # would materialize the whole tree before folding ever sees it.
    block = serialize_value(
        article,
        out_dir / "objects" / "article.json",
        SerializeConfig(max_value_size=300, default=vars),
    )
    print(block)
    print("\nThe `default` hook is the same contract as json.dumps(default=...),")
    print("so pydantic (`lambda o: o.model_dump()`), attrs, ORM rows and plain")
    print("classes all work the same way.")


def localization(out_dir):
    """messages= rewrites both the first-line notice and the fold notes."""
    head(6, "Localization: messages=CHINESE, and fully custom wording")

    payload = {
        "标题": "渐进式披露：为 Agent 压缩工具返回值",
        "正文": "大模型的上下文窗口是稀缺资源，不应被用不到的字段占满。" * 600,
        "作者": {"姓名": "李雷", "单位": "平台组"},
        "错误码": 0,
    }

    sub("messages=CHINESE")
    print(
        serialize_value(
            payload,
            out_dir / "zh" / "搜索结果.json",
            SerializeConfig(max_value_size=400, messages=CHINESE),
        )
    )

    sub("messages=CHINESE + generate_sdk=True")
    block = serialize_value(
        payload,
        out_dir / "zh_sdk" / "搜索结果.json",
        SerializeConfig(max_value_size=400, messages=CHINESE, generate_sdk=True),
    )
    print(block[: block.index("*/") + 2])
    print("  ...")
    print("\nCJK has no spaces to wrap on, so lines break at the Latin runs and")
    print("may overrun the width (a CJK glyph is 2 columns but counts as 1).")
    print("Cosmetic only -- `*/` still bounds the comment unambiguously.")

    sub("Your own wording, via Messages(...)")
    print(
        serialize_value(
            {"正文": "甲" * 5000, "错误码": 0},
            out_dir / "zh_custom" / "文档.json",
            SerializeConfig(
                max_value_size=200,
                messages=Messages(
                    string_folded="此处省略 {length} 字",
                    folded_notice="【完整数据】${full_path}\n\n",
                ),
            ),
        )
    )
    print("\nThe notice template is written for markdown (hard breaks, trailing")
    print("blank line); its whitespace is normalized and then re-wrapped to the")
    print("comment's own width.")


def comment_style(out_dir):
    """Every comment is /* … */ here, and why."""
    head(7, "Comment style: /* … */ throughout, and comment_width")

    payload = report_payload()
    out_file = out_dir / "width" / "report.json"

    print("`//` runs to end of line and has no terminator. A comment long")
    print("enough to wrap -- or one the reader's terminal soft-wraps -- is")
    print("then indistinguishable from the next line of JSON. Compare:")

    sub("comment_style='line': what the notice would have to look like")
    print(
        serialize_value(
            payload,
            out_file,
            SerializeConfig(
                max_value_size=300, generate_sdk=True, comment_style="line"
            ),
        ).splitlines()[0]
    )
    print("\n^ where does the notice end? Nothing in the text says.")

    sub("comment_style='block' (the default here): `*/` says where each ends")
    print(
        serialize_value(
            payload, out_file, SerializeConfig(max_value_size=300, generate_sdk=True)
        )
    )
    print("\nNote that the *fold notes* are block comments too, not just the")
    print("notice -- one convention throughout, so a reader never has to track")
    print("which comments are terminated and which are not.")

    sub("comment_width=56: narrower, continuation still aligned under the /*")
    print(
        serialize_value(
            payload,
            out_file,
            SerializeConfig(max_value_size=300, comment_width=56),
        )
    )

    sub("A path is never split, whatever the width")
    narrow = serialize_value(
        payload,
        out_dir / "width" / "a_rather_long_report_filename.json",
        SerializeConfig(max_value_size=300, comment_width=30),
    )
    print(narrow[: narrow.index("*/") + 2])
    print("\nThe path overruns the width rather than breaking -- the whole point")
    print("of printing it is that the reader can copy it in one piece.")

    sub("comment_width=0: no wrapping at all")
    print(
        serialize_value(
            payload, out_file, SerializeConfig(max_value_size=300, comment_width=0)
        ).splitlines()[0]
    )

    sub("`serialize` keeps // by default; both APIs accept either style")
    for style in ("line", "block"):
        result = serialize(
            {"report": payload},
            out_dir / f"style_{style}",
            SerializeConfig(max_value_size=300, comment_style=style),
        )
        note = next(
            ln.strip() for ln in result.overview_text.splitlines() if '"title"' in ln
        )
        print(f"  serialize(comment_style={style!r:7}) -> {note}")
    print()
    print("`serialize` defaults to 'line' because its JSON sits inside a fenced")
    print("markdown block, where nothing after a comment could be mistaken for")
    print("its continuation. Only the bare-string mode has that problem.")


def versus_serialize(out_dir):
    """Same payload through both APIs, side by side."""
    head(8, "serialize vs serialize_value, on the same payload")

    payload = report_payload()
    config = SerializeConfig(max_value_size=300)

    result = serialize({"report": payload}, out_dir / "cmp_serialize", config)
    block = serialize_value(payload, out_dir / "cmp_value" / "report.json", config)

    sub("serialize() -> overview.md")
    print(result.overview_text)

    sub("serialize_value() -> the string, and only the string")
    print(block)

    sub("Same folding, different packaging")
    rows = [
        ("input", "dict[key, value] + out dir", "one value + out file"),
        ("returns", "SerializeResult", "str"),
        ("writes overview.md", "yes", "no"),
        ("markdown fence", "yes", "no"),
        ("notice location", "prose above the block", "/* … */ on line 1"),
        ("comment style", "// (default)", "/* … */ (default)"),
        ("config.renderer", "used", "ignored"),
        ("config.messages", "used", "used"),
    ]
    width = max(len(r[0]) for r in rows)
    print(f"  {'':<{width}}  {'serialize':<26}  serialize_value")
    for label, a, b in rows:
        print(f"  {label:<{width}}  {a:<26}  {b}")

    sub("Same folding underneath: only packaging and comment style differ")
    # Ask serialize_value for the style serialize uses, and the JSONC inside
    # the fence is then identical character for character.
    as_line = serialize_value(
        payload,
        out_dir / "cmp_value" / "report.json",
        SerializeConfig(max_value_size=300, comment_style="line"),
    )
    print(f"same block: {result.items[0].json_block == _strip_root_comment(as_line)}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_comments(text):
    """Remove both comment forms, leaving parseable JSON."""
    no_block = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return "\n".join(re.sub(r"\s*//.*$", "", ln) for ln in no_block.splitlines())


def _strip_root_comment(block):
    """Drop the notice from line 1, leaving exactly what serialize() renders.

    Handles both styles: a block notice may span lines, a line notice runs to
    the end of line 1 only.
    """
    lines = block.splitlines()
    if lines and "  /*" in lines[0]:
        without = re.sub(r"  /\*.*?\*/", "", block, count=1, flags=re.DOTALL)
        return without
    lines[0] = lines[0].split("  //")[0]
    return "\n".join(lines)


def _root_class(notice_line):
    """Pull the root class name out of the notice, as an agent would.

    Not by scanning the SDK for `class `: nested objects get classes too, and
    they are emitted *before* the root (a parent is appended after the children
    it references), so the first match is the wrong one.
    """
    match = re.search(r"class `([^`]+)`", notice_line)
    if not match:
        raise AssertionError(f"no class named in notice: {notice_line!r}")
    return match.group(1)


def _import_root(sdk_file, class_name):
    """Load the generated module the way an agent would."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_demo_value_sdk", sdk_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, class_name)


def main():
    # Start clean so stale files from a previous run can't confuse the output.
    if DEMO_OUT.exists():
        shutil.rmtree(DEMO_OUT)
    basic(DEMO_OUT)
    print()
    splicing(DEMO_OUT)
    print()
    under_budget(DEMO_OUT)
    print()
    sdk(DEMO_OUT)
    print()
    class_objects(DEMO_OUT)
    print()
    localization(DEMO_OUT)
    print()
    comment_style(DEMO_OUT)
    print()
    versus_serialize(DEMO_OUT)
    print()
    print("=" * 72)
    print(f"All output kept at: {DEMO_OUT}")
    print("Try: cat demo_out/single_value/sdk/quarterly_report_sdk.py")
    print("     cat demo_out/single_value/basic/report.json | head -c 300")
    print("     ls demo_out/single_value/   # no small/: nothing was folded there,")
    print("                                 # so not even the directory was made")
    print("=" * 72)


if __name__ == "__main__":
    main()
