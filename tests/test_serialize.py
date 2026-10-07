import base64
import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from progressive_serializer import OVERVIEW_NAME, SerializeConfig, serialize
from progressive_serializer.serializer import _safe_stem


class SerializeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "out"
        self.addCleanup(self._tmp.cleanup)

    def run_serialize(self, data, **kwargs):
        return serialize(data, self.out, SerializeConfig(**kwargs))


class TestBudget(SerializeTestCase):
    def test_small_value_inlined_without_file(self):
        res = self.run_serialize({"k": {"foo": "bar"}})
        self.assertEqual(res.files, {})
        self.assertFalse(res.items[0].folded)
        self.assertIn('"foo": "bar"', res.overview_text)
        self.assertEqual(list(self.out.iterdir()), [self.out / OVERVIEW_NAME])

    def test_value_exactly_at_budget_is_inlined(self):
        value = {"s": "x" * 50}
        size = len(json.dumps(value, indent=2, ensure_ascii=False))
        res = self.run_serialize({"k": value}, max_value_size=size)
        self.assertFalse(res.items[0].folded)
        self.assertEqual(res.files, {})

    def test_value_one_over_budget_is_folded(self):
        value = {"s": "x" * 50}
        size = len(json.dumps(value, indent=2, ensure_ascii=False))
        res = self.run_serialize(
            {"k": value}, max_value_size=size - 1, max_string_len=10
        )
        self.assertTrue(res.items[0].folded)
        self.assertIn("k", res.files)

    def test_over_budget_but_nothing_to_fold_writes_no_file(self):
        # Many tiny ints: over budget, yet no rule can elide anything.
        res = self.run_serialize(
            {"k": list(range(50))},
            max_value_size=10,
            max_list_items=10_000,
            escalate_rounds=0,
        )
        self.assertFalse(res.items[0].folded)
        self.assertEqual(res.files, {})


class TestOutputFiles(SerializeTestCase):
    def test_overview_written_to_disk(self):
        res = self.run_serialize({"k": {"a": 1}})
        self.assertEqual(
            res.overview_path.read_text(encoding="utf-8"), res.overview_text
        )

    def test_full_file_round_trips(self):
        value = {"article": "甲" * 5000, "summary": "乙" * 10, "err_code": 0}
        res = self.run_serialize({"doc": value}, max_value_size=200)
        loaded = json.loads(res.files["doc"].read_text(encoding="utf-8"))
        self.assertEqual(loaded, value)

    def test_bytes_become_base64_in_full_file(self):
        res = self.run_serialize({"d": {"blob": b"a" * 5000}}, max_value_size=200)
        loaded = json.loads(res.files["d"].read_text(encoding="utf-8"))
        self.assertEqual(base64.b64decode(loaded["blob"]), b"a" * 5000)

    def test_existing_files_are_preserved(self):
        self.out.mkdir(parents=True)
        keeper = self.out / "keep.txt"
        keeper.write_text("keep me", encoding="utf-8")
        self.run_serialize({"k": {"a": 1}})
        self.assertEqual(keeper.read_text(encoding="utf-8"), "keep me")

    def test_nested_out_dir_created(self):
        deep = self.out / "a" / "b"
        res = serialize({"k": {"a": 1}}, deep)
        self.assertTrue((deep / OVERVIEW_NAME).exists())
        self.assertEqual(res.out_dir, deep)

    def test_idempotent_across_runs(self):
        data = {"doc": {"article": "甲" * 5000, "err_code": 0}}
        first = self.run_serialize(data, max_value_size=200)
        second = self.run_serialize(data, max_value_size=200)
        self.assertEqual(first.overview_text, second.overview_text)
        self.assertEqual(sorted(first.files), sorted(second.files))
        self.assertEqual(
            first.files["doc"].read_text(encoding="utf-8"),
            second.files["doc"].read_text(encoding="utf-8"),
        )


class TestFilenames(SerializeTestCase):
    def test_slash_in_key_stays_inside_out_dir(self):
        res = self.run_serialize(
            {"a/b": {"s": "x" * 5000}}, max_value_size=100, max_string_len=10
        )
        path = res.files["a/b"]
        self.assertEqual(path.parent, self.out)
        self.assertEqual(path.name, "a_b.json")

    def test_colliding_keys_get_distinct_files(self):
        big = {"s": "x" * 5000}
        res = self.run_serialize(
            {"a/b": big, "a b": big, "a-b": big},
            max_value_size=100,
            max_string_len=10,
        )
        names = {p.name for p in res.files.values()}
        self.assertEqual(names, {"a_b.json", "a_b_2.json", "a-b.json"})
        self.assertEqual(len(res.files), 3)

    def test_overview_points_at_real_filenames(self):
        res = self.run_serialize(
            {"a/b": {"s": "x" * 5000}}, max_value_size=100, max_string_len=10
        )
        self.assertIn(str(res.files["a/b"]), res.overview_text)

    def test_safe_stem_edge_cases(self):
        self.assertEqual(_safe_stem(".."), "item")
        self.assertEqual(_safe_stem(""), "item")
        self.assertEqual(_safe_stem("/"), "item")
        self.assertEqual(_safe_stem("../../etc/passwd"), "etc_passwd")
        self.assertEqual(_safe_stem("ok.name-1"), "ok.name-1")
        self.assertEqual(len(_safe_stem("x" * 500)), 80)

    def test_case_insensitive_collision(self):
        big = {"s": "x" * 5000}
        res = self.run_serialize(
            {"Key": big, "key": big}, max_value_size=100, max_string_len=10
        )
        self.assertEqual(len({p.name.lower() for p in res.files.values()}), 2)


class TestEscalation(SerializeTestCase):
    def test_many_small_fields_still_land_in_budget(self):
        # No single rule fires at the default thresholds, so only escalation
        # can bring this under budget.
        data = {f"field_{i}": f"value_{i}" for i in range(400)}
        res = self.run_serialize({"k": data}, max_value_size=500)
        self.assertTrue(res.items[0].folded)
        self.assertLessEqual(res.items[0].rendered_size, 500)

    def test_escalation_disabled_leaves_value_over_budget(self):
        data = {f"field_{i}": f"value_{i}" for i in range(400)}
        res = self.run_serialize(
            {"k": data}, max_value_size=500, max_object_keys=10_000, escalate_rounds=0
        )
        self.assertGreater(res.items[0].rendered_size, 500)

    def test_deeply_nested_converges(self):
        node = {"leaf": "x" * 300}
        for _ in range(12):
            node = {"child": node, "pad": "y" * 300}
        res = self.run_serialize({"k": node}, max_value_size=300)
        self.assertLessEqual(res.items[0].rendered_size, 300)


class TestHooks(SerializeTestCase):
    def test_custom_size_fn_changes_decisions(self):
        value = {"s": "x" * 30}
        inlined = self.run_serialize({"k": value}, max_value_size=100)
        self.assertFalse(inlined.items[0].folded)

        # Same data, same budget, but every char counts as 100 -> over budget.
        scaled = self.run_serialize(
            {"k": value},
            max_value_size=100,
            max_string_len=10,
            size_fn=lambda s: len(s) * 100,
        )
        self.assertTrue(scaled.items[0].folded)
        self.assertIn("k", scaled.files)

    def test_custom_renderer_used(self):
        class Renderer:
            def render(self, ctx):
                return "|".join(i.key for i in ctx.items)

        res = self.run_serialize({"a": {}, "b": {}}, renderer=Renderer())
        self.assertEqual(res.overview_text, "a|b")
        self.assertEqual(res.overview_path.read_text(encoding="utf-8"), "a|b")

    def test_default_hook_handles_dataclass(self):
        @dataclass
        class Doc:
            article: str
            err_code: int

        from dataclasses import asdict, is_dataclass

        res = self.run_serialize(
            {"doc": Doc("甲" * 5000, 0)},
            max_value_size=200,
            default=lambda o: asdict(o) if is_dataclass(o) else str(o),
        )
        self.assertTrue(res.items[0].folded)
        self.assertIn("err_code", res.items[0].json_block)
        loaded = json.loads(res.files["doc"].read_text(encoding="utf-8"))
        self.assertEqual(loaded["article"], "甲" * 5000)

    def test_default_hook_handles_plain_class(self):
        class Point:
            def __init__(self):
                self.x = 1
                self.y = 2

        res = self.run_serialize({"p": Point()}, default=lambda o: vars(o))
        self.assertIn('"x": 1', res.overview_text)

    def test_missing_default_raises(self):
        with self.assertRaises(TypeError):
            self.run_serialize({"p": object()})

    def test_ensure_ascii_propagates(self):
        res = self.run_serialize({"k": {"a": "中文"}}, ensure_ascii=True)
        self.assertIn("\\u4e2d", res.overview_text)

    def test_messages_default_to_english(self):
        res = self.run_serialize({"k": {"s": "x" * 5000}}, max_value_size=100)
        self.assertIn("chars originally", res.items[0].json_block)
        self.assertIn("were folded automatically", res.overview_text)

    def test_chinese_messages(self):
        from progressive_serializer import CHINESE

        res = self.run_serialize(
            {"k": {"s": "x" * 5000, "items": list(range(50))}},
            max_value_size=100,
            messages=CHINESE,
        )
        self.assertIn("已折叠，原长 5000 字符", res.items[0].json_block)
        self.assertIn("部分字段由于过长已被自动折叠", res.overview_text)

    def test_custom_messages(self):
        from progressive_serializer import Messages

        res = self.run_serialize(
            {"k": {"s": "x" * 5000}},
            max_value_size=100,
            messages=Messages(string_folded="CUT {length}"),
        )
        self.assertIn("// CUT 5000", res.items[0].json_block)

    def test_explicit_renderer_overrides_messages_notice(self):
        from progressive_serializer import CHINESE, StringTemplateRenderer

        # A caller-supplied renderer owns its own notice text.
        res = self.run_serialize(
            {"k": {"s": "x" * 5000}},
            max_value_size=100,
            messages=CHINESE,
            renderer=StringTemplateRenderer(folded_notice="SEE ${full_path}\n\n"),
        )
        self.assertIn("SEE ", res.overview_text)
        # Fold notes still follow `messages`.
        self.assertIn("已折叠", res.items[0].json_block)


class TestAdversarialInput(SerializeTestCase):
    def test_circular_reference_raises(self):
        # The full dump happens before the fold walk, so json.dumps' own cycle
        # detection fires instead of blowing the recursion limit.
        data = {"a": 1}
        data["self"] = data
        with self.assertRaises(ValueError):
            self.run_serialize({"k": data})

    def test_comment_stripped_block_is_valid_json(self):
        import re

        res = self.run_serialize(
            {
                "k": {
                    "text": "x" * 3000,
                    "blob": b"\x00" * 500,
                    "items": [{"n": i} for i in range(50)],
                    "deep": {"a": {"b": {"c": {"e": {"f": 1}}}}},
                    "code": 0,
                }
            },
            max_value_size=400,
            max_depth=4,
        )
        stripped = re.sub(r"\s*//.*$", "", res.items[0].json_block, flags=re.M)
        json.loads(stripped)  # raises if the JSONC is malformed

    def test_string_containing_fence_does_not_break_markdown(self):
        res = self.run_serialize({"k": {"s": "``` not a fence"}})
        self.assertIn("````json", res.overview_text)

    def test_non_string_top_level_keys_stringified(self):
        res = self.run_serialize({1: {"a": 1}, None: {"b": 2}})
        self.assertEqual([i.key for i in res.items], ["1", "None"])


    def test_ellipsis_visible_in_overview(self):
        res = self.run_serialize(
            {"k": {"items": [{"n": i} for i in range(320)], "code": 0}},
            max_value_size=200,
        )
        block = res.items[0].json_block
        # Elision shows in the structure, not only in the trailing comment.
        self.assertIn('"..."', block)
        self.assertIn("320 items total", block)


class TestResultShape(SerializeTestCase):
    def test_items_follow_input_order(self):
        res = self.run_serialize({"b": {}, "a": {}, "c": {}})
        self.assertEqual([i.key for i in res.items], ["b", "a", "c"])

    def test_sizes_reported(self):
        res = self.run_serialize(
            {"k": {"s": "x" * 5000}}, max_value_size=100, max_string_len=10
        )
        item = res.items[0]
        self.assertGreater(item.original_size, 5000)
        self.assertLess(item.rendered_size, 200)

    def test_empty_input(self):
        res = self.run_serialize({})
        self.assertEqual(res.items, [])
        self.assertEqual(res.files, {})
        self.assertTrue(res.overview_path.exists())


class TestSpecExample(SerializeTestCase):
    """The example from the original requirements."""

    def test_matches_expected_shape(self):
        res = self.run_serialize(
            {
                "key1": {"foo": "bar"},
                "key2": {
                    "article": "甲" * 100_000,
                    "summary": "乙" * 100,
                    "err_code": 0,
                },
            }
        )
        text = res.overview_text
        self.assertIn("- key1", text)
        self.assertIn('"foo": "bar"', text)
        self.assertIn("- key2", text)
        # article folded with a trailing comment; summary and err_code intact.
        self.assertIn('"article": "...",  // folded, 100000 chars originally', text)
        self.assertIn("乙" * 100, text)
        self.assertIn('"err_code": 0', text)
        self.assertIn(str(res.files["key2"]), text)
        self.assertNotIn("甲" * 1000, text)
        # And the full article is retrievable.
        loaded = json.loads(res.files["key2"].read_text(encoding="utf-8"))
        self.assertEqual(loaded["article"], "甲" * 100_000)


if __name__ == "__main__":
    unittest.main()
