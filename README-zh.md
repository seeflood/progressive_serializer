# progressive_serializer

把体积过大的 JSON 渐进式披露给 LLM Agent：先给一份紧凑的 `overview.md`，
被折叠的完整内容另存为 `<key>.json`，Agent 真的需要时再读。

**零运行时依赖**，只用标准库。

[English](README.md)

> 本文的示例输出都基于 `messages=CHINESE`。库的**默认提示语是英文**，
> 中文输出需要显式传入：
>
> ```python
> from progressive_serializer import CHINESE, SerializeConfig
> serialize(data, out_dir, SerializeConfig(messages=CHINESE))
> ```

## 问题

工具返回的 JSON 往往远大于 Agent 实际关心的部分：

```json
{ "article": "……(10 万字)", "summary": "……(100 字)", "err_code": 0 }
```

Agent 只需要 `summary` 和 `err_code`，但 `article` 白白烧掉了上下文窗口。

## 用法

```python
from progressive_serializer import CHINESE, SerializeConfig, serialize

result = serialize(
    {
        "key1": {"foo": "bar"},
        "key2": {"article": "甲" * 100_000, "summary": "乙" * 100, "err_code": 0},
    },
    "/tmp/out",
    SerializeConfig(messages=CHINESE),   # 默认是英文提示语
)
print(result.overview_text)
```

`/tmp/out/overview.md`：

````markdown
- key1

```json
{
  "foo": "bar"
}
```

- key2

部分字段由于过长已被自动折叠, 完整内容见 `/tmp/out/key2.json`

```json
{
  "article": "...",  // 已折叠，原长 100000 字符
  "summary": "乙乙乙……（完整 100 字）",
  "err_code": 0
}
```
````

`article` 的 10 万字完整原文在 `/tmp/out/key2.json` 里，Agent 用自带的文件读取工具取回即可。

返回值：

| 字段 | 说明 |
| --- | --- |
| `out_dir` | 输出目录 |
| `overview_path` / `overview_text` | `overview.md` 的路径与内容 |
| `files` | `dict[key, Path]`，只含被折叠因而落盘的 key |
| `items` | 每个 key 的渲染结果与尺寸统计 |
| `sdk_files` | `dict[key, Path]`，SDK `.py` 文件；仅 `generate_sdk=True` 时填充 |

`serialize` **不清空**输出目录，只写 `overview.md` 和本次产生的 `<key>.json`。

## `serialize_value`：单个数据结构，返回纯 JSONC 字符串

`serialize` 接管整个输出：传入一个 dict，产出一份 `overview.md`。
如果 prompt 是你自己拼的，只想把**这一个值**折叠掉，就用 `serialize_value`——
它接收单个数据结构（JSON 或 class 对象）和落盘文件路径，只返回 JSONC 文本本身：
没有 markdown、没有代码围栏、没有 key 标题，因此可以直接拼进你自己更大的
JSON / JSONC 字符串里。

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

`serialize` 写在代码块上方的那句提示语，在这里变成第一行的注释，
保证返回值自包含。提示语同样受 `messages=CHINESE` 控制。

### 注释风格

这个模式下**所有**注释都是 `/* … */` 块注释，而不是 `//`。原因在于 `//` 没有
结束符、一直吃到行尾：一条长到需要换行的注释（或被读者终端软换行的注释），
与"JSON 的下一行"在视觉上无法区分。`*/` 明确标出每条注释到哪里结束。
全局统一成一种风格，也省得读者同时记两套约定。

| | `comment_style="line"` | `comment_style="block"` |
| --- | --- | --- |
| 形式 | `// note` | `/* note */` |
| 谁的默认值 | `serialize` | `serialize_value` |
| 能否换行 | 不能 | 能，按 `comment_width` |

`serialize` 保持 `//`：它的 JSON 放在 markdown 代码围栏里，注释后面不会有任何
东西被误读成它的续行。两个 API 都接受任意一种风格：

```python
serialize_value(data, path, SerializeConfig(comment_style="line"))   # 强制用 //
serialize(data, out_dir, SerializeConfig(comment_style="block"))     # 强制用 /* … */
```

块注释按 `SerializeConfig(comment_width=88)` 换行，传 `0` 则不换行。
不含空格的长串（比如文件路径）不会被拆开——打印路径的意义就在于能被整条复制。
`"line"` 风格下 `comment_width` 被忽略，因为 `//` 不能换行。

| | `serialize` | `serialize_value` |
| --- | --- | --- |
| 入参 | `dict[key, value]` + 输出**目录** | 单个值 + 输出**文件** |
| 返回 | `SerializeResult`，写 `overview.md` | `str`（JSONC），不写 `overview.md` |
| 提示语 | 代码块上方的正文 | 第一行的 `/* … */` 注释 |
| 注释风格 | `//` | `/* … */` |
| `config.renderer` | 生效 | 忽略——没有模板可渲染 |

命名规则沿用输出文件名的 stem，对应 `serialize` 里的 key：
`report.json` 生成 `report_sdk.py`，根类名为 `Report`。父目录不存在时自动创建。

如果值本身没超过 `max_value_size`，则不折叠、**不落盘**，直接返回
`json.dumps` 的原始输出，不带任何注释。

跑 `python3 demo_serialize_value.py` 看完整演示：拼接、SDK 模式、class 对象、
中文提示语，以及与 `serialize` 的逐项对比。

## SDK 模式：为落盘文件生成类型化访问器

折叠落盘解决了"overview 太大"，但没解决另一个问题：**Agent 手里只有 `cat`**。
它想看某个字段时只能把 5 万字全读回来，折叠省下的上下文当场还回去。

根因是落盘文件对 Agent 是个**黑盒**——它不知道里面有什么，所以只能全读。
`generate_sdk=True` 在 JSON 旁边再生成一个 `<key>_sdk.py`，给它一张**地图**（schema）
和一把**镊子**（`get("a.b.c")`）。

```python
result = serialize({"report": {...}}, "/tmp/out", SerializeConfig(generate_sdk=True))
```

输出目录：

```
/tmp/out/
  overview.md
  report.json        # 完整原文，5 万多字
  report_sdk.py      # 类型化访问器
```

`overview.md` 的提示语会同时指向两个文件，并点明根类名：

```
部分字段由于过长已被自动折叠, 完整内容见 `/tmp/out/report.json`。由于文件内容
可能较大，请尽量避免直接读取完整文件内容(除非你确实需要看 json 的所有字段)。
如果你只关心 json 中的某些字段，你可以通过写 python 脚本、调用
`/tmp/out/report_sdk.py` (类 `Report`) 来按需读取字段。
```

### schema docstring

`report_sdk.py` 开头是完整 schema——**覆盖每一层**，包括对象列表的元素类型。
Agent 只读文件头部就能掌握整个结构，**完全不用碰 JSON**：

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

schema 的体积只与**字段数量**相关，与数据量无关——50 MB 的文件也是这个开销。

### 按路径取值

`get()` 接受点分路径，只返回那一个值；整数段表示列表下标：

```python
import importlib.util

spec = importlib.util.spec_from_file_location("sdk", "/tmp/out/report_sdk.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
Report = mod.Report

Report.get("finance.quarter.metrics.revenue.by_region.emea")   # 1720000
Report.get("teams.1.lead.email")                               # 'bo@example.com'
Report.get("nope.at.all", default=None)                        # 不抛异常，返回 None
```

`keys()` 用于**先探索再取值**——对象返回 key 列表，列表返回长度，标量返回类型名：

```python
Report.keys("finance.quarter.metrics")   # ['revenue', 'gross_margin_pct']
Report.keys("teams")                     # 2
Report.keys("teams.0")                   # ['name', 'lead', 'headcount']
```

路径不存在时抛 `KeyError` 并指明**断在哪一段**
（`no such path: 'metrics.nope' (in 'metrics.nope.deeper')`），除非传了 `default`。

### 类型化 dataclass

**每一层**对象都会生成 dataclass；对象列表还会生成单数化的元素类
（`teams` → `Team`），整棵树都有类型：

```python
obj = Report.load()
obj.finance.quarter.metrics.revenue.total_usd   # 4820000
[t.lead.name for t in obj.teams]                # ['Ann Lee', 'Bo Chen']
```

所有字段都是 `Optional[...] = None`——JSON 里 key 可能缺失，且 dataclass 不允许
无默认值字段排在有默认值字段之后。

### 返回值字段

```python
result.sdk_files["report"]   # Path("/tmp/out/report_sdk.py")
result.items[0].sdk_class    # "Report"
result.items[0].sdk_path     # "/tmp/out/report_sdk.py"
```

只有**真的折叠并落盘**的 key 才生成 SDK；没超 `max_value_size` 的值直接内联在
overview 里，不产 SDK。`generate_sdk=False`（默认）时不写任何 `.py`，
overview 输出与该功能引入前**逐字符一致**。

> 设计动机、schema 推断与已知限制见 [docs/sdk-mode.md](docs/sdk-mode.md)。

## 折叠规则

一个 value 的完整 JSON 超过 `max_value_size` 才会被折叠并落盘。折叠时逐层走查：

| 规则 | 参数（默认值） |
| --- | --- |
| 长字符串 → `"..."` | `max_string_len=200` |
| 二进制（`bytes`/`bytearray`/`memoryview`）→ 占位 | `fold_binary=True` |
| 长列表 → 只留前 N 项 | `max_list_items=10`, `list_preview_items=1` |
| 宽对象 → 只留前 N 个字段 | `max_object_keys=None`（**默认关闭**，传整数才启用） |
| 过深嵌套 → `{}` / `[]` | `max_depth=10` |

折叠一轮后若仍超预算（比如"字段很多但每个都不大"），会按梯度收紧阈值重试，
最多 `escalate_rounds=3` 轮。升级只会收紧**已启用**的规则——关闭状态的
`max_object_keys` 不会被偷偷打开。

丢字段是唯一**默认关闭**的规则：其他规则触发时都会在原地留下可见的占位符，
但被省略的字段是**直接消失**的——Agent 根本看不出它存在，而字段名正是
Agent 的导航信息。如果你宁愿丢字段名也不愿超预算，再显式打开：

```python
SerializeConfig(max_object_keys=20)
```

> 折叠算法的完整说明（设计取舍、调参指引、已知限制）见
> [docs/folding-algorithm.md](docs/folding-algorithm.md)。

折叠标记是**行尾 `//` 注释**（JSONC），不污染 value 本身——模型不会把占位文案误当数据；
同时在结构里留一个 `"..."` 省略号，直观表示"这里还有其他数据"：

```jsonc
{
  "attachments": [  // 已折叠，共 320 项，仅展示 1 项
    { "name": "a.pdf" },
    "..."
  ],
  "metrics": {  // 已折叠，共 40 个字段，省略 35 个
    "qps": 1200,
    "...": "..."
  }
}
```

省略号用的是合法 JSON 字符串 `"..."`（而非裸 `...`），所以把 `//` 注释剥掉之后
整块仍然可以被 `json.loads` 解析。如果数据本身就有 `"..."` 这个 key，不会被覆盖。

没有任何折叠发生时，输出与 `json.dumps(indent=2, ensure_ascii=False)` 逐字符相同。

## 配置与钩子

```python
from progressive_serializer import SerializeConfig, serialize

serialize(data, out_dir, SerializeConfig(
    max_value_size=2000,
    max_string_len=200,
))
```

### 按 token 计费而非字符数

```python
import tiktoken
enc = tiktoken.get_encoding("cl100k_base")

SerializeConfig(size_fn=lambda s: len(enc.encode(s)))
```

### class 对象 / dataclass / pydantic

`default` 的语义与 `json.dumps(default=...)` 完全一致：

```python
from dataclasses import asdict, is_dataclass

SerializeConfig(default=lambda o: asdict(o) if is_dataclass(o) else vars(o))

# pydantic
SerializeConfig(default=lambda o: o.model_dump())
```

`default` 返回值会继续被走查，所以嵌套的自定义对象也能处理。

### 提示语语言

折叠注释和 overview 提示语**默认是英文**。切换成中文，或自定义：

```python
from progressive_serializer import CHINESE, Messages, SerializeConfig

SerializeConfig(messages=CHINESE)

SerializeConfig(messages=Messages(
    string_folded="省略了 {length} 字符",
    folded_notice="完整内容: `${full_path}`\n\n",
))
```

折叠注释用 `str.format` 占位符；`folded_notice` 用 `string.Template` 语法
（因为它与其他模板片段一起渲染）。每条折叠注释还会收到一个 `{s}` 占位符
（`""` 或 `"s"`）用于英文单复数，中文模板可以忽略它。

### 自定义模板

默认渲染器由可覆盖的模板片段拼装：

```python
from progressive_serializer import StringTemplateRenderer, SerializeConfig

renderer = StringTemplateRenderer(
    header="# 工具返回\n\n",
    item_template="## ${key} (${original_size} chars)\n\n${notice}${fence}json\n${json_block}\n${fence}",
    folded_notice="⚠️ 已折叠，完整内容: `${full_path}`\n\n",
)
SerializeConfig(renderer=renderer)
```

可用占位符：`key`、`notice`、`json_block`、`fence`、`full_path`、`out_dir`、
`original_size`、`rendered_size`。自定义模板里的字面 `$` 需写成 `$$`；
但 JSON 内容里的 `$` 是安全的（`Template.substitute` 不重扫插入值）。

想要循环/条件，实现 `render(ctx) -> str` 接任意模板引擎：

```python
class JinjaRenderer:
    def __init__(self, env, name):
        self.template = env.get_template(name)

    def render(self, ctx):
        return self.template.render(out_dir=ctx.out_dir, items=ctx.items)

SerializeConfig(renderer=JinjaRenderer(env, "overview.md.j2"))
```

注意：传入自己的 `renderer` 后，提示语文案由它自己负责；`messages` 仍然控制折叠注释。

## 注意

- 完整文件里的二进制是 **base64 字符串**，不是原始字节。
- key 会被 sanitize 成安全文件名，碰撞时加计数后缀。**Unicode 会保留**
  （`搜索结果` → `搜索结果.json`），分隔符、控制字符与 shell 元字符替换为 `_`
  （`a/b` → `a_b.json`）。长度上限是 180 **字节**（POSIX 限制的是字节数）。
  目标平台为 macOS 与 Linux，不特殊处理 Windows 保留名。
  overview 里引用的始终是真实落盘的文件名。
- 输入含循环引用时抛 `ValueError`（来自 `json.dumps` 的环检测）。
- 预算升级不保证收敛：**没有规则会缩短字段名**，所以 key 本身极长的值可能
  折不进预算。这是刻意取舍——字段名是 Agent 的导航信息。

## 开发

```bash
python3 -m unittest discover -s tests -t .   # 测试（无需第三方包）
python3 demo.py                              # 跑一遍各类场景，输出留在 ./demo_out/
python3 demo_serialize_value.py              # serialize_value() 专项演示
```

文档：

- [docs/DESIGN.md](docs/DESIGN.md) —— 整体架构与设计决策
- [docs/folding-algorithm.md](docs/folding-algorithm.md) —— 折叠算法详解
- [docs/sdk-mode.md](docs/sdk-mode.md) —— SDK 模式设计：动机、schema 推断、实现约束
- [README.md](README.md) —— English
