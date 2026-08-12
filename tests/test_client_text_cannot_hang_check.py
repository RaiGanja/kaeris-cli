"""`kaeris check` не должен захлёбываться на файле клиента — пункт 5 плана безопасности
разбора файлов (репозиторий бэкенда, `docs/security-file-parsing-2026-08-07.md`).

ЧТО ОХРАНЯЕМ. `check` работает офлайн, на машине клиента и в его CI. Файл, который считается
минутами, — это красный билд без объяснения и «ваш инструмент повесил нам конвейер».
Детекторы здесь те же, что на сервере (паритет-корпус их сверяет), поэтому и уязвимость
у них общая: медленно становится не от РАЗМЕРА файла, а от его ФОРМЫ.

КАКОЙ ЦЕНОЙ УЗНАЛИ (12.08.2026). `_icu_arms` искала метку ветки регуляркой по растущему
префиксу (`re.search(..., body[:s])`) — то есть перечитывала блок заново на каждую ветку.
Настоящая команда на файле с ОДНИМ ICU-блоком:

    41 КБ ICU-веток   → 8.18 с
    41 КБ обычного текста → 0.12 с

Рост квадратичный: 85 КБ — уже 35 с. Это тот самый признак из 12-го правила доктрины —
контроль и ловушка обязаны отличаться, и здесь они отличались в 68 раз.

ПОЧЕМУ ЧЕРЕЗ ПОДПРОЦЕСС. Правило 3: тест функции ≠ тест пути. Замер импортированной
функции не заметил бы, что цикл вокруг неё вызывается на каждый язык и каждую строку.
Меряем ровно то, что запускает клиент: `python -m kaeris check`.
"""
import json
import os
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 40 КБ — обычный размер живого файла локали, не рекорд.
SIZE = 40_000
# Потолок на весь запуск команды, включая старт интерпретатора (~0.3 с). Честный прогон
# укладывается в десятые доли секунды; сломанный тратил восемь.
CEILING = 4.0
# Ловушка не должна быть радикально дороже честного текста той же длины.
RATIO = 8.0


def _icu_select(n: int) -> str:
    """Тысячи веток в ОДНОМ блоке — форма, которая стоила 8 секунд."""
    arms = max(1, n // 11)
    return "{n, select, " + " ".join(f"c{i} {{x{i}}}" for i in range(arms)) + "}"


def _icu_plural(n: int) -> str:
    arms = max(1, n // 14)
    return "{n, plural, " + " ".join("=%d {# item}" % i for i in range(arms)) + "}"


def _icu_nested(n: int) -> str:
    """Вложенность веток. Тысяча уровней — это 21 КБ текста и переполнение стека: команда
    отвечала трейсбеком `RecursionError` вместо вердикта. Заметить это временем НЕЛЬЗЯ —
    падение происходит быстро; поэтому ниже проверяется ещё и то, ЧЕМ команда ответила."""
    inner = "{n, plural, one {#} other {#}}"
    while len(inner) < n:
        inner = "{g, select, a {" + inner + "} other {x}}"
    return inner


def _braces(n: int) -> str:
    return "{" * (n // 2) + "}" * (n // 2)


def _placeholders(n: int) -> str:
    return ("{name} %s {{v}} %1$s %(x)d ${y} " * (n // 38))[:n]


def _one_word(n: int) -> str:
    return "a" * n


def _prose(n: int) -> str:
    """КОНТРОЛЬ."""
    return ("Hello world, this is a normal string. " * (n // 38))[:n]


TRAPS = {
    "ICU: тысячи веток в одном блоке": _icu_select,
    "ICU: plural с числовыми ветками": _icu_plural,
    "ICU: глубокая вложенность": _icu_nested,
    "скобки без текста": _braces,
    "плейсхолдеры сплошняком": _placeholders,
    "одно слово без пробелов": _one_word,
}


def _run_check(tmp_path, value: str) -> tuple[float, str]:
    """(секунды, вывод) настоящей команды. Исходник И перевод — иначе детекторы сравнения не
    работают вовсе: без `--langs` команда печатала отказ за 0.04 с, и мы мерили СБОЙ."""
    body = json.dumps({"k": value})
    (tmp_path / "en.json").write_text(body, encoding="utf-8")
    (tmp_path / "de.json").write_text(body, encoding="utf-8")
    t0 = time.perf_counter()
    r = subprocess.run(
        [sys.executable, "-m", "kaeris", "check", "--source", str(tmp_path / "en.json"),
         "--langs", "de", "--out", str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    return time.perf_counter() - t0, (r.stdout or "") + (r.stderr or "")


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    """Честный файл той же длины — точка отсчёта. Если и он медленный, сравнивать нечего."""
    d = tmp_path_factory.mktemp("control")
    t, out = _run_check(d, _prose(SIZE))
    assert t < CEILING, f"даже обычный текст считается {t:.2f} c — измеритель или машина не в порядке"
    assert "Traceback" not in out, f"команда падает уже на обычном тексте:\n{out[-500:]}"
    return t


@pytest.mark.parametrize("label", sorted(TRAPS))
def test_check_does_not_choke_on_shaped_input(tmp_path, baseline, label):
    t, out = _run_check(tmp_path, TRAPS[label](SIZE))
    # Падение стека происходит БЫСТРО: смотреть только на секундомер — значит пропустить его.
    assert "Traceback" not in out, (
        f"«{label}»: команда ответила трейсбеком вместо вердикта:\n{out[-600:]}")
    assert t < CEILING, (
        f"«{label}»: {t:.2f} c на {SIZE // 1000} КБ (потолок {CEILING} c). "
        f"Тот же объём обычного текста — {baseline:.2f} c")
    assert t < baseline * RATIO, (
        f"«{label}»: {t:.2f} c против {baseline:.2f} c на обычном тексте той же длины "
        f"(×{t / baseline:.1f}) — время зависит от ФОРМЫ файла, а не от размера")


def test_the_clock_would_see_a_slow_run(tmp_path, baseline):
    """КАНАРЕЙКА. Измеритель обязан видеть разницу там, где она есть: файл в двадцать раз
    длиннее обязан считаться заметно дольше. Если все замеры дают одно число — мы меряем
    не работу детекторов, а старт интерпретатора (так и вышло 12.08 с `check` без --langs)."""
    big, _ = _run_check(tmp_path, _prose(SIZE * 20))
    assert big > baseline * 1.5, (
        f"файл ×20 считается за то же время ({big:.2f} c против {baseline:.2f} c) — "
        f"замер не зависит от содержимого, и остальные числа ничего не доказывают")
    assert big < CEILING * 5, f"20-кратный честный файл считается {big:.2f} c — это уже само по себе дефект"
