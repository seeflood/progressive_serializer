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

`serialize` **不清空**输出目录，只写 `overview.md` 和本次产生的 `<key>.json`。

## 折叠规则

一个 value 的完整 JSON 超过 `max_value_size` 才会被折叠并落盘。折叠时逐层走查：

| 规则 | 参数（默认值） |
| --- | --- |
| 长字符串 → `"..."` | `max_string_len=200` |
| 二进制（`bytes`/`bytearray`/`memoryview`）→ 占位 | `fold_binary=True` |
| 长列表 → 只留前 N 项 | `max_list_items=10`, `list_preview_items=1` |
| 宽对象 → 只留前 N 个字段 | `max_object_keys=20` |
| 过深嵌套 → `{}` / `[]` | `max_depth=6` |

折叠一轮后若仍超预算（比如"字段很多但每个都不大"），会按梯度收紧阈值重试，
最多 `escalate_rounds=3` 轮。

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
- key 会被 sanitize 成安全文件名（`a/b` → `a_b.json`），碰撞时加计数后缀。
  overview 里引用的始终是真实落盘的文件名。
- 输入含循环引用时抛 `ValueError`（来自 `json.dumps` 的环检测）。
- 预算升级不保证收敛：**没有规则会缩短字段名**，所以 key 本身极长的值可能
  折不进预算。这是刻意取舍——字段名是 Agent 的导航信息。

## 开发

```bash
python3 -m unittest discover -s tests -t .   # 测试（无需第三方包）
python3 demo.py                              # 跑一遍各类场景，输出留在 ./demo_out/
```

文档：

- [docs/DESIGN.md](docs/DESIGN.md) —— 整体架构与设计决策
- [docs/folding-algorithm.md](docs/folding-algorithm.md) —— 折叠算法详解
- [README.md](README.md) —— English
