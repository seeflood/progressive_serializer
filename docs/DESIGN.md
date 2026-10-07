# 渐进式披露序列化库 · 设计文档

## 1. 背景与目标

开发 AI Agent 时，工具返回的 JSON 往往远大于 Agent 实际关心的部分。例如：

```json
{ "article": "……(10 万字)", "summary": "……(100 字)", "err_code": 0 }
```

Agent 只需要 `summary` 与 `err_code`，但 `article` 白白烧掉了上下文窗口。

本库把"一次性把全量 JSON 塞进 prompt"改成**渐进式披露**：

- 先给 Agent 一份 `overview.md` 概要——小数据原样内联，大数据启发式折叠；
- 被折叠的数据完整原文落盘为 `<key>.json`，Agent 真的需要时再用自带的文件读取工具取回。

设计约束：**零运行时依赖**（仅标准库），所有策略可通过钩子替换。

## 2. 公共 API

```python
from progressive_serializer import serialize, SerializeConfig

result = serialize(
    {"key1": {...}, "key2": SomeObject()},   # dict[str, dict | 任意对象]
    "/path/to/out",                          # 预期输出目录
    SerializeConfig(max_value_size=2000),    # 可选
)
```

返回 `SerializeResult`：

| 字段 | 说明 |
| --- | --- |
| `out_dir` | 输出目录（绝对路径） |
| `overview_path` / `overview_text` | `overview.md` 的路径与内容 |
| `files` | `dict[key, Path]`，只包含被折叠因而落盘的 key |
| `items` | `list[ItemView]`，每个 key 的渲染结果与尺寸统计 |

输出目录结构：

```
out/
  overview.md     # 概要 prompt
  key2.json       # 被折叠 key 的完整原文
```

`serialize` **不清空**已有目录（`mkdir(parents=True, exist_ok=True)`），只写
`overview.md` 和本次产生的 `<key>.json`，避免误删用户文件。

## 3. 设计决策

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| 模板渲染 | 标准库 `string.Template`，`Renderer` 协议可插拔 | 零依赖；想要循环/条件可自接 Jinja2 |
| 尺寸度量 | 字符数 `len`，经 `size_fn` 钩子可替换 | 确定、可测；换 tiktoken 即按 token 计费 |
| 对象转换 | 唯一钩子 `default`，语义同 `json.dumps(default=...)` | 复用标准库既有约定，无需发明新协议 |
| 折叠标记 | JSONC——行尾 `//` 注释 | 注释不污染 value 本身，模型不会把占位文案误当数据 |
| 回查 | 库只写文件，不提供 lookup API | Agent 已有 Read 工具，保持库职责单一 |
| 提示语 | 默认英文，`messages=CHINESE` 可切换 | 面向国际用户；文案与代码解耦便于本地化 |

## 4. 模块划分

```
progressive_serializer/
  __init__.py    公共 API 再导出
  config.py      SerializeConfig
  messages.py    折叠注释文案 (ENGLISH / CHINESE)
  fold.py        折叠走查 -> 注解树（Folded 节点）
  jsonc.py       JSONC pretty-printer（行尾 // 注释）
  render.py      Renderer 协议 / 默认模板 / StringTemplateRenderer
  serializer.py  编排：逐 key 决策、落盘、渲染 overview
```

依赖方向单向无环：`messages ← config`、`config ← fold`、`fold ← jsonc`、
`messages ← render`，以上全部 ← `serializer`。

## 5. 折叠：`fold.py`

> 折叠算法的详细介绍（设计取舍、调参、已知限制）见
> [folding-algorithm.md](folding-algorithm.md)。

`fold.py` 的走查**不直接产字符串**，而是产一棵注解树，让「折叠决策」与「文本打印」解耦：

```python
@dataclass(frozen=True)
class Folded:
    preview: Any   # 保留下来的片段："..." / 头 N 项 / {} 
    note: str      # 渲染成行尾注释，文案来自 config.messages
    kind: str      # "string" | "binary" | "list" | "object" | "depth"
```

未折叠的部分保持原生 `dict/list/str/int/float/bool/None`，折叠处替换为 `Folded`。

### 折叠规则（命中即替换）

1. **字符串**：`len(s) > max_string_len` → `preview="..."`，note 记原长。
2. **二进制**：`bytes`/`bytearray`/`memoryview` 无条件折叠（`fold_binary=False`
   时退化为 base64 字符串，再走字符串规则）。
3. **列表**：`len(lst) > max_list_items` → 只保留前 `list_preview_items` 项
   （元素本身继续递归折叠），末尾追加 `"..."`，note 记总项数与展示项数。
4. **对象**：`len(d) > max_object_keys` → 保留前 N 个 key 并追加 `"...": "..."`
   成员，note 记省略的字段数。
5. **深度**：容器所在层级 `>= max_depth` → 整个子树折叠为 `{"...": "..."}` / `["..."]`
   （原容器本就为空时折叠为 `{}` / `[]`，避免暗示有隐藏内容）。
6. **非原生类型**：调用 `default(obj)`，拿到替换值后**继续递归折叠**；
   `default is None` 时按 `json.dumps` 语义抛 `TypeError`（错误信息提示传 `default`）。
   `default` 可能返回新的非原生对象，故限制连续 coerce 次数（4 次）防死循环。

### 省略号为什么是 `"..."` 而不是裸 `...`

注释已经说明了"折叠了什么"，但注释在**行尾**，而被省略的内容在**结构内部**——
只看结构看不出列表还有后续。所以在结构里也放一个省略号标记。

用合法 JSON 字符串 `"..."` 而非裸 `...`，是为了保住「剥掉 `//` 注释后整块可被
`json.loads` 解析」这一性质（有测试固定）。代价是理论上与真实数据 `"..."` 无法区分；
对象折叠时若数据本身已有 `"..."` key，则不覆盖它。

### 触发阈值与保留数量分离

列表规则刻意用了两个参数：`max_list_items`（**触发**折叠的长度，默认 10）与
`list_preview_items`（折叠后**保留**几项，默认 1）。若二者合一取 1，则 `["a","b","c"]`
这样的短列表也会被折叠成 1 项——注释比数据还长，是净损失。

### 预算升级（escalation）

折叠一轮后若 JSONC 文本仍 `> max_value_size`，按固定梯度收紧参数重试
（`max_string_len //= 4`、`list 保留 1 项`、`max_object_keys //= 2`、`max_depth -= 1`），
最多 `escalate_rounds`（默认 3）轮。这保证 overview 不会被单个 key 撑爆，也覆盖了
「字段很多但每个都不大」这类单轮规则打不中的输入。

## 6. JSONC 打印：`jsonc.py`

这是全库唯一不能直接用 `json.dumps` 的地方——注释必须挂在**行尾**，且位于逗号之后：

```jsonc
{
  "article": "...",  // 已折叠，原长 102400 字符
  "summary": "本文讨论了……（完整 100 字）",
  "err_code": 0,
  "attachments": [  // 已折叠，共 320 项，仅展示 1 项
    {
      "name": "a.pdf"
    },
    "..."
  ]
}
```

实现要点：

- 先把树摊平成 `_Line(depth, text, comment)` 列表，最后统一 join。
  逗号在生成期追加到**上一元素的最后一行**，注释挂在**该元素的第一行**——
  标量时两者是同一行（注释自然落在逗号之后），容器时分别落在 `]` 与 `[` 上。
- 标量叶子的转义**复用 `json.dumps`**，不自己实现字符串转义。
- 无 `Folded` 节点时，输出与 `json.dumps(indent=2)` 逐字符相等（有测试固定此性质）。

## 7. 渲染：`render.py`

```python
class Renderer(Protocol):
    def render(self, ctx: OverviewContext) -> str: ...
```

`string.Template` 没有循环，所以默认渲染器由**分片模板**拼装，每片都可在构造时覆盖：

- `header` / `footer` / `separator`
- `item_template`：`- ${key}\n\n${notice}${fence}json\n${json_block}\n${fence}`
- `folded_notice`：默认取 `config.messages.folded_notice`（英文；`CHINESE` 为
  `` 部分字段由于过长已被自动折叠, 完整内容见 `${full_path}` ``）。
  未折叠时 `notice` 为空串；显式传入 `renderer` 时由该 renderer 自己负责文案。

两个容易踩的点：

- **`$` 安全**：`Template.substitute` 只扫描模板、不重扫插入值，所以 JSON 内容里的
  `$` 不会被二次解析。自定义模板中的字面 `$` 仍需写 `$$`。
- **围栏长度**：JSON 字符串不转义反引号，若内容含 ``` ``` ``` 会撑破 markdown 代码块。
  `${fence}` 按内容里最长的反引号串动态加长。

想要循环/条件/过滤器，自带 Jinja2 实现 `render(ctx) -> str` 传给 `SerializeConfig(renderer=...)` 即可。

## 8. 编排：`serializer.py`

逐 key 处理：

1. `json.dumps(value, indent=…, default=_dump_default)` 得到 `full_text`，
   用 `size_fn` 量尺寸。**全量文本只 dump 一次**，既用于量尺寸也用于落盘。
2. `size <= max_value_size` → 直接内联，不产文件。
3. 超预算 → 折叠（含 escalation）→ JSONC 文本；**确实发生了折叠**才把
   `full_text` 落盘并在 overview 中标注完整文件路径。
4. 渲染 `overview.md`。

`_dump_default` 先处理 `bytes`（→ base64 字符串）再委托用户 `default`，保证"完整文件"
一定写得出来。注意：完整文件中的二进制是 base64，不是原始字节。

### 文件名安全

key 来自调用方，可能含 `/`、空格、`..`，可能超长或为空。`_safe_stem` 把非
`[A-Za-z0-9._-]` 字符替换为 `_`，剥掉首尾 `._-`（于是 `".."` → 空 → 回退 `item`），
截断到 80 字符，再用计数后缀解决碰撞（`a_2.json`）。碰撞检测**不区分大小写**，
因为 macOS/Windows 文件系统大小写不敏感。

overview 中引用的必须是**真实落盘的文件名**，而不是原始 key。

## 9. 测试与验证

```bash
python3 -m unittest discover -s tests -t .     # 环境无 pytest，用标准库跑
python3 demo.py                               # 跑需求里的原始例子
```

覆盖点：阈值边界、五条折叠规则、JSONC 与 `json.dumps` 的等价性、注释位置、
escalation 收敛、文件名 sanitize 与碰撞、完整文件可读回、四个钩子
（`size_fn` / `renderer` / `default` / `messages`）、重复运行的幂等性。
