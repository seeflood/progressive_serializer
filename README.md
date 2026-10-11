# progressive_serializer

Progressive disclosure of oversized JSON for LLM agents: hand the agent a compact
`overview.md`, and spill the folded-away content to `<key>.json` files it can read
only when it actually needs them.

**Zero runtime dependencies** — standard library only.

[中文文档](README-zh.md)

## The problem

Tool results are routinely far larger than the part an agent cares about:

```json
{ "article": "…(100k chars)", "summary": "…(100 chars)", "err_code": 0 }
```

The agent only needs `summary` and `err_code`, but `article` burns the context
window for nothing. Naive fixes don't work either — truncating the whole blob can
cut mid-`article` and lose `summary` entirely, and field allowlists require knowing
every tool's schema up front.

## Usage

```python
from progressive_serializer import serialize

result = serialize(
    {
        "key1": {"foo": "bar"},
        "key2": {"article": "x" * 100_000, "summary": "…", "err_code": 0},
    },
    "/tmp/out",
)
print(result.overview_text)
```

`/tmp/out/overview.md`:

````markdown
- key1

```json
{
  "foo": "bar"
}
```

- key2

Oversized fields were folded automatically; full content at `/tmp/out/key2.json`

```json
{
  "article": "...",  // folded, 100000 chars originally
  "summary": "…(the complete summary)",
  "err_code": 0
}
```
````

The full 100k-char `article` sits in `/tmp/out/key2.json`; the agent reads it with
whatever file-reading tool it already has.

Return value:

| Field | Meaning |
| --- | --- |
| `out_dir` | Output directory |
| `overview_path` / `overview_text` | Path to and content of `overview.md` |
| `files` | `dict[key, Path]` — only keys that were folded and spilled |
| `items` | Per-key render result and size stats |
| `sdk_files` | `dict[key, Path]` — SDK `.py` files, only when `generate_sdk=True` |

`serialize` does **not** wipe the output directory; it only writes `overview.md`
and the `<key>.json` files it produces.

## `serialize_value`: one value, a bare JSONC string

`serialize` owns the whole output: a dict of keys in, an `overview.md` out.
When you are assembling the prompt yourself and just want *this one value*
folded, use `serialize_value` — it takes a single structure (JSON-ish or a
class instance) plus the file to spill to, and returns nothing but the JSONC
block. No markdown, no code fence, no key heading, so the result can be spliced
straight into a larger JSON/JSONC document of your own.

```python
from progressive_serializer import SerializeConfig, serialize_value

block = serialize_value(report, "/tmp/out/report.json",
                        SerializeConfig(generate_sdk=True))
print(block)
```

```jsonc
{  /* Oversized fields were folded automatically; full content at
      `/tmp/out/report.json`. The file may be large, so avoid reading it in full
      unless you really do need every field in the JSON. If you only care about
      certain fields, write a Python script that calls `/tmp/out/report_sdk.py`
      (class `Report`) to read just those fields. */
  "title": "...",  /* folded, 21 chars originally */
  "executive_summary": "...",  /* folded, 49600 chars originally */
  "finance": {  /* folded, nested too deep (1 field) */
    "...": "..."
  },
  "teams": [  /* folded, nested too deep (2 items) */
    "..."
  ]
}
```

The notice that `serialize` puts in prose above the block lands here as a
comment on the first line, so the string stays self-contained.

### Comment style

Every comment in this mode is a `/* … */` **block** comment, not `//`. The
reason is that `//` has no terminator: it runs to end of line, so a comment long
enough to wrap — or one the reader's terminal soft-wraps — is indistinguishable
from the next line of JSON. `*/` states where each note ends. Using one
convention throughout also means a reader never has to track two.

| | `comment_style="line"` | `comment_style="block"` |
| --- | --- | --- |
| Form | `// note` | `/* note */` |
| Default for | `serialize` | `serialize_value` |
| Can wrap | no | yes, at `comment_width` |

`serialize` keeps `//`, because its JSON goes inside a fenced markdown block
where nothing following a comment could be mistaken for its continuation.
Either API accepts either style:

```python
serialize_value(data, path, SerializeConfig(comment_style="line"))   # // anyway
serialize(data, out_dir, SerializeConfig(comment_style="block"))     # /* … */ anyway
```

Block comments wrap at `SerializeConfig(comment_width=88)`; pass `0` to keep
each on one line. Long unbroken runs — a filesystem path — are never split,
since the point of printing a path is that it can be copied. In `"line"` style
`comment_width` is ignored, as `//` cannot wrap.

| | `serialize` | `serialize_value` |
| --- | --- | --- |
| Input | `dict[key, value]` + out **dir** | one value + out **file** |
| Output | `SerializeResult`, writes `overview.md` | `str` (JSONC), no `overview.md` |
| Notice | prose line above the block | `/* … */` on the first line |
| Comments | `//` | `/* … */` |
| `config.renderer` | used | ignored — there is no template |

Naming follows the out-file stem the way `serialize` follows the key:
`report.json` gives `report_sdk.py` with root class `Report`. The parent
directory is created if missing.

If the value already fits `max_value_size`, nothing is folded, **no file is
written**, and you get plain `json.dumps` output with no comments at all.

Run `python3 demo_serialize_value.py` for a worked walkthrough: splicing,
SDK mode, class objects, localization, and a side-by-side against `serialize`.

## SDK mode: typed accessor for folded files

When `generate_sdk=True`, each JSON file that spills to disk gets a companion
`<key>_sdk.py` alongside it. It solves the problem folding alone does not: once
the payload is in a file, an agent's only tool is `cat`, so it reads all 50k
chars back and the saving is undone. The SDK gives it a map (the schema) and
tweezers (`get("a.b.c")`) instead.

```python
result = serialize({"report": {...}}, "/tmp/out", SerializeConfig(generate_sdk=True))
```

Output directory:

```
/tmp/out/
  overview.md
  report.json        # full payload, 50k+ chars
  report_sdk.py      # typed accessor
```

The `overview.md` notice points at both files and names the root class:

```
Oversized fields were folded automatically; full content at `/tmp/out/report.json`.
The file may be large, so avoid reading it in full unless you really do need
every field in the JSON. If you only care about certain fields, write a Python
script that calls `/tmp/out/report_sdk.py` (class `Report`) to read just those
fields.
```

### The schema docstring

`report_sdk.py` opens with the complete schema — **every level**, including the
element type of lists of objects. An agent that reads only the head of this file
knows the whole structure without touching the JSON:

```python
"""
Read-only SDK for report.json

Schema (Report):
    title: str
    executive_summary: str
    finance: dict
        quarter: dict
            metrics: dict
                revenue: dict
                    total_usd: int
                    by_region: dict
                        amer: int
                        emea: int
                gross_margin_pct: float
    teams: list[dict]
        [each item]
            name: str
            lead: dict
                name: str
                email: str
            headcount: int

Usage:
    Report.get('a.b.c')        # one field, dotted path
    Report.get('items.0.id')   # list index as a path segment
    Report.keys('a.b')         # what is under a node
    Report.load()              # whole payload, typed
"""
```

The schema's size tracks field count, not payload size — so it stays cheap even
for a 50 MB file.

### Access by path

`get()` takes a dotted path and returns just that value. Integer segments index
into lists:

```python
import importlib.util

spec = importlib.util.spec_from_file_location("sdk", "/tmp/out/report_sdk.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
Report = mod.Report

Report.get("finance.quarter.metrics.revenue.by_region.emea")   # 1720000
Report.get("teams.1.lead.email")                               # 'bo@example.com'
Report.get("nope.at.all", default=None)                        # None instead of raising
```

`keys()` explores a node without pulling its values — object → key names,
list → length, scalar → type name:

```python
Report.keys("finance.quarter.metrics")   # ['revenue', 'gross_margin_pct']
Report.keys("teams")                     # 2
Report.keys("teams.0")                   # ['name', 'lead', 'headcount']
```

A missing path raises `KeyError` naming the segment that failed
(`no such path: 'metrics.nope' (in 'metrics.nope.deeper')`), unless you pass
`default`.

### Typed dataclasses

Objects become dataclasses at **every depth**, and a list of objects gets a
singularized element class (`teams` → `Team`), so the whole tree is typed:

```python
obj = Report.load()
obj.finance.quarter.metrics.revenue.total_usd   # 4820000
[t.lead.name for t in obj.teams]                # ['Ann Lee', 'Bo Chen']
```

Every field is `Optional[...] = None`, since JSON keys may be absent and
dataclass forbids a non-default field after a defaulted one.

### Result fields

```python
result.sdk_files["report"]   # Path("/tmp/out/report_sdk.py")
result.items[0].sdk_class    # "Report"
result.items[0].sdk_path     # "/tmp/out/report_sdk.py"
```

SDK files are only written for keys that actually fold and spill to disk. Values
within `max_value_size` are inlined in the overview and get no SDK. With
`generate_sdk=False` (the default) no `.py` files are written, and the overview
is byte-identical to before this feature existed.

> Design rationale, schema inference, and known limits:
> [docs/sdk-mode.md](docs/sdk-mode.md).

## Folding rules

A value is only folded (and spilled to a file) if its full JSON exceeds
`max_value_size`. When folding, the walk descends the whole tree:

| Rule | Parameters (defaults) |
| --- | --- |
| Long string → `"..."` | `max_string_len=200` |
| Binary (`bytes`/`bytearray`/`memoryview`) → placeholder | `fold_binary=True` |
| Long list → keep leading N items | `max_list_items=10`, `list_preview_items=1` |
| Wide object → keep leading N fields | `max_object_keys=None` (**off by default**, set an int to enable) |
| Deep nesting → `{"...": "..."}` / `["..."]` | `max_depth=10` |

If one pass still leaves the value over budget — e.g. "many fields, none of them
individually large" — thresholds are tightened and retried, up to
`escalate_rounds=3` times. Escalation only tightens rules that are enabled; a
disabled `max_object_keys` stays disabled.

Dropping fields is the one rule that is **off by default**: every other rule
leaves a visible placeholder where it fired, but an omitted field is simply
gone — the agent cannot tell it exists, and field names are its navigation
information. Enable it when you would rather lose field names than exceed the
budget:

```python
SerializeConfig(max_object_keys=20)
```

> The folding algorithm is documented in full (design trade-offs, tuning guidance,
> known limitations) in [docs/folding-algorithm.md](docs/folding-algorithm.md).

Fold markers are **trailing `//` comments** (JSONC) so the placeholder prose never
contaminates the data itself — a model won't mistake it for real content. An
`"..."` sentinel is also left in the structure, so the elision is visible where it
happened:

```jsonc
{
  "attachments": [  // folded, 320 items total, showing 1
    { "name": "a.pdf" },
    "..."
  ],
  "metrics": {  // folded, 40 fields total, 35 omitted
    "qps": 1200,
    "...": "..."
  }
}
```

The sentinel is a valid JSON string (`"..."`, not a bare `...`), so stripping the
`//` comments leaves a block `json.loads` still accepts. If your data genuinely has
a `"..."` key, it is not overwritten.

When nothing is folded, output is byte-identical to
`json.dumps(indent=2, ensure_ascii=False)`.

## Configuration and hooks

```python
from progressive_serializer import SerializeConfig, serialize

serialize(data, out_dir, SerializeConfig(
    max_value_size=2000,
    max_string_len=200,
))
```

### Budget by tokens instead of characters

```python
import tiktoken
enc = tiktoken.get_encoding("cl100k_base")

SerializeConfig(size_fn=lambda s: len(enc.encode(s)))
```

### Class objects / dataclasses / pydantic

`default` has exactly the same contract as `json.dumps(default=...)`:

```python
from dataclasses import asdict, is_dataclass

SerializeConfig(default=lambda o: asdict(o) if is_dataclass(o) else vars(o))

# pydantic
SerializeConfig(default=lambda o: o.model_dump())
```

Whatever `default` returns is walked again, so nested custom objects and long
strings inside them are handled too.

### Message language

Fold notes and the overview notice default to English. Swap in Chinese, or supply
your own strings:

```python
from progressive_serializer import CHINESE, Messages, SerializeConfig

SerializeConfig(messages=CHINESE)

SerializeConfig(messages=Messages(
    string_folded="elided {length} chars",
    folded_notice="Full payload: `${full_path}`\n\n",
))
```

Fold notes use `str.format` placeholders; `folded_notice` uses `string.Template`
syntax because it is rendered alongside the other template fragments. Every fold
note also gets an `{s}` placeholder (`""` or `"s"`) for English plural agreement.

### Custom templates

The default renderer is assembled from overridable fragments:

```python
from progressive_serializer import StringTemplateRenderer, SerializeConfig

renderer = StringTemplateRenderer(
    header="# Tool results\n\n",
    item_template="## ${key} (${original_size} chars)\n\n${notice}${fence}json\n${json_block}\n${fence}",
    folded_notice="⚠️ Folded. Full content: `${full_path}`\n\n",
)
SerializeConfig(renderer=renderer)
```

Available placeholders: `key`, `notice`, `json_block`, `fence`, `full_path`,
`out_dir`, `original_size`, `rendered_size`. A literal `$` in your own template
must be written `$$` — but `$` inside the JSON content is safe, since
`Template.substitute` does not rescan inserted values.

For loops and conditionals, implement `render(ctx) -> str` against any template
engine:

```python
class JinjaRenderer:
    def __init__(self, env, name):
        self.template = env.get_template(name)

    def render(self, ctx):
        return self.template.render(out_dir=ctx.out_dir, items=ctx.items)

SerializeConfig(renderer=JinjaRenderer(env, "overview.md.j2"))
```

Note that passing your own `renderer` means it owns the notice text; `messages`
still governs the fold notes.

## Gotchas

- Binary data in the spilled file is a **base64 string**, not raw bytes.
- Keys are sanitized into safe filenames, with a counter suffix on collision.
  Unicode is preserved (`搜索结果` → `搜索结果.json`), while separators,
  control characters and shell metacharacters become `_` (`a/b` → `a_b.json`).
  The length cap is 180 **bytes**, since that is what POSIX limits. Targets
  macOS and Linux; Windows-reserved names are not special-cased. The overview
  always cites the real on-disk filename.
- Input containing a circular reference raises `ValueError` (from `json.dumps`'
  own cycle detection).
- Escalation is not guaranteed to converge: no rule shortens *field names*, so a
  value whose keys are themselves huge can stay over budget. This is deliberate —
  field names are the agent's navigation information.

## Development

```bash
python3 -m unittest discover -s tests -t .   # tests, no third-party packages needed
python3 demo.py                              # walk through the scenarios; output kept in ./demo_out/
python3 demo_serialize_value.py              # serialize_value() in depth
```

Docs:

- [docs/DESIGN.md](docs/DESIGN.md) — architecture and design decisions
- [docs/folding-algorithm.md](docs/folding-algorithm.md) — the folding algorithm in depth
- [docs/sdk-mode.md](docs/sdk-mode.md) — SDK mode design: why, schema inference, constraints
- [README-zh.md](README-zh.md) — 中文文档
