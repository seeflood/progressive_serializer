import json
import tempfile
import unittest
from pathlib import Path

from progressive_serializer import Folded, SerializeConfig, fold, serialize, serialize_value


class FoldHookTest(unittest.TestCase):
    def test_annotation_only_preview_does_not_persist_or_generate_sdk(self):
        data = {"items": []}
        config = SerializeConfig(
            fold_hook=lambda path, value: (
                Folded(value, "nothing omitted", "annotation", elided=False)
                if not path else None
            ),
            generate_sdk=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            text = serialize_value(data, path, config)
            self.assertIn("nothing omitted", text)
            self.assertNotIn("full content", text)
            self.assertFalse(path.exists())
            self.assertFalse(path.with_name("result_sdk.py").exists())
            result = serialize({"report": data}, directory, config)
            self.assertEqual(result.files, {})
            self.assertEqual(result.sdk_files, {})

    def test_paths_and_final_preview_precede_default_rules(self):
        paths = []

        def hook(path, value):
            paths.append(path)
            if path == ("items",):
                return Folded(value[1:], "first item hidden", "custom")
            return None

        data = {"items": list(range(30))}
        tree, stats = fold(data, SerializeConfig(fold_hook=hook))
        self.assertTrue(stats.folded)
        self.assertEqual(tree["items"].preview, list(range(1, 30)))
        self.assertEqual(paths, [(), ("items",)])
        paths.clear()
        fold({"rows": [{"id": 1}]}, SerializeConfig(fold_hook=hook))
        self.assertIn(("rows", 0, "id"), paths)

    def test_small_values_run_hook_and_persist_original(self):
        data = {"items": [1, 2]}
        config = SerializeConfig(
            fold_hook=lambda path, value: (
                Folded([2], "success hidden", "custom") if path == ("items",) else None
            ), generate_sdk=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            text = serialize_value(data, path, config)
            self.assertIn("success hidden", text)
            self.assertEqual(json.loads(path.read_text()), data)
            self.assertTrue(path.with_name("result_sdk.py").exists())
            result = serialize({"report": data}, directory, config)
            self.assertIn("success hidden", result.overview_text)
            self.assertEqual(json.loads(result.files["report"].read_text()), data)

    def test_noop_hook_preserves_small_values_and_does_not_spill(self):
        data = {"long_but_in_budget": "x" * 300}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            text = serialize_value(
                data, path, SerializeConfig(fold_hook=lambda path, value: None),
            )
            self.assertEqual(text, json.dumps(data, ensure_ascii=False, indent=2))
            self.assertFalse(path.exists())

    def test_invalid_return_and_exceptions_propagate(self):
        with self.assertRaisesRegex(TypeError, "fold_hook"):
            fold({}, SerializeConfig(fold_hook=lambda path, value: {}))

        def broken(path, value):
            raise RuntimeError("hook failure")

        with self.assertRaisesRegex(RuntimeError, "hook failure"):
            fold({}, SerializeConfig(fold_hook=broken))

    def test_final_preview_survives_budget_escalation(self):
        data = {"items": list(range(100))}
        config = SerializeConfig(
            max_value_size=10,
            fold_hook=lambda path, value: (
                Folded(value, "all important", "custom") if not path else None
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            text = serialize_value(data, Path(directory) / "data.json", config)
            self.assertIn("99", text)
            self.assertIn("all important", text)
