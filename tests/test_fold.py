import json
import re
import unittest

from progressive_serializer import SerializeConfig, fold, jsonc_dumps
from progressive_serializer.fold import ELLIPSIS, Folded


def tree(value, **kwargs):
    folded, _ = fold(value, SerializeConfig(**kwargs))
    return folded


def stats(value, **kwargs):
    _, st = fold(value, SerializeConfig(**kwargs))
    return st


class TestStringFolding(unittest.TestCase):
    def test_short_string_survives(self):
        self.assertEqual(tree("hello", max_string_len=10), "hello")

    def test_at_threshold_survives(self):
        self.assertEqual(tree("a" * 10, max_string_len=10), "a" * 10)

    def test_over_threshold_folds(self):
        node = tree("a" * 11, max_string_len=10)
        self.assertIsInstance(node, Folded)
        self.assertEqual(node.kind, "string")
        self.assertEqual(node.preview, "...")
        self.assertIn("11", node.note)

    def test_nested_string_folds(self):
        node = tree({"a": {"b": "x" * 50}}, max_string_len=10)
        self.assertIsInstance(node["a"]["b"], Folded)


class TestBinaryFolding(unittest.TestCase):
    def test_bytes_always_fold(self):
        node = tree(b"ab")
        self.assertIsInstance(node, Folded)
        self.assertEqual(node.kind, "binary")
        self.assertIn("2 bytes", node.note)

    def test_bytearray_and_memoryview(self):
        for value in (bytearray(b"abc"), memoryview(b"abcd")):
            self.assertEqual(tree(value).kind, "binary")

    def test_fold_binary_disabled_base64s(self):
        # Short enough to survive the string rule -> plain base64 text.
        self.assertEqual(tree(b"ab", fold_binary=False), "YWI=")

    def test_fold_binary_disabled_still_folds_when_long(self):
        node = tree(b"a" * 1000, fold_binary=False, max_string_len=10)
        self.assertEqual(node.kind, "string")


class TestListFolding(unittest.TestCase):
    def test_short_list_survives(self):
        self.assertEqual(tree([1, 2, 3], max_list_items=3), [1, 2, 3])

    def test_long_list_folds_to_preview_items(self):
        node = tree(list(range(100)), max_list_items=10, list_preview_items=2)
        self.assertIsInstance(node, Folded)
        self.assertEqual(node.kind, "list")
        # Trailing ELLIPSIS marks that more items exist.
        self.assertEqual(node.preview, [0, 1, ELLIPSIS])
        self.assertIn("100 items total", node.note)
        self.assertIn("showing 2", node.note)

    def test_tuple_treated_as_list(self):
        self.assertEqual(tree((1, 2), max_list_items=5), [1, 2])

    def test_kept_items_are_still_folded(self):
        node = tree([{"s": "x" * 50}] * 20, max_list_items=1, max_string_len=10)
        self.assertIsInstance(node.preview[0]["s"], Folded)


class TestObjectFolding(unittest.TestCase):
    def test_narrow_object_survives(self):
        self.assertEqual(tree({"a": 1}, max_object_keys=5), {"a": 1})

    def test_rule_disabled_by_default(self):
        # max_object_keys is opt-in: without it, a 500-key object keeps every
        # field, because dropping fields costs the agent its navigation info.
        data = {f"k{i}": i for i in range(500)}
        node = tree(data)
        self.assertEqual(node, data)
        self.assertNotIn(ELLIPSIS, node)

    def test_explicit_none_disables_rule(self):
        data = {f"k{i}": i for i in range(50)}
        self.assertEqual(tree(data, max_object_keys=None), data)

    def test_wide_object_keeps_leading_keys(self):
        data = {f"k{i}": i for i in range(10)}
        node = tree(data, max_object_keys=3)
        self.assertEqual(node.kind, "object")
        self.assertEqual(list(node.preview), ["k0", "k1", "k2", ELLIPSIS])
        self.assertEqual(node.preview[ELLIPSIS], ELLIPSIS)
        self.assertIn("7 omitted", node.note)

    def test_non_string_keys_coerced(self):
        # True is not included: True == 1 in Python, so it would collide.
        node = tree({1: "a", None: "b", 2.5: "c", False: "d"}, max_object_keys=9)
        self.assertEqual(set(node), {"1", "null", "2.5", "false"})


class TestTightened(unittest.TestCase):
    """Escalation tightens opted-in rules; it never enables new ones."""

    def test_disabled_object_rule_stays_disabled(self):
        cfg = SerializeConfig()
        self.assertIsNone(cfg.max_object_keys)
        for _ in range(5):
            cfg = cfg.tightened()
            self.assertIsNone(cfg.max_object_keys)

    def test_enabled_object_rule_steps_down(self):
        cfg = SerializeConfig(max_object_keys=20)
        self.assertEqual(cfg.tightened().max_object_keys, 10)
        self.assertEqual(cfg.tightened().tightened().max_object_keys, 5)

    def test_enabled_object_rule_floors_at_one(self):
        cfg = SerializeConfig(max_object_keys=2)
        for _ in range(5):
            cfg = cfg.tightened()
        self.assertEqual(cfg.max_object_keys, 1)

    def test_depth_halves_and_floors_at_one(self):
        # Halving, not -1: convergence must not depend on where max_depth
        # started, or a deep default would outlast escalate_rounds.
        cfg = SerializeConfig()
        self.assertEqual(cfg.max_depth, 10)
        steps = []
        for _ in range(4):
            cfg = cfg.tightened()
            steps.append(cfg.max_depth)
        self.assertEqual(steps, [5, 2, 1, 1])

    def test_deep_value_converges_within_default_rounds(self):
        # The reason depth halves: three rounds must be enough to bite.
        cfg = SerializeConfig()
        for _ in range(3):
            cfg = cfg.tightened()
        self.assertLessEqual(cfg.max_depth, 2)


class TestDepthFolding(unittest.TestCase):
    def test_deep_subtree_folds(self):
        node = tree({"a": {"b": {"c": 1}}}, max_depth=2)
        self.assertIsInstance(node["a"]["b"], Folded)
        self.assertEqual(node["a"]["b"].kind, "depth")
        self.assertEqual(node["a"]["b"].preview, {ELLIPSIS: ELLIPSIS})

    def test_deep_list_folds_to_ellipsis(self):
        node = tree({"a": {"b": [1, 2]}}, max_depth=2)
        self.assertEqual(node["a"]["b"].preview, [ELLIPSIS])

    def test_deep_empty_container_claims_nothing_hidden(self):
        node = tree({"a": {"b": {}, "c": []}}, max_depth=2)
        self.assertEqual(node["a"]["b"].preview, {})
        self.assertEqual(node["a"]["c"].preview, [])

    def test_scalars_at_depth_survive(self):
        self.assertEqual(tree({"a": {"b": 1}}, max_depth=2), {"a": {"b": 1}})

    def test_default_depth_keeps_ten_levels(self):
        # Pins the default: a 10-level chain survives untouched, the 11th folds.
        def chain(levels):
            node = {"leaf": 1}
            for _ in range(levels - 1):
                node = {"child": node}
            return node

        self.assertEqual(tree(chain(10)), chain(10))

        node = tree(chain(11))
        for _ in range(9):
            node = node["child"]
        self.assertIsInstance(node["child"], Folded)
        self.assertEqual(node["child"].kind, "depth")


class TestCoercion(unittest.TestCase):
    def test_unknown_type_without_default_raises(self):
        with self.assertRaises(TypeError) as ctx:
            tree(object())
        self.assertIn("default", str(ctx.exception))

    def test_default_hook_applied(self):
        class Point:
            def __init__(self):
                self.x = 1

        node = tree(Point(), default=lambda o: o.__dict__)
        self.assertEqual(node, {"x": 1})

    def test_default_result_is_walked_again(self):
        class Wrapper:
            def __init__(self, inner):
                self.inner = inner

        node = tree(
            Wrapper(Wrapper("x" * 50)),
            default=lambda o: o.__dict__,
            max_string_len=10,
        )
        self.assertIsInstance(node["inner"]["inner"], Folded)

    def test_endless_coercion_chain_raises(self):
        class Endless:
            pass

        with self.assertRaises(TypeError) as ctx:
            tree(Endless(), default=lambda o: Endless())
        self.assertIn("kept returning", str(ctx.exception))


class TestEllipsisSentinel(unittest.TestCase):
    """The "..." marker makes elision visible in the structure, not just in a comment."""

    def test_unfolded_containers_have_no_sentinel(self):
        self.assertEqual(tree([1, 2], max_list_items=5), [1, 2])
        self.assertEqual(tree({"a": 1}, max_object_keys=5), {"a": 1})

    def test_existing_ellipsis_key_not_overwritten(self):
        data = {"...": "real value"}
        data.update({f"k{i}": i for i in range(10)})
        node = tree(data, max_object_keys=3)
        # "..." is the first key, so it is kept as real data and not clobbered.
        self.assertEqual(node.preview["..."], "real value")

    def test_sentinel_is_valid_json(self):
        node = tree(
            {"l": list(range(50)), "o": {f"k{i}": i for i in range(50)}},
            max_list_items=2,
            max_object_keys=2,
        )
        text = jsonc_dumps(node)
        json.loads(re.sub(r"\s*//.*$", "", text, flags=re.M))


class TestStats(unittest.TestCase):
    def test_no_folding_reported(self):
        st = stats({"a": 1})
        self.assertFalse(st.folded)
        self.assertEqual(st.notes, 0)

    def test_multiple_folds_counted(self):
        st = stats({"a": "x" * 50, "b": "y" * 50}, max_string_len=10)
        self.assertTrue(st.folded)
        self.assertEqual(st.notes, 2)


if __name__ == "__main__":
    unittest.main()
