"""KAERIS i18n command-line interface."""

import argparse
import glob
import json
import os
import sys

from . import __version__
from .client import KaerisClient, KaerisError, DEFAULT_API
from . import incremental as inc
from . import check as chk

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
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        raise KaerisError(f"Invalid JSON in {path}: {e}")
    if not isinstance(raw, dict):
        raise KaerisError(f"Invalid config in {path}: expected a JSON object")
    cfg = {k: v for k, v in raw.items() if not k.startswith("//") and not k.startswith("_")}
    return cfg, path


def _resolve_source(path):
    """Expand a glob pattern in a source path to a single file, if any glob chars are present."""
    if not any(ch in path for ch in "*?["):
        return path
    matches = sorted(glob.glob(path))
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
            matches.update(glob.glob(entry))
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

    if stem == source_lang:
        d = out_dir if out_dir else src_dir
        return os.path.join(d, f"{lang}{ext}")

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
    if not path.lower().endswith((".json", ".arb")):
        err(f"kaeris check currently supports JSON and ARB (got {path}) — "
            "other formats land next release")
        return 2
    is_arb = path.lower().endswith(".arb")

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

    out_dir = args.out or config.get("out") or os.path.dirname(os.path.abspath(path))
    source_lang = config.get("source_lang") or "en"
    _target_path.warned = False  # warn at most once per check run

    def _resolve_target(lang):
        # No explicit --pattern: resolve exactly like `kaeris translate` writes, so a
        # namespace layout (locales/<lang>/translation.json, the shipped i18next preset)
        # is found instead of a phantom locales/<lang>.json. An explicit --pattern is an
        # override for non-standard layouts.
        if args.pattern:
            return os.path.join(out_dir, args.pattern.format(lang=lang))
        return _target_path(path, lang, out_dir, source_lang)

    try:
        source_obj = inc.load_json(path)
    except (OSError, ValueError) as e:
        err(f"Could not read source file {path}: {e}")
        return 2
    if not isinstance(source_obj, dict):
        err(f"Invalid source file {path}: expected a JSON object")
        return 2
    if is_arb:
        source_obj = _strip_arb_meta(source_obj)

    def load_target(lang):
        target_path = _resolve_target(lang)
        if not os.path.isfile(target_path):
            return None
        try:
            obj = inc.load_json(target_path)
        except (OSError, ValueError) as e:
            warn(f"{lang}: could not parse {target_path}: {e} — treating as missing")
            return None
        if not isinstance(obj, dict):
            warn(f"{lang}: {target_path} is not a JSON object — treating as missing")
            return None
        return _strip_arb_meta(obj) if is_arb else obj

    # "keep" is the documented key `kaeris init` writes and `translate` reads; check used to
    # read only "glossary", a key nothing ever generated — so the dropped-term detector was
    # silently off in CI for every real project. Flag wins, then either config key.
    if getattr(args, "glossary", None):
        glossary = [t.strip() for t in args.glossary.split(",") if t.strip()]
    else:
        glossary = config.get("keep") or config.get("glossary") or None
    result = chk.check_locales(source_obj, langs, load_target, glossary)

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
                err(f"{lang}: locale file not found ({_resolve_target(lang)})")
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
                err(f"{lang}: {len(missing)} missing key(s): {', '.join(missing[:10])}"
                    + (" ..." if len(missing) > 10 else ""))
            if issues:
                err(f"{lang}: {len(issues)} placeholder mismatch(es):")
                for i in issues:
                    print(f"    {i['key']}: missing {i['missing'] or '-'}, added {i['added'] or '-'}",
                          file=sys.stderr)
            if lang_faults:
                err(f"{lang}: {len(lang_faults)} translation fault(s):")
                for f in lang_faults:
                    print(f"    {f.get('key', '')}: {f['msg']}", file=sys.stderr)
            if lang_warns:
                level = err if args.strict else warn
                level(f"{lang}: {len(lang_warns)} warning(s):")
                for w in lang_warns:
                    print(f"    {w.get('key', '')}: {w['msg']}".rstrip(), file=sys.stderr)
            if extra:
                level = err if args.strict else warn
                level(f"{lang}: {len(extra)} extra/stale key(s): {', '.join(extra[:10])}"
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
        print(json.dumps(result, ensure_ascii=False, indent=2))

    return 1 if fail else 0


def _progress_printer():
    last = [-1]
    def show(status):
        total = status.get("total") or 0
        done = status.get("progress") or 0
        if total and done != last[0]:
            pct = int(done / total * 100)
            lang = status.get("current_lang") or ""
            print(_c("→", "36") + f" {pct:3}%  {lang}", file=sys.stderr)
            last[0] = done
    return show


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

    if args.out:
        os.makedirs(args.out, exist_ok=True)

    client = _client(args)
    _target_path.warned = False  # warn at most once per `translate` run, not per file/lang

    multi = len(files) > 1
    exit_code = 0
    for path in files:
        if multi:
            info(f"── {os.path.relpath(path)} ──")
        try:
            code = _translate_one(client, path, langs, args)
        except KaerisError as e:
            err(str(e))
            code = 1
        exit_code = max(exit_code, code)
    # What the run cost of the month, once per invocation rather than once per file — and here
    # rather than inside _translate_one so the incremental (--only-new) path, which returns
    # earlier, reports it too.
    _show_quota(client)
    return exit_code


def _translate_one(client, path, langs, args):
    """Translate a single resolved source file. Returns 0 (clean), 3 (some
    languages fell back to source text) or 1 (a per-file error — the caller
    keeps going to the next file rather than aborting the whole run)."""
    fname = os.path.basename(path)
    incremental_ok = fname.lower().endswith((".json", ".arb"))
    display_out = args.out or os.path.dirname(os.path.abspath(path))
    os.makedirs(display_out, exist_ok=True)

    # ── incremental (delta) mode — JSON and ARB (ARB is plain JSON) ───────────
    if args.only_new:
        if not incremental_ok:
            ext = os.path.splitext(fname)[1]
            warn(f"--only-new supports JSON/ARB only (zero-dependency CLI); "
                 f"translating the whole {ext} file instead")
        else:
            return _translate_incremental(client, path, args.out, langs, args)

    with open(path, "rb") as f:
        content = f.read()

    info(f"Translating {fname} → {', '.join(langs)}")
    job = client.submit(fname, content, langs, _glossary(args),
                        verify=args.verify, back_lang=args.back_lang,
                        tone=_tone(args), icu=args.icu,
                        app_context=_app_context(args))
    status = client.poll(job, on_progress=None if args.quiet else _progress_printer())
    members = client.download(job)
    written = _write_members(members, path, args.out, args.source_lang)
    ok(f"Wrote {len(written)} file(s) to {display_out}")
    for w in written:
        print(w)
    # Translation QA — placeholder loss + UI-overflow (free), plus back-translation if --verify
    _show_qa(client, job, display_out, args.verify, args.back_lang)
    failed = status.get("failed_langs") or []
    if failed:
        warn(f"{len(failed)} language(s) fell back to source text (not translated): {', '.join(failed)}")
        return 3
    return 0


def _show_qa(client, job, out_dir, verify, back_lang):
    """Print Translation QA (placeholder loss, UI-overflow) and, if requested, write the
    verify-meaning back-translations to a file. Best-effort — never fails the run."""
    try:
        data = client.preview(job)
    except KaerisError:
        return
    warnings = data.get("_warnings") or {}
    qa = data.get("_qa") or {}
    back = data.get("_back") or {}

    ph = sum(len(kv) for kv in warnings.values())
    if ph:
        warn(f"{ph} translation(s) dropped a placeholder — your app may break:")
        for lang, kv in warnings.items():
            for k, lost in list(kv.items())[:5]:
                print(f"    [{lang}] {k}: missing {', '.join(lost)}", file=sys.stderr)

    over = sum(len(v.get("overflow", [])) for v in qa.values())
    if over:
        warn(f"{over} translation(s) may overflow your UI (grew much longer than source):")
        shown = 0
        for lang, v in qa.items():
            for o in v.get("overflow", []):
                if shown >= 6:
                    break
                print(f"    [{lang}] {o['key']}: +{o['pct']}%  \"{o['tr'][:50]}\"", file=sys.stderr)
                shown += 1

    if verify and back:
        vpath = os.path.join(out_dir, "verify.json")
        write_atomic(vpath, json.dumps(back, ensure_ascii=False, indent=2))
        ok(f"Verify-meaning back-translations (→ {back_lang}) written to {vpath}")

    if not ph and not over:
        ok("Translation QA: no placeholder loss, no overflow risks")


def _arb_meta_key(flat_key):
    """True if a flattened key belongs to ARB non-translatable metadata:
    its FIRST dotted segment starts with '@' (`@@locale`, `@foo`,
    `@foo.placeholders...`). These must never be sent to the model."""
    return flat_key.split(".", 1)[0].startswith("@")


def _current_model(client):
    """The exact model this key's tier translates with, recorded in the lock so a tier switch
    (Free->Pro swaps DeepSeek for GPT-4o-mini) is caught as a settings change.

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

    for lang in langs:
        target_path = _target_path(path, lang, out_dir, args.source_lang)
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
        job = client.submit(os.path.basename(path), content, [lang], _glossary(args),
                            tone=_tone(args), icu=args.icu,
                            app_context=_app_context(args))
        status = client.poll(job, on_progress=None if args.quiet else _progress_printer())
        members = client.download(job)
        translated = _find_json_member(members, lang)
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
        merged = inc.merge_translation(existing, translated)
        if is_arb:
            merged["@@locale"] = lang  # stamp the target locale on the written ARB
        inc.dump_json(merged, target_path)
        ok(f"{lang}: merged {len(todo)} key(s) → {target_path}")

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
    return 0


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


def _write_members(members, src_path, out_dir, source_lang):
    """Write each downloaded member (named `<lang><ext>`) to its namespace-mirrored
    target path (see _target_path) instead of a flat out_dir."""
    written = []
    for name, data in members.items():
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
        lang = os.path.splitext(os.path.basename(name))[0]
        dest = _target_path(src_path, lang, out_dir, source_lang)
        os.makedirs(os.path.dirname(dest) or out_dir or ".", exist_ok=True)
        write_atomic(dest, data, binary=True)
        written.append(dest)
    return written


def build_parser():
    p = argparse.ArgumentParser(
        prog="kaeris",
        description="AI localization from your terminal — translate strings files into 46 languages.",
    )
    p.add_argument("--version", action="version", version=f"kaeris {__version__}")
    p.add_argument("--api-url", default=os.environ.get("KAERIS_API_URL", DEFAULT_API),
                   help="API base URL (default: https://kaeris.dev)")
    p.add_argument("--key", "-k", help="API key (or env KAERIS_API_KEY)")
    p.add_argument("--openrouter-key", help="OpenRouter key for Lifetime/BYOK (or env KAERIS_OPENROUTER_KEY)")
    p.add_argument("--config", help=f"Path to a config file (default: ./{CONFIG_FILENAME} if present). "
                                    "CLI flags override the config; the config overrides built-in defaults.")

    sub = p.add_subparsers(dest="command")

    t = sub.add_parser(
        "translate", help="Translate a strings file",
        description="Translate a strings file. Reads ./kaeris.json (or --config PATH) for defaults "
                    "when 'file'/--langs/etc. are omitted — precedence is CLI flag > kaeris.json > "
                    "built-in default. Run `kaeris init` to create one.",
    )
    t.add_argument("files", nargs="*",
                   help="Source file(s) (.json/.yml/.strings/.xcstrings/.po/.arb/.xml/.csv/.xliff/.properties/.resx/.ftl) — accepts several "
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
                   help="Verify meaning: back-translate results into --back-lang and write verify.json to check accuracy")
    t.add_argument("--back-lang", default="en",
                   help="Language for --verify back-translation (default: en)")
    t.add_argument("--tone", choices=["neutral", "formal", "casual"], default=None,
                   help="Tone of voice for translations (default: 'tone' in kaeris.json, else neutral)")
    t.add_argument("--icu", action="store_true", default=None,
                   help="Hint that source values may use ICU MessageFormat (plurals/select) so it's preserved")
    t.add_argument("--no-icu", dest="icu", action="store_false", default=None,
                   help="Disable the ICU hint (overrides kaeris.json's icu)")
    t.add_argument("--quiet", "-q", action="store_true", help="Suppress progress output")
    t.set_defaults(func=cmd_translate)

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
    c.add_argument("--source", help="Source (base-language) JSON or ARB file; optional if 'source' is set in kaeris.json")
    c.add_argument("--langs", "-l", help="Comma-separated target languages, e.g. es,fr,de; "
                                        "optional if 'langs' is set in kaeris.json")
    c.add_argument("--out", "-o", help="Directory containing target locale files "
                                       "(default: alongside the source, or 'out' in kaeris.json)")
    c.add_argument("--pattern", default=None,
                   help="Target filename pattern, '{lang}' is replaced with the language code "
                        "(default: '{lang}.json')")
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


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not getattr(args, "command", None):
        build_parser().print_help()
        return 1
    try:
        return args.func(args)
    except KaerisError as e:
        err(str(e))
        return 2
    except KeyboardInterrupt:
        err("Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
