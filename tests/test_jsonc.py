import json
import unittest

from progressive_serializer import jsonc_dumps
from progressive_serializer.fold import Folded


class TestMatchesJsonDumps(unittest.TestCase):
    """With no Folded nodes the output must be plain json.dumps(indent=2)."""

    CASES = [
        {},
        [],
        {"a": 1},
        {"a": [1, 2, {"b": None}], "c": {"d": []}},
        [[[1]]],
        {"s": 'quo"te\\ and \n newline\ttab'},
        {"n": [1.5, -0, 1e20, True, False, None]},
        {"中文": "值"},
        {"deep": {"a": {"b": {"c": {"d": [1, {"e": 2}]}}}}},
        {"empty_nested": {"a": {}, "b": []}},
    ]

    def test_identical_output(self):
        for case in self.CASES:
            with self.subTest(case=case):
                self.assertEqual(
                    jsonc_dumps(case),
                    json.dumps(case, indent=2, ensure_ascii=False),
                )

    def test_scalar_roots(self):
        for case in [1, "x", None, True, 2.5]:
            with self.subTest(case=case):
                self.assertEqual(jsonc_dumps(case), json.dumps(case, indent=2))

    def test_ensure_ascii_matches(self):
        case = {"中文": "值"}
        self.assertEqual(
            jsonc_dumps(case, ensure_ascii=True),
            json.dumps(case, indent=2, ensure_ascii=True),
        )

    def test_indent_respected(self):
        case = {"a": {"b": 1}}
        self.assertEqual(
            jsonc_dumps(case, indent=4), json.dumps(case, indent=4, ensure_ascii=False)
        )


class TestComments(unittest.TestCase):
    def test_comment_follows_comma_on_scalar(self):
        out = jsonc_dumps({"a": Folded("...", "note", "string"), "b": 1})
        self.assertEqual(out, '{\n  "a": "...",  // note\n  "b": 1\n}')

    def test_last_member_gets_comment_but_no_comma(self):
        out = jsonc_dumps({"a": 1, "b": Folded("...", "note", "string")})
        self.assertEqual(out, '{\n  "a": 1,\n  "b": "..."  // note\n}')

    def test_comment_sits_on_container_open_brace(self):
        node = Folded([{"name": "a.pdf"}], "共 320 项", "list")
        out = jsonc_dumps({"attachments": node, "n": 1})
        lines = out.splitlines()
        self.assertEqual(lines[1], '  "attachments": [  // 共 320 项')
        # The comma for a container lands on its closing bracket, not the open one.
        self.assertEqual(lines[5], "  ],")

    def test_empty_preview_container_is_inline(self):
        out = jsonc_dumps({"a": Folded({}, "too deep", "depth")})
        self.assertEqual(out, '{\n  "a": {}  // too deep\n}')

    def test_folded_root(self):
        self.assertEqual(jsonc_dumps(Folded("...", "note", "string")), '"..."  // note')

    def test_folded_inside_list(self):
        out = jsonc_dumps([Folded("...", "n1", "string"), 2])
        self.assertEqual(out, '[\n  "...",  // n1\n  2\n]')

    def test_nested_folds_each_get_comments(self):
        out = jsonc_dumps(
            {
                "a": Folded("...", "n1", "string"),
                "b": {"c": Folded("...", "n2", "string")},
            }
        )
        self.assertEqual(out.count("//"), 2)
        self.assertIn("// n1", out)
        self.assertIn("// n2", out)

    def test_chinese_not_escaped_by_default(self):
        out = jsonc_dumps({"a": "中文", "b": Folded("...", "已折叠", "string")})
        self.assertIn("中文", out)
        self.assertIn("// 已折叠", out)


if __name__ == "__main__":
    unittest.main()
