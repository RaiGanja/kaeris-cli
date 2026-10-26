// The `check` command: flags, kaeris.json, how a source file finds its translations, the report,
// and the exit code (0 clean, 1 a problem was found, 2 bad usage or an unreadable file).
import fs from "node:fs";
import { checkLocales } from "./check.js";
import { readText, UnknownEncoding } from "./encoding.js";
import { ValueError } from "./errors.js";
import * as fm from "./formats.js";
import { dumps, parse as parseJson } from "./json.js";
import { reprList, rx, strip } from "./py.js";
import * as pp from "./pypath.js";
import { TooDeep } from "./flatten.js";
import * as sarif from "./sarif.js";
import { version } from "./version.js";

const CONFIG_FILENAME = "kaeris.json";

// ── output ────────────────────────────────────────────────────────────────────

const color = (text, code) => (process.env.NO_COLOR || !process.stderr.isTTY ? text : `\x1b[${code}m${text}\x1b[0m`);
const out = (line) => process.stderr.write(line + "\n");
const info = (msg) => out(`${color("→", "36")} ${msg}`);
const ok = (msg) => out(`${color("✓", "32")} ${msg}`);
const warn = (msg) => out(`${color("!", "33")} ${msg}`);
const err = (msg) => out(`${color("✗", "31")} ${msg}`);

class KaerisError extends Error {}

// ── arguments, the way argparse reads them ────────────────────────────────────

const PYPI_COMMANDS = ["translate", "fix", "languages", "init", "quota"];
const COMMANDS = ["check", ...PYPI_COMMANDS];

const GLOBAL_OPTIONS = [
  { long: "--version", flag: true },
  { long: "--api-url", value: true },
  { long: "--key", short: "-k", value: true },
  { long: "--openrouter-key", value: true },
  { long: "--config", value: true },
  { long: "--help", short: "-h", flag: true },
];

const CHECK_OPTIONS = [
  { long: "--source", value: true },
  { long: "--langs", short: "-l", value: true },
  { long: "--out", short: "-o", value: true },
  { long: "--pattern", value: true },
  { long: "--glossary", value: true },
  { long: "--keep", value: true, dest: "glossary" },
  { long: "--strict", flag: true },
  { long: "--json", flag: true },
  { long: "--sarif", value: true },
  { long: "--ci", flag: true },
  { long: "--help", short: "-h", flag: true },
];

class UsageError extends Error {}

// Longest unambiguous prefix wins, as in argparse.
function findOption(options, token) {
  const exact = options.find((o) => o.long === token || o.short === token);
  if (exact) return exact;
  if (token.startsWith("--")) {
    const hits = options.filter((o) => o.long.startsWith(token));
    if (hits.length === 1) return hits[0];
    if (hits.length > 1) throw new UsageError(`ambiguous option: ${token} could match ${hits.map((h) => h.long).join(", ")}`);
  }
  return null;
}

// Reads options out of argv until a non-option (the command) when `stopAtPositional`.
function readOptions(options, argv, start, stopAtPositional) {
  const values = {};
  const positional = [];
  const unknown = [];
  let i = start;
  for (; i < argv.length; i++) {
    const tok = argv[i];
    if (tok === "--") {
      positional.push(...argv.slice(i + 1));
      i = argv.length;
      break;
    }
    if (!tok.startsWith("-") || tok === "-") {
      if (stopAtPositional) break;
      positional.push(tok);
      continue;
    }
    let name = tok;
    let inline = null;
    if (tok.startsWith("--") && tok.includes("=")) {
      name = tok.slice(0, tok.indexOf("="));
      inline = tok.slice(tok.indexOf("=") + 1);
    } else if (!tok.startsWith("--") && tok.length > 2) {
      name = tok.slice(0, 2);
      inline = tok.slice(2);
    }
    const opt = findOption(options, name);
    if (!opt) {
      unknown.push(tok);
      continue;
    }
    const dest = opt.dest ?? opt.long.slice(2).replace(/-/g, "_");
    if (opt.flag) {
      if (inline !== null && tok.startsWith("--")) throw new UsageError(`argument ${opt.long}: ignored explicit argument '${inline}'`);
      values[dest] = true;
    } else {
      let v = inline;
      if (v === null) {
        const next = argv[i + 1];
        if (next === undefined || (next.startsWith("-") && next !== "-" && !/^-\d/.test(next))) throw new UsageError(`argument ${opt.short ? `${opt.short}/` : ""}${opt.long}: expected one argument`);
        v = next;
        i++;
      }
      values[dest] = v;
    }
  }
  return { values, positional, unknown, next: i };
}

const CHECK_USAGE =
  "usage: kaeris check [-h] [--source SOURCE] [--langs LANGS] [--out OUT] [--pattern PATTERN]\n" +
  "                    [--glossary GLOSSARY] [--strict] [--json] [--sarif PATH] [--ci]";

const CHECK_HELP = `${CHECK_USAGE}

Static, local, no-API check: compares the source (base-language) file against each target
locale file and reports missing keys, placeholder mismatches ({name}, %s, %d, {{x}}, ICU
{count, plural, ...}) and translation faults. Exits non-zero so it can gate a CI merge.
Reads ./kaeris.json for source/langs/out when flags are omitted.

options:
  -h, --help            show this help message and exit
  --source SOURCE       Source (base-language) file in any format \`translate\` takes (${fm.SUPPORTED_TEXT});
                        optional if 'source' is set in kaeris.json
  --langs, -l LANGS     Comma-separated target languages, e.g. es,fr,de; optional if 'langs' is
                        set in kaeris.json
  --out, -o OUT         Directory containing target locale files (default: where \`translate\`
                        writes them, next to the source, or 'out' in kaeris.json)
  --pattern PATTERN     Target filename pattern, '{lang}' is replaced with the language code
                        (default: the layout \`translate\` writes, e.g. de.json, app_de.arb,
                        values-de/strings.xml)
  --glossary, --keep GLOSSARY
                        Comma-separated terms the translation must keep verbatim, e.g.
                        --glossary 'KAERIS,GitHub' (default: 'keep' in kaeris.json). A target
                        that dropped one fails the check.
  --strict              Also fail (non-zero exit) if a target has extra/stale keys not in the
                        source, or any warning
  --json                Machine-readable JSON output instead of the human report
  --sarif PATH          Also write a SARIF 2.1.0 report to PATH, for github/codeql-action/upload-sarif
  --ci                  No functional difference from plain \`check\`: the exit code already gates a
                        merge (0 clean, 1 missing/placeholder problem found, 2 bad usage)
`;

const MAIN_USAGE = "usage: kaeris [-h] [--version] [--config CONFIG] {check,translate,fix,languages,init,quota} ...";

const MAIN_HELP = `${MAIN_USAGE}

kaeris check: the i18n firewall. Compares a source locale file with its translations and fails
CI when strings are untranslated, keys are missing or extra, or placeholders are broken. Runs
offline; no key, no account.

The other commands (translate, fix, languages, init, quota) are in the Python package:
  pipx install kaeris

options:
  -h, --help       show this help message and exit
  --version        show the version and exit
  --config CONFIG  Path to a config file (default: ./${CONFIG_FILENAME} if present). CLI flags
                   override the config.
`;

// ── configuration ─────────────────────────────────────────────────────────────

// kaeris.json (explicit --config PATH, else ./kaeris.json if present). Comment keys ("//", "_...")
// are ignored.
function loadConfig(explicit) {
  const path = explicit || (pp.isfile(CONFIG_FILENAME) ? CONFIG_FILENAME : null);
  if (!path) return [new Map(), null];
  if (!pp.isfile(path)) throw new KaerisError(`Config file not found: ${path}`);
  let raw;
  try {
    raw = parseJson(readText(path));
  } catch (e) {
    if (e instanceof ValueError) throw new KaerisError(`Invalid JSON in ${path}: ${e.message}`);
    throw e;
  }
  if (!(raw instanceof Map)) throw new KaerisError(`Invalid config in ${path}: expected a JSON object`);
  const cfg = new Map([...raw].filter(([k]) => !k.startsWith("//") && !k.startsWith("_")));
  return [cfg, path];
}

const truthy = (v) => (v instanceof Map ? v.size > 0 : Array.isArray(v) ? v.length > 0 : Boolean(v));
const cfgGet = (cfg, k) => (cfg.has(k) ? cfg.get(k) : undefined);

// ── where the translations are ────────────────────────────────────────────────

class BadTarget extends Error {}

// Encoding is not syntax: without this a person goes hunting for a broken bracket in a file that
// is intact — it is just not saved as UTF-8.
const hint = (e) => (e instanceof UnknownEncoding ? " — save it as UTF-8 (or UTF-16 with a byte-order mark)" : "");

// Expand a glob pattern in a source path to a single file, if any glob characters are present.
function resolveSource(rawPath) {
  if (!/[*?[]/.test(rawPath)) return rawPath;
  const matches = pp.glob(rawPath).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  if (!matches.length) return null;
  if (matches.length > 1) warn(`Multiple files match '${rawPath}' — using ${matches[0]} (others: ${matches.slice(1).join(", ")})`);
  return matches[0];
}

let layoutWarned = false;

// The output path for `lang`'s translation of `srcPath`, mirroring common locale-file layouts.
function targetPath(srcPath, lang, outDir, sourceLang) {
  const [stem, ext] = pp.splitext(pp.basename(srcPath));
  let srcDir = pp.dirname(srcPath);

  const parent = pp.basename(srcDir);
  if (ext.toLowerCase() === ".xml" && (parent === "values" || parent.startsWith("values-"))) {
    const resDir = outDir || pp.dirname(srcDir);
    return pp.join(resDir, `values-${lang}`, pp.basename(srcPath));
  }

  if (stem === sourceLang) {
    const d = outDir ? outDir : srcDir;
    return pp.join(d, `${lang}${ext}`);
  }

  if (pp.altsep) srcDir = srcDir.split(pp.altsep).join(pp.sep);
  const segs = srcDir ? srcDir.split(pp.sep) : [];
  let lastIdx = null;
  segs.forEach((seg, i) => {
    if (seg === sourceLang) lastIdx = i;
  });
  if (lastIdx !== null) {
    const newSegs = [...segs];
    newSegs[lastIdx] = lang;
    if (outDir) return pp.join(outDir, ...newSegs.slice(lastIdx), pp.basename(srcPath));
    return pp.join(newSegs.join(pp.sep), pp.basename(srcPath));
  }

  // Prefixed single-file convention: app_en.arb -> app_de.arb (Flutter), messages_en -> messages_de.
  if (stem.endsWith("_" + sourceLang) && stem.length > sourceLang.length + 1) {
    const prefix = stem.slice(0, -(sourceLang.length + 1));
    const d = outDir ? outDir : srcDir;
    return pp.join(d, `${prefix}_${lang}${ext}`);
  }

  if (!layoutWarned) {
    warn(`Could not detect a known locale-file layout for '${srcPath}' (source_lang='${sourceLang}') — nesting output by language instead`);
    layoutWarned = true;
  }
  const baseDir = outDir || srcDir;
  return pp.join(baseDir, lang, pp.basename(srcPath));
}

// `path`, with each part that names `lang` spelled the way it already is on disk (zh-Hant.json
// next to a requested zh-hant.json). An exact match always wins.
function existingCase(path, lang) {
  const [drive, rest] = pp.splitdrive(pp.normpath(path));
  const parts = rest.split(pp.sep);
  const want = lang.toLowerCase();
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    if (!part || !part.toLowerCase().includes(want)) continue;
    const parent = drive + (parts.slice(0, i).join(pp.sep) || (rest.startsWith(pp.sep) ? pp.sep : "."));
    let names;
    try {
      names = fs.readdirSync(parent);
    } catch {
      break;
    }
    if (names.includes(part)) continue;
    const same = names.filter((n) => n.toLowerCase() === part.toLowerCase());
    if (same.length === 1) parts[i] = same[0];
  }
  return drive + parts.join(pp.sep);
}

const outPath = (srcPath, lang, outDir, sourceLang) => existingCase(targetPath(srcPath, lang, outDir, sourceLang), lang);

// Xcode's own layout: en.lproj/Localizable.strings -> de.lproj/Localizable.strings.
function lprojSibling(srcPath, lang, sourceLang, outDir) {
  const folder = pp.dirname(srcPath);
  if (pp.basename(folder) !== `${sourceLang}.lproj`) return null;
  return pp.join(outDir || pp.dirname(folder), `${lang}.lproj`, pp.basename(srcPath));
}

// ── language codes ────────────────────────────────────────────────────────────

const LANG_TAG_RE = rx("^[A-Za-z]{2,3}(?:[-_](?:[A-Za-z]{2}|[A-Za-z]{4}|\\d{3}))*$");

// 'fr-ca' -> 'fr-CA', 'zh_hant' -> 'zh-Hant', 'PT-pt' -> 'pt-PT'. Anything that is not shaped like
// a language tag comes back as it was written, so it can be refused by name.
function canonLang(tag) {
  tag = strip(String(tag));
  if (!LANG_TAG_RE.test(tag)) return tag;
  const [first, ...rest] = tag.split(/[-_]/);
  return [first.toLowerCase(), ...rest.map((p) => (p.length === 2 ? p.toUpperCase() : p.length === 4 ? p[0].toUpperCase() + p.slice(1).toLowerCase() : p))].join("-");
}

// 'fr-ca, pt_PT' (or a list from kaeris.json) -> ['fr-CA', 'pt-PT'], each language once.
function splitLangs(raw) {
  const items = typeof raw === "string" ? raw.split(",") : Array.isArray(raw) ? raw : [];
  const result = [];
  for (const item of items) {
    const s = strip(String(item));
    if (s && !result.includes(canonLang(s))) result.push(canonLang(s));
  }
  return result;
}

// A String Catalog's synthetic sub-keys, readable: `cart⟦sub:count⟧⟦plural⟧` -> `cart (sub count, plural)`.
const XC_MARKER_RE = /((?:⟦[^⟦⟧]*⟧)+)/g;

function prettyKey(text) {
  if (!text || !text.includes("⟦")) return text;
  return text.replace(XC_MARKER_RE, (_, group) => {
    const parts = group.slice(1, -1).split("⟧⟦").filter(Boolean);
    return " (" + parts.map((p) => p.replace(/:/g, " ")).join(", ") + ")";
  });
}

const plural = (n, one, many) => (n !== 1 ? many : one);

// ── the command ───────────────────────────────────────────────────────────────

function stripArbMeta(obj) {
  if (obj instanceof Map) return new Map([...obj].filter(([k]) => !String(k).startsWith("@")));
  return obj;
}

function cmdCheck(args, globals) {
  let config;
  let configPath;
  try {
    [config, configPath] = loadConfig(globals.config);
  } catch (e) {
    if (e instanceof KaerisError) {
      err(e.message);
      return 2;
    }
    throw e;
  }
  if (configPath && !args.json) info(`Using config: ${configPath}`);

  const rawPath = args.source || cfgGet(config, "source");
  if (!rawPath) {
    err("No source file given (--source PATH, or 'source' in kaeris.json)");
    return 2;
  }
  const path = typeof rawPath === "string" ? resolveSource(rawPath) : null;
  if (!path || !pp.isfile(path)) {
    err(`Source file not found: ${path || rawPath}`);
    return 2;
  }
  const kind = fm.kindOf(path);
  if (kind === null) {
    err(`kaeris check reads ${fm.SUPPORTED_TEXT} (got ${path})`);
    return 2;
  }
  const isArb = kind === "arb";
  const merged = fm.MERGED.includes(kind);
  const jsonLike = kind === "json" || kind === "arb";

  const langs = splitLangs(args.langs ? args.langs : cfgGet(config, "langs"));
  if (!langs.length) {
    err("No target languages given (--langs es,fr,de, or 'langs' in kaeris.json)");
    return 2;
  }

  // null means "where `translate` writes by default": the source's own folder layout decides
  // (res/values -> res/values-<lang>, locales/en -> locales/<lang>).
  const outDirRaw = args.out || cfgGet(config, "out") || null;
  const outDir = typeof outDirRaw === "string" ? outDirRaw : null;
  let sourceLang = cfgGet(config, "source_lang") || "en";
  layoutWarned = false; // warn at most once per check run

  const resolveTarget = (lang) => {
    if (args.pattern) {
      return pp.join(outDir || pp.dirname(pp.abspath(path)), args.pattern.replace(/\{lang\}/g, lang));
    }
    const found = outPath(path, lang, outDir, sourceLang);
    if (!pp.isfile(found)) {
      const sibling = lprojSibling(path, lang, sourceLang, outDir);
      if (sibling && pp.isfile(sibling)) return sibling;
    }
    return found;
  };

  // One file holds the source AND every language (a String Catalog, a CSV with a column per
  // language): there is no per-language file to resolve.
  let byLang = null;
  let sourceObj;
  try {
    if (merged) {
      let fileLang;
      [fileLang, byLang] = fm.readMerged(path);
      sourceLang = cfgGet(config, "source_lang") || fileLang;
      sourceObj = fm.findLang(byLang, sourceLang) || new Map();
      if (!sourceObj.size) {
        err(`No strings for the source language '${sourceLang}' in ${path}`);
        return 2;
      }
      if (kind === "csv") {
        // `translate` writes the CSV with its language columns beside the source, in a folder of
        // its own (translations/<name>.csv): read those columns too.
        layoutWarned = true;
        const written = outPath(path, "translations", outDir, sourceLang);
        if (pp.isfile(written) && pp.abspath(written) !== pp.abspath(path)) byLang = new Map([...byLang, ...fm.readMerged(written)[1]]);
      }
    } else if (jsonLike) {
      sourceObj = parseJson(readText(path));
      if (!(sourceObj instanceof Map)) {
        err(`Invalid source file ${path}: expected a JSON object`);
        return 2;
      }
      if (isArb) sourceObj = stripArbMeta(sourceObj);
    } else {
      sourceObj = fm.readOne(path, "source");
      if (!sourceObj.size) {
        err(`No translatable strings found in ${path}`);
        return 2;
      }
    }
  } catch (e) {
    if (e instanceof ValueError || e.code) {
      const what = kind === "xcstrings" ? "String Catalog" : "source file";
      err(`Could not read ${what} ${path}: ${e.code ? osMessage(e) : e.message}${hint(e)}`);
      return 2;
    }
    throw e;
  }
  if (!args.json) for (const note of sourceObj.notes ?? []) warn(`${path}: ${note.msg}`);

  const loadTarget = (lang) => {
    if (merged) {
      // Absent from the file is the same as an absent file: every key is missing.
      return fm.findLang(byLang, lang);
    }
    const targetFile = resolveTarget(lang);
    if (!pp.isfile(targetFile)) return null;
    if (!jsonLike) {
      let obj;
      try {
        obj = fm.readOne(targetFile, "target", lang);
      } catch (e) {
        if (e instanceof ValueError || e.code) throw new BadTarget(`${lang}: could not parse ${targetFile}: ${e.code ? osMessage(e) : e.message}${hint(e)}`);
        throw e;
      }
      if (kind === "markdown" && obj.size !== sourceObj.size) {
        // A document has no keys: its blocks are paired by position, so a block added, merged or
        // lost anywhere shifts every one after it.
        obj.skipPairwise = true;
        obj.notes.push({
          key: "", severity: "error",
          msg: `${obj.size} translatable blocks, the source has ${sourceObj.size}: a heading, paragraph, list item or table cell was added, merged or lost`,
        });
      }
      return obj;
    }
    let obj;
    try {
      obj = parseJson(readText(targetFile));
    } catch (e) {
      if (e instanceof ValueError || e.code) throw new BadTarget(`${lang}: could not parse ${targetFile}: ${e.code ? osMessage(e) : e.message}${hint(e)}`);
      throw e;
    }
    if (!(obj instanceof Map)) throw new BadTarget(`${lang}: ${targetFile} is not a JSON object`);
    return isArb ? stripArbMeta(obj) : obj;
  };

  // "keep" is the documented key `kaeris init` writes; flag wins, then either config key.
  let glossary;
  if (args.glossary) glossary = args.glossary.split(",").map((t) => strip(t)).filter(Boolean);
  else {
    const g = cfgGet(config, "keep");
    const g2 = cfgGet(config, "glossary");
    glossary = truthy(g) ? g : truthy(g2) ? g2 : null;
    if (Array.isArray(glossary)) glossary = glossary.filter((t) => typeof t === "string");
    else if (glossary !== null) glossary = null;
  }
  let result;
  try {
    result = checkLocales(sourceObj, langs, loadTarget, glossary);
  } catch (e) {
    if (e instanceof BadTarget) {
      err(e.message);
      return 2;
    }
    throw e;
  }

  if (args.sarif) {
    // Written even when nothing is wrong: upload-sarif fails a workflow if the file is missing.
    try {
      sarif.write(args.sarif, sarif.build(result, path, resolveTarget, process.cwd()));
      if (!args.json) info(`SARIF report → ${args.sarif}`);
    } catch (e) {
      if (!e.code) throw e;
      warn(`Could not write SARIF report to ${args.sarif}: ${osMessage(e)}`);
    }
  }

  const faults = result.faults;
  const warnings = result.warnings;
  const sum = (m) => [...m.values()].reduce((n, v) => n + v.length, 0);
  const nMissing = sum(result.missing);
  const nExtra = sum(result.extra);
  const nPh = result.placeholder_issues.length;
  const nFaults = faults.length;
  const nWarn = warnings.length;
  // RED (missing / placeholder mismatch / detector fault) already flips result.ok. --strict
  // escalates the soft signals (extra/stale keys, YELLOW warnings) to a failure.
  const fail = !result.ok || (args.strict && (nExtra || nWarn));

  if (!args.json) {
    for (const lang of langs) {
      if (result.missing_files.includes(lang)) {
        err(merged ? `${lang}: no translations in ${path}` : `${lang}: locale file not found (${resolveTarget(lang)})`);
        continue;
      }
      const missing = result.missing.get(lang) || [];
      const extra = result.extra.get(lang) || [];
      const issues = result.placeholder_issues.filter((i) => i.lang === lang);
      const langFaults = faults.filter((f) => f.lang === lang);
      const langWarns = warnings.filter((w) => w.lang === lang);
      if (!missing.length && !extra.length && !issues.length && !langFaults.length && !langWarns.length) {
        ok(`${lang}: complete & placeholder-safe`);
        continue;
      }
      if (missing.length)
        err(`${lang}: ${missing.length} missing key(s): ${missing.slice(0, 10).map(prettyKey).join(", ")}${missing.length > 10 ? " ..." : ""}`);
      if (issues.length) {
        err(`${lang}: ${issues.length} placeholder mismatch(es):`);
        for (const i of issues)
          out(prettyKey(`    ${i.key}: missing ${i.missing.length ? reprList(i.missing) : "-"}, added ${i.added.length ? reprList(i.added) : "-"}`));
      }
      if (langFaults.length) {
        err(`${lang}: ${langFaults.length} translation fault(s):`);
        for (const f of langFaults) out(prettyKey(f.key ? `    ${f.key}: ${f.msg}` : `    ${f.msg}`));
      }
      if (langWarns.length) {
        (args.strict ? err : warn)(`${lang}: ${langWarns.length} warning(s):`);
        for (const w of langWarns) out(prettyKey(`    ${w.key ?? ""}: ${w.msg}`.trimEnd()));
      }
      if (extra.length)
        (args.strict ? err : warn)(`${lang}: ${extra.length} extra/stale key(s): ${extra.slice(0, 10).map(prettyKey).join(", ")}${extra.length > 10 ? " ..." : ""}`);
    }

    if (fail) {
      const parts = [];
      if (nMissing) parts.push(`${nMissing} missing ${plural(nMissing, "key", "keys")}`);
      if (nPh) parts.push(`${nPh} placeholder ${plural(nPh, "mismatch", "mismatches")}`);
      if (nFaults) parts.push(`${nFaults} translation ${plural(nFaults, "fault", "faults")}`);
      if (args.strict && nWarn) parts.push(`${nWarn} ${plural(nWarn, "warning", "warnings")} (--strict)`);
      if (args.strict && nExtra) parts.push(`${nExtra} extra ${plural(nExtra, "key", "keys")} (--strict)`);
      err(parts.join(", "));
    } else if (nWarn) warn(`${nWarn} warning(s) — non-blocking (use --strict to fail on them)`);
    else ok("all locales complete & placeholder-safe");
  }

  if (args.json) process.stdout.write(dumps(result, 2, false) + "\n");

  return fail ? 1 : 0;
}

// Node's file-system error in the words Python's OSError uses: [Errno N] reason: 'path'
const ERRNO = { ENOENT: [2, "No such file or directory"], EACCES: [13, "Permission denied"], EISDIR: [21, "Is a directory"], EPERM: [1, "Operation not permitted"] };
function osMessage(e) {
  const [n, text] = ERRNO[e.code] ?? [0, e.message];
  return `[Errno ${n}] ${text}${e.path ? `: '${e.path}'` : ""}`;
}

// ── entry point ───────────────────────────────────────────────────────────────

export function main(argv) {
  try {
    const g = readOptions(GLOBAL_OPTIONS, argv, 0, true);
    if (g.unknown.length && !g.values.help) throw new UsageError(`unrecognized arguments: ${g.unknown.join(" ")}`);
    if (g.values.version) {
      process.stdout.write(`kaeris ${version}\n`);
      return 0;
    }
    const command = argv[g.next];
    if (g.values.help || command === undefined) {
      const stream = g.values.help ? process.stdout : process.stdout;
      stream.write(MAIN_HELP);
      return g.values.help ? 0 : 1;
    }
    if (!COMMANDS.includes(command)) {
      throw new UsageError(`argument command: invalid choice: '${command}' (choose from ${COMMANDS.map((c) => `'${c}'`).join(", ")})`);
    }
    if (command !== "check") {
      err(`kaeris ${command} is not in this package: it needs the Python version, which has translate, fix, languages, init and quota. Install it with: pipx install kaeris`);
      return 2;
    }
    const c = readOptions(CHECK_OPTIONS, argv, g.next + 1, false);
    if (c.values.help) {
      process.stdout.write(CHECK_HELP);
      return 0;
    }
    const extra = [...c.unknown, ...c.positional];
    if (extra.length) throw new UsageError(`unrecognized arguments: ${extra.join(" ")}`);
    return cmdCheck(c.values, g.values);
  } catch (e) {
    if (e instanceof UsageError) {
      process.stderr.write(`${MAIN_USAGE}\nkaeris: error: ${e.message}\n`);
      return 2;
    }
    if (e instanceof KaerisError || e instanceof TooDeep) {
      err(e.message);
      return 2;
    }
    throw e;
  }
}
