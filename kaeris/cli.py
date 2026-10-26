"""KAERIS i18n command-line interface."""

import argparse
import re
import glob
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sys
import time

from . import __version__
from .client import KaerisClient, KaerisError, KaerisInterrupted, DEFAULT_API
from .encoding import read_text, UnknownEncoding
from . import incremental as inc
from .incremental import TooDeep
from . import check as chk
from . import formats as fm
from . import xcstrings as xc
from . import tm as tmem
from . import detectors as det
from . import digits as dg
from . import fixes as fx

CONFIG_FILENAME = "kaeris.json"

# Generic defaults written by `kaeris init` (and used to fill in gaps left by presets).
DEFAULT_CONFIG = {
    "source": "locales/en.json",
    "source_lang": "en",
    "langs": ["es", "fr", "de"],
    "keep": [],
    "context": "",
    "tone": "neutral",
    "icu": False,
    "only_new": False,
    "out": "locales",
    "format": "auto",
}

# `kaeris init --preset NAME` overrides on top of DEFAULT_CONFIG.
PRESET_OVERRIDES = {
    "i18next": {"source": "locales/en/translation.json", "out": "locales"},
    "react-i18next": {"source": "locales/en/translation.json", "out": "locales"},
    "next-intl": {"source": "messages/en.json", "icu": True, "out": "messages"},
    "react-intl": {"icu": True},
    "vue-i18n": {"source": "src/locales/en.json", "out": "src/locales"},
    "flutter-arb": {"source": "lib/l10n/app_en.arb", "format": "arb", "out": "lib/l10n"},
}

PRESET_NOTES = {
    "i18next": "i18next/react-i18next stores strings per namespace (locales/<lang>/<namespace>.json) — "
               "adjust 'source'/'out' per namespace if you have more than one.",
    "react-i18next": "i18next/react-i18next stores strings per namespace (locales/<lang>/<namespace>.json) — "
                     "adjust 'source'/'out' per namespace if you have more than one.",
    "next-intl": "next-intl messages use ICU MessageFormat (plurals/select) — icu is enabled.",
    "react-intl": "react-intl uses ICU MessageFormat — icu is enabled; adjust 'source' to wherever "
                  "your extracted en.json lives (e.g. lang/en.json).",
    "vue-i18n": "adjust 'source'/'out' if your vue-i18n locale files live elsewhere.",
    "flutter-arb": "kaeris writes one .arb per language (app_es.arb, app_fr.arb, ...) — rename to "
                   "match your l10n.yaml if needed.",
}


def _c(text, code):
    if os.environ.get("NO_COLOR") or not sys.stderr.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def info(msg):  print(_c("→", "36"), msg, file=sys.stderr)
def ok(msg):    print(_c("✓", "32"), msg, file=sys.stderr)
def warn(msg):  print(_c("!", "33"), msg, file=sys.stderr)
def err(msg):   print(_c("✗", "31"), msg, file=sys.stderr)


# ── Monthly volume ────────────────────────────────────────────────────────────
# Percentages of the paid monthly volume at which the remaining-quota line stops being an
# aside. The same two steps colour the badge on the site and trigger the warning email — the
# server's main.QUOTA_NOTICE_PCT is the source, and a backend test fails if these drift from
# it. Before 04.08.2026 the CLI showed the monthly volume nowhere at all: a customer running
# translations from CI first heard about their quota when a run was refused.
QUOTA_WARN_PCT = 80
QUOTA_CRIT_PCT = 95


def _fmt_chars(n):
    """1_234_567 -> '1.2M', 45_000 -> '45k'. Always floors: rounding 29,786,587 up to '30M'
    reads as 'nothing spent yet', which is the opposite of what the number is for."""
    n = max(0, int(n))
    if n >= 1_000_000:
        return f"{n // 100_000 / 10:g}M"
    if n >= 1_000:
        return f"{n // 1_000}k"
    return str(n)


def _quota_line(info_json):
    """(text, level) for a key's monthly volume, or None if this key has no monthly volume
    (no key at all, or Lifetime/BYOK which is uncapped). level is 'ok'/'warn'/'crit'."""
    cap = info_json.get("month_cap") or 0
    if not cap:
        return None
    used = int(info_json.get("month_used") or 0)
    left = int(info_json.get("month_remaining", max(0, cap - used)))
    pct = used * 100 / cap
    level = "crit" if pct >= QUOTA_CRIT_PCT else "warn" if pct >= QUOTA_WARN_PCT else "ok"
    text = (f"Monthly volume: {_fmt_chars(used)} of {_fmt_chars(cap)} used · "
            f"{_fmt_chars(left)} left · resets {info_json.get('month_resets', '')}".rstrip(" ·"))
    return text, level


# The receipt carries the tier id the server keys limits by; people read the plan's name.
_PLAN_NAMES = {"premium": "Pro", "teamapi": "Team/API", "byok": "Lifetime", "team": "Scale",
               "free": "Free key", "anonymous": "Free"}


def _fmt_receipt(rec):
    """Two lines a human reads, plus the things worth interrupting them for.

    The plan and the spend come back only to the job's owner (the server splits the receipt
    by edit_token). When they are absent — reading a shared job, or an older client that did
    not keep the token — say so. Printing "0 charged" for a number we were not given is a
    lie in the one place a customer checks what a run cost them."""
    langs = rec.get("languages") or {}
    chars = rec.get("characters") or {}
    settings = rec.get("settings") or {}
    mine = "characters" in rec
    lines = []
    delivered, failed = langs.get("delivered") or [], langs.get("failed") or []
    # The model that did the work, not the one configured: with the primary down the fallback
    # translated and this line still named the primary (R29).
    served = [m for m in (rec.get("models_served") or []) if m]
    lines.append(
        f"Run: {' + '.join(served) if served else rec.get('model', '?')} · "
        f"{_PLAN_NAMES.get(rec.get('plan'), rec.get('plan')) or 'plan hidden'} · "
        f"{len(delivered)}/{len(langs.get('requested') or [])} languages · "
        f"{rec.get('strings', 0)} strings · {rec.get('seconds', 0)}s")
    if not mine:
        lines.append("     spend and settings are the job owner's — not shown here")
        return lines, failed
    bits = [f"{_fmt_chars(chars.get('metered', 0))} charged"]
    if chars.get("reused_not_charged"):
        bits.append(f"{_fmt_chars(chars['reused_not_charged'])} reused free")
    if chars.get("refunded"):
        bits.append(f"{_fmt_chars(chars['refunded'])} refunded")
    on = [k for k in ("icu", "verify") if settings.get(k)]
    if settings.get("tone") and settings["tone"] != "neutral":
        on.append(settings["tone"])
    if settings.get("context"):
        on.append("context")
    lines.append("     " + " · ".join(bits) + (("  ·  " + ", ".join(on)) if on else ""))
    return lines, failed


# Glossary terms the run's receipts reported, by whether the file used them. A run over
# several files used to warn after each one — four warnings in a customer re-run
# (26.10.2026), each naming terms that another file of the same run did use.
# A receipt only knows what its job was SENT: an --only-new run sends the changed keys, and a job
# that died sends no receipt at all — so after a cut-off run four terms that were in the source
# were called absent (a customer run, 26.10.2026). "sources" holds the run's whole source files,
# read here, and is the last word on absence.
_GLOSSARY_SEEN = {"found": set(), "absent": set(), "sources": []}


def _source_text(path):
    """The text a glossary term can occur in: the values of a JSON/ARB file, else the file."""
    try:
        text = read_text(path)
    except (OSError, UnknownEncoding):
        return ""
    if path.lower().endswith((".json", ".arb")):
        try:
            return "\n".join(str(v) for v in inc.flatten(json.loads(text)).values())
        except (ValueError, TooDeep, AttributeError):
            pass
    return text


def _say_absent_glossary():
    sources = [t.lower() for t in _GLOSSARY_SEEN["sources"]]
    absent = sorted(t for t in _GLOSSARY_SEEN["absent"] - _GLOSSARY_SEEN["found"]
                    if not any(t.lower() in src for src in sources))
    if absent:
        warn(f"{len(absent)} glossary term(s) never appear in your source, so nothing kept "
             f"them: {', '.join(absent[:5])}")


def _show_receipt(client, job):
    """Print what the run actually did. Best-effort, like the other after-the-fact lines: an
    older server has no /api/receipt and an unreachable one has nothing at all."""
    try:
        rec = client.receipt(job)
    except Exception:
        return None
    lines, failed = _fmt_receipt(rec)
    info(lines[0])
    print(lines[1], file=sys.stderr)
    # A glossary term that was never in the file was never going to be honoured. Collected
    # here and said once at the end of the run, about the whole run (_say_absent_glossary).
    for g in rec.get("glossary") or []:
        (_GLOSSARY_SEEN["found"] if g.get("found_in_source") else _GLOSSARY_SEEN["absent"]).add(g["term"])
    if failed:
        warn(f"not delivered: {', '.join(failed)}")
    return rec


def _show_quota(client):
    """Print the monthly volume after a run. Best-effort by design, exactly like
    _current_model: an older server has no month_* fields and an unreachable one has nothing.
    A courtesy line must never turn a delivered translation into a failure."""
    try:
        line = _quota_line(client.key_info())
    except Exception:
        return
    if not line:
        return
    text, level = line
    if level == "crit":
        warn(text + "  — nearly out; raise it at kaeris.dev/pricing.html")
    elif level == "warn":
        warn(text)
    else:
        info(text)


def cmd_quota(args):
    """Answer 'how much have I got left' BEFORE a big run, not after one."""
    client = _client(args)
    try:
        data = client.key_info()
    except KaerisError as e:
        err(str(e))
        return 2
    line = _quota_line(data)
    if not line:
        # Branch on whether a key was SUPPLIED, not on the tier name. The live server answers
        # tier "anonymous" with no key — a name this code did not expect — and the fallback
        # then told someone with no key at all about Lifetime/BYOK. Caught only by running it
        # against production; a stub returning "none" had looked fine.
        if not (args.key or os.environ.get("KAERIS_API_KEY")):
            info("No API key — the keyless demo has no monthly volume. "
                 "Get a free key at kaeris.dev/developer")
        elif (data.get("tier") or "") == "byok":
            info("Lifetime (BYOK) has no monthly volume cap — you pay OpenRouter directly "
                 "for tokens. The per-file character limit still applies.")
        else:
            info(f"Plan '{data.get('tier') or 'unknown'}' reports no monthly volume. "
                 "See kaeris.dev/pricing.html")
        return 0
    text, level = line
    (warn if level in ("warn", "crit") else ok)(text)
    return 0


def _glossary(args):
    return [t.strip() for t in (getattr(args, "keep", "") or "").split(",") if t.strip()]


# The API truncates the app context to this; sending more only wastes the upload.
APP_CONTEXT_LIMIT = 300


def _app_context(args):
    return (getattr(args, "context", "") or "").strip()[:APP_CONTEXT_LIMIT]


def _service_keys_opt(args):
    """Sent only when asked for, so a client stub or an older server sees the call it knows."""
    return {"translate_service_keys": True} if getattr(args, "translate_service_keys", False) else {}


def _tone(args):
    tone = getattr(args, "tone", "neutral")
    return "" if tone == "neutral" else tone


def _client(args):
    return KaerisClient(
        api_url=args.api_url,
        api_key=args.key or os.environ.get("KAERIS_API_KEY"),
        openrouter_key=args.openrouter_key or os.environ.get("KAERIS_OPENROUTER_KEY"),
    )


def _config_comment(extra=None):
    base = ("KAERIS config — `kaeris translate` (no args) reads this file for defaults. "
            "Precedence: CLI flags > this file > built-in defaults. "
            "Fields: source (base-language file, path or glob — or a list of paths/globs for "
            "multi-namespace projects), source_lang (base language code, e.g. 'en'; used to "
            "detect locales/<lang>/<namespace>.json-style layouts so each target lands next to "
            "its namespace), langs (target language codes), "
            "keep (glossary terms to never translate — `kaeris check` fails a target that "
            "dropped one), context (one line about what the app is, so the model picks the "
            "right sense of ambiguous strings), tone (neutral/formal/casual), "
            "icu (true if your strings use ICU MessageFormat plurals/select), "
            "only_new (reproducible incremental: translate only new/missing/edited keys — and "
            "re-translate everything if tone/icu/glossary/context changed; JSON and ARB), lock (path to the "
            "incremental lock file; default kaeris.lock next to the source), out (output "
            "directory), format (informational; auto-detected from the source file extension).")
    return base + " " + extra if extra else base


def _load_config(args):
    """Load kaeris.json (explicit --config PATH, else ./kaeris.json if present).

    Returns (config_dict, path_or_None). Comment keys ("//", "_..." ) are ignored.
    Raises KaerisError on a missing --config path or invalid JSON.
    """
    path = getattr(args, "config", None) or (CONFIG_FILENAME if os.path.isfile(CONFIG_FILENAME) else None)
    if not path:
        return {}, None
    if not os.path.isfile(path):
        raise KaerisError(f"Config file not found: {path}")
    try:
        raw = json.loads(read_text(path))
    except json.JSONDecodeError as e:
        raise KaerisError(f"Invalid JSON in {path}: {e}")
    if not isinstance(raw, dict):
        raise KaerisError(f"Invalid config in {path}: expected a JSON object")
    cfg = {k: v for k, v in raw.items() if not k.startswith("//") and not k.startswith("_")}
    return cfg, path


class _BadTarget(Exception):
    pass


def _hint(e):
    """Кодировка — не синтаксис. Без этой подсказки человек идёт искать сломанную скобку в
    файле, который цел: он просто сохранён не в UTF-8, и чинится это одной командой."""
    return " — save it as UTF-8 (or UTF-16 with a byte-order mark)" if isinstance(
        e, UnknownEncoding) else ""


def _resolve_source(path):
    """Expand a glob pattern in a source path to a single file, if any glob chars are present."""
    if not any(ch in path for ch in "*?["):
        return path
    matches = sorted(glob.glob(path, recursive=True))
    if not matches:
        return None
    if len(matches) > 1:
        warn(f"Multiple files match '{path}' — using {matches[0]} (others: {', '.join(matches[1:])})")
    return matches[0]


def _resolve_sources(raw):
    """Resolve a `translate` source spec — a single path/glob, or a list of them —
    into a sorted, deduped list of existing files. Glob patterns (containing
    *?[ ) are expanded via glob.glob; plain paths are used literally."""
    entries = raw if isinstance(raw, list) else [raw]
    matches = set()
    for entry in entries:
        entry = str(entry).strip()
        if not entry:
            continue
        if any(ch in entry for ch in "*?["):
            matches.update(glob.glob(entry, recursive=True))
        else:
            matches.add(entry)
    return sorted(p for p in matches if os.path.isfile(p))


def _target_path(src_path, lang, out_dir, source_lang):
    """Compute the output path for `lang`'s translation of `src_path`, mirroring
    common locale-file layouts so multi-namespace projects land correctly:

    - Flat: locales/en.json -> locales/<lang>.json (today's behavior).
    - Namespace/dir: locales/en/common.json -> locales/<lang>/common.json.
    - Prefixed file: app_en.arb -> app_de.arb (Flutter gen-l10n), messages_en.properties ->
      messages_de.properties (Java bundles).
    - Fallback (layout not detected): <out_dir or src dir>/<lang>/<basename>.
    """
    stem, ext = os.path.splitext(os.path.basename(src_path))
    src_dir = os.path.dirname(src_path)

    parent = os.path.basename(src_dir)
    if ext.lower() == ".xml" and (parent == "values" or parent.startswith("values-")):
        res_dir = out_dir or os.path.dirname(src_dir)
        return os.path.join(res_dir, f"values-{lang}", os.path.basename(src_path))

    if stem == source_lang:
        d = out_dir if out_dir else src_dir
        return os.path.join(d, f"{lang}{ext}")

    if os.altsep:
        src_dir = src_dir.replace(os.altsep, os.sep)
    segs = src_dir.split(os.sep) if src_dir else []
    last_idx = None
    for i, seg in enumerate(segs):
        if seg == source_lang:
            last_idx = i
    if last_idx is not None:
        new_segs = list(segs)
        new_segs[last_idx] = lang
        if out_dir:
            sub = new_segs[last_idx:]
            return os.path.join(out_dir, *sub, os.path.basename(src_path))
        # os.sep.join (not os.path.join) so a leading "" from an absolute
        # src_dir's split keeps the leading separator instead of being dropped.
        new_dir = os.sep.join(new_segs)
        return os.path.join(new_dir, os.path.basename(src_path))

    # Prefixed single-file convention: the stem is <prefix>_<source_lang>, so the target keeps
    # the prefix and swaps the language — app_en.arb -> app_de.arb (Flutter), messages_en ->
    # messages_de. Without this the Flutter/Java layouts fell through to the nested fallback and
    # `kaeris translate`/`check` wrote/looked in the wrong place.
    if stem.endswith("_" + source_lang) and len(stem) > len(source_lang) + 1:
        prefix = stem[: -(len(source_lang) + 1)]
        d = out_dir if out_dir else src_dir
        return os.path.join(d, f"{prefix}_{lang}{ext}")

    if not _target_path.warned:
        warn(f"Could not detect a known locale-file layout for '{src_path}' "
             f"(source_lang='{source_lang}') — nesting output by language instead")
        _target_path.warned = True
    base_dir = out_dir or src_dir
    return os.path.join(base_dir, lang, os.path.basename(src_path))


_target_path.warned = False


LANG_CASES = ("keep", "lower")


def _existing_case(path, lang):
    """`path`, with each part that names `lang` spelled the way it already is on disk. The
    server names its files after the language code as the API spells it (`zh-Hant.json`,
    `devlog/zh-Hant/`); a project that already has `zh-hant.json` got a second file beside it
    on Linux, and its own file renamed on Windows (client report, 0.2.31). Only the parts that
    carry the language are looked at, and an exact match always wins, so two files that differ
    only in case are never confused and nobody's other folders change."""
    drive, rest = os.path.splitdrive(os.path.normpath(path))
    parts = rest.split(os.sep)
    want = lang.lower()
    for i, part in enumerate(parts):
        if not part or want not in part.lower():
            continue
        parent = drive + (os.sep.join(parts[:i]) or (os.sep if rest.startswith(os.sep) else "."))
        try:
            names = os.listdir(parent)
        except OSError:
            break                      # nothing below a folder that does not exist yet
        if part in names:
            continue
        same = [n for n in names if n.lower() == part.lower()]
        if len(same) == 1:
            parts[i] = same[0]
    return drive + os.sep.join(parts)


def _out_path(src_path, lang, out_dir, source_lang, lang_case="keep"):
    """Where `lang`'s translation of `src_path` goes: _target_path, in the project's own case."""
    if lang_case == "lower":
        lang = lang.lower()
    return _existing_case(_target_path(src_path, lang, out_dir, source_lang), lang)


def _lproj_sibling(src_path, lang, source_lang, out_dir):
    """Xcode's own layout: en.lproj/Localizable.strings -> de.lproj/Localizable.strings."""
    folder = os.path.dirname(src_path)
    if os.path.basename(folder) != f"{source_lang}.lproj":
        return None
    return os.path.join(out_dir or os.path.dirname(folder), f"{lang}.lproj",
                        os.path.basename(src_path))


def cmd_init(args):
    if os.path.isfile(CONFIG_FILENAME) and not args.force:
        warn(f"{CONFIG_FILENAME} already exists — use --force to overwrite")
        return 1

    cfg = dict(DEFAULT_CONFIG)
    note = None
    if args.preset:
        cfg.update(PRESET_OVERRIDES[args.preset])
        note = PRESET_NOTES.get(args.preset)

    doc = {"//": _config_comment(note)}
    doc.update(cfg)
    write_atomic(CONFIG_FILENAME, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")

    suffix = f" (preset: {args.preset})" if args.preset else ""
    ok(f"Wrote {CONFIG_FILENAME}{suffix}")
    info("Edit 'langs' (and 'keep' for brand names), then just run: kaeris translate")
    return 0


def cmd_languages(args):
    langs = _client(args).languages()
    for code, name in sorted(langs.items(), key=lambda kv: kv[1]):
        print(f"  {code:5} {name}")
    return 0


def _unknown_langs(client, langs):
    """Targets whose language the server does not offer, found before any file is sent.

    Only the primary subtag is compared (pt-BR is fine when pt is offered), so a regional
    spelling the server accepts is never refused here. When the list cannot be fetched
    (offline, an older server) nothing is refused: the server still answers for itself."""
    try:
        known = client.languages()
    except Exception:
        return []
    if not isinstance(known, dict) or not known:
        return []
    bases = {str(c).replace("_", "-").split("-")[0].lower() for c in known}
    return [l for l in langs if re.split(r"[-_]", l)[0].lower() not in bases]


_XC_MARKER_RE = re.compile(r"((?:⟦[^⟦⟧]*⟧)+)")


def _pretty_key(text):
    """Make a String Catalog's synthetic sub-keys readable, wherever they appear.

    Keys inside a catalog are addressed as `%lld items⟦plural⟧` or `cart⟦sub:count⟧⟦plural⟧`,
    so a plural set and a device arm can be told apart from the plain string. That encoding is
    ours, not the developer's — printing it raw asks them to decode our bookkeeping to find the
    line of their file that is wrong.

    Applied to whole messages, not just the key field: some detectors put the key inside their
    own sentence, and a half-decoded output is worse than none."""
    if not text or "⟦" not in text:
        return text

    def _one(m):
        parts = [p for p in m.group(1).strip("⟦⟧").split("⟧⟦") if p]
        return " (" + ", ".join(p.replace(":", " ") for p in parts) + ")"

    return _XC_MARKER_RE.sub(_one, text)


def cmd_check(args):
    """The i18n firewall: compare source vs each target locale, no API call.
    Exits non-zero if anything is missing/broken so CI can gate a merge on it."""
    config, config_path = _load_config(args)
    if config_path and not args.json:
        info(f"Using config: {config_path}")

    raw_path = args.source or config.get("source")
    if not raw_path:
        err("No source file given (--source PATH, or 'source' in kaeris.json)")
        return 2
    path = _resolve_source(raw_path)
    if not path or not os.path.isfile(path):
        err(f"Source file not found: {path or raw_path}")
        return 2
    kind = fm.kind_of(path)
    if kind is None:
        err(f"kaeris check reads {fm.SUPPORTED_TEXT} (got {path})")
        return 2
    is_arb = kind == "arb"
    merged = kind in fm.MERGED
    json_like = kind in ("json", "arb")

    def _strip_arb_meta(obj):
        # ARB non-translatable metadata is top-level '@'-prefixed (@@locale, @key).
        # Drop it so it isn't flattened into spurious missing/extra keys.
        if isinstance(obj, dict):
            return {k: v for k, v in obj.items() if not str(k).startswith("@")}
        return obj

    if args.langs:
        langs = [l.strip() for l in args.langs.split(",") if l.strip()]
    else:
        langs = [str(l).strip() for l in (config.get("langs") or []) if str(l).strip()]
    if not langs:
        err("No target languages given (--langs es,fr,de, or 'langs' in kaeris.json)")
        return 2

    # None means "where `translate` writes by default": the source's own folder layout decides
    # (res/values -> res/values-<lang>, locales/en -> locales/<lang>). Defaulting to the
    # source's folder looked for res/values/values-de/strings.xml.
    out_dir = args.out or config.get("out") or None
    source_lang = config.get("source_lang") or "en"
    _target_path.warned = False  # warn at most once per check run

    def _resolve_target(lang):
        if args.pattern:
            return os.path.join(out_dir or os.path.dirname(os.path.abspath(path)),
                                args.pattern.format(lang=lang))
        found = _out_path(path, lang, out_dir, source_lang)
        if not os.path.isfile(found):
            # Xcode keeps one folder per language: en.lproj/Localizable.strings -> de.lproj/.
            sibling = _lproj_sibling(path, lang, source_lang, out_dir)
            if sibling and os.path.isfile(sibling):
                return sibling
        return found

    # One file holds the source AND every language (a String Catalog, a CSV with a column per
    # language): there is no per-language file to resolve — both sides come out of one parse.
    by_lang = None
    try:
        if merged:
            file_lang, by_lang = fm.read_merged(path)
            source_lang = config.get("source_lang") or file_lang
            source_obj = fm.find_lang(by_lang, source_lang) or {}
            if not source_obj:
                err(f"No strings for the source language '{source_lang}' in {path}")
                return 2
        elif json_like:
            source_obj = inc.load_json(path)
            if not isinstance(source_obj, dict):
                err(f"Invalid source file {path}: expected a JSON object")
                return 2
            if is_arb:
                source_obj = _strip_arb_meta(source_obj)
        else:
            source_obj = fm.read_one(path, "source")
            if not source_obj:
                err(f"No translatable strings found in {path}")
                return 2
    except (OSError, ValueError) as e:
        what = "String Catalog" if kind == "xcstrings" else "source file"
        err(f"Could not read {what} {path}: {e}{_hint(e)}")
        return 2
    if not args.json:
        for note in getattr(source_obj, "notes", []):
            warn(f"{path}: {note['msg']}")

    def load_target(lang):
        if merged:
            # Absent from the file is the same as an absent file: every key is missing, which
            # is exactly what a developer who added a language wants to be told.
            return fm.find_lang(by_lang, lang)
        target_path = _resolve_target(lang)
        if not os.path.isfile(target_path):
            return None
        if not json_like:
            try:
                obj = fm.read_one(target_path, "target", lang)
            except (OSError, ValueError) as e:
                raise _BadTarget(f"{lang}: could not parse {target_path}: {e}{_hint(e)}")
            if kind == "markdown" and len(obj) != len(source_obj):
                # A document has no keys: its blocks are paired by position, so a block added,
                # merged or lost anywhere shifts every one after it.
                obj.skip_pairwise = True
                obj.notes.append({"key": "", "severity": "error",
                                  "msg": f"{len(obj)} translatable blocks, the source has "
                                         f"{len(source_obj)}: a heading, paragraph, list item or "
                                         f"table cell was added, merged or lost"})
            return obj
        try:
            obj = inc.load_json(target_path)
        except (OSError, ValueError) as e:
            raise _BadTarget(f"{lang}: could not parse {target_path}: {e}{_hint(e)}")
        if not isinstance(obj, dict):
            raise _BadTarget(f"{lang}: {target_path} is not a JSON object")
        return _strip_arb_meta(obj) if is_arb else obj

    # "keep" is the documented key `kaeris init` writes and `translate` reads; check used to
    # read only "glossary", a key nothing ever generated — so the dropped-term detector was
    # silently off in CI for every real project. Flag wins, then either config key.
    if getattr(args, "glossary", None):
        glossary = [t.strip() for t in args.glossary.split(",") if t.strip()]
    else:
        glossary = config.get("keep") or config.get("glossary") or None
    try:
        result = chk.check_locales(source_obj, langs, load_target, glossary)
    except _BadTarget as e:
        err(str(e))
        return 2

    if getattr(args, "sarif", None):
        # Written even when nothing is wrong: upload-sarif fails a workflow if the file is
        # missing, and "the check passed" must not look like "the step is broken".
        from . import sarif as _sarif
        try:
            _sarif.write(args.sarif, _sarif.build(result, source=path,
                                                  target_for=_resolve_target,
                                                  root=os.getcwd()))
            if not args.json:
                info(f"SARIF report → {args.sarif}")
        except OSError as e:
            warn(f"Could not write SARIF report to {args.sarif}: {e}")

    faults = result.get("faults", [])
    warnings = result.get("warnings", [])
    n_missing = sum(len(v) for v in result["missing"].values())
    n_extra = sum(len(v) for v in result["extra"].values())
    n_ph = len(result["placeholder_issues"])
    n_faults = len(faults)
    n_warn = len(warnings)
    # RED (missing / placeholder mismatch / detector fault) already flips result["ok"].
    # --strict escalates the soft signals (extra/stale keys, YELLOW warnings) to a failure.
    fail = (not result["ok"]) or (args.strict and (n_extra or n_warn))

    if not args.json:
        for lang in langs:
            if lang in result["missing_files"]:
                err(f"{lang}: no translations in {path}" if merged
                    else f"{lang}: locale file not found ({_resolve_target(lang)})")
                continue
            missing = result["missing"].get(lang) or []
            extra = result["extra"].get(lang) or []
            issues = [i for i in result["placeholder_issues"] if i["lang"] == lang]
            lang_faults = [f for f in faults if f["lang"] == lang]
            lang_warns = [w for w in warnings if w["lang"] == lang]
            if not missing and not extra and not issues and not lang_faults and not lang_warns:
                ok(f"{lang}: complete & placeholder-safe")
                continue
            if missing:
                err(f"{lang}: {len(missing)} missing key(s): "
                    f"{', '.join(_pretty_key(k) for k in missing[:10])}"
                    + (" ..." if len(missing) > 10 else ""))
            if issues:
                err(f"{lang}: {len(issues)} placeholder mismatch(es):")
                for i in issues:
                    print(_pretty_key(f"    {i['key']}: missing {i['missing'] or '-'}, "
                                      f"added {i['added'] or '-'}"),
                          file=sys.stderr)
            if lang_faults:
                err(f"{lang}: {len(lang_faults)} translation fault(s):")
                for f in lang_faults:
                    print(_pretty_key(f"    {f['key']}: {f['msg']}" if f.get("key")
                                      else f"    {f['msg']}"), file=sys.stderr)
            if lang_warns:
                level = err if args.strict else warn
                level(f"{lang}: {len(lang_warns)} warning(s):")
                for w in lang_warns:
                    print(_pretty_key(f"    {w.get('key', '')}: {w['msg']}".rstrip()),
                          file=sys.stderr)
            if extra:
                level = err if args.strict else warn
                level(f"{lang}: {len(extra)} extra/stale key(s): "
                      f"{', '.join(_pretty_key(k) for k in extra[:10])}"
                      + (" ..." if len(extra) > 10 else ""))

        if fail:
            parts = []
            if n_missing:
                parts.append(f"{n_missing} missing key{'s' if n_missing != 1 else ''}")
            if n_ph:
                parts.append(f"{n_ph} placeholder mismatch{'es' if n_ph != 1 else ''}")
            if n_faults:
                parts.append(f"{n_faults} translation fault{'s' if n_faults != 1 else ''}")
            if args.strict and n_warn:
                parts.append(f"{n_warn} warning{'s' if n_warn != 1 else ''} (--strict)")
            if args.strict and n_extra:
                parts.append(f"{n_extra} extra key{'s' if n_extra != 1 else ''} (--strict)")
            err(", ".join(parts))
        elif n_warn:
            warn(f"{n_warn} warning(s) — non-blocking (use --strict to fail on them)")
        else:
            ok("all locales complete & placeholder-safe")

    if args.json:
        # A cp1251/cp1252 console (PowerShell 5.1, cmd, windows-latest) cannot encode "→" or
        # the translations themselves: the report raised and CI got an empty file (R01).
        utf8 = (sys.stdout.encoding or "").lower().replace("-", "") in ("utf8", "utf8sig")
        print(json.dumps(result, ensure_ascii=not utf8, indent=2))

    return 1 if fail else 0


def _progress_printer(label=""):
    """Progress lines for one job. With several jobs in flight their lines interleave, so each
    names its file (or language) — a bare "40%  ja" no longer says whose."""
    last = [-1]
    def show(status):
        total = status.get("total") or 0
        done = status.get("progress") or 0
        if total and done != last[0]:
            pct = int(done / total * 100)
            lang = status.get("current_lang") or ""
            where = f"{label}  {lang}" if label else lang
            print(_c("→", "36") + f" {pct:3}%  {where}", file=sys.stderr)
            last[0] = done
    return show


# How many jobs the CLI keeps in flight at once. The server runs each job with its own share
# of model calls and caps the total itself, so this is about not sitting idle, not about
# pushing the provider: a run over en.json and three pages sent them one after another and
# spent most of its 23-26 minutes waiting (client report, 0.2.31).
DEFAULT_JOBS = 4


def _jobs(args):
    try:
        return max(1, int(getattr(args, "jobs", None) or DEFAULT_JOBS))
    except (TypeError, ValueError):
        return DEFAULT_JOBS


# A job that died with a server restart is sent again this many times, after a growing pause:
# the server returned its quota when it failed it, so this is not a second charge.
RESTART_RETRIES = 2
RESTART_PAUSE = 20


def _run_job(client, fname, content, langs, args, reuse=None, label=""):
    """Submit one job, wait for it, fetch the archive: the part that only waits on the server,
    so it can run beside other jobs. Returns (job, status, members)."""
    for attempt in range(RESTART_RETRIES + 1):
        job = client.submit(fname, content, langs, _glossary(args), reuse=reuse,
                            verify=args.verify, back_lang=args.back_lang,
                            tone=_tone(args), icu=args.icu,
                            app_context=_app_context(args),
                            **_service_keys_opt(args))
        try:
            status = client.poll(job, on_progress=None if args.quiet else _progress_printer(label))
            break
        except KaerisInterrupted:
            if attempt == RESTART_RETRIES:
                raise
            pause = RESTART_PAUSE * (attempt + 1)
            warn(f"{label or fname}: the server restarted mid-run (nothing was charged) — "
                 f"sending it again in {pause}s ({attempt + 1}/{RESTART_RETRIES})")
            time.sleep(pause)
    return job, status, client.download(job)


def _outcome(future):
    """(value, None) or (None, KaerisError) — so a job that failed is reported in its turn."""
    try:
        return future.result(), None
    except KaerisError as e:
        return None, e


def _show_demo_notice(client):
    """One line when there is no key: translate does not refuse, it silently runs as the
    anonymous demo. The limits come from the server that enforces them, never from this file;
    if it cannot be asked, the line still says what mode this is, just without numbers."""
    limits = ""
    try:
        chars = client.key_info().get("char_limit")
        langs = client.config().get("max_langs_anon")
        if chars and langs:
            limits = f"{chars:,} chars per file, {langs} languages per run"
    except Exception:
        pass
    info(f"no key: free demo limits{' ' + limits if limits else ''}, "
         "get a key: kaeris.dev/developer")


def cmd_translate(args):
    config, config_path = _load_config(args)
    if config_path:
        info(f"Using config: {config_path}")

    raw = args.files if args.files else config.get("source")
    if not raw:
        err("No source file given (positional argument(s), or 'source' in kaeris.json)")
        return 1
    files = _resolve_sources(raw)
    if not files:
        err(f"No source file(s) found: {raw}")
        return 1

    if args.langs:
        langs = [l.strip() for l in args.langs.split(",") if l.strip()]
    else:
        langs = [str(l).strip() for l in (config.get("langs") or []) if str(l).strip()]
    if not langs:
        err("No target languages given (use --langs es,fr,de, or set 'langs' in kaeris.json)")
        return 1

    # Merge remaining options — precedence: explicit CLI flag > kaeris.json > built-in default.
    args.keep = args.keep if args.keep else ",".join(config.get("keep") or config.get("glossary") or [])
    args.context = args.context if args.context else (config.get("context") or "")
    if args.tone is None:
        cfg_tone = config.get("tone")
        args.tone = cfg_tone if cfg_tone in ("neutral", "formal", "casual") else "neutral"
    args.icu = args.icu if args.icu is not None else bool(config.get("icu", False))
    args.only_new = args.only_new if args.only_new is not None else bool(config.get("only_new", False))
    args.assume_current = getattr(args, "assume_current", False) or bool(config.get("assume_current", False))
    args.lock = args.lock or config.get("lock")
    args.out = args.out or config.get("out")
    args.source_lang = args.source_lang or config.get("source_lang") or "en"
    args.jobs = args.jobs or config.get("jobs")
    args.digits = args.digits or (config.get("digits") if config.get("digits") in dg.MODES
                                  else "native")
    args.lang_case = args.lang_case or (config.get("lang_case")
                                        if config.get("lang_case") in LANG_CASES else "keep")

    if args.out:
        os.makedirs(args.out, exist_ok=True)

    client = _client(args)
    unknown = _unknown_langs(client, langs)
    if unknown:
        err(f"Unknown language code: {', '.join(unknown)}. Run 'kaeris languages' for the list "
            "(codes like de, ja, pt-BR, zh-Hant).")
        return 1
    if not getattr(client, "api_key", "fake"):
        _show_demo_notice(client)
    _target_path.warned = False  # warn at most once per `translate` run, not per file/lang

    multi = len(files) > 1
    exit_code = 0
    args.verify_back = {}
    _GLOSSARY_SEEN["found"].clear()
    _GLOSSARY_SEEN["absent"].clear()
    _GLOSSARY_SEEN["sources"] = [_source_text(p) for p in files] if _glossary(args) else []
    # Whole-file jobs go to the server side by side (up to --jobs at once); what they wrote,
    # what QA found and what they cost is still reported file by file, in order. --only-new
    # files run in their turn — they already send one job per language, side by side.
    with ThreadPoolExecutor(max_workers=_jobs(args)) as pool:
        pending = {}
        for path in (files if multi else []):
            if not _incremental(path, args):
                try:
                    pending[path] = _start_one(client, pool, path, langs, args,
                                               label=os.path.basename(path) if multi else "")
                except KaerisError as e:
                    pending[path] = e
        for path in files:
            if multi:
                info(f"── {os.path.relpath(path)} ──")
            try:
                ticket = pending.get(path)
                if isinstance(ticket, KaerisError):
                    raise ticket
                code = (_finish_one(client, ticket, args) if ticket is not None
                        else _translate_one(client, path, langs, args))
            except KaerisError as e:
                err(str(e))
                code = 1
            exit_code = max(exit_code, code)
    vpath = _write_verify(args, files)
    if getattr(args, "apply_fixes", False):
        if not args.verify:
            warn("--apply-fixes needs --verify: the fixes come from the meaning check. "
                 "Nothing applied.")
        elif vpath:
            _apply_verify(vpath, glossary=_glossary(args))
    _say_absent_glossary()
    # What the run cost of the month, once per invocation rather than once per file — and here
    # rather than inside _translate_one so the incremental (--only-new) path, which returns
    # earlier, reports it too.
    _show_quota(client)
    return exit_code


def _write_verify(args, files):
    """One verify.json per run. It used to be written once per file and once per job, so a run
    over several Markdown pages kept only the last page's back-translations (client report 8)."""
    back = getattr(args, "verify_back", None)
    if not back:
        return None
    reviews = getattr(args, "verify_review", None) or {}
    targets = getattr(args, "verify_files", None) or {}
    if len(files) == 1:
        out_dir = args.out or os.path.dirname(os.path.abspath(files[0]))
    else:
        out_dir = args.out or os.getcwd()

    def one(f):
        # Back-translations keep their old place ({lang: {key: text}}) so a script that read
        # verify.json yesterday still reads it; what the check found sits beside them.
        b = dict(back[f])
        r = reviews.get(f) or {}
        if r:
            b["_review"] = {lang: v["lines"] for lang, v in r.items()}
            b["_summary"] = {name: sum(v["summary"][name] for v in r.values())
                             for name in ("lines", "to_review", "error", "review", "unchecked")}
            # The same counts per language, so a reader need not add up `_review` itself.
            b["_summary"]["by_lang"] = {lang: dict(v["summary"]) for lang, v in sorted(r.items())}
        if targets.get(f):
            # Relative to verify.json, so the project folder can move and `kaeris fix` still
            # finds each language's file.
            b["_files"] = {lang: os.path.relpath(p, out_dir).replace(os.sep, "/")
                           for lang, p in sorted(targets[f].items())}
        return b

    if len(files) == 1:
        body = one(files[0])
    else:
        body = {os.path.relpath(f).replace(os.sep, "/"): one(f) for f in back}
    vpath = os.path.join(out_dir, "verify.json")
    write_atomic(vpath, json.dumps(body, ensure_ascii=False, indent=2))
    ok(f"Back-translations (→ {args.back_lang}) and the lines to review written to {vpath}")
    return vpath


def _fix_records(done):
    """The machine-readable result of one verify.json section: `_applied` and `_skipped`, each
    [{"lang", "key", "how", "why"}].

    `how` is where an applied line came from: "fix" (the meaning check's corrected line) or
    "structure" (line breaks / emphasis put back); null for a skipped line. `why` is a short
    sentence: what was done, that it was already in the file, or why the line was left alone.
    Lines that were already fixed on an earlier run stay in `_applied`, so running `kaeris fix`
    twice leaves the same list."""
    applied, skipped = [], []
    for lang, key, _path, how, status, why in done:
        if status == fx.SKIPPED:
            skipped.append({"lang": lang, "key": key, "how": None, "why": why})
        else:
            said = ("already in the file" if status == fx.ALREADY
                    else "suggested fix" if how == "fix" else "line breaks / emphasis put back")
            applied.append({"lang": lang, "key": key, "how": how, "why": said})
    return applied, skipped


def _apply_verify(vpath, glossary=None, only=None, dry_run=False, as_json=False):
    """Write the fixes verify.json carries into the translated files (kaeris/fixes.py). Offline:
    nothing here talks to the server, and nothing is charged. Records what was done in
    verify.json itself (`_applied`, `_skipped` of each section) so nothing has to read the log.
    Returns the exit code."""
    try:
        body = json.loads(read_text(vpath))
    except (OSError, ValueError) as e:
        err(f"Cannot read {vpath}: {getattr(e, 'strerror', None) or e}")
        return 2
    before = json.dumps(body, ensure_ascii=False, indent=2)
    base = os.path.dirname(os.path.abspath(vpath))
    done, sections = [], []
    for name, section in fx.sections(body):
        if only and name and not any(os.path.relpath(o).replace(os.sep, "/") == name for o in only):
            continue
        rows = fx.plan(section.get("_review"), section.get("_files"), base, glossary)
        result = fx.apply(rows, dry_run=dry_run)
        done += result
        sections.append((name, section, result))
    report = {"verify": vpath.replace(os.sep, "/"), "dry_run": dry_run, "applied": [], "skipped": [],
              "counts": {"applied": 0, "already": 0, "skipped": 0}}
    for name, section, result in sections:
        applied, skipped = _fix_records(result)
        report["applied"] += applied
        report["skipped"] += skipped
        if name:
            report.setdefault("sections", {})[name] = {"applied": applied, "skipped": skipped}
        if not dry_run:
            section["_applied"], section["_skipped"] = applied, skipped
    if sections and not dry_run:
        text = json.dumps(body, ensure_ascii=False, indent=2)
        if text != before:
            write_atomic(vpath, text)
    applied = [r for r in done if r[4] == fx.APPLIED]
    already = [r for r in done if r[4] == fx.ALREADY]
    skipped = [r for r in done if r[4] == fx.SKIPPED]
    report["counts"] = {"applied": len(applied), "already": len(already), "skipped": len(skipped)}
    if as_json:
        utf8 = (sys.stdout.encoding or "").lower().replace("-", "") in ("utf8", "utf8sig")
        print(json.dumps(report, ensure_ascii=not utf8, indent=2))
    if not done:
        ok(f"Nothing to fix: {vpath} lists no lines to review")
        return 0
    by_fix = sum(1 for r in applied if r[3] == "fix")
    files = {r[2] for r in applied}
    if applied:
        verb = "Would apply" if dry_run else "Applied"
        ok(f"{verb} {len(applied)} fix(es) in {len(files)} file(s): {by_fix} suggested by "
           f"verify, {len(applied) - by_fix} line break / emphasis put back. Free, no network.")
        for lang, key, path, how, _s, _w in applied:
            what = "suggested fix" if how == "fix" else "line breaks / emphasis"
            print(f"    [{lang}] {key}: {what} → {os.path.relpath(path)}", file=sys.stderr)
    if already:
        info(f"{len(already)} fix(es) already in the files")
    if skipped:
        warn(f"{len(skipped)} line(s) not fixed:")
        for lang, key, _p, _h, _s, why in skipped[:30]:
            print(f"    [{lang}] {key}: {why}", file=sys.stderr)
        if len(skipped) > 30:
            print(f"    … and {len(skipped) - 30} more — all of them in {vpath}", file=sys.stderr)
    return 0


def cmd_fix(args):
    config, _ = _load_config(args)
    keep = args.keep if args.keep else ",".join(config.get("keep") or config.get("glossary") or [])
    glossary = [t.strip() for t in keep.split(",") if t.strip()] or None
    if not os.path.isfile(args.source):
        err(f"No verify file at {args.source} — run `kaeris translate … --verify` first")
        return 2
    return _apply_verify(args.source, glossary=glossary, only=args.files or None,
                         dry_run=args.dry_run, as_json=args.json)


def _tm_signature(args, client):
    """The settings a remembered translation was produced under. Same components the lock
    uses — a string translated formally, or under a different glossary or model, is a
    different answer and must not be served from memory."""
    return inc.settings_signature(tone=_tone(args), icu=args.icu, keep=_glossary(args),
                                  model=_current_model(client), app_context=_app_context(args))


def _tm_open(args, path):
    """(memory, file path) or (None, None) when the project memory does not apply.

    Only JSON and ARB: looking a string UP before a run needs the source flattened locally,
    and that is the only thing this dependency-free package can flatten. Recording for the
    other formats would grow a file that is written and never read — worse than not having
    one, because it looks like a feature."""
    if getattr(args, "no_tm", False):
        return None, None
    if not path.lower().endswith((".json", ".arb")):
        return None, None
    tm_path = tmem.default_path(path, getattr(args, "tm", None))
    return tmem.load(tm_path), tm_path


def _tm_reuse(memory, source_flat, langs, sig):
    """What the API can skip entirely. Handed over as its `reuse` map — strings it does not
    translate are strings it does not charge for."""
    if memory is None:
        return None, 0
    hits = tmem.lookup(memory, source_flat, langs, sig)
    n = sum(len(v) for v in hits.values())
    return (hits or None), n


def _tm_commit(memory, tm_path, source_flat, preview, sig):
    """Fold this run's results back into the project memory."""
    if memory is None or not preview:
        return
    translations = {k: v for k, v in preview.items()
                    if not k.startswith("_") and isinstance(v, dict)}
    added = tmem.record(memory, source_flat, translations, sig)
    if not added:
        return
    try:
        tmem.save(tm_path, memory, lambda p, body: write_atomic(p, body))
    except OSError as e:
        warn(f"could not write the project memory {tm_path}: {e}")
        return
    strings, pairs = tmem.count(memory)
    info(f"Project memory: +{added} · {strings} strings, {pairs} translations in "
         f"{os.path.basename(tm_path)}")


def _incremental(path, args):
    """True when this file goes through --only-new (JSON and ARB); other formats warn and are
    translated whole."""
    if not args.only_new:
        return False
    if path.lower().endswith((".json", ".arb")):
        return True
    ext = os.path.splitext(path)[1]
    warn(f"--only-new supports JSON/ARB only (zero-dependency CLI); "
         f"translating the whole {ext} file instead")
    return False


def _translate_one(client, path, langs, args):
    """Translate a single resolved source file. Returns 0 (clean), 3 (some
    languages fell back to source text) or 1 (a per-file error — the caller
    keeps going to the next file rather than aborting the whole run)."""
    if _incremental(path, args):
        return _translate_incremental(client, path, args.out, langs, args)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return _finish_one(client, _start_one(client, pool, path, langs, args), args)


def _start_one(client, pool, path, langs, args, label=""):
    """Everything up to the job being on the server: read the file, look it up in the project
    memory, submit. Returns a ticket for _finish_one; the waiting happens in `pool`."""
    fname = os.path.basename(path)
    display_out = args.out or os.path.dirname(os.path.abspath(path))
    os.makedirs(display_out, exist_ok=True)

    with open(path, "rb") as f:
        content = f.read()

    # Project memory: everything already translated under these settings, whatever key or
    # file it lived in last time. The API skips those and does not charge for them.
    memory, tm_path = _tm_open(args, path)
    sig = _tm_signature(args, client) if memory is not None else ""
    source_flat = {}
    if memory is not None:
        try:
            source_flat = inc.flatten(inc.load_json(path)) if fname.lower().endswith(
                (".json", ".arb")) else {}
        except (OSError, ValueError):
            source_flat = {}
    reuse, n_reused = _tm_reuse(memory, source_flat, langs, sig)
    if n_reused:
        info(f"Project memory: {n_reused} string/language pair(s) reused, not re-translated")

    info(f"Translating {fname} → {', '.join(langs)}")
    future = pool.submit(_run_job, client, fname, content, langs, args, reuse, label)
    return {"path": path, "future": future, "tm_path": tm_path, "sig": sig,
            "memory": memory is not None, "source_flat": source_flat}


def _finish_one(client, ticket, args):
    """Wait for the job a ticket stands for, then write, check and report it."""
    path = ticket["path"]
    result, error = _outcome(ticket["future"])
    if error is not None:
        raise error
    job, status, members = result
    by_lang = {}
    written = _write_members(members, path, args.out, args.source_lang,
                             digits=args.digits, lang_case=args.lang_case, by_lang=by_lang)
    # Where each language landed, for verify.json: `kaeris fix` writes the fixes there.
    args.__dict__.setdefault("verify_files", {})[path] = by_lang
    # Named from where the files actually went: the source folder was printed here even when
    # every file landed in work/<lang>/ (client report 9).
    dirs = {os.path.dirname(os.path.abspath(w)) for w in written}
    ok(f"Wrote {len(written)} file(s)" + (f" to {dirs.pop()}" if len(dirs) == 1 else ":"))
    for w in written:
        print(w)
    # Translation QA — placeholder loss + UI-overflow (free), plus back-translation if --verify
    data = _show_qa(client, job, args, path)
    if ticket["memory"]:
        # Read again rather than reuse what was loaded before the job: with several files in
        # flight and one --tm, another file may have saved its strings in the meantime.
        _tm_commit(tmem.load(ticket["tm_path"]), ticket["tm_path"],
                   ticket["source_flat"] or (data or {}).get("_source") or {},
                   data, ticket["sig"])
    rec = _show_receipt(client, job)
    if rec and getattr(args, "receipt", None):
        write_atomic(args.receipt, json.dumps(rec, ensure_ascii=False, indent=2))
        ok(f"Run receipt written to {args.receipt}")
    failed = status.get("failed_langs") or []
    if failed:
        warn(f"{len(failed)} language(s) fell back to source text (not translated): {', '.join(failed)}")
        return 3
    return 0


def _show_qa(client, job, args, path):
    """Print Translation QA (placeholder loss, UI-overflow) and, if requested, keep the
    verify-meaning back-translations for the run's verify.json. Best-effort — never fails
    the run."""
    try:
        data = client.preview(job)
    except Exception:
        return None
    warnings = data.get("_warnings") or {}
    qa = data.get("_qa") or {}
    back = data.get("_back") or {}
    meaning = data.get("_meaning") or {}
    review = data.get("_review") or {}

    ph = 0
    # Two headings for two kinds of finding (X2): a lost placeholder breaks the app, a moved
    # number or a localized price may only read wrong. Under one "your app may break" header a
    # correct "2倍" was told the build would fail (client report 11, N7). A bare token is a
    # lost one; a sentence is read out.
    for group, heading in (("break", "may break your app"),
                           ("meaning", "check the meaning before you ship")):
        lines = [(lang, k, [f for f in found if det.warning_group(f) == group])
                 for lang, kv in warnings.items() for k, found in kv.items()]
        lines = [line for line in lines if line[2]]
        if not lines:
            continue
        ph += len(lines)
        warn(f"{len(lines)} translation(s) {heading}:")
        # A line that breaks the app is worth its own line in the log, up to a screenful per
        # language; past the limit the rest is counted, never dropped. Five per language and
        # nothing said hid the eighth bn line, a stray * the page prints (0.2.32 re-run).
        limit = 20 if group == "break" else 5
        shown = {}
        for lang, k, found in lines:
            shown[lang] = shown.get(lang, 0) + 1
            if shown[lang] > limit:
                continue
            said = [f"missing {f}" if re.match(r"^[{%<$]", f) else f for f in found]
            print(f"    [{lang}] {k}: {'; '.join(said)}", file=sys.stderr)
        for lang, n in shown.items():
            if n > limit:
                where = "all of them in verify.json" if args.verify else "run with --verify to list them all"
                print(f"    … and {n - limit} more for [{lang}] — {where}", file=sys.stderr)

    over = sum(len(v.get("overflow", [])) for v in qa.values())
    if over:
        warn(f"{over} translation(s) may overflow your UI (grew much longer than source):")
        shown = 0
        for lang, v in qa.items():
            for o in v.get("overflow", []):
                if shown >= 6:
                    break
                grew = f"+{o['chars']} chars" if o.get("short") else f"+{o['pct']}%"
                print(f"    [{lang}] {o['key']}: {grew}  \"{o['tr'][:50]}\"", file=sys.stderr)
                shown += 1

    if args.verify:
        _show_meaning(meaning, review)
    if args.verify and back:
        sink = args.__dict__.setdefault("verify_back", {}).setdefault(path, {})
        for lang, kv in back.items():
            sink.setdefault(lang, {}).update(kv)
        lines = args.__dict__.setdefault("verify_review", {}).setdefault(path, {})
        for lang, r in review.items():
            lines[lang] = _review_entries(lang, r, meaning.get(lang) or {},
                                          warnings.get(lang) or {}, data.get("_source") or {},
                                          data.get(lang) or {})

    if not ph and not over:
        ok("Translation QA: no placeholder loss, no overflow risks")
    # Returned so the caller can fold the results into the project memory without a second
    # round trip for the same payload.
    return data


def _show_meaning(meaning, review):
    """What the meaning check found (U1), why, and the fix that passed the detectors (U4);
    then the per-file count of lines a person should read (U3)."""
    shown = 0
    for lang, kv in meaning.items():
        for k, f in kv.items():
            shown += 1
            if shown > 10:
                continue
            why = f" — {f['explain']}" if f.get("explain") else ""
            print(f"    ⚠ meaning: [{lang}] {k}: {f.get('issue', '')}{why}", file=sys.stderr)
            if f.get("fix"):
                print(f"      suggested fix: {f['fix']}", file=sys.stderr)
    if shown > 10:
        print(f"    … and {shown - 10} more in verify.json", file=sys.stderr)
    if not review:
        return
    total = sum(r.get("to_review", 0) for r in review.values())
    lines = sum(r.get("lines", 0) for r in review.values())
    errors = sum(len(r.get("error", [])) for r in review.values())
    unchecked = sum(len(r.get("unchecked", [])) for r in review.values())
    if total:
        warn(f"Verify: {total} lines to review of {lines} ({errors} likely wrong, "
             f"{total - errors} to check) — the list is in verify.json")
    else:
        ok(f"Verify: {lines} lines checked, none to review")
    if unchecked:
        warn(f"Verify: {unchecked} lines could not be checked for meaning (the checker did "
             "not answer) — read them yourself")


def _review_entries(lang, review, meaning, warnings, source=None, translations=None):
    """verify.json's `_review` for one language: every line that is not ok, with why — and the
    source and the translation it judged, so `kaeris fix` can check a fix and find the line
    without asking the server again."""
    out = {}
    source, translations = source or {}, translations or {}
    for status in ("error", "review", "unchecked"):
        for k in review.get(status, []):
            entry = {"status": status}
            if isinstance(source.get(k), str) and isinstance(translations.get(k), str):
                entry["source"], entry["translation"] = source[k], translations[k]
            f = meaning.get(k) or {}
            if f.get("issue"):
                entry["meaning"] = f["issue"]
            for field in ("explain", "fix"):
                if f.get(field):
                    entry[field] = f[field]
            if warnings.get(k):
                entry["warnings"] = warnings[k]
            out[k] = entry
    out_summary = {"lines": review.get("lines", 0), "to_review": review.get("to_review", 0),
                   "error": len(review.get("error", [])), "review": len(review.get("review", [])),
                   "unchecked": len(review.get("unchecked", []))}
    return {"lines": out, "summary": out_summary}


def _arb_meta_key(flat_key):
    """True if a flattened key belongs to ARB non-translatable metadata:
    its FIRST dotted segment starts with '@' (`@@locale`, `@foo`,
    `@foo.placeholders...`). These must never be sent to the model."""
    return flat_key.split(".", 1)[0].startswith("@")


def _current_model(client):
    """The exact model this key's tier translates with, recorded in the lock so a tier switch
    (before 01.08.2026 Free->Pro swapped the model; every tier has run the same one since,
    but a server-side model change still has to invalidate the lock) is caught as a settings
    change.

    Best-effort: an older server has no model_id and an unreachable one has nothing at all.
    Both report "" — settings_changed() treats an unknown model as "not changed", so the run
    proceeds exactly as before rather than failing or billing a needless full retranslate.
    """
    try:
        return client.key_info().get("model_id", "") or ""
    except Exception:
        return ""


def _translate_incremental(client, path, out_dir, langs, args):
    is_arb = path.lower().endswith(".arb")
    source_obj = inc.load_json(path)
    source_flat = inc.flatten(source_obj)
    memory, tm_path = _tm_open(args, path)
    sig = _tm_signature(args, client) if memory is not None else ""
    # One job per language here, so one receipt per language. --receipt used to write nothing
    # at all on this path: a documented flag that silently does nothing is worse than one that
    # does not exist.
    receipts = []
    if is_arb:
        # ARB metadata (@@locale, @key.placeholders...) is not translatable text —
        # drop it from detection and (via todo) from the submitted subset so it's
        # never shipped to the model or corrupted. It survives on merge untouched.
        source_flat = {k: v for k, v in source_flat.items() if not _arb_meta_key(k)}
    flat_style = inc.is_flat(source_obj)
    source_hashes = inc.hash_flat(source_flat)

    lock_path = args.lock or inc.default_lock_path(path)
    lock_raw = inc.load_lock(lock_path)
    # The lock is a SINGLE per-source record meaning "this source hash is already
    # propagated to ALL target languages". Snapshot it once so every language
    # detects against the same baseline — never against a lock a previous
    # language mutated mid-loop (that bug silently skipped later languages).
    detect_lock = inc.lock_keys(lock_raw)          # глобальный снимок (для совместимости)
    lang_locks = inc.lock_langs(lock_raw)           # состояние по каждому языку
    broken_by_lang: dict[str, set] = {}
    # Reproducibility: if the tone/ICU/glossary/context/model changed since the lock was written, an
    # unchanged source string would still hash-match and be kept — leaving a locale that mixes
    # old and new settings. Detect that and re-translate every key so the output stays consistent.
    cur_settings = inc.settings_signature(_tone(args), args.icu, _glossary(args),
                                          _current_model(client), _app_context(args))
    locked_settings = inc.lock_settings(lock_raw)
    settings_changed = inc.settings_changed(locked_settings, cur_settings)
    if settings_changed:
        changed_model = (locked_settings or {}).get("model", "") != cur_settings["model"] \
            and (locked_settings or {}).get("model", "")
        why = ("the model changed (tier switch)" if changed_model
               else "tone/ICU/glossary/context changed")
        info(f"Translation settings changed since the lock — {why}. Re-translating every key "
             "so the whole locale stays consistent")
    # Source keys NOT fully propagated to every language after this run — a key
    # is "broken" if any language it was due in failed to merge. Broken keys are
    # left stale-locked so the NEXT run retries the language(s) that failed.
    broken = set()
    any_work = False
    unverified_by_lang: dict[str, set] = {}

    # Every language is its own job, and they do not depend on each other: send them side by
    # side (up to --jobs), then merge and report in language order, exactly as before.
    work = []
    pool = ThreadPoolExecutor(max_workers=_jobs(args))
    for lang in langs:
        target_path = _out_path(path, lang, out_dir, args.source_lang,
                                getattr(args, "lang_case", "keep"))
        os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
        existing = inc.load_json(target_path) if os.path.isfile(target_path) else {}
        existing_flat = inc.flatten(existing) if existing else {}
        # Detect against THIS language's baseline: a run that touched only German must not
        # mark an edited string as done for French (see incremental.lock_keys).
        lang_baseline = inc.lock_keys(lock_raw, lang)
        # Keys already in the target that no lock has ever vouched for. Not translated, not
        # locked, and named below — see incremental.unverified_keys.
        if not settings_changed:
            unverified_by_lang[lang] = inc.unverified_keys(source_flat, existing_flat, lang_baseline)
        todo = (dict(source_flat) if settings_changed
                else inc.changed_or_missing_keys(source_flat, existing_flat, lang_baseline))
        if not todo:
            ok(f"{lang}: up to date ({len(existing_flat)} keys)")
            continue
        any_work = True
        changed = sum(1 for k in todo if k in existing_flat)
        if changed:
            info(f"{lang}: {len(todo)} key(s) to translate "
                 f"({changed} edited, {len(todo) - changed} new)")
        else:
            info(f"{lang}: {len(todo)} new key(s) to translate")
        subset = inc.build_subset(source_obj, set(todo), flat_style)
        content = json.dumps(subset, ensure_ascii=False).encode()
        # Of the keys the lock says still need work, some are renames and moves of text we
        # have already translated. Those are exactly what the lock cannot see and the project
        # memory can — and the API does not charge for what it is told it already knows.
        reuse, n_reused = _tm_reuse(memory, todo, [lang], sig)
        if n_reused:
            info(f"{lang}: {n_reused} of them come from the project memory, not the model")
        future = pool.submit(_run_job, client, os.path.basename(path), content, [lang], args,
                             reuse, lang if len(langs) > 1 else "")
        work.append((lang, target_path, existing, todo, future))
    pool.shutdown(wait=False)

    job_failed = False
    for lang, target_path, existing, todo, future in work:
        result, error = _outcome(future)
        if error is not None:
            # One language's job failing no longer takes the others down with it: they were
            # already translated (and paid for) side by side, so they are merged; this one
            # keeps its keys stale-locked for the next run.
            err(f"{lang}: {error}")
            job_failed = True
            broken.update(todo)
            broken_by_lang.setdefault(lang, set()).update(todo)
            continue
        job, status, members = result
        translated = _find_json_member(members, lang)
        if translated is not None and getattr(args, "digits", "native") == "latin":
            translated = dg.in_values(translated)
        if translated is None:
            err(f"{lang}: no output returned")
            broken.update(todo)  # not merged — keep these keys stale-locked
            broken_by_lang.setdefault(lang, set()).update(todo)
            continue
        if lang in (status.get("failed_langs") or []):
            warn(f"{lang}: translation fell back to source text — NOT merged (fix and re-run)")
            broken.update(todo)  # not merged — keep these keys stale-locked
            broken_by_lang.setdefault(lang, set()).update(todo)
            continue
        if memory is not None:
            _tm_commit(memory, tm_path, todo, {lang: translated}, sig)
        rec = _show_receipt(client, job)
        if rec:
            receipts.append(rec)
        _show_qa(client, job, args, path)
        merged = inc.merge_translation(existing, translated, source_obj)
        if is_arb:
            merged["@@locale"] = lang  # stamp the target locale on the written ARB
        inc.dump_json(merged, target_path)
        ok(f"{lang}: merged {len(todo)} key(s) → {target_path}")

    if receipts and getattr(args, "receipt", None):
        body = receipts[0] if len(receipts) == 1 else receipts
        write_atomic(args.receipt, json.dumps(body, ensure_ascii=False, indent=2))
        ok(f"Run receipt written to {args.receipt}")

    # Advance the lock exactly once, AFTER every language: record a source key's
    # current hash only if it is current in EVERY language processed (i.e. not in
    # `broken`). This folds in the old self-heal (keys already correct everywhere
    # get recorded) while never poisoning detection for a later language.
    # Per-language baselines: record this run's hashes for each language that actually took
    # them, so a later run for a DIFFERENT language still sees the edit as pending.
    unverified_all = set()
    if not args.assume_current:
        for s_ in unverified_by_lang.values():
            unverified_all |= s_
    for lang in langs:
        base = dict(lang_locks.get(lang) or inc.lock_keys(lock_raw, lang))
        failed = broken_by_lang.get(lang, set())
        skip = set() if args.assume_current else unverified_by_lang.get(lang, set())
        for k, h in source_hashes.items():
            if k not in failed and k not in skip:
                base[k] = h
        lang_locks[lang] = {k: v for k, v in base.items() if k in source_hashes}
    # Never record a hash for something nobody checked: that is what made the loss permanent.
    broken |= unverified_all

    # The global map keeps its old meaning — "current in EVERY language we know about" — so an
    # older client reading this lock is not told a key is done when some language still lags.
    new_keys = dict(detect_lock)
    for k in source_hashes:
        if k in broken:
            continue
        if all(m.get(k) == source_hashes[k] for m in lang_locks.values()):
            new_keys[k] = source_hashes[k]
        else:
            new_keys.pop(k, None)
    # Record the new settings only if they were fully applied everywhere. If a settings change
    # left some language broken, keep the OLD settings so the next run re-forces the stragglers.
    final_settings = locked_settings if (settings_changed and broken) else cur_settings
    inc.dump_lock(inc.build_lock(new_keys, final_settings, lang_locks), lock_path)

    if unverified_all:
        langs_hit = sorted(l for l, s_ in unverified_by_lang.items() if s_)
        info(f"{len(unverified_all)} key(s) already exist in {', '.join(langs_hit)} but nothing in "
             f"kaeris.lock vouches for them, so they were left alone AND left unlocked. If the "
             f"source changed after those files were made, those edits are NOT translated. "
             f"Re-run once with --no-only-new to rebuild them, or with --assume-current if the "
             f"files are known to match.")
    if not any_work:
        ok("Everything already up to date — nothing to translate")
    return 1 if job_failed else 0


def _find_json_member(members, lang):
    for name, data in members.items():
        if name.lower().endswith((".json", ".arb")):
            try:
                return json.loads(data.decode("utf-8"))
            except Exception:
                continue
    return None



def write_atomic(path, data, *, binary=False):
    """Write a file so an interrupted run cannot destroy what was already there.

    `open(path, "w")` truncates the destination the moment it is called, so a Ctrl+C, a full
    disk or a killed process between that and the final byte leaves the user with a half file
    — and these are their locale files and their lock. Reproduced: a 56-byte fr.json became 14
    unparseable bytes, with the previous translation gone.

    Writing beside the target and renaming means a reader (and the next run) sees either the
    old file or the new one. The rename is atomic on POSIX and on Windows via os.replace.
    """
    path = str(path)
    directory = os.path.dirname(os.path.abspath(path))
    tmp = os.path.join(directory, f".{os.path.basename(path)}.kaeris-tmp")
    mode = "wb" if binary else "w"
    kwargs = {} if binary else {"encoding": "utf-8"}
    try:
        with open(tmp, mode, **kwargs) as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# Formats where ONE file carries every language, so the archive holds a single merged member
# rather than one per language. Deriving a language from such a member's name would produce
# nonsense: "Localizable.xcstrings" would be filed under a language called "Localizable".
_MERGED_MEMBER_EXTS = (".xcstrings",)


def _write_members(members, src_path, out_dir, source_lang, digits="native", lang_case="keep",
                   by_lang=None):
    """Write each downloaded member (named `<lang><ext>`) to its namespace-mirrored
    target path (see _target_path) instead of a flat out_dir. `by_lang` collects where each
    language went."""
    written = []
    for name, data in members.items():
        if digits == "latin":
            data = dg.in_file(data)
        if name.lower().endswith(_MERGED_MEMBER_EXTS):
            # An Xcode String Catalog holds every language at once, so the result belongs
            # where the source is — that is the file Xcode reads and the developer keeps
            # editing. Everything we did not translate (comments, other languages, entries
            # marked shouldTranslate:false) was carried through, so this is an update of
            # their catalog, not a replacement of it.
            dest = (os.path.join(out_dir, os.path.basename(src_path)) if out_dir
                    else src_path)
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            write_atomic(dest, data, binary=True)
            written.append(dest)
            continue
        folder = os.path.basename(os.path.dirname(name.replace("\\", "/")))
        lang = (folder[len("values-"):] if folder.startswith("values-")
                else os.path.splitext(os.path.basename(name))[0])
        dest = _out_path(src_path, lang, out_dir, source_lang, lang_case)
        os.makedirs(os.path.dirname(dest) or out_dir or ".", exist_ok=True)
        write_atomic(dest, data, binary=True)
        written.append(dest)
        if by_lang is not None:
            by_lang[lang] = dest
    return written


def build_parser():
    p = argparse.ArgumentParser(
        prog="kaeris",
        description="AI localization from your terminal — translate strings files into 46 languages.",
    )
    p.add_argument("--version", action="version", version=f"kaeris {__version__}")
    p.add_argument("--api-url", default=os.environ.get("KAERIS_API_URL", DEFAULT_API),
                   help="API base URL (default: https://kaeris.dev)")
    p.add_argument("--key", "-k", help="API key (or env KAERIS_API_KEY). A key typed here stays in your "
                                       "shell history and shows in the process list; prefer the env var.")
    p.add_argument("--openrouter-key", help="OpenRouter key for Lifetime/BYOK (or env KAERIS_OPENROUTER_KEY)")
    p.add_argument("--config", help=f"Path to a config file (default: ./{CONFIG_FILENAME} if present). "
                                    "CLI flags override the config; the config overrides built-in defaults.")

    sub = p.add_subparsers(dest="command")

    t = sub.add_parser(
        "translate", help="Translate a strings file",
        description="Translate a strings file. Reads ./kaeris.json (or --config PATH) for defaults "
                    "when 'file'/--langs/etc. are omitted — precedence is CLI flag > kaeris.json > "
                    "built-in default. Run `kaeris init` to create one. "
                    "Without a key it runs as a free anonymous demo with smaller limits "
                    "(get a key: kaeris.dev/developer).",
    )
    t.add_argument("files", nargs="*",
                   help="Source file(s) (.json/.yml/.strings/.xcstrings/.md/.mdx/.po/.arb/.xml/.csv/.xliff/.properties/.resx/.ftl) — accepts several "
                        "paths and/or glob patterns (e.g. 'locales/en/*.json') for multi-namespace "
                        "projects; optional if 'source' is set in kaeris.json (string or list)")
    t.add_argument("--langs", "-l",
                   help="Comma-separated target languages, e.g. es,fr,de; optional if 'langs' is set in kaeris.json")
    t.add_argument("--out", "-o", help="Output directory (default: alongside each source, or 'out' in kaeris.json)")
    t.add_argument("--source-lang", dest="source_lang", default=None,
                   help="Base language code used to detect locales/<lang>/<namespace> layouts so "
                        "each target lands next to its namespace (default: 'source_lang' in "
                        "kaeris.json, else 'en')")
    t.add_argument("--only-new", dest="only_new", action="store_true", default=None,
                   help="Incremental: translate only keys missing from existing targets (JSON)")
    t.add_argument("--no-only-new", dest="only_new", action="store_false", default=None,
                   help="Disable incremental mode (overrides kaeris.json's only_new)")
    t.add_argument("--assume-current", dest="assume_current", action="store_true", default=False,
                   help="Incremental: treat existing target strings with no entry in "
                        "kaeris.lock as already matching the source, and lock them. Without "
                        "this they are left alone and left unlocked, because nothing has "
                        "checked them")
    t.add_argument("--lock", default=None,
                   help="Path to the incremental lock file used by --only-new to detect edited "
                        "source strings and setting changes (tone/icu/glossary/context), so output stays "
                        "reproducible (default: kaeris.lock next to the source file, or 'lock' "
                        "in kaeris.json)")
    t.add_argument("--keep", "--glossary", dest="keep", default=None,
                   help="Comma-separated terms to never translate (brand/product names), e.g. --keep 'KAERIS,GitHub'"
                        " (default: 'keep' in kaeris.json). --glossary is the same flag.")
    t.add_argument("--context", default=None,
                   help="One line about what the app is, e.g. --context 'a bank app for teenagers'. "
                        "The model uses it to pick the right sense of ambiguous strings "
                        f"(max {APP_CONTEXT_LIMIT} chars; default: 'context' in kaeris.json)")
    t.add_argument("--verify", action="store_true",
                   help="Back-translation and meaning check for your review: back-translate "
                        "into --back-lang, flag lines whose meaning may have changed, and "
                        "write verify.json with the lines to read")
    t.add_argument("--back-lang", default="en",
                   help="Language for --verify back-translation (default: en)")
    t.add_argument("--apply-fixes", dest="apply_fixes", action="store_true",
                   help="With --verify: write the fixes it found into the translated files — "
                        "only those our checks pass (placeholders, tags, ICU, line breaks, "
                        "*emphasis* intact); a lost line break or emphasis is put back. Free and "
                        "offline; what was not applied is listed with the reason. Same as "
                        "running `kaeris fix` afterwards")
    t.add_argument("--tone", choices=["neutral", "formal", "casual"], default=None,
                   help="Tone of voice for translations (default: 'tone' in kaeris.json, else neutral)")
    t.add_argument("--icu", action="store_true", default=None,
                   help="Hint that source values may use ICU MessageFormat (plurals/select) so it's preserved")
    t.add_argument("--no-icu", dest="icu", action="store_false", default=None,
                   help="Disable the ICU hint (overrides kaeris.json's icu)")
    t.add_argument("--translate-service-keys", action="store_true",
                   help="Also translate keys that start with '_' (e.g. \"_status\"). By default "
                        "they are copied through untouched in JSON, YAML and ARB.")
    # Help text stays ASCII: `--help` printed to a legacy Windows console (cp1252) would raise
    # on the native digits themselves.
    t.add_argument("--digits", choices=dg.MODES, default=None,
                   help="Digits in the result: 'native' (default) keeps what the language writes "
                        "(Persian, Bengali, Hindi, Arabic... digits), 'latin' rewrites every one to 0-9. "
                        "Placeholders are left as they are. Also \"digits\" in kaeris.json")
    t.add_argument("--lang-case", dest="lang_case", choices=LANG_CASES, default=None,
                   help="Case of the language code in new file and folder names: 'keep' "
                        "(default, zh-Hant.json) or 'lower' (zh-hant.json). A file or folder "
                        "that already exists in another case is always written under its own "
                        "name. Also \"lang_case\" in kaeris.json")
    t.add_argument("--jobs", "-j", type=int, default=None, metavar="N",
                   help=f"How many jobs to keep running on the server at once (default "
                        f"{DEFAULT_JOBS}): files side by side, and with --only-new the "
                        "languages of a file. Results are still written and reported in order. "
                        "--jobs 1 sends one at a time")
    t.add_argument("--quiet", "-q", action="store_true", help="Suppress progress output")
    t.add_argument("--tm", metavar="PATH",
                   help=f"Project translation memory file (default: {tmem.FILENAME} next to "
                        "the source). Remembers translations by TEXT, so a renamed key, a "
                        "string moved between files, or a fresh clone reuses them instead of "
                        "paying again. Commit it.")
    t.add_argument("--no-tm", action="store_true",
                   help="Ignore and do not update the project translation memory")
    t.add_argument("--receipt", metavar="PATH",
                   help="Write a JSON record of the run (model, languages delivered, "
                        "characters charged vs reused, settings, glossary terms actually "
                        "found) — counts only, no strings, safe to commit.")
    t.set_defaults(func=cmd_translate)

    fp = sub.add_parser(
        "fix", help="Apply the fixes in verify.json to the translated files (free, offline)",
        description="Write what `kaeris translate --verify` found into the translated files: "
                    "the meaning check's corrected line where our checks pass it (placeholders, "
                    "tags, ICU, line breaks, *emphasis* intact, nothing new broken), and a lost "
                    "line break or torn emphasis put back where no fix was suggested. Nothing is "
                    "sent anywhere and nothing is charged; a line that needs a new translation "
                    "is listed, not re-translated. Running it twice changes nothing.",
    )
    fp.add_argument("files", nargs="*",
                    help="Only these source files, for a verify.json written by a run over "
                         "several (default: all of them)")
    fp.add_argument("--from", dest="source", default="verify.json", metavar="PATH",
                    help="The verify.json to apply (default: ./verify.json)")
    fp.add_argument("--keep", "--glossary", dest="keep", default=None,
                    help="Terms a fix must keep verbatim (default: 'keep' in kaeris.json)")
    fp.add_argument("--dry-run", action="store_true",
                    help="Say what would be written, write nothing")
    fp.add_argument("--json", action="store_true",
                    help="Print the result as JSON on stdout: {applied, skipped, counts}, each line "
                         "{lang, key, how, why}. The same lists are written into verify.json as "
                         "_applied and _skipped (not with --dry-run).")
    fp.set_defaults(func=cmd_fix)

    ls = sub.add_parser("languages", help="List supported target languages")
    ls.set_defaults(func=cmd_languages)

    c = sub.add_parser(
        "check", help="i18n firewall: fail CI if translations are missing or placeholder-broken",
        description="Static, local, no-API check: compares the source (base-language) file "
                    "against each target locale file and reports missing keys and placeholder "
                    "mismatches ({name}, %s, %d, {{x}}, ICU {count, plural, ...}) — the kind of "
                    "bug that silently breaks the app at runtime. Exits non-zero so it can gate "
                    "a CI merge. Reads ./kaeris.json for source/langs/out when flags are omitted.",
    )
    c.add_argument("--source", help="Source (base-language) file in any format `translate` takes "
                                    "(JSON, ARB, YAML, .strings, .xcstrings, .po, Android XML, CSV, "
                                    "XLIFF, .properties, .resx, Fluent, Markdown); optional if "
                                    "'source' is set in kaeris.json")
    c.add_argument("--langs", "-l", help="Comma-separated target languages, e.g. es,fr,de; "
                                        "optional if 'langs' is set in kaeris.json")
    c.add_argument("--out", "-o", help="Directory containing target locale files "
                                       "(default: where `translate` writes them, next to the source, "
                                       "or 'out' in kaeris.json)")
    c.add_argument("--pattern", default=None,
                   help="Target filename pattern, '{lang}' is replaced with the language code "
                        "(default: the layout `translate` writes, e.g. de.json, app_de.arb, "
                        "values-de/strings.xml)")
    c.add_argument("--glossary", "--keep", dest="glossary", default=None,
                   help="Comma-separated terms the translation must keep verbatim, e.g. "
                        "--glossary 'KAERIS,GitHub' (default: 'keep' in kaeris.json). "
                        "A target that dropped one fails the check.")
    c.add_argument("--strict", action="store_true",
                   help="Also fail (non-zero exit) if a target has extra/stale keys not in the source")
    c.add_argument("--json", action="store_true", help="Machine-readable JSON output instead of the human report")
    c.add_argument("--sarif", metavar="PATH", default=None,
                   help="Also write a SARIF 2.1.0 report to PATH. Upload it with "
                        "github/codeql-action/upload-sarif and every finding appears as an "
                        "annotation on the exact line in the pull request.")
    c.add_argument("--ci", action="store_true",
                   help="No functional difference from plain `check` — the exit code already gates a "
                        "merge (0 clean, 1 missing/placeholder problem found, 2 bad usage). A stable, "
                        "self-documenting flag name for CI workflows/i18n-firewall gates.")
    c.set_defaults(func=cmd_check)

    i = sub.add_parser(
        "init", help="Write a kaeris.json config file in the current directory",
        description="Write a kaeris.json so `kaeris translate` can run config-driven, with no flags. "
                    "Use --preset for common i18n framework layouts.",
    )
    i.add_argument("--preset", choices=sorted(PRESET_OVERRIDES),
                   help="Pre-fill source/format/icu for a framework: i18next, react-i18next, "
                        "next-intl, react-intl, vue-i18n, flutter-arb")
    i.add_argument("--force", action="store_true", help=f"Overwrite an existing {CONFIG_FILENAME}")
    i.set_defaults(func=cmd_init)

    q = sub.add_parser(
        "quota", help="Show how much of this month's volume is left",
        description="Show the monthly character volume on your key: used, left, and the date "
                    "it comes back. Ask BEFORE a large run — a run that needs more than is "
                    "left is refused, and the answer used to arrive only as that refusal.",
    )
    q.set_defaults(func=cmd_quota)

    return p


def _update_hint(args):
    """One quiet stderr line when PyPI has a newer kaeris, after the command's own work."""
    from . import updatecheck
    from . import __version__
    machine = bool(getattr(args, "json", False) or getattr(args, "sarif", None))
    updatecheck.maybe_notify("kaeris", __version__, quiet=machine)


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not getattr(args, "command", None):
        build_parser().print_help()
        return 1
    try:
        code = args.func(args)
        _update_hint(args)
        return code
    except KaerisError as e:
        err(str(e))
        return 2
    except TooDeep as e:
        # Слишком глубокий файл раньше печатал в чужой CI питоновский трейсбек
        # (RecursionError на 19 КБ) — а трейсбек в логе сборки читается как «ваша локаль
        # сломана». Тот же код выхода 2, что у остальных отказов, и текст для человека.
        err(str(e))
        return 2
    except KeyboardInterrupt:
        err("Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
