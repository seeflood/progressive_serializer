"""Tests for serialize_value(): one value in, a bare JSONC string out."""

import json
import re
import tempfile
import unittest
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path

from progressive_serializer import CHINESE, Messages, SerializeConfig, serialize_value


def strip_comments(jsonc_text: str) -> str:
    """Drop the comments so the remainder can be parsed as JSON.

    Safe only because the test payloads contain no "//" or "/*" inside a string.
    """
    without_block = re.sub(r"/\*.*?\*/", "", jsonc_text, flags=re.DOTALL)
    return "\n".join(
        re.sub(r"\s*//.*$", "", line) for line in without_block.splitlines()
    )


def notice_of(jsonc_text: str) -> str:
    """The root ``/* … */`` notice, unwrapped back to a single line.

    The notice is wrapped across lines, so assertions about its content cannot
    just look at line 1.
    """
    match = re.search(r"/\*(.*?)\*/", jsonc_text, flags=re.DOTALL)
    return " ".join(match.group(1).split()) if match else ""


class SerializeValueTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(
            lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True)
        )
        self.out = self.dir / "report.json"

    # --- small values: untouched passthrough ---------------------------

    def test_small_value_returned_verbatim_as_json(self):
        data = {"foo": "bar", "n": 1}
        out = serialize_value(data, self.out)
        self.assertEqual(out, json.dumps(data, indent=2, ensure_ascii=False))

    def test_small_value_writes_no_file(self):
        serialize_value({"foo": "bar"}, self.out)
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_small_value_has_no_comment(self):
        out = serialize_value({"foo": "bar"}, self.out)
        self.assertNotIn("//", out)
        self.assertNotIn("/*", out)

    # --- no wrapping text ----------------------------------------------

    def test_no_markdown_fence_or_heading(self):
        out = serialize_value({"body": "x" * 5000}, self.out)
        self.assertNotIn("```", out)
        self.assertFalse(out.startswith("-"))
        self.assertFalse(out.startswith("#"))

    def test_starts_with_brace_and_ends_with_brace(self):
        out = serialize_value({"body": "x" * 5000}, self.out)
        self.assertTrue(out.startswith("{  /*"))
        self.assertTrue(out.endswith("}"))

    def test_result_splices_into_a_larger_document(self):
        inner = serialize_value({"body": "x" * 5000}, self.out)
        combined = '{\n  "wrapped":\n' + inner + "\n}"
        parsed = json.loads(strip_comments(combined))
        self.assertIn("wrapped", parsed)
        self.assertEqual(parsed["wrapped"]["body"], "...")

    def test_block_parses_as_json_once_comments_are_stripped(self):
        out = serialize_value({"body": "x" * 5000, "n": 3}, self.out)
        self.assertEqual(json.loads(strip_comments(out))["n"], 3)

    # --- folding + spill -------------------------------------------------

    def test_oversized_value_spills_full_json_to_out_file(self):
        data = {"body": "x" * 5000, "n": 3}
        serialize_value(data, self.out)
        self.assertEqual(json.loads(self.out.read_text(encoding="utf-8")), data)

    def test_notice_names_the_out_file(self):
        out = serialize_value({"body": "x" * 5000}, self.out)
        notice = notice_of(out)
        self.assertIn(str(self.out), notice)
        self.assertIn("folded automatically", notice)
        self.assertEqual(out.count("folded automatically"), 1)

    def test_notice_is_a_delimited_block_comment(self):
        # A "//" notice has no terminator, so a wrapped one is ambiguous: the
        # reader cannot tell prose from the next line of JSON.
        out = serialize_value({"body": "x" * 5000}, self.out)
        self.assertTrue(out.startswith("{  /* "))
        # One pair for the notice, one for the single fold note.
        self.assertEqual(out.count("/*"), 2)
        self.assertEqual(out.count("*/"), 2)

    def test_notice_is_wrapped_not_left_on_one_long_line(self):
        out = serialize_value(
            {"body": "x" * 5000}, self.out, SerializeConfig(generate_sdk=True)
        )
        comment_lines = out[: out.index("*/")].splitlines()
        self.assertGreater(len(comment_lines), 1)
        # Only the path may overrun; prose lines stay within the budget.
        for line in comment_lines:
            if str(self.dir) not in line:
                self.assertLessEqual(len(line), 100)

    def test_every_comment_is_a_block_comment(self):
        # Uniform delimiters: a reader never has to track two conventions, and
        # no comment in the output can run to end of line.
        out = serialize_value({"body": "x" * 5000}, self.out)
        self.assertNotIn("//", out)
        self.assertIn('"body": "..."  /* folded, 5000 chars originally */', out)
        self.assertEqual(out.count("/*"), out.count("*/"))

    def test_line_style_can_be_forced(self):
        out = serialize_value(
            {"body": "x" * 5000}, self.out, SerializeConfig(comment_style="line")
        )
        self.assertNotIn("/*", out)
        self.assertIn('"body": "..."  // folded, 5000 chars originally', out)

    def test_parent_directory_is_created(self):
        nested = self.dir / "a" / "b" / "report.json"
        serialize_value({"body": "x" * 5000}, nested)
        self.assertTrue(nested.exists())

    def test_fold_notes_still_appear_on_inner_fields(self):
        out = serialize_value({"body": "x" * 5000}, self.out)
        self.assertIn("/* folded, 5000 chars originally */", out)

    def test_over_budget_but_nothing_folded_writes_no_file(self):
        # 300 tiny fields: no rule fires with max_object_keys disabled, so
        # there is nothing elided and nothing to point at.
        data = {f"m{i}": i for i in range(300)}
        out = serialize_value(data, self.out, SerializeConfig(max_value_size=100))
        self.assertFalse(self.out.exists())
        self.assertNotIn("//", out)
        self.assertNotIn("/*", out)

    # --- non-dict roots ---------------------------------------------------

    def test_list_root(self):
        out = serialize_value([{"body": "x" * 5000}], self.out)
        self.assertTrue(out.startswith("[  /*"))
        self.assertTrue(self.out.exists())

    def test_class_instance_via_default_hook(self):
        @dataclass
        class Report:
            body: str
            score: float

        out = serialize_value(
            Report("y" * 5000, 0.93),
            self.out,
            SerializeConfig(default=lambda o: asdict(o) if is_dataclass(o) else vars(o)),
        )
        self.assertIn('"score": 0.93', out)
        self.assertEqual(
            json.loads(self.out.read_text(encoding="utf-8"))["body"], "y" * 5000
        )

    # --- SDK mode ---------------------------------------------------------

    def test_sdk_file_named_after_the_out_file_stem(self):
        serialize_value(
            {"body": "x" * 5000},
            self.out,
            SerializeConfig(generate_sdk=True),
        )
        self.assertTrue((self.dir / "report_sdk.py").exists())

    def test_sdk_notice_names_path_and_root_class(self):
        out = serialize_value(
            {"body": "x" * 5000},
            self.out,
            SerializeConfig(generate_sdk=True),
        )
        notice = notice_of(out)
        self.assertIn(str(self.dir / "report_sdk.py"), notice)
        self.assertIn("(class `Report`)", notice)

    def test_sdk_root_class_derives_from_stem(self):
        out_file = self.dir / "search_result.json"
        out = serialize_value(
            {"body": "x" * 5000}, out_file, SerializeConfig(generate_sdk=True)
        )
        self.assertIn("(class `SearchResult`)", notice_of(out))

    def test_no_sdk_file_when_disabled(self):
        serialize_value({"body": "x" * 5000}, self.out)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["report.json"])

    # --- config knobs -----------------------------------------------------

    def test_messages_localize_the_notice_and_the_notes(self):
        out = serialize_value(
            {"body": "x" * 5000},
            self.out,
            SerializeConfig(messages=CHINESE),
        )
        self.assertIn("完整内容见", notice_of(out))
        self.assertIn("/* 已折叠，原长 5000 字符 */", out)

    def test_custom_messages_respected(self):
        out = serialize_value(
            {"body": "x" * 5000},
            self.out,
            SerializeConfig(
                messages=Messages(
                    string_folded="省略 {length} 字",
                    folded_notice="FULL: ${full_path}\n\n",
                )
            ),
        )
        self.assertEqual(out.splitlines()[0], f"{{  /* FULL: {self.out} */")
        self.assertIn("/* 省略 5000 字 */", out)

    def test_renderer_is_ignored(self):
        class Boom:
            def render(self, ctx):  # pragma: no cover - must never be called
                raise AssertionError("renderer must not be used")

        out = serialize_value(
            {"body": "x" * 5000}, self.out, SerializeConfig(renderer=Boom())
        )
        self.assertTrue(out.startswith("{  /*"))

    def test_max_value_size_decides_folding(self):
        data = {"body": "x" * 100}
        big_budget = serialize_value(data, self.out, SerializeConfig(max_value_size=5000))
        self.assertNotIn("/*", big_budget)
        small_budget = serialize_value(
            data, self.out, SerializeConfig(max_value_size=50, max_string_len=10)
        )
        self.assertIn("/*", small_budget)

    def test_indent_respected(self):
        out = serialize_value(
            {"body": "x" * 5000, "d": {"k": 1}},
            self.out,
            SerializeConfig(indent=4),
        )
        self.assertIn('\n        "k": 1', out)

    def test_ensure_ascii_respected(self):
        out = serialize_value(
            {"名字": "值", "body": "x" * 5000},
            self.out,
            SerializeConfig(ensure_ascii=True),
        )
        self.assertNotIn("名字", out)
        self.assertIn("\\u", out)

    def test_accepts_str_path(self):
        out = serialize_value({"body": "x" * 5000}, str(self.out))
        self.assertIn(str(self.out), notice_of(out))


if __name__ == "__main__":
    unittest.main()
