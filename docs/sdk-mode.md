# SDK 模式 · 设计文档

> 对应配置项：`SerializeConfig(generate_sdk=True)`
> 实现：`progressive_serializer/sdk_gen.py`

## 1. 动机：折叠只解决了一半问题

基础的渐进式披露是这样工作的：

```
overview.md       ← 塞进 prompt，小而紧凑
key2.json         ← 落盘，Agent 需要时再读
```

这个方案假设了一件事：**Agent 会"按需"读那个文件**。但实际上 Agent 手里只有一把
锤子——`Read` / `cat`。当它想知道 `metrics.revenue_usd` 时，它的动作往往是：

```bash
cat /tmp/out/report.json     # 5 万字，全部进上下文
```

于是折叠省下来的上下文，在 Agent 读回的那一刻又全部还了回去，甚至更糟——
overview 里已经有一份折叠摘要，现在又叠了一份全量原文。

**根因**：落盘文件是一个**不透明的黑盒**。Agent 不知道里面有什么字段，
所以唯一安全的策略就是全读。它缺的不是"更小的文件"，而是**一张地图 + 一把镊子**：

- 地图：这个 JSON 有哪些字段、什么类型、嵌在哪一层——让它能判断该取哪个；
- 镊子：一种只取一个（深层）字段的手段——让它不必全读。

SDK 模式就是把这张地图和这把镊子，以 Python 源码的形式一起放到落盘文件旁边。

## 2. 方案：JSON 旁边再放一个 `.py`

```
out/
  overview.md         # 概要（提示语现在同时指向 json 和 sdk）
  report.json         # 完整原文
  report_sdk.py       # 类型化访问器 ← 新增
```

SDK 文件有四层内容，分别服务于 Agent 的四种动作：

| 层 | 内容 | Agent 的动作 |
| --- | --- | --- |
| 模块 docstring | 完整 schema（每一层字段 + 推断类型 + 列表元素结构） | 读文件头，决定要哪个字段 |
| `get(path)` | 按点分路径返回**单个**值 | 取深层数据，不全读 |
| `keys(path)` | 某节点下有什么（key 列表 / 长度 / 类型名） | 先探索再取值 |
| dataclass 定义 | 与 JSON 同构的类型声明，覆盖每一层 | 要整棵树时用；IDE / 类型检查器也受益 |

关键设计点：**docstring 放在文件最前面**。Agent 哪怕只 `head` 了这个 `.py`，
也已经拿到了完整 schema——这是整个方案成本最低的一环，因为 schema 的体积与
数据量无关，只与字段数量有关。

### 为什么是"生成源码"而不是"提供一个通用库函数"

另一条路是：库里提供 `ps.get(path, "a.b.c")`，Agent 直接调。但那样 Agent
仍然不知道有哪些 key——它得先探索。生成的源码把 schema **物化**成了
Agent 本来就会读的那种东西（代码），顺带让类型检查器和 IDE 也能受益，
而且不需要 Agent 的运行环境安装本库。

## 3. 两个关键能力

这两点是方案成立的前提——缺任何一个，SDK 对真实 API 响应都不够用。

### 3.1 结构化覆盖每一层，不止外层

早期实现有个 `_MAX_SCHEMA_DEPTH = 4` 的硬上限，超过 4 层就退化成裸 `dict`：

```python
@dataclass
class L4:
    l5: Optional[dict] = None    # ← 结构信息到此为止
```

schema docstring 同样在第 4 层截断。对一个 8 层深的 JSON，里层字段对 Agent
**完全不可见**——它又只能回去 `cat`。

现在**每一层对象都生成 dataclass**，深度上限提到 `_MAX_CLASS_DEPTH = 24`
（纯粹是为病态输入兜底，比如上千层的链表；真实载荷远达不到）。
schema docstring 的展开深度是独立的 `_SCHEMA_DOC_DEPTH = 6`——
再深就只列字段名不再展开，因为 **docstring 本身也占上下文**，
一个 10 层全展开的结构会生成几百行注释，那就又变成了我们要解决的那个问题。

### 3.2 对象列表生成元素类

`list[dict]` 是真实 API 最常见的形状，早期实现对它**一个类都不生成**：

```
Schema (U):
    users: list[dict]            # ← 元素里有什么，完全看不到
```

现在会为列表元素生成一个**单数化**的类（`users` → `User`），
并在 schema 里用 `[each item]` 标出元素结构：

```
    teams: list[dict]
        [each item]
            name: str
            lead: dict
                name: str
                email: str
```

嵌套列表（`list[list[Item]]`）保持形状，`_from_dict` 生成对应层数的推导式。

**元素字段取并集**：JSON 列表常常是参差的（有的元素多几个字段），
只看第 0 个元素会漏字段。`_merge_objects` 抽样前 5 个元素做并集，
且"首个非 null 值胜出"——避免元素 0 的 `null` 掩盖元素 1 里的真实类型。

## 4. 访问 API：为什么是路径而不是单字段

初版只有一个 `load_field(key)`，**只能取 top-level 字段**。这与 §3.1 是同一个
问题的两面：即使 schema 告诉了 Agent `finance.quarter.metrics.revenue` 的存在，
它也没有手段**只取那一个值**——只能 `load()` 整棵树，或 `load_field("finance")`
把整个子树拉回来自己索引。镊子够不着深处，地图就白画了。

`load_field` 已删除（未发版，无需保留兼容）。现在的三个入口：

```python
Report.get("finance.quarter.metrics.revenue.by_region.emea")   # 1720000
Report.get("teams.1.lead.email")                               # 'bo@example.com'
Report.get("nope.at.all", default=None)                        # 不抛异常

Report.keys("finance.quarter.metrics")   # ['revenue', 'gross_margin_pct']
Report.keys("teams")                     # 2（列表返回长度）
Report.keys("teams.0")                   # ['name', 'lead', 'headcount']

Report.load()                            # 整棵树，带类型
```

设计细节：

- **路径段是整数时索引列表**，所以 `teams.1.lead.email` 能穿过列表。
  代价见 §7：真实 key 里的 `.` 无法用路径表达。
- **报错指明断在哪一段**：`no such path: 'metrics.nope' (in 'metrics.nope.deeper')`。
  Agent 能据此判断是自己拼错了路径，还是数据确实没有这个字段。
- **`default` 用哨兵对象 `_MISSING` 而非 `None`**，否则无法区分
  "字段不存在"和"字段存在且值为 `None`"。
- **`keys()` 的返回值按类型分流**（dict → key 列表、list → 长度、
  标量 → 类型名）。目的是"探索但不付出内容的上下文代价"——
  返回 list 的长度而不是元素，Agent 就知道该用哪个下标。
- **只有这三个方法生成在根类上**。嵌套类只拿 `_from_dict`——它们不是文件的
  入口，给它们加 `get()` 只会让 Agent 困惑"该从哪个类开始"。

## 5. Schema 推断

`sdk_gen.py` 走查**已经落盘的那份 JSON 数据**（不是折叠后的树——折叠树丢了
类型信息），逐字段推断。

### 类型映射

| JSON 值 | 推断类型 |
| --- | --- |
| `None` | `Any`（样本为 null，真实类型未知） |
| `bool` / `int` / `float` / `str` | 同名 Python 类型 |
| 非空 `dict` | 嵌套 dataclass |
| 空 `dict` | `dict` |
| `list` 底部是对象 | `list[ElementClass]` |
| `list`（同质标量） | `list[T]` |
| `list`（异质 / 空） | `list[Any]` / `list` |

列表元素类型只**抽样前 5 项**（`_LIST_SAMPLE`）。全扫一遍对 32 万项的列表没有
收益——类型推断是给 Agent 的提示，不是契约；抽样让生成耗时与数据量脱钩。

### 命名

**类名**（`_to_class_name`）：按分隔符（非标识符字符与 `_`）切分 → 各段首字母
大写 → 拼接。`"search_result"` → `SearchResult`。首字符是数字则前缀 `V`；
结果不是合法标识符时回退 `Root`。

**属性名**（`_to_attr`）：分隔符 → `_`，剥掉首尾 `_`。若结果是 **Python 关键字**
或不是合法标识符（空、以数字开头），前缀 `f_`：`"class"` → `f_class`。

**Unicode key 直接保留**。Python 3 的标识符允许 Unicode 字母，所以
`{"标题": …}` 生成的属性就叫 `标题`、类就叫 `搜索结果`，中文 key 的可读性完全不损失：

```python
@dataclass
class 搜索结果:
    标题: Optional[str] = None
    作者: Optional[作者] = None
```

> 这一条起初写错了：早期的 `_UNSAFE` 是 `[^a-zA-Z0-9]+`，把所有非 ASCII 字符
> 当分隔符剥掉，于是**每个纯中文 key 都塌缩成同一个 `field_`**，生成
> `cls(field_=…, field_=…)`——连 `compile` 都过不了的 `SyntaxError`。
> 详见 §6.4。

> 原始 key 由 `# JSON key: 'xxx'` 注释和 `get()` 的路径参数两处保留——
> **取数据永远用原始 key**，不受转义影响。

**同类内属性名去重**（`_Class.add`）：转义是有损的，`"a b"` / `"a-b"` / `"a.b"`
都归一化成 `a_b`。重名字段会生成 `cls(a_b=…, a_b=…)`，同样是 `SyntaxError`，
所以冲突时加数字后缀（`a_b` / `a_b_2` / `a_b_3`）。

**单数化**（`_singular`）：`Users` → `User`，让元素类读起来自然。
纯英文启发式，刻意保守——猜错只是类名难看一点，不影响取数。
`_INVARIANT_NOUNS` 挡掉单复数同形的词（`series` / `data` / `status` /
`children`），`-ss` / `-us` / `-is` 结尾视为已是单数（否则 `status` → `Statu`），
`-sses` / `-xes` / `-ches` / `-shes` 砍 2 个字符（`boxes` → `Box`），
`-ies` → `-y`（`entries` → `Entry`）。

**名字去重必须在递归之前**。早期实现是"先递归子类、再给父类取名"，
于是 `{"meta": {"meta": …}}` 里内层会先抢走 `Meta`。
现在 `_Builder.reserve()` 在下探前就占名，冲突加数字后缀——
嵌套同名、兄弟同名（`{"a":{"cfg":…},"b":{"cfg":…}}`）都有测试固定。

## 6. 三个必须这样写的实现约束

这三处都是实际跑起来才暴露的——早期单测只做字符串匹配、从未真正 `exec`
生成的代码，所以全漏了。现在测试一律**导入并执行**生成的模块。

### 6.1 所有字段都必须有默认值

最初只给嵌套 dict 字段加 `= None`，标量字段不加。结果：

```python
@dataclass
class Report:
    metrics: Optional[Metrics] = None   # 有默认值
    recommendations: list[str]          # 没有 → TypeError
```

> `TypeError: non-default argument 'recommendations' follows default argument`

dataclass 不允许无默认值字段出现在有默认值字段之后，而**字段顺序由 JSON 的
key 顺序决定**，调用方完全不可控。

修法：**所有字段统一 `Optional[T] = None`**。这同时也更正确——JSON 里
key 本就可能缺失，`d.get(key)` 返回 `None` 时字段需要能接住。

### 6.2 每个类都需要 `_from_dict`

最初 `_from_dict` 只生成在根类上。但根类构造嵌套对象时会调
`Metrics._from_dict(...)`：

> `AttributeError: type object 'Metrics' has no attribute '_from_dict'`

修法：`_from_dict` 对**每个**类都生成；只有 `load()` / `get()` / `keys()`
是根类专属——嵌套类不是文件的入口，给它们加 `load()` 只会让 Agent 困惑
"该从哪个类开始"。

### 6.3 生成的代码里没有 `from __future__ import annotations`

本库自己的模块都带这一行，生成的代码**刻意不带**。原因：Agent 加载 SDK 的
典型方式是 `importlib.util.spec_from_file_location` + `exec_module`，此时模块
还没进 `sys.modules`；而 `@dataclass` 在该状态下解析延迟注解会炸：

> `AttributeError: 'NoneType' object has no attribute '__dict__'`
> （`dataclasses._is_type` 里 `sys.modules.get(cls.__module__)` 拿到 `None`）

生成的代码本来也不需要它——`_Builder.build()` 先递归子类、后 append 父类，
所以**每个类都定义在引用它的类之前**，不存在前向引用。
方法签名里的 `"Report"` 用字符串字面量，同样安全。

### 6.4 标识符转义必须保证"合法且不重名"

写中文 demo 时才暴露的一类问题，而且是最严重的一个：生成的代码**无法编译**。

`_UNSAFE` 最初是 `[^a-zA-Z0-9]+`，把一切非 ASCII 当分隔符剥掉。于是纯中文
key 归一化后是空串，统统回退到同一个占位名：

```python
@dataclass
class X:
    field_: Optional[str] = None  # JSON key: '标题'
    field_: Optional[str] = None  # JSON key: '正文'   ← 重名
```

> `SyntaxError: keyword argument repeated: field_`

**只要对象里有两个以上非 ASCII key，SDK 就彻底不可用**——不是名字难看，
是 `compile` 直接失败。而中文 key 在国内的 API 响应里很常见。

两处修法，对应两个不变量：

1. **合法**：`_UNSAFE` 改为 `[\W_]+`（Unicode 模式），Unicode 字母因此被保留；
   判定一律改用 `str.isidentifier()` 而不是手写规则——它才是 Python 的真正标准。
   注意必须显式把 `_` 列为分隔符，否则 `\w` 会匹配它，`search_result`
   就不再切词（我改第一版时就踩了这个，5 个测试同时红）。
2. **不重名**：`_Class.add()` 在同一个类内检查属性名，冲突加数字后缀。
   因为转义天生有损——`"a b"` / `"a-b"` / `"a.b"` 都是 `a_b`——
   光靠 `isidentifier()` 挡不住重名。

教训与 §6.1–6.3 同源：**生成器的测试必须真的执行生成物**。这条 bug 能活下来，
是因为当时只有 `test_unicode_keys` 这个用例，而它刚好只用了**一个**中文 key。

## 7. 已知限制

- **`get()` 仍然读整个文件再取值**。`json.loads` 没有流式/惰性接口，
  要真正"只读需要的字节"得引入 `ijson` 之类的依赖，与零依赖约束冲突。
  当前方案省下的是**上下文**（Agent 只看到一个值），不是磁盘 I/O。
  对本库的目标——上下文窗口——这正是要省的那一样。
- **key 里的字面 `.` 无法用路径表达**。`{"a.b": 1, "a": {"b": 2}}` 中
  `get("a.b")` 解析为 `a` → `b`，返回 `2`。逃生口是 `get()` 取回父节点
  自己索引。选点分路径是因为它是 Agent 最熟悉的写法；为这个边角情况引入
  转义语法不值得。
- **类型是从一份样本推断的**，不是契约。某字段这次是 `int`、下次是 `str`
  时，SDK 只反映生成时看到的那一份；列表只抽样前 5 项。
- **单数化只对英文生效**。非英文 key 原样返回（`标题` 不会被改动），
  这是对的；但英文不规则复数只靠 `_INVARIANT_NOUNS` 词表兜住
  （`children` / `feet` / `geese` 原样返回），词表之外的 `-s` 结尾外来词
  仍可能被误切。不影响取数。
- **落盘文件名也保留 Unicode**，与 SDK 内的命名一致：`搜索结果` 的文件就是
  `搜索结果.json` / `搜索结果_sdk.py`。细节（字节截断、NFC 归一化）见
  [DESIGN.md 的「文件名安全」](DESIGN.md#文件名安全)。
- **深度超过 `_MAX_CLASS_DEPTH`（24）的层级退化为 `dict`**，
  schema docstring 超过 `_SCHEMA_DOC_DEPTH`（6）只列字段名不展开。
  两者都是刻意的上下文预算。

## 8. 测试

```bash
python3 -m unittest discover -s tests -t .
python3 demo.py                              # 场景 5 即 SDK 模式
```

`tests/test_sdk.py` 的分组（共 112 个用例）。**所有涉及行为的用例都
import 并执行生成的模块**，不只做字符串匹配——这是前述三个 bug 漏网的教训：

- `SdkGenUnitTests`——返回值形状、类名派生、源码能 `compile`、
  schema 进 docstring、关键字/数字开头 key 转义。
- `DeepNestingTests`——8 层全部生成类、深层 typed 链路可达、深层路径
  `get`、schema 覆盖旧的 4 层截断点之外、40 层病态输入能收敛。
- `ListElementTests`——元素类生成与单数化、元素内嵌套对象、
  列表水合成 dataclass、`list[list]` 保持形状、参差列表取并集、
  标量列表不生成类、空列表不生成类。
- `PathAccessTests`——各层级 `get`、穿列表下标、子树、空路径、
  缺失路径抛错且指明断点、`default` 兜底、越界下标、非整数下标、
  `keys()` 三种返回。
- `EdgeCaseTests`——空容器、顶层 list/标量、空根对象、深层关键字 key、
  **多个纯中文 key 各自得到不同属性名**（§6.4 回归）、Unicode 类名、
  日文/西里尔/韩文 key、**归一化后重名的 key 去重**、带分隔符的中文 key、
  纯标点 key、嵌套同名类去重、兄弟同名类去重、字面 `.` key 的已知限制。
- `SdkSerializeIntegrationTests`——经 `serialize()`：默认不生成、
  开启后生成、文件位置与命名、overview 引用 SDK 路径、未开启时提示语不变、
  内联值不产 SDK、多 key 各自产 SDK、中文提示语、关闭时目录无 `.py`。
