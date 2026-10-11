"""Tests for the generate_sdk mode (SerializeConfig(generate_sdk=True))."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from progressive_serializer import SerializeConfig, serialize
from progressive_serializer.sdk_gen import generate_sdk


class SdkGenUnitTests(unittest.TestCase):
    """Unit tests for sdk_gen.generate_sdk() in isolation."""

    def _gen(self, key, value):
        with tempfile.TemporaryDirectory() as d:
            json_path = Path(d) / f"{key}.json"
            json_path.write_text(json.dumps(value), encoding="utf-8")
            return generate_sdk(key, value, json_path)

    def _load(self, key, value):
        """Generate, write, import, and return the root class.

        Importing is the point: string-matching the source misses the errors
        that only surface when the dataclasses are actually constructed.
        """
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        json_path = Path(d) / f"{key}.json"
        json_path.write_text(json.dumps(value), encoding="utf-8")
        src, root = generate_sdk(key, value, json_path)
        sdk_path = Path(d) / f"{key}_sdk.py"
        sdk_path.write_text(src, encoding="utf-8")

        spec = importlib.util.spec_from_file_location(f"_sdk_{key}_{id(self)}", sdk_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return src, getattr(mod, root)

    def _class_names(self, src):
        return [
            line[len("class ") : -1]
            for line in src.splitlines()
            if line.startswith("class ")
        ]

    def test_returns_source_and_class_name(self):
        src, cls = self._gen("my_doc", {"title": "hi", "count": 1})
        self.assertIsInstance(src, str)
        self.assertIsInstance(cls, str)
        self.assertTrue(cls[0].isupper())

    def test_class_name_derived_from_key(self):
        _, cls = self._gen("search_result", {"hits": []})
        self.assertEqual(cls, "SearchResult")

    def test_source_compiles(self):
        src, _ = self._gen("doc", {"title": "x", "count": 0})
        compile(src, "<test>", "exec")  # raises SyntaxError if broken

    def test_schema_in_docstring(self):
        src, _ = self._gen("doc", {"title": "hello", "count": 42})
        self.assertIn("title", src)
        self.assertIn("count", src)

    def test_get_single_field(self):
        value = {"article": "x" * 100, "summary": "short", "code": 0}
        _, cls = self._load("doc", value)
        self.assertEqual(cls.get("summary"), "short")
        self.assertEqual(cls.get("code"), 0)

    def test_load_returns_dataclass(self):
        value = {"name": "Alice", "score": 99}
        with tempfile.TemporaryDirectory() as d:
            json_path = Path(d) / "player.json"
            json_path.write_text(json.dumps(value), encoding="utf-8")
            src, root = generate_sdk("player", value, json_path)
            sdk_path = Path(d) / "player_sdk.py"
            sdk_path.write_text(src, encoding="utf-8")

            spec = importlib.util.spec_from_file_location("player_sdk", sdk_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            cls = getattr(mod, root)

            obj = cls.load()
            self.assertEqual(obj.name, "Alice")
            self.assertEqual(obj.score, 99)

    def test_nested_dict_becomes_nested_class(self):
        value = {"meta": {"author": "Bob", "version": 2}, "body": "text"}
        src, root = self._gen("doc", value)
        self.assertIn("class Meta", src)
        self.assertIn("class Doc", src)

    def test_nested_dict_sdk_loads(self):
        # A nested dict field is emitted with a default; any plain field after
        # it must also get one or the dataclass refuses to build.
        value = {"meta": {"author": "Bob", "version": 2}, "body": "text"}
        with tempfile.TemporaryDirectory() as d:
            json_path = Path(d) / "doc.json"
            json_path.write_text(json.dumps(value), encoding="utf-8")
            src, root = generate_sdk("doc", value, json_path)
            sdk_path = Path(d) / "doc_sdk.py"
            sdk_path.write_text(src, encoding="utf-8")

            spec = importlib.util.spec_from_file_location("nested_sdk", sdk_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            cls = getattr(mod, root)

            obj = cls.load()
            self.assertEqual(obj.body, "text")
            self.assertEqual(obj.meta.author, "Bob")
            self.assertEqual(obj.meta.version, 2)

    def test_field_order_with_dict_before_scalar(self):
        # Regression: dict field first, scalars after -> must still compile.
        value = {"cfg": {"x": 1}, "name": "n", "count": 3}
        src, _ = self._gen("rec", value)
        compile(src, "<test>", "exec")

    def test_non_dict_value(self):
        # Lists and scalars at the top level should not crash.
        src, root = self._gen("items", [1, 2, 3])
        compile(src, "<test>", "exec")

    def test_keyword_field_name_escaped(self):
        src, _ = self._gen("data", {"class": "foo", "return": 1})
        compile(src, "<test>", "exec")
        # Attribute names must be escaped away from bare Python keywords.
        self.assertIn("f_class", src)
        self.assertIn("f_return", src)

    def test_numeric_key_escaped(self):
        src, _ = self._gen("data", {"1abc": "val"})
        compile(src, "<test>", "exec")


class DeepNestingTests(SdkGenUnitTests):
    """Deep structures must stay structured all the way down."""

    @staticmethod
    def _chain(depth, leaf="deep!"):
        node = {"leaf": leaf, "n": 0}
        for i in reversed(range(depth)):
            node = {f"l{i}": node, "pad": "y"}
        return node

    def test_eight_levels_all_get_classes(self):
        src, cls = self._load("deep", self._chain(8))
        names = self._class_names(src)
        # One class per level plus the root; no level degrades to bare dict.
        for i in range(8):
            self.assertIn(f"L{i}", names)
        self.assertNotIn("Optional[dict]", src)

    def test_deep_typed_chain_resolves(self):
        _, cls = self._load("deep", self._chain(8))
        node = cls.load()
        for i in range(8):
            node = getattr(node, f"l{i}")
        self.assertEqual(node.leaf, "deep!")

    def test_deep_path_get(self):
        _, cls = self._load("deep", self._chain(8))
        path = ".".join(f"l{i}" for i in range(8)) + ".leaf"
        self.assertEqual(cls.get(path), "deep!")

    def test_schema_docstring_covers_deep_levels(self):
        src, _ = self._load("deep", self._chain(6))
        head = src[: src.index("import json")]
        # Levels beyond the old depth-4 cutoff are described, not truncated.
        self.assertIn("l5", head)
        self.assertIn("leaf", head)

    def test_pathological_depth_terminates(self):
        # Far past _MAX_CLASS_DEPTH: must generate, compile and load.
        src, cls = self._load("deep", self._chain(40))
        self.assertEqual(cls.get("l0.l1.l2.pad"), "y")


class ListElementTests(SdkGenUnitTests):
    """Lists of objects get an element class, not a bare `list`."""

    def test_list_of_objects_gets_singular_element_class(self):
        src, _ = self._load(
            "u", {"users": [{"name": "ann"}, {"name": "bo"}]}
        )
        self.assertIn("User", self._class_names(src))
        self.assertIn("Optional[list[User]]", src)

    def test_nested_object_inside_list_element(self):
        src, cls = self._load(
            "u",
            {"users": [{"name": "ann", "addr": {"city": "SF", "zip": "94102"}}]},
        )
        self.assertIn("Addr", self._class_names(src))
        self.assertEqual(cls.load().users[0].addr.city, "SF")

    def test_list_elements_hydrate_to_dataclasses(self):
        _, cls = self._load(
            "u", {"users": [{"name": "ann"}, {"name": "bo"}]}
        )
        users = cls.load().users
        self.assertEqual([u.name for u in users], ["ann", "bo"])

    def test_list_of_lists_keeps_shape(self):
        _, cls = self._load("m", {"grid": [[{"v": 1}, {"v": 2}], [{"v": 3}]]})
        grid = cls.load().grid
        self.assertEqual([[c.v for c in row] for row in grid], [[1, 2], [3]])

    def test_ragged_list_unions_keys(self):
        # A null in element 0 must not mask the real type from element 1.
        src, _ = self._load("r", {"rows": [{"a": None}, {"a": 5, "b": "x"}]})
        self.assertIn("a: Optional[int]", src)
        self.assertIn("b: Optional[str]", src)

    def test_list_of_scalars_stays_plain(self):
        src, cls = self._load("t", {"tags": ["a", "b"]})
        self.assertIn("Optional[list[str]]", src)
        self.assertEqual(cls.load().tags, ["a", "b"])

    def test_singularization(self):
        for key, expected in [
            ("users", "User"),
            ("entries", "Entry"),
            ("addresses", "Address"),
            ("boxes", "Box"),
            ("dishes", "Dish"),
            # Invariant or already-singular nouns must be left alone.
            ("data", "Data"),
            ("series", "Series"),
            ("status", "Status"),
            ("children", "Children"),
        ]:
            src, _ = self._load(key, {key: [{"x": 1}]})
            self.assertIn(expected, self._class_names(src), f"for {key}")

    def test_empty_list_generates_no_element_class(self):
        src, cls = self._load("e", {"items": []})
        self.assertEqual(self._class_names(src), ["E"])
        self.assertEqual(cls.get("items"), [])


class PathAccessTests(SdkGenUnitTests):
    """get() / keys() address nested values without loading the payload."""

    SAMPLE = {
        "title": "Q3",
        "metrics": {"revenue": 4820000, "margin": 61.3},
        "users": [{"name": "ann", "addr": {"city": "SF"}}, {"name": "bo"}],
    }

    def test_get_top_level(self):
        _, cls = self._load("r", self.SAMPLE)
        self.assertEqual(cls.get("title"), "Q3")

    def test_get_nested(self):
        _, cls = self._load("r", self.SAMPLE)
        self.assertEqual(cls.get("metrics.revenue"), 4820000)

    def test_get_through_list_index(self):
        _, cls = self._load("r", self.SAMPLE)
        self.assertEqual(cls.get("users.0.addr.city"), "SF")
        self.assertEqual(cls.get("users.1.name"), "bo")

    def test_get_subtree(self):
        _, cls = self._load("r", self.SAMPLE)
        self.assertEqual(cls.get("metrics"), {"revenue": 4820000, "margin": 61.3})

    def test_get_empty_path_returns_whole_payload(self):
        _, cls = self._load("r", self.SAMPLE)
        self.assertEqual(cls.get(), self.SAMPLE)

    def test_missing_path_raises_keyerror(self):
        _, cls = self._load("r", self.SAMPLE)
        with self.assertRaises(KeyError):
            cls.get("metrics.nope")

    def test_missing_path_error_names_failing_segment(self):
        _, cls = self._load("r", self.SAMPLE)
        with self.assertRaises(KeyError) as ctx:
            cls.get("metrics.nope.deeper")
        self.assertIn("metrics.nope", str(ctx.exception))

    def test_missing_path_with_default(self):
        _, cls = self._load("r", self.SAMPLE)
        self.assertIsNone(cls.get("metrics.nope", default=None))
        self.assertEqual(cls.get("a.b.c", default="fb"), "fb")

    def test_out_of_range_index(self):
        _, cls = self._load("r", self.SAMPLE)
        with self.assertRaises(KeyError):
            cls.get("users.99")
        self.assertEqual(cls.get("users.99", default="fb"), "fb")

    def test_non_integer_index_into_list(self):
        _, cls = self._load("r", self.SAMPLE)
        self.assertEqual(cls.get("users.abc", default="fb"), "fb")

    def test_keys_of_object(self):
        _, cls = self._load("r", self.SAMPLE)
        self.assertEqual(cls.keys("metrics"), ["revenue", "margin"])
        self.assertEqual(cls.keys(), ["title", "metrics", "users"])

    def test_keys_of_list_returns_length(self):
        _, cls = self._load("r", self.SAMPLE)
        self.assertEqual(cls.keys("users"), 2)

    def test_keys_of_scalar_returns_type_name(self):
        _, cls = self._load("r", self.SAMPLE)
        self.assertEqual(cls.keys("title"), "str")


class EdgeCaseTests(SdkGenUnitTests):
    def test_empty_dict_value(self):
        src, cls = self._load("e", {"cfg": {}, "n": 1})
        self.assertEqual(cls.get("cfg"), {})

    def test_all_empty_containers(self):
        _, cls = self._load("e", {"d": {}, "l": [], "n": None})
        self.assertEqual(cls.get("d"), {})
        self.assertEqual(cls.get("l"), [])
        self.assertIsNone(cls.get("n"))

    def test_top_level_list(self):
        _, cls = self._load("top", [1, 2, 3])
        self.assertEqual(cls.get(), [1, 2, 3])
        self.assertEqual(cls.get("1"), 2)

    def test_top_level_scalar(self):
        _, cls = self._load("s", "hi")
        self.assertEqual(cls.get(), "hi")

    def test_empty_root_dict(self):
        _, cls = self._load("empty", {})
        self.assertEqual(cls.get(), {})

    def test_keyword_and_numeric_keys_deep(self):
        src, cls = self._load("d", {"outer": {"class": 1, "1x": 2, "return": 3}})
        self.assertIn("f_class", src)
        self.assertIn("f_return", src)
        # Data is still reachable under its real key.
        self.assertEqual(cls.get("outer.class"), 1)

    def test_unicode_keys(self):
        _, cls = self._load("u", {"标题": {"内容": "值"}})
        self.assertEqual(cls.get("标题.内容"), "值")

    def test_all_unicode_keys_get_distinct_attributes(self):
        # Regression: stripping non-ASCII collapsed every CJK key to the same
        # placeholder, emitting cls(field_=..., field_=...) -> SyntaxError.
        value = {"标题": "a", "正文": "b", "作者": "c"}
        src, cls = self._load("doc", value)
        compile(src, "<test>", "exec")
        self.assertEqual(cls.get("正文"), "b")
        obj = cls.load()
        self.assertEqual([obj.标题, obj.正文, obj.作者], ["a", "b", "c"])

    def test_unicode_class_names(self):
        src, cls = self._load("搜索结果", {"作者": {"姓名": "李雷"}})
        self.assertIn("作者", self._class_names(src))
        self.assertEqual(cls.load().作者.姓名, "李雷")

    def test_non_latin_scripts(self):
        for key in ["日本語", "ключ", "한국어"]:
            _, cls = self._load("k", {key: {"inner": 1}})
            self.assertEqual(cls.get(f"{key}.inner"), 1, f"for {key}")

    def test_keys_normalizing_to_same_attribute_are_deduped(self):
        # "a b", "a-b" and "a.b" all sanitize to a_b; duplicates would make
        # _from_dict a repeated-keyword SyntaxError.
        src, cls = self._load("x", {"a b": 1, "a-b": 2, "a.b": 3})
        compile(src, "<test>", "exec")
        attrs = [
            line.split(":")[0].strip()
            for line in src.splitlines()
            if "JSON key" in line
        ]
        self.assertEqual(len(attrs), len(set(attrs)))
        # Every original value is still reachable.
        self.assertEqual(sorted(vars(cls.load()).values()), [1, 2, 3])

    def test_unicode_key_with_separators(self):
        _, cls = self._load("k", {"搜索 结果": 1, "错误-码": 0})
        self.assertEqual(cls.get("搜索 结果"), 1)
        self.assertEqual(cls.get("错误-码"), 0)

    def test_punctuation_only_key(self):
        # Such keys get an f_ placeholder attribute and the code still
        # compiles. They are not addressable by path (the dots split into
        # empty segments) — same limitation as any key containing a dot.
        src, cls = self._load("x", {"...": 1, "???": 2})
        compile(src, "<test>", "exec")
        self.assertEqual(cls.get("???"), 2)
        self.assertEqual(cls.get()["..."], 1)

    def test_inner_key_does_not_steal_outer_class_name(self):
        src, cls = self._load("x", {"meta": {"meta": {"v": 1}}})
        names = self._class_names(src)
        self.assertEqual(len(names), len(set(names)), "duplicate class names")
        self.assertEqual(cls.load().meta.meta.v, 1)

    def test_sibling_keys_same_name_deduped(self):
        src, cls = self._load("x", {"a": {"cfg": {"v": 1}}, "b": {"cfg": {"v": 2}}})
        names = self._class_names(src)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(cls.get("b.cfg.v"), 2)

    def test_dotted_key_in_data_is_a_known_limitation(self):
        # A literal dot in a key cannot be addressed by dotted path; load()
        # or a parent get() is the escape hatch.
        _, cls = self._load("x", {"a.b": 1, "a": {"b": 2}})
        self.assertEqual(cls.get("a.b"), 2)  # parses as a -> b
        self.assertEqual(cls.get()["a.b"], 1)  # literal key still reachable



    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "out"
        self.addCleanup(self._tmp.cleanup)

    def run_serialize(self, data, **kwargs):
        return serialize(data, self.out, SerializeConfig(**kwargs))

    def test_sdk_not_generated_by_default(self):
        big = {"article": "甲" * 5000, "code": 0}
        res = self.run_serialize({"doc": big}, max_value_size=200)
        self.assertEqual(res.sdk_files, {})
        self.assertIsNone(res.items[0].sdk_path)

    def test_sdk_generated_when_enabled(self):
        big = {"article": "甲" * 5000, "code": 0}
        res = self.run_serialize({"doc": big}, max_value_size=200, generate_sdk=True)
        self.assertIn("doc", res.sdk_files)
        sdk_path = res.sdk_files["doc"]
        self.assertTrue(sdk_path.exists())
        self.assertTrue(sdk_path.name.endswith(".py"))

    def test_sdk_file_next_to_json(self):
        big = {"article": "甲" * 5000, "code": 0}
        res = self.run_serialize({"doc": big}, max_value_size=200, generate_sdk=True)
        json_path = res.files["doc"]
        sdk_path = res.sdk_files["doc"]
        self.assertEqual(json_path.parent, sdk_path.parent)
        self.assertEqual(sdk_path.stem, json_path.stem + "_sdk")

    def test_overview_mentions_sdk_path(self):
        big = {"article": "甲" * 5000, "code": 0}
        res = self.run_serialize({"doc": big}, max_value_size=200, generate_sdk=True)
        self.assertIn(str(res.sdk_files["doc"]), res.overview_text)

    def test_overview_uses_plain_notice_without_sdk(self):
        big = {"article": "甲" * 5000, "code": 0}
        res = self.run_serialize({"doc": big}, max_value_size=200, generate_sdk=False)
        self.assertIn("full content at", res.overview_text)
        # The SDK notice must not appear: no _sdk.py path, no class mention.
        self.assertNotIn("_sdk.py", res.overview_text)
        self.assertNotIn("class", res.overview_text)

    def test_inlined_values_get_no_sdk(self):
        res = self.run_serialize({"small": {"a": 1}}, generate_sdk=True)
        self.assertEqual(res.sdk_files, {})
        self.assertIsNone(res.items[0].sdk_path)

    def test_sdk_file_is_importable_and_works(self):
        value = {"title": "hello", "count": 42, "tags": ["a", "b"]}
        big = {k: v if k != "title" else "甲" * 5000 for k, v in value.items()}
        big["title"] = "甲" * 5000
        res = self.run_serialize({"doc": big}, max_value_size=200, generate_sdk=True)

        sdk_path = res.sdk_files["doc"]
        mod_name = f"_test_sdk_{id(self)}"
        spec = importlib.util.spec_from_file_location(mod_name, sdk_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        root_class_name = res.items[0].sdk_class
        cls = getattr(mod, root_class_name)
        self.assertEqual(cls.get("count"), 42)

    def test_multiple_folded_keys_each_get_sdk(self):
        big = {"s": "x" * 5000}
        res = self.run_serialize(
            {"a": big, "b": big}, max_value_size=200, generate_sdk=True
        )
        self.assertIn("a", res.sdk_files)
        self.assertIn("b", res.sdk_files)
        self.assertEqual(len(res.sdk_files), 2)

    def test_chinese_messages_sdk_notice(self):
        from progressive_serializer import CHINESE

        big = {"article": "甲" * 5000, "code": 0}
        res = self.run_serialize(
            {"doc": big}, max_value_size=200, generate_sdk=True, messages=CHINESE
        )
        self.assertIn("按需读取", res.overview_text)

    def test_sdk_disabled_with_generate_sdk_false(self):
        big = {"article": "甲" * 5000}
        res = self.run_serialize({"doc": big}, max_value_size=200, generate_sdk=False)
        py_files = list(self.out.glob("*.py"))
        self.assertEqual(py_files, [])


if __name__ == "__main__":
    unittest.main()
