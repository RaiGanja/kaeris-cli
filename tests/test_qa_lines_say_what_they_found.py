"""Translation QA printed every finding as a lost placeholder (client report 11; замер 0.2 R32).

ja "twice → 2倍" came out as "1 translation(s) dropped a placeholder — your app may break:
[ja] a: missing number 2 appears in the translation but not the source". Nothing was dropped
and nothing would break; the header and the "missing" prefix were glued onto any finding.
"""
import io
import types
import unittest.mock as mock

from kaeris import cli as K


def _qa(warnings):
    class _C:
        def preview(self, job):
            return {"_warnings": warnings}

    err = io.StringIO()
    args = types.SimpleNamespace(verify=False, back_lang="en")
    with mock.patch("sys.stderr", err):
        K._show_qa(_C(), "j", args, "en.json")
    return err.getvalue()


def test_a_number_finding_is_not_called_a_lost_placeholder():
    out = _qa({"ja": {"a": ["number 2 appears in the translation but not the source"]}})
    assert "dropped a placeholder" not in out
    assert "missing number" not in out
    assert "[ja] a: number 2 appears in the translation but not the source" in out


def test_a_lost_placeholder_still_reads_as_one():
    out = _qa({"de": {"greet": ["{name}"]}})
    assert "[de] greet: missing {name}" in out
