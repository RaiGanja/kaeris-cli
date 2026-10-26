"""`kaeris check --json > out.json` on Windows wrote an empty file and a traceback (замер 0.2, R01).

PowerShell 5.1, cmd, Git Bash and a windows-latest runner give Python a cp1251/cp1252 stdout.
The report holds "→" (and the translations hold anything at all), so the print raised
UnicodeEncodeError: CI got zero bytes and exit 1 for the wrong reason. JSON can say every
character in ASCII, so that is what it says when the console cannot take UTF-8.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("encoding", ["cp1251", "cp1252"])
def test_the_report_reaches_the_file_whole(tmp_path, encoding):
    (tmp_path / "en.json").write_text(json.dumps({"price": "$15k", "hi": "Привет {name}"}),
                                      encoding="utf-8")
    (tmp_path / "de.json").write_text(json.dumps({"price": "15.000 €", "hi": "日本"},
                                                 ensure_ascii=False), encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": encoding, "PYTHONUTF8": "0",
           "PYTHONPATH": ROOT, "KAERIS_NO_UPDATE_CHECK": "1"}
    p = subprocess.run([sys.executable, "-m", "kaeris", "check", "--source",
                        str(tmp_path / "en.json"), "--langs", "de", "--json"],
                       capture_output=True, env=env, cwd=str(tmp_path))
    assert b"Traceback" not in p.stderr, p.stderr.decode(encoding, "replace")
    assert p.returncode == 1
    report = json.loads(p.stdout.decode("ascii"))
    assert report["ok"] is False
    assert any(i["key"] == "hi" for i in report["placeholder_issues"])
