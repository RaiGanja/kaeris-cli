"""Files the CLI writes keep their line endings: LF for a new file, CRLF only where the file
already had CRLF.

A live run on Windows (27.10.2026) found CRLF where the source was LF. Reproduced with this
tree: kaeris-tm.json came out 12 of 12 lines CRLF, and by the same code path kaeris.lock,
kaeris.json, --receipt and verify.json — write_atomic opened its temp file in text mode
without `newline`, so Python turned every "\\n" into os.linesep. The --only-new merge of a
JSON/ARB locale file (incremental.dump_json) and the SARIF report did the same. For a user on
Windows that is a whole-file diff in git on the next run.

On Linux and macOS os.linesep is "\\n", so these tests can only fail on Windows.
"""
import json

from kaeris import cli as K
from kaeris import incremental as inc
from kaeris import sarif


def _raw(p):
    return p.read_bytes()


def test_a_new_text_file_is_written_with_lf(tmp_path):
    p = tmp_path / "kaeris-tm.json"
    K.write_atomic(p, json.dumps({"a": {"de": "b"}}, indent=2) + "\n")
    assert b"\r\n" not in _raw(p), _raw(p)
    assert _raw(p).count(b"\n") == 5


def test_an_existing_crlf_file_stays_crlf(tmp_path):
    p = tmp_path / "kaeris.json"
    p.write_bytes(b'{\r\n  "langs": ["de"]\r\n}\r\n')
    K.write_atomic(p, '{\n  "langs": ["de", "fr"]\n}\n')
    assert _raw(p) == b'{\r\n  "langs": ["de", "fr"]\r\n}\r\n', _raw(p)


def test_an_existing_lf_file_stays_lf(tmp_path):
    p = tmp_path / "kaeris.lock"
    p.write_bytes(b"{\n}\n")
    K.write_atomic(p, '{\n  "a": 1\n}\n')
    assert b"\r" not in _raw(p)


def test_binary_writes_are_byte_exact(tmp_path):
    """Translated files come from the server already shaped in the source's endings."""
    p = tmp_path / "de.po"
    data = b'msgid "a"\r\nmsgstr "b\\r"\n'
    K.write_atomic(p, data, binary=True)
    assert _raw(p) == data


def test_only_new_merge_of_a_json_locale_is_lf(tmp_path):
    p = tmp_path / "de.json"
    inc.dump_json({"a": "b", "c": {"d": "e"}}, str(p))
    assert b"\r\n" not in _raw(p), _raw(p)
    assert json.loads(_raw(p)) == {"a": "b", "c": {"d": "e"}}


def test_only_new_merge_keeps_a_crlf_locale_crlf(tmp_path):
    p = tmp_path / "de.json"
    p.write_bytes(b'{\r\n  "a": "x"\r\n}\r\n')
    inc.dump_json({"a": "b"}, str(p))
    assert _raw(p) == b'{\r\n  "a": "b"\r\n}\r\n', _raw(p)


def test_the_sarif_report_is_lf(tmp_path):
    p = tmp_path / "kaeris.sarif"
    sarif.write(str(p), {"version": "2.1.0", "runs": []})
    assert b"\r\n" not in _raw(p), _raw(p)
