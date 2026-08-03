# cli/tests/test_readme_flags_are_real.py
"""Every flag the README tells someone to type must exist in the parser.

Found by walking our own documentation as a stranger on 03.08.2026: the README's
`kaeris check --sarif kaeris.sarif` came back "unrecognized arguments" — the flag was
written, committed, advertised, and never published. Because the version number in the
repository matched the one on PyPI, there was no way for the reader to tell the docs
were ahead of the package.

This is the same shape as the assistant-prompt truthfulness tests in the backend, which
compare every number and flag in the prompt against the real parser in both directions.
A promise in documentation is a promise in the product.

Both directions matter: a flag in the README that the parser does not have sends the
reader into an error, and a flag the parser has that the README never mentions is a
feature nobody can find.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from kaeris.cli import build_parser  # noqa: E402

README = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "README.md"))

# argparse adds this to every subparser; it is not ours to document.
NOT_A_COMMAND = {"--help"}

# Long flags only: short ones are ambiguous against prose like "-5%".
FLAG_RE = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]+)")


def _parser_flags():
    """Every long option string the CLI accepts, top level and per subcommand."""
    flags = set()

    def collect(parser):
        for action in parser._actions:
            for opt in action.option_strings:
                if opt.startswith("--"):
                    flags.add(opt)
            # argparse keeps subparsers in a _SubParsersAction with .choices
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                for sub in choices.values():
                    collect(sub)

    collect(build_parser())
    return flags


def _readme_flags():
    with open(README, encoding="utf-8") as f:
        text = f.read()
    return {f for f in FLAG_RE.findall(text)} - NOT_A_COMMAND


class ReadmeAndParserAgree(unittest.TestCase):
    def test_every_readme_flag_exists_in_the_parser(self):
        missing = sorted(_readme_flags() - _parser_flags())
        self.assertEqual(
            missing, [],
            "README documents flags the CLI does not accept — a reader who types them "
            f"gets 'unrecognized arguments': {missing}",
        )

    def test_every_parser_flag_is_documented(self):
        """A flag nobody can discover was not worth writing. If one is deliberately
        internal, list it here with the reason rather than leaving the gap silent."""
        undocumented_by_design = {
            "--api-url",  # documented as the KAERIS_API_URL environment variable instead
            "--version",  # universal convention, not worth a README line
            "--config",   # documented in prose as `--config PATH`, caught by the regex anyway
        }
        missing = sorted(
            _parser_flags() - _readme_flags() - undocumented_by_design - NOT_A_COMMAND
        )
        self.assertEqual(
            missing, [],
            f"CLI accepts flags the README never mentions: {missing}",
        )


if __name__ == "__main__":
    unittest.main()
