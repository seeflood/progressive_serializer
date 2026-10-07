"""Human-readable strings for fold notes and the overview notice.

Defaults are English. Pass ``SerializeConfig(messages=CHINESE)`` for Chinese, or
build your own :class:`Messages` to localize or reword.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Messages:
    """Format strings for everything the library writes in prose.

    The fold notes become trailing ``//`` comments; ``folded_notice`` is the
    line in ``overview.md`` pointing at the complete file.

    Fold notes are filled with ``str.format``. Every note receives an ``{s}``
    placeholder holding ``""`` or ``"s"`` so English plurals read correctly;
    languages without plural agreement can simply ignore it.
    """

    # Fold notes. Placeholders are filled with str.format.
    string_folded: str = "folded, {length} chars originally"
    binary_folded: str = "folded binary ({type_name}, {size} bytes)"
    list_folded: str = "folded, {total} items total, showing {shown}"
    object_folded: str = "folded, {total} fields total, {dropped} omitted"
    depth_object_folded: str = "folded, nested too deep ({total} field{s})"
    depth_list_folded: str = "folded, nested too deep ({total} item{s})"

    # Overview notice. Uses string.Template syntax (``${full_path}``) because it
    # is rendered by StringTemplateRenderer alongside the other fragments.
    folded_notice: str = (
        "Oversized fields were folded automatically; "
        "full content at `${full_path}`\n\n"
    )
    # Notice appended when a typed SDK was also generated alongside the JSON.
    # Variables: ``${full_path}``, ``${sdk_path}``, ``${sdk_class}``.
    sdk_notice: str = (
        "Oversized fields were folded automatically; "
        "full content at `${full_path}`. "
        "Use `${sdk_path}` (class `${sdk_class}`) to load only the fields you need "
        "instead of reading the whole file.\n\n"
    )


ENGLISH = Messages()

CHINESE = Messages(
    string_folded="已折叠，原长 {length} 字符",
    binary_folded="已折叠的二进制数据 ({type_name}, {size} 字节)",
    list_folded="已折叠，共 {total} 项，仅展示 {shown} 项",
    object_folded="已折叠，共 {total} 个字段，省略 {dropped} 个",
    depth_object_folded="已折叠，嵌套过深 (共 {total} 个字段)",
    depth_list_folded="已折叠，嵌套过深 (共 {total} 项)",
    folded_notice="部分字段由于过长已被自动折叠, 完整内容见 `${full_path}`\n\n",
    sdk_notice=(
        "部分字段由于过长已被自动折叠, 完整内容见 `${full_path}`。"
        "请使用 `${sdk_path}` (类 `${sdk_class}`) 按需读取字段，"
        "避免一次性加载整个文件。\n\n"
    ),
)
