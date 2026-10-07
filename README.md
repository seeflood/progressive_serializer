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

## SDK mode: typed accessor for folded files

When `generate_sdk=True`, each JSON file that spills to disk gets a companion
`<key>_sdk.py` alongside it. The SDK lets an agent call `load_field("key")` to
fetch exactly one field, so it never has to read the whole file into context.

```python
result = serialize(
    {"report": {"title": "Q3", "body": "…" * 50_000, "metrics": {…}}},
    "/tmp/out",
    SerializeConfig(generate_sdk=True),
)
```

Output directory:

```
/tmp/out/
  overview.md
  report.json        # full payload, 50k+ chars
  report_sdk.py      # typed accessor
```

The `overview.md` notice now points at both files and names the root class:

```
Oversized fields were folded automatically; full content at `/tmp/out/report.json`.
Use `/tmp/out/report_sdk.py` (class `Report`) to load only the fields you need
instead of reading the whole file.
```

`report_sdk.py` opens with a schema docstring so an agent reading just the top
of the file immediately knows the structure — without touching the JSON:

```python
"""
Read-only SDK for report.json

Schema (Report):
    title: str
    body: str
    metrics: dict
        revenue_usd: int
        gross_margin_pct: float

Usage:
    obj = Report.load()
    val = Report.load_field('some_key')
"""
```

Then typed dataclasses mirror the schema, with `_from_dict` on every class so
nested objects hydrate correctly:

```python
@dataclass
class Metrics:
    revenue_usd: Optional[int] = None
    gross_margin_pct: Optional[float] = None

    @classmethod
    def _from_dict(cls, d: dict) -> "Metrics": ...

@dataclass
class Report:
    title: Optional[str] = None
    body: Optional[str] = None
    metrics: Optional[Metrics] = None

    @classmethod
    def load(cls) -> "Report": ...          # full object

    @classmethod
    def load_field(cls, key: str) -> Any: ...  # one field only
```

The agent fetches only what it needs:

```python
import importlib.util, pathlib

spec = importlib.util.spec_from_file_location("sdk", "/tmp/out/report_sdk.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# One field — reads only the bytes needed to extract it.
metrics = mod.Report.load_field("metrics")
# → {"revenue_usd": 4820000, "gross_margin_pct": 61.3}
```

`SerializeResult.sdk_files` maps each key to its SDK path, mirroring `files`:

```python
result.sdk_files["report"]   # Path("/tmp/out/report_sdk.py")
result.items[0].sdk_class    # "Report"
result.items[0].sdk_path     # "/tmp/out/report_sdk.py"
```

SDK files are only written for keys that actually fold and spill to disk.
Values that fit within `max_value_size` are inlined in the overview and get no
SDK. If `generate_sdk=False` (the default), no `.py` files are written.

> The design rationale, schema inference algorithm, and edge-case decisions are
> documented in [docs/sdk-mode.md](docs/sdk-mode.md).


## Folding rules

A value is only folded (and spilled to a file) if its full JSON exceeds
`max_value_size`. When folding, the walk descends the whole tree:

| Rule | Parameters (defaults) |
| --- | --- |
| Long string → `"..."` | `max_string_len=200` |
| Binary (`bytes`/`bytearray`/`memoryview`) → placeholder | `fold_binary=True` |
| Long list → keep leading N items | `max_list_items=10`, `list_preview_items=1` |
| Wide object → keep leading N fields | `max_object_keys=20` |
| Deep nesting → `{"...": "..."}` / `["..."]` | `max_depth=6` |

If one pass still leaves the value over budget — e.g. "many fields, none of them
individually large" — thresholds are tightened and retried, up to
`escalate_rounds=3` times.

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
- Keys are sanitized into safe filenames (`a/b` → `a_b.json`), with a counter
  suffix on collision. The overview always cites the real on-disk filename.
- Input containing a circular reference raises `ValueError` (from `json.dumps`'
  own cycle detection).
- Escalation is not guaranteed to converge: no rule shortens *field names*, so a
  value whose keys are themselves huge can stay over budget. This is deliberate —
  field names are the agent's navigation information.

## Development

```bash
python3 -m unittest discover -s tests -t .   # tests, no third-party packages needed
python3 demo.py                              # walk through the scenarios; output kept in ./demo_out/
```

Docs:

- [docs/DESIGN.md](docs/DESIGN.md) — architecture and design decisions
- [docs/folding-algorithm.md](docs/folding-algorithm.md) — the folding algorithm in depth
- [docs/sdk-mode.md](docs/sdk-mode.md) — SDK mode design: why, schema inference, constraints
- [README-zh.md](README-zh.md) — 中文文档
