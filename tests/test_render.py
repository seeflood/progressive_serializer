import unittest

from progressive_serializer import ItemView, OverviewContext, StringTemplateRenderer
from progressive_serializer.render import _fence_for


def item(key="k", block='{\n  "a": 1\n}', folded=False, path=None):
    return ItemView(
        key=key,
        json_block=block,
        folded=folded,
        full_path=path,
        original_size=len(block),
        rendered_size=len(block),
    )


class TestDefaultRenderer(unittest.TestCase):
    def setUp(self):
        self.renderer = StringTemplateRenderer()

    def render(self, items, out_dir="/tmp/out"):
        return self.renderer.render(OverviewContext(out_dir=out_dir, items=items))

    def test_unfolded_item_has_no_notice(self):
        out = self.render([item()])
        self.assertEqual(out, '- k\n\n```json\n{\n  "a": 1\n}\n```\n')

    def test_folded_item_mentions_full_path(self):
        out = self.render([item(folded=True, path="/tmp/out/k.json")])
        self.assertIn("were folded automatically", out)
        self.assertIn("`/tmp/out/k.json`", out)

    def test_folded_without_path_omits_notice(self):
        out = self.render([item(folded=True, path=None)])
        self.assertNotIn("folded automatically", out)

    def test_items_separated_by_blank_line(self):
        out = self.render([item(key="a"), item(key="b")])
        self.assertIn("```\n\n- b", out)

    def test_ends_with_single_newline(self):
        out = self.render([item()])
        self.assertTrue(out.endswith("```\n"))
        self.assertFalse(out.endswith("\n\n"))

    def test_empty_items(self):
        self.assertEqual(self.render([]), "\n")

    def test_dollar_sign_in_json_is_not_substituted(self):
        # Template.substitute does not rescan inserted values.
        out = self.render([item(block='{"a": "$key ${foo}"}')])
        self.assertIn('"$key ${foo}"', out)

    def test_custom_templates(self):
        renderer = StringTemplateRenderer(
            item_template="## ${key} (${original_size}B)\n${json_block}",
            header="# Overview\n\n",
            footer="\n\nend",
            separator="\n---\n",
        )
        out = renderer.render(
            OverviewContext(out_dir="/o", items=[item(key="a"), item(key="b")])
        )
        self.assertTrue(out.startswith("# Overview\n\n## a "))
        self.assertIn("\n---\n## b ", out)
        self.assertTrue(out.endswith("end\n"))

    def test_custom_notice_can_use_out_dir_and_key(self):
        renderer = StringTemplateRenderer(folded_notice="see ${out_dir} for ${key}\n")
        out = renderer.render(
            OverviewContext(
                out_dir="/o", items=[item(folded=True, path="/o/k.json")]
            )
        )
        self.assertIn("see /o for k", out)


class TestCustomRendererProtocol(unittest.TestCase):
    def test_any_object_with_render_works(self):
        class Minimal:
            def render(self, ctx):
                return f"{len(ctx.items)} items in {ctx.out_dir}"

        out = Minimal().render(OverviewContext(out_dir="/o", items=[item()]))
        self.assertEqual(out, "1 items in /o")


class TestFence(unittest.TestCase):
    def test_default_fence(self):
        self.assertEqual(_fence_for('{"a": 1}'), "```")

    def test_fence_grows_past_embedded_backticks(self):
        self.assertEqual(_fence_for('{"a": "```"}'), "````")
        self.assertEqual(_fence_for('{"a": "`````"}'), "``````")

    def test_rendered_block_is_not_broken_by_backticks(self):
        out = StringTemplateRenderer().render(
            OverviewContext(out_dir="/o", items=[item(block='{"a": "``` hi"}')])
        )
        self.assertIn("````json", out)
        self.assertTrue(out.rstrip("\n").endswith("````"))


if __name__ == "__main__":
    unittest.main()
