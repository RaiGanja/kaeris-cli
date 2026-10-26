"""`kaeris check` did not read strings inside JSON arrays (замер 0.2, R17).

A dropped {name} and $ turned into EUR inside `budgets` passed green in CI, while the site's
/check flagged them: the CLI compared the whole list as one non-string value and skipped it.
"""
import json

from kaeris import check as chk
from kaeris import cli as K

EN = {"budgets": ["Under $15k", "Hello {name}"], "title": "Price: $15k"}
DE = {"budgets": ["Unter 15.000 EUR", "Hallo"], "title": "Preis: 15.000 EUR"}


def _result(en=EN, de=DE):
    return chk.check_locales(en, ["de"], lambda lang: de)


def test_a_placeholder_dropped_inside_an_array_fails():
    r = _result()
    assert {"lang": "de", "key": "budgets.1", "missing": ["{name}"], "added": []} \
        in r["placeholder_issues"]
    assert r["ok"] is False


def test_a_currency_swap_inside_an_array_is_found():
    keys = {f["key"] for f in _result()["faults"]}
    assert "budgets.0" in keys and "title" in keys


def test_a_missing_array_item_is_a_missing_key():
    r = _result(de={"budgets": ["Unter 15.000 $"], "title": "Preis: 15.000 $"})
    assert "budgets.1" in r["missing"]["de"]


def test_the_command_reports_it(tmp_path, capsys):
    (tmp_path / "en.json").write_text(json.dumps(EN), encoding="utf-8")
    (tmp_path / "de.json").write_text(json.dumps(DE), encoding="utf-8")
    code = K.main(["check", "--source", str(tmp_path / "en.json"), "--langs", "de", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1
    assert any(i["key"] == "budgets.1" for i in out["placeholder_issues"])
