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

    def test_load_field_works(self):
        value = {"article": "x" * 100, "summary": "short", "code": 0}
        with tempfile.TemporaryDirectory() as d:
            json_path = Path(d) / "doc.json"
            json_path.write_text(json.dumps(value), encoding="utf-8")
            src, root = generate_sdk("doc", value, json_path)
            sdk_path = Path(d) / "doc_sdk.py"
            sdk_path.write_text(src, encoding="utf-8")

            spec = importlib.util.spec_from_file_location("doc_sdk", sdk_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            cls = getattr(mod, root)

            self.assertEqual(cls.load_field("summary"), "short")
            self.assertEqual(cls.load_field("code"), 0)

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


class SdkSerializeIntegrationTests(unittest.TestCase):
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
        # SDK notice text should not appear.
        self.assertNotIn("load_field", res.overview_text)

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
        self.assertEqual(cls.load_field("count"), 42)

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
