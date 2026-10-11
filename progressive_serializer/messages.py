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
        "The file may be large, so avoid reading it in full unless you really "
        "do need every field in the JSON. If you only care about certain "
        "fields, write a Python script that calls `${sdk_path}` "
        "(class `${sdk_class}`) to read just those fields.\n\n"
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
        "由于文件内容可能较大，请尽量避免直接读取完整文件内容"
        "(除非你确实需要看 json 的所有字段)。"
        "如果你只关心 json 中的某些字段，你可以通过写 python 脚本、"
        "调用 `${sdk_path}` (类 `${sdk_class}`) 来按需读取字段。\n\n"
    ),
)
