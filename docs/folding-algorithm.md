# 折叠算法 (Folding Algorithm)

> 本文介绍 `progressive_serializer` 如何把一个过大的 JSON 值压缩成"概要 + 完整文件"
> 两层结构。面向需要理解、调参或扩展折叠行为的人。
>
> 本文示例中的折叠注释用中文展示（`messages=CHINESE`）；**库的默认提示语是英文**。
>
> 相关文档：[README](../README.md)（English）、[README-zh](../README-zh.md)（中文用法）、
> [DESIGN](DESIGN.md)（整体架构）

---

## 1. 问题

Agent 工具返回的 JSON 常常远大于 Agent 实际关心的部分：

```json
{ "article": "……(10 万字)", "summary": "……(100 字)", "err_code": 0 }
```

Agent 只需要 `summary` 和 `err_code`。但如果整个 JSON 直接进 prompt，10 万字的
`article` 就白白烧掉了上下文窗口。

**朴素方案的问题：**

| 方案 | 问题 |
| --- | --- |
| 整体截断（`text[:2000]`） | 可能切在 `article` 中间，后面的 `summary`、`err_code` 彻底丢失 |
| 只保留白名单字段 | 需要事先知道每个工具的 schema，不通用 |
| 让模型自己挑字段 | 需要先把全量数据发给模型，鸡生蛋问题 |

折叠算法的思路是：**保留 JSON 的完整骨架，只把过大的叶子替换成占位符**。
Agent 能看到所有字段名、所有小字段的真实值，以及"哪些字段被省略了、原本多大"。

---

## 2. 核心思想：骨架 vs 血肉

JSON 的信息密度极不均匀：

```
{                          ← 骨架：极便宜，必须保留
  "article": "10万字...",   ← 血肉：极贵，可以折叠
  "err_code": 0            ← 骨架+血肉都便宜，保留
}
```

**字段名和结构是导航信息**——Agent 靠它判断"我要的东西在不在这里"。
**大体积的叶子值才是成本来源**。

所以算法只做一件事：递归走查整棵树，把**体积大的叶子**换成占位符，骨架一律保留。

---

## 3. 两级决策

折叠不是无条件发生的。每个 top-level key 独立走两级判断：

```
          ┌──────────────────────────┐
          │ json.dumps(value) 全量文本 │
          └────────────┬─────────────┘
                       │ size_fn 量尺寸
                       ▼
           size <= max_value_size ?
                 ┌─────┴─────┐
              是 │           │ 否
                 ▼           ▼
         ┌───────────┐  ┌──────────────────┐
         │ 原样内联   │  │ 折叠走查 (第 4 节) │
         │ 不产文件   │  │  + 完整原文落盘    │
         └───────────┘  └──────────────────┘
```

**第一级（预算判断）** 决定"这个值值不值得折叠"。小值原样内联——
对一个 `{"foo": "bar"}` 做折叠毫无意义，注释比数据还长。

**第二级（折叠走查）** 才是本文主题。

> 注意：只有**真的发生了折叠**才会落盘。如果一个值超了预算但没有任何可折叠的东西
> （例如 500 个小整数组成的列表、而 `max_list_items` 被调得很大），
> 就不会产生 `<key>.json` 文件——因为概要已经是全量内容，再存一份是浪费。

---

## 4. 折叠走查

走查是一个深度优先递归，对每个节点按类型分派。源码见
[`fold.py`](../progressive_serializer/fold.py) 的 `_walk`。

```
_walk(value, depth)
  │
  ├─ _coerce(value)          ← 非 JSON 原生类型先转换（第 5 节）
  │
  ├─ bytes/bytearray/…  →  _fold_binary   规则 ②
  ├─ str                →  _fold_string   规则 ①
  ├─ dict               →  _fold_dict     规则 ④ / ⑤
  ├─ list/tuple         →  _fold_list     规则 ③ / ⑤
  └─ int/float/bool/None → 原样返回（标量永远不折叠）
```

### 五条规则

| # | 规则 | 触发条件 | 结果 |
| --- | --- | --- | --- |
| ① | 长字符串 | `len(s) > max_string_len` | `"..."` + 注释记原长 |
| ② | 二进制 | 是 `bytes`/`bytearray`/`memoryview` | `"..."` + 注释记字节数 |
| ③ | 长列表 | `len(lst) > max_list_items` | 保留前 `list_preview_items` 项 + `"..."` |
| ④ | 宽对象 | `max_object_keys` 非 `None` 且 `len(d) >` 它 | 保留前 N 个字段 + `"...": "..."` |
| ⑤ | 深嵌套 | `depth >= max_depth` | 整个子树换成 `{"...": "..."}` / `["..."]` |

默认阈值（见 `SerializeConfig`）：

```python
max_string_len     = 200   # 字符
max_list_items     = 10    # 超过 10 项才折
list_preview_items = 1     # 折叠后保留 1 项
max_object_keys    = None  # 规则 ④ 默认关闭，传整数才启用
max_depth          = 10
```

### 为什么规则 ④ 默认关闭

其余四条规则触发时，都会在**原地留下可见的痕迹**——`"..."` 占位符加行尾注释，
Agent 看得到"这里有东西被折叠了"，于是知道可以去读完整文件。

规则 ④ 不一样：被省略的字段是**整个消失**的。虽然末尾有个 `"...": "..."` 说明
"还有别的字段"，但 Agent **不知道有哪些**。而字段名恰恰是它最需要的导航信息——
知道 `error_code` 这个字段存在，比知道它的值是 `0` 更有用（后者可以去读完整文件，
前者一旦丢了就无从下手）。字段名通常也很短，省掉它们换来的预算有限。

这与 §「已知限制」里"没有规则会缩短字段名"是同一个取舍的延伸：
**宁可超预算，也不让 Agent 失去导航能力**。

需要时显式打开：

```python
SerializeConfig(max_object_keys=20)
```

典型场景是字段数极多且字段名本身没有语义的数据（如 `{"metric_0": …, …,
"metric_999": …}`），此时丢字段名的代价才真的小于超预算。

### 为什么列表有两个参数

规则 ③ 刻意把**触发阈值**（`max_list_items=10`）和**保留数量**
（`list_preview_items=1`）拆开。

如果合并成一个"保留 1 项"的参数，那么 `["a", "b", "c"]` 也会被折叠成：

```jsonc
[             // 已折叠，共 3 项，仅展示 1 项
  "a",
  "..."
]
```

注释比省下来的内容还长——**净亏损**。所以短列表完整保留，只有真正长的列表才折。

### 规则的组合是递归的

被保留下来的部分会**继续走查**。例如一个 320 项的列表，保留的那 1 项如果本身
是个含长字符串的对象，里面的长字符串照样会被折叠：

```jsonc
"attachments": [  // 已折叠，共 320 项，仅展示 1 项
  {
    "name": "report.pdf",
    "content": "...",  // 已折叠，原长 50000 字符
  },
  "..."
]
```

### depth 的计数方式

`depth` 从根节点的 **0** 开始，进入子容器时 +1。规则 ⑤ 的条件是
`depth >= max_depth`，即"当前容器所处层级已达上限"：

```python
fold({"a": {"b": {"c": 1}}}, SerializeConfig(max_depth=1))
# {"a": { ... }}            ← "a" 的值在 depth=1，被折叠

fold({"a": {"b": {"c": 1}}}, SerializeConfig(max_depth=2))
# {"a": {"b": { ... }}}     ← "b" 的值在 depth=2，被折叠

fold({"a": {"b": {"c": 1}}}, SerializeConfig(max_depth=3))
# {"a": {"b": {"c": 1}}}    ← 完整保留
```

`max_depth=0` 会把根容器本身折掉，只剩 `{"...": "..."}`。

### 为什么默认 10

深度折叠是最"钝"的规则——它整块砍掉一棵子树，Agent 连里面有哪些字段都看不到
（对比规则 ①③ 只替换叶子、骨架完整保留）。所以阈值要留足余量，让它**只在真正
病态的结构上兜底**，而不是在正常数据上频繁触发。

真实 API 响应的有意义层级通常在 3～6 层，10 层给了足够空间。真正深到十几层的
结构，往往本身就不适合整块塞给 Agent，此时折叠恰好是想要的行为。

超预算时升级会自动收紧它（见 §6），所以放宽默认值不会让 overview 失控——
只是把"什么时候开始砍"的决定交给预算，而不是先入为主的层数。

---

## 5. 非 JSON 原生对象

走查遇到 `dict`/`list`/`str`/数字/`None`/二进制 之外的类型时，调用
`SerializeConfig.default` 钩子。**语义与 `json.dumps(default=...)` 完全一致**——
返回一个可 JSON 序列化的替代值：

```python
from dataclasses import asdict, is_dataclass

SerializeConfig(default=lambda o: asdict(o) if is_dataclass(o) else vars(o))

# pydantic
SerializeConfig(default=lambda o: o.model_dump())
```

两个细节：

1. **转换结果会继续被走查**。所以一个 dataclass 里嵌套的自定义对象、长字符串
   都能被正确处理。
2. **防死循环**：`default` 可能返回又一个非原生对象。连续转换超过
   `_MAX_COERCE_CHAIN = 4` 次仍未得到原生类型，就抛 `TypeError`。

没传 `default` 又遇到未知类型时，抛 `TypeError` 并在错误信息里提示该传什么——
和标准库行为对齐，不静默降级成 `repr()`。

---

## 6. 预算升级 (Escalation)

### 单轮规则打不中的情况

考虑 300 个小字段组成的对象：

```python
{"metric_0": 0.0, "metric_1": 1.5, ..., "metric_299": 448.5}
```

每个字段都很小（规则 ① 不触发），不是列表（③ 不触发），不深（⑤ 不触发）。
而规则 ④ **默认关闭**，所以**一轮折叠下来毫无变化**——全量 JSON 有 6718 字符，
远超预算。

> 这类输入就是规则 ④ 的适用场景：字段名 `metric_0…metric_299` 本身没有语义，
> 丢掉它们的代价小于超预算。下面的数据都基于显式传入
> `max_object_keys=20`；不传则升级也救不回来（见本节末）。

### 解法：收紧阈值重试

```
for round in 0 .. escalate_rounds:
    tree = fold(value, current_config)
    text = jsonc.dumps(tree)
    if size_fn(text) <= max_value_size:
        break                        ← 进预算了，收工
    current_config = current_config.tightened()
```

`tightened()` 的梯度（`config.py`），以 `max_object_keys=20` 为例：

| 轮次 | `max_string_len` | `max_list_items` | `max_object_keys` | `max_depth` |
| --- | --- | --- | --- | --- |
| 0（原始） | 200 | 10 | 20 | 10 |
| 1 | 50 | 1 | 10 | 5 |
| 2 | 16 | 1 | 5 | 2 |
| 3 | 16 | 1 | 2 | 1 |

字符串阈值 `// 4`，对象阈值与深度 `// 2`，列表直接压到 1。
各参数都有下限（字符串 16、对象 1、深度 1），不会归零。

> 深度用**折半**而不是 `-1`，是为了让收敛所需轮次与 `max_depth` 的起点无关。
> 若按 1 递减，默认值 10 需要 9 轮才能压到 1，而默认 `escalate_rounds` 只有 3——
> 深层嵌套的数据根本来不及收敛。

**升级不会启用被关闭的规则**：`max_object_keys=None` 在每一轮之后依然是
`None`。升级的职责是收紧调用方已经选择的规则，而不是替调用方做新的取舍。

实测上面的 300 字段案例（预算 300）：

```
max_object_keys=20,   escalate_rounds=0:   463 字符   ← 超预算
max_object_keys=20,   escalate_rounds=1:   253 字符   ← 一轮就够了
max_object_keys=None, escalate_rounds=3:  6718 字符   ← 规则关闭，升级无从下手
```

默认 `escalate_rounds=3`。设为 `0` 可以完全关掉升级。

### 已知限制：不收敛的情况

升级不保证一定进预算。**没有任何规则会缩短字段名**，所以这种输入折不下去：

```python
serialize({"k": {"x" * 500: 1}}, out, SerializeConfig(max_value_size=50))
# 渲染结果 511 字符，folded=False
```

这是刻意的取舍——字段名是 Agent 的导航信息，截断它比超预算更有害。
实践中 key 长度失控的情况极少；真的遇到，应该在上游清理数据。

---

## 7. 输出表示：JSONC + 省略号

折叠后的树由 [`jsonc.py`](../progressive_serializer/jsonc.py) 打印成 **JSONC**
（JSON with Comments），折叠信息走**行尾 `//` 注释**：

```jsonc
{
  "article": "...",  // 已折叠，原长 102400 字符
  "summary": "本文讨论了……（完整 100 字）",
  "err_code": 0,
  "attachments": [  // 已折叠，共 320 项，仅展示 1 项
    { "name": "a.pdf" },
    "..."
  ]
}
```

### 为什么用注释而不是占位字符串

另一种方案是把折叠信息写进 value：

```json
{ "article": "<folded: 102400 chars>" }
```

但这样**占位文案混在数据里**，模型理论上可能把 `"<folded: ...>"` 当成
`article` 的真实内容。注释在语法上明确是元信息，不是数据。

### 为什么结构里还要放 `"..."`

注释在**行尾**，而被省略的内容在**结构内部**。只看结构，看不出列表后面还有东西：

```jsonc
"attachments": [  // 注释在这里
  { "name": "a.pdf" }
  ← 这里看不出还有 319 项
]
```

所以在结构里也放一个省略号标记。用**合法 JSON 字符串 `"..."`** 而非裸 `...`，
是为了保住一个有用的性质：**剥掉 `//` 注释后整块仍能被 `json.loads` 解析**
（有测试固定）。

代价是理论上与真实数据 `"..."` 无法区分。作为补偿，对象折叠时如果数据本身
已有 `"..."` 这个 key，**不覆盖它**。

### 两个边界

- **空容器因深度被折叠**时不加省略号——`{}` 而非 `{"...": "..."}`，
  否则会暗示有隐藏内容而实际没有。
- **无折叠发生**时，输出与 `json.dumps(indent=2, ensure_ascii=False)`
  **逐字符相同**。这条有专门的测试套件固定，是重构时的安全网。

---

## 8. 调参指引

| 想要…… | 调整 |
| --- | --- |
| 整体更激进（省更多 token） | 降 `max_value_size` |
| 字符串保留更多预览 | 升 `max_string_len`（但本库折叠后不保留前缀，见下） |
| 列表展示更多样本 | 升 `list_preview_items`（如 3，便于 Agent 看出元素结构） |
| 按 token 而非字符计费 | `size_fn=lambda s: len(enc.encode(s))` |
| 完全禁用升级 | `escalate_rounds=0` |
| 二进制转 base64 而非折叠 | `fold_binary=False`（之后仍受字符串规则约束） |
| 折叠注释换语言/措辞 | `messages=CHINESE` 或自定义 `Messages(...)` |

### 关于字符串不保留前缀

当前实现把长字符串整个换成 `"..."`，不保留开头片段。设计时考虑过保留前 N 字符
让 Agent 能判断"这字段是不是我要的"，最终没做——因为**字段名通常已经提供了
足够的判断依据**（`article` vs `summary`），而前缀会稳定地吃掉每个被折叠字段
几十个 token。

如果你的场景里字段名不够自解释，这是一个合理的扩展点：在 `_fold_string`
里改成 `preview=value[:n] + "..."` 即可。

---

## 9. 代码地图

| 文件 | 职责 |
| --- | --- |
| [`fold.py`](../progressive_serializer/fold.py) | 本文主题：走查 + 五条规则 + `Folded` 注解树 |
| [`config.py`](../progressive_serializer/config.py) | 阈值定义 + `tightened()` 升级梯度 |
| [`messages.py`](../progressive_serializer/messages.py) | 折叠注释的文案（`ENGLISH` / `CHINESE`） |
| [`jsonc.py`](../progressive_serializer/jsonc.py) | 把注解树打印成带 `//` 注释的 JSONC |
| [`serializer.py`](../progressive_serializer/serializer.py) | 两级决策编排 + 落盘 |
| [`tests/test_fold.py`](../tests/test_fold.py) | 五条规则逐条的边界测试 |

### 关键数据结构

```python
@dataclass(frozen=True)
class Folded:
    preview: Any   # 保留下来的部分："..." / 头 N 项 / {"...": "..."}
    note: str      # 渲染成行尾 // 注释
    kind: str      # "string" | "binary" | "list" | "object" | "depth"
```

走查**不直接产字符串**而是产注解树，这样"折叠决策"和"文本打印"解耦——
`fold.py` 不需要知道注释该放在哪一行，`jsonc.py` 不需要知道为什么要折叠。

---

## 10. 动手验证

```bash
python3 demo.py                              # 四类场景，输出留在 ./demo_out/
python3 -m unittest discover -s tests -t .   # 全部测试
python3 -m unittest tests.test_fold -v       # 只跑折叠规则的测试
```

快速试验某个输入会被折成什么样：

```python
from progressive_serializer import fold, jsonc_dumps, SerializeConfig

tree, stats = fold(your_data, SerializeConfig(max_string_len=20, max_list_items=2))
print(jsonc_dumps(tree))
print(f"folded={stats.folded}, notes={stats.notes}")
```
