import contextlib
import io
import os
import tempfile
import unittest

from kaeris import cli


class TestBrokenTargetJson(unittest.TestCase):
    def test_one_message_with_file_and_position_exit_2(self):
        with tempfile.TemporaryDirectory() as t:
            with open(os.path.join(t, "en.json"), "w", encoding="utf-8") as f:
                f.write('{"a": "Hello"}')
            broken = os.path.join(t, "de.json")
            with open(broken, "w", encoding="utf-8") as f:
                f.write('{"a" "Hallo"}')
            args = cli.build_parser().parse_args(
                ["check", "--source", os.path.join(t, "en.json"), "--langs", "de", "--out", t])
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                code = cli.cmd_check(args)
            text = buf.getvalue()
            self.assertEqual(code, 2)
            self.assertIn(broken, text)
            self.assertIn("line 1 column", text)
            self.assertNotIn("not found", text)
            self.assertNotIn("missing", text)


if __name__ == "__main__":
    unittest.main()
