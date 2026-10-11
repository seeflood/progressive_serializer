"""Comment delimiters: comment_style="line" vs "block", and wrapping."""

import json
import unittest

from progressive_serializer import jsonc_dumps
from progressive_serializer.fold import Folded

BLOCK = "block"
LINE = "line"


def folded(note="note"):
    return Folded("...", note, "string")


class TestStyleSelection(unittest.TestCase):
    def test_line_is_the_default(self):
        out = jsonc_dumps({"a": folded()})
        self.assertEqual(out, '{\n  "a": "..."  // note\n}')

    def test_block_style_wraps_every_note(self):
        out = jsonc_dumps({"a": folded(), "b": folded("n2")}, comment_style=BLOCK)
        self.assertEqual(out.count("/*"), 2)
        self.assertEqual(out.count("*/"), 2)
        self.assertNotIn("//", out)

    def test_block_style_on_container_open(self):
        node = Folded([{"name": "a.pdf"}], "320 items total", "list")
        out = jsonc_dumps({"attachments": node}, comment_style=BLOCK)
        self.assertEqual(out.splitlines()[1], '  "attachments": [  /* 320 items total */')

    def test_block_style_at_every_depth(self):
        out = jsonc_dumps(
            {"a": {"b": {"c": folded("deep")}}}, comment_style=BLOCK
        )
        self.assertIn('"c": "..."  /* deep */', out)

    def test_comma_still_precedes_the_comment(self):
        out = jsonc_dumps({"a": folded(), "b": 1}, comment_style=BLOCK)
        self.assertIn('"a": "...",  /* note */', out)

    def test_empty_preview_container_stays_inline(self):
        out = jsonc_dumps({"a": Folded({}, "too deep", "depth")}, comment_style=BLOCK)
        self.assertEqual(out, '{\n  "a": {}  /* too deep */\n}')

    def test_unknown_style_rejected(self):
        with self.assertRaises(ValueError):
            jsonc_dumps({"a": 1}, comment_style="hash")

    def test_no_comments_means_plain_json_in_either_style(self):
        case = {"a": [1, {"b": None}]}
        expected = json.dumps(case, indent=2, ensure_ascii=False)
        for style in (LINE, BLOCK):
            with self.subTest(style=style):
                self.assertEqual(jsonc_dumps(case, comment_style=style), expected)


class TestRootPlusNote(unittest.TestCase):
    """Both a root comment and a fold note land on line 1."""

    def test_line_style_merges_them_into_one_comment(self):
        # "//" swallows the line, so a line can hold only one.
        out = jsonc_dumps(folded("own note"), root_comment="notice")
        self.assertEqual(out, '"..."  // notice own note')

    def test_block_style_keeps_them_separate(self):
        out = jsonc_dumps(folded("own note"), root_comment="notice",
                          comment_style=BLOCK)
        self.assertEqual(out, '"..."  /* notice */  /* own note */')

    def test_block_style_root_comes_first(self):
        out = jsonc_dumps({"a": 1}, root_comment="notice", comment_style=BLOCK)
        self.assertEqual(out.splitlines()[0], "{  /* notice */")

    def test_note_survives_a_wrapped_root_comment(self):
        long = "word " * 40
        out = jsonc_dumps(
            folded("own note"),
            root_comment=long,
            comment_style=BLOCK,
            comment_width=40,
        )
        self.assertIn("/* own note */", out)
        self.assertTrue(out.rstrip().endswith("/* own note */"))
        # The JSON value is not lost to the wrap.
        self.assertTrue(out.startswith('"..."  /*'))


class TestWrapping(unittest.TestCase):
    LONG = (
        "Oversized fields were folded automatically and the complete payload "
        "now lives in a separate file that the agent can read on demand."
    )

    def wrapped(self, **kw):
        kw.setdefault("comment_width", 60)
        return jsonc_dumps(
            {"a": 1}, root_comment=self.LONG, comment_style=BLOCK, **kw
        )

    def test_wraps_onto_several_lines(self):
        self.assertGreater(len(self.wrapped().splitlines()), 3)

    def test_json_is_not_lost_to_the_wrap(self):
        out = self.wrapped()
        self.assertTrue(out.startswith("{  /* "))
        self.assertIn('  "a": 1', out)
        self.assertTrue(out.endswith("}"))

    def test_no_json_leaks_into_the_comment_body(self):
        body = self.wrapped().split("/*", 1)[1].split("*/", 1)[0]
        self.assertNotIn('"a"', body)
        self.assertIn("read on demand", body)

    def test_continuation_lines_align_under_the_opening_delimiter(self):
        out = self.wrapped()
        lines = out.splitlines()
        body_col = lines[0].index("/* ") + len("/* ")
        # Lines between the first and the one carrying "*/" are continuations.
        last = next(i for i, ln in enumerate(lines) if "*/" in ln)
        for line in lines[1 : last + 1]:
            self.assertEqual(line[:body_col], " " * body_col)
            self.assertNotEqual(line[body_col : body_col + 1], " ")

    def test_exactly_one_delimiter_pair(self):
        out = self.wrapped()
        self.assertEqual(out.count("/*"), 1)
        self.assertEqual(out.count("*/"), 1)

    def test_width_zero_keeps_one_line(self):
        out = self.wrapped(comment_width=0)
        self.assertEqual(out.splitlines()[0], "{  /* " + self.LONG + " */")

    def test_long_path_is_not_split(self):
        path = "/Users/someone/projects/progressive_serializer/demo_out/report.json"
        out = jsonc_dumps(
            {"a": 1},
            root_comment=f"full content at `{path}`",
            comment_style=BLOCK,
            comment_width=40,
        )
        # Overruns the width rather than breaking, so it stays copy-pasteable.
        self.assertTrue(any(path in line for line in out.splitlines()))

    def test_tiny_width_still_emits_the_text(self):
        body = self.wrapped(comment_width=4).split("/*", 1)[1].split("*/", 1)[0]
        self.assertIn("Oversized", body)

    def test_line_style_ignores_width_and_never_wraps(self):
        # A "//" continuation would be read as JSON, so it must not wrap.
        out = jsonc_dumps(
            {"a": 1}, root_comment=self.LONG, comment_style=LINE, comment_width=20
        )
        self.assertEqual(out.splitlines()[0], "{  // " + self.LONG)

    def test_deep_note_wraps_without_eating_its_own_line(self):
        out = jsonc_dumps(
            {"outer": {"inner": Folded("...", self.LONG, "string")}},
            comment_style=BLOCK,
            comment_width=60,
        )
        # The JSON member survived, and the note's tail is still present.
        self.assertIn('"inner": "..."  /*', out)
        self.assertIn("demand. */", out)
        self.assertEqual(out.count("*/"), 1)
        # Continuations align under the "/*" even when it starts deep.
        lines = out.splitlines()
        opener = next(ln for ln in lines if "/*" in ln)
        body_col = opener.index("/* ") + len("/* ")
        last = next(i for i, ln in enumerate(lines) if "*/" in ln)
        first = lines.index(opener)
        for line in lines[first + 1 : last + 1]:
            self.assertEqual(line[:body_col], " " * body_col)


class TestTerminatorEscaping(unittest.TestCase):
    def test_embedded_terminator_in_root_comment(self):
        out = jsonc_dumps(
            {"a": 1}, root_comment="ends early */ then json", comment_style=BLOCK
        )
        self.assertEqual(out.count("*/"), 1)
        self.assertTrue(out.splitlines()[0].endswith("*/"))

    def test_embedded_terminator_in_a_fold_note(self):
        out = jsonc_dumps({"a": folded("note */ leak")}, comment_style=BLOCK)
        self.assertEqual(out.count("*/"), 1)

    def test_terminator_left_alone_in_line_style(self):
        # Harmless there: "//" has no terminator to close early.
        out = jsonc_dumps({"a": folded("a */ b")}, comment_style=LINE)
        self.assertIn("// a */ b", out)


if __name__ == "__main__":
    unittest.main()
