# SDK 模式 · 设计文档

> 对应配置项：`SerializeConfig(generate_sdk=True)`
> 实现：`progressive_serializer/sdk_gen.py`

## 1. 动机：折叠解决了一半问题

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

- 地图：这个 JSON 有哪些字段、什么类型——让它能判断该取哪个；
- 镊子：一种只取一个字段的手段——让它不必全读。

SDK 模式就是把这张地图和这把镊子，以 Python 源码的形式一起放到落盘文件旁边。

## 2. 方案：JSON 旁边再放一个 `.py`

```
out/
  overview.md         # 概要（提示语现在同时指向 json 和 sdk）
  report.json         # 完整原文
  report_sdk.py       # 类型化访问器 ← 新增
```

SDK 文件有三层内容，分别服务于 Agent 的三种动作：

| 层 | 内容 | Agent 的动作 |
| --- | --- | --- |
| 模块 docstring | schema 概览（字段名 + 推断类型 + 嵌套结构） | 读文件头，决定要哪个字段 |
| dataclass 定义 | 与 JSON 结构同构的类型声明 | 看类型；IDE/类型检查器也能用 |
| `load_field()` | 只返回一个 top-level 字段 | 取数据，不全读 |

关键设计点：**docstring 放在文件最前面**。Agent 哪怕只 `head` 了这个 `.py`，
也已经拿到了完整 schema——这是整个方案成本最低的一环，因为 schema 的体积与
数据量无关，只与字段数量有关。

### 为什么是"生成源码"而不是"提供一个通用库函数"

另一条路是：库里提供 `ps.load_field(path, key)`，Agent 直接调。但那样 Agent
仍然不知道有哪些 key——它得先探索。生成的源码把 schema **物化**成了
Agent 本来就会读的那种东西（代码），顺带让类型检查器和 IDE 也能受益，
而且不需要 Agent 的运行环境安装本库。

## 3. 生成的代码长什么样

输入：

```python
{"title": "Q3", "body": "甲" * 50_000,
 "metrics": {"revenue_usd": 4820000, "gross_margin_pct": 61.3},
 "recommendations": ["a", "b"]}
```

产出 `report_sdk.py`：

```python
"""
Read-only SDK for report.json

Schema (Report):
    title: str
    body: str
    metrics: dict
        revenue_usd: int
        gross_margin_pct: float
    recommendations: list[str]

Usage:
    obj = Report.load()
    val = Report.load_field('some_key')
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

_JSON_PATH = Path(__file__).with_name("report.json")


@dataclass
class Metrics:
    revenue_usd: Optional[int] = None          # JSON key: 'revenue_usd'
    gross_margin_pct: Optional[float] = None   # JSON key: 'gross_margin_pct'

    @classmethod
    def _from_dict(cls, d: dict) -> "Metrics":
        return cls(
            revenue_usd=d.get('revenue_usd'),
            gross_margin_pct=d.get('gross_margin_pct'),
        )


@dataclass
class Report:
    title: Optional[str] = None
    body: Optional[str] = None
    metrics: Optional[Metrics] = None
    recommendations: Optional[list[str]] = None

    @classmethod
    def _from_dict(cls, d: dict) -> "Report":
        return cls(
            title=d.get('title'),
            body=d.get('body'),
            metrics=Metrics._from_dict(d['metrics'])
                    if isinstance(d.get('metrics'), dict) else None,
            recommendations=d.get('recommendations'),
        )

    @classmethod
    def load(cls) -> "Report":
        """Load and return the full object from disk."""
        return cls._from_dict(json.loads(_JSON_PATH.read_text(encoding='utf-8')))

    @classmethod
    def load_field(cls, key: str) -> Any:
        """Load one top-level field, keeping the rest out of context."""
        return json.loads(_JSON_PATH.read_text(encoding='utf-8'))[key]
```

几处细节：

- **`_JSON_PATH` 用 `Path(__file__).with_name(...)`**，不写绝对路径。
  整个输出目录可以被移动/拷贝，SDK 依然能找到它的 JSON。
- **`# JSON key: 'xxx'` 行尾注释**：Python 属性名可能与 JSON key 不同
  （见 §4 的转义规则），注释保留原始 key 作为对照。
- **`load()` 和 `load_field()` 只生成在根类上**。嵌套类不是文件的入口，
  给它们加 `load()` 只会让 Agent 困惑"该从哪个类开始"。

## 4. Schema 推断

`sdk_gen.py` 直接走查**已经落盘的那份 JSON 数据**（不是折叠后的树——
折叠树丢了类型信息），逐字段推断。

### 类型映射

| JSON 值 | 推断类型 |
| --- | --- |
| `None` | `None` |
| `bool` | `bool` |
| `int` / `float` | `int` / `float` |
| `str` | `str` |
| `dict` | 嵌套 dataclass（深度允许时）/ 否则 `dict` |
| `list`（空） | `list` |
| `list`（同质） | `list[T]` |
| `list`（异质） | `list[Any]` |

列表元素类型只**抽样前 5 项**判断。全扫一遍对 32 万项的列表没有收益——
类型推断是给 Agent 的提示，不是契约；而抽样让生成耗时与数据量脱钩。

### 深度上限 `_MAX_SCHEMA_DEPTH = 4`

超过 4 层的 `dict` 不再展开成嵌套类，退化为 `dict`。理由与折叠的
`max_depth` 一致：**schema 本身也会占上下文**。一个 10 层深的结构全展开会生成
十几个类，docstring 几百行——那就又变成了我们要解决的那个问题。

4 层足够覆盖绝大多数真实 API 响应的"有意义层级"；更深的部分 Agent 可以
`load_field()` 取回来自己看。

### 命名转义

key 来自任意 JSON，Python 标识符有约束。两套规则：

**类名**（`_to_class_name`）：按非字母数字切分 → 各段首字母大写 → 拼接。
`"search_result"` → `SearchResult`。空结果回退 `Root`；首字符是数字则前缀 `V`。

**属性名**（`_to_attr`）：非字母数字 → `_`，剥掉首尾 `_`。
若结果是 **Python 关键字**（`class` / `return` / `import` …）或**以数字开头**，
前缀 `f_`：`"class"` → `f_class`，`"1abc"` → `f_1abc`。

> 这里只保留了"能构造出合法 Python"这个下限，不追求可逆。原始 key 由
> `# JSON key: 'xxx'` 注释和 `load_field()` 的字符串参数两处保留——
> 取数据永远用原始 key，不受转义影响。

**类名去重**：不同层级可能出现同名 key（`{"a": {"meta": …}, "b": {"meta": …}}`）。
生成时检查已有类名，冲突则加数字后缀（`Meta`、`Meta2`）。

## 5. 两个必须这样写的实现约束

这两处都是 demo 跑起来才暴露的问题——单测只做了字符串匹配、从未真正
`exec` 生成的代码，所以漏掉了。现在都有执行级别的回归测试。

### 5.1 所有字段都必须有默认值

最初的实现只给嵌套 dict 字段加 `= None`（因为它要表达"可能不是 dict"），
标量字段不加。结果：

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

### 5.2 每个类都需要 `_from_dict`

最初 `_from_dict` 只生成在根类上。但根类构造嵌套对象时会调
`Metrics._from_dict(...)`：

> `AttributeError: type object 'Meta' has no attribute '_from_dict'`

修法：`_from_dict` 对**每个**类都生成；只有 `load()` / `load_field()` 是根类专属。

### 5.3 生成的代码里没有 `from __future__ import annotations`

本库自己的模块都带这一行，生成的代码**刻意不带**。原因：Agent 加载 SDK 的
典型方式是 `importlib.util.spec_from_file_location` + `exec_module`，此时模块
还没进 `sys.modules`；而 `@dataclass` 在该状态下解析延迟注解会炸：

> `AttributeError: 'NoneType' object has no attribute '__dict__'`
> （`dataclasses._is_type` 里 `sys.modules.get(cls.__module__)` 拿到 `None`）

生成的代码本来也不需要它——嵌套类总是**先于**引用它的父类输出，
所以不存在前向引用。方法返回值的 `"Report"` 用的是字符串字面量，同样安全。

## 6. 与现有模块的接线

改动面刻意保持窄，`fold.py` / `jsonc.py` 完全未动：

| 文件 | 改动 |
| --- | --- |
| `sdk_gen.py` | 新增。schema 推断 + 代码生成，唯一的新模块 |
| `config.py` | `generate_sdk: bool = False` |
| `messages.py` | `sdk_notice` 文案（英文 + 中文） |
| `render.py` | `ItemView.sdk_path` / `.sdk_class`；renderer 按二者是否存在选提示语 |
| `serializer.py` | 落盘后调 `generate_sdk()`，填 `SerializeResult.sdk_files` |

依赖方向仍单向无环，`sdk_gen` 只被 `serializer` 依赖。

### 提示语的分支

`StringTemplateRenderer` 现在有两套折叠提示语：

- `folded_notice`——老文案，只指向 JSON；
- `sdk_notice`——新文案，同时给出 SDK 路径和根类名。

选择条件是 `item.sdk_path and item.sdk_class` 同时存在。这让"没开 SDK 模式"的
输出与改动前**逐字符一致**，老用户零感知；也让自定义 renderer 可以只覆盖
其中一套。

### 生成时机

SDK 只在**确实发生折叠并落盘**的分支里生成：

```python
if folded:
    path.write_text(full_text)        # 先落盘
    if config.generate_sdk:
        sdk_source, sdk_class = generate_sdk(key, json.loads(full_text), path)
        path.with_name(path.stem + "_sdk.py").write_text(sdk_source)
```

没超预算的值直接内联在 overview 里，Agent 已经看到全部内容，生成 SDK 纯属浪费。

文件名用 `path.stem + "_sdk.py"` 而非 `with_suffix(".py")`——后者会把
`report.json` 变成 `report.py`，与 JSON 同名不同后缀，容易在 import 时
与真实模块混淆。

## 7. 已知限制

- **`load_field()` 仍然读整个文件再取字段**。`json.loads` 没有流式/惰性接口，
  要真正做到"只读需要的字节"得引入 `ijson` 之类的依赖，与零依赖约束冲突。
  当前方案省下的是**上下文**（Agent 只看到一个字段的值），不是磁盘 I/O。
  对本库的目标——上下文窗口——这正是要省的那一样。
- **类型是从一份样本推断的**，不是契约。某字段这次是 `int`、下次是 `str`
  （联合类型）时，SDK 只反映生成时看到的那一份。
- **只有 top-level 字段能用 `load_field()`**。嵌套字段要 `load()` 完整对象，
  或 `load_field("parent")` 取回父对象再自己索引。
- **`list[dict]` 不生成元素类
  型**。列表里的 dict 不会展开成 dataclass，保持为 `dict`——否则 320 项的
  列表会生成 320 个（大多相同的）类。

## 8. 测试

```bash
python3 -m unittest discover -s tests -t .
python3 demo.py                              # 场景 5 即 SDK 模式
```

`tests/test_sdk.py` 分两组：

**单元**（`sdk_gen.generate_sdk` 直接调用）：返回值形状、类名派生、
生成的源码能 `compile`、schema 进 docstring、`load()` / `load_field()` 真实执行、
嵌套类生成且**能加载**、dict 字段在标量字段之前的顺序回归、关键字与数字开头
的 key 转义、非 dict 顶层值。

**集成**（经 `serialize()`）：默认不生成、开启后生成、文件位置与命名、
overview 引用 SDK 路径、未开启时提示语不变、内联值不产 SDK、
生成的文件可 import 可用、多 key 各自产 SDK、中文提示语、
关闭时目录里没有 `.py`。
