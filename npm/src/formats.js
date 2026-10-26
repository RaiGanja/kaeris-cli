// Locale files of every format `translate` writes, read into {key: text} for `check`. The readers
// mirror the server's parsers: the same keys and the same text per key. Two deliberate
// differences, both because `check` compares a translation with its source:
//   * a plural group is read as one ICU string (Android <plurals>, Fluent selectors) or kept per
//     msgstr[n] (.po), so the plural rules of the TARGET language decide what is missing;
//   * in a TARGET file an entry that is empty where the format means "not translated yet"
//     (.po msgstr "", an XLIFF <target> that is empty or still state="new", a blank CSV cell)
//     is absent, so it is reported as a missing key.
// Keys that are not the developer's own text are written between ⟦ ⟧.
import { readText } from "./encoding.js";
import { ValueError } from "./errors.js";
import { flattenAll } from "./flatten.js";
import * as ftl from "./ftl.js";
import { parseMarkdown } from "./mdformat.js";
import * as yaml from "./miniyaml.js";
import { rx, strip, lstrip, rstrip } from "./py.js";
import * as xc from "./xcstrings.js";
import * as xml from "./xml.js";

const OPEN = "⟦";
const CLOSE = "⟧";

export class FormatError extends ValueError {}

// {key: text} plus what the reader found out about the file as a whole. `notes` are findings
// that belong to no single key ({key, severity, msg}); `skipPairwise` says the strings cannot be
// paired with the source's by key; `countOptional` names the keys whose text may leave a lone
// count out (a .po singular).
export class Flat extends Map {
  constructor(entries) {
    super(entries);
    this.notes = [];
    this.skipPairwise = false;
    this.countOptional = new Set();
  }
}

const EXTENSIONS = [
  [".json", "json"], [".arb", "arb"], [".xcstrings", "xcstrings"], [".yml", "yaml"],
  [".yaml", "yaml"], [".strings", "strings"], [".po", "po"], [".xml", "android"],
  [".csv", "csv"], [".xliff", "xliff"], [".xlf", "xliff"], [".properties", "properties"],
  [".resx", "resx"], [".ftl", "ftl"], [".md", "markdown"], [".mdx", "markdown"],
  [".markdown", "markdown"],
];
// One file holds the source and every language: nothing to look up per language on disk.
export const MERGED = ["xcstrings", "csv"];

export const SUPPORTED_TEXT =
  "JSON, ARB, YAML, .strings, .xcstrings, .po, Android XML, CSV, XLIFF, .properties, .resx, Fluent and Markdown";

export function kindOf(path) {
  const name = path.toLowerCase();
  for (const [ext, kind] of EXTENSIONS) if (name.endsWith(ext)) return kind;
  return null;
}

const key = (base, ...parts) => base + parts.map((p) => `${OPEN}${p}${CLOSE}`).join("");

// The entry for `lang` in a file that holds several, spelled however the file spells it.
export function findLang(byLang, lang) {
  if (byLang.has(lang)) return byLang.get(lang);
  const want = lang.replace(/_/g, "-").toLowerCase();
  for (const [name, value] of byLang) if (String(name).replace(/_/g, "-").toLowerCase() === want) return value;
  return null;
}

// Python's str.splitlines()
export function splitlines(text) {
  const out = text.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u{2028}\u{2029}]/u);
  if (out.length && out[out.length - 1] === "") out.pop();
  return out;
}

// ── .strings ──────────────────────────────────────────────────────────────────

const STRINGS_RE = rx('"((?:[^"\\\\]|\\\\.)*)"\\s*=\\s*"((?:[^"\\\\]|\\\\.)*)"\\s*;', "g");

// One left-to-right pass, so an escaped backslash is consumed as one unit.
function unescape(s, mapping) {
  if (!s.includes("\\")) return s;
  const out = [];
  const n = s.length;
  let i = 0;
  while (i < n) {
    const c = s[i];
    if (c === "\\" && i + 1 < n) {
      const nxt = s[i + 1];
      if (Object.hasOwn(mapping, nxt)) out.push(mapping[nxt]);
      else out.push("\\", nxt);
      i += 2;
    } else {
      out.push(c);
      i++;
    }
  }
  return out.join("");
}

const STRINGS_UNESCAPE = { '"': '"', "\\": "\\", n: "\n", t: "\t", r: "\r" };
const PO_UNESCAPE = { '"': '"', "\\": "\\", n: "\n", t: "\t" };

function readStrings(text) {
  const out = new Flat();
  for (const m of text.matchAll(STRINGS_RE)) out.set(unescape(m[1], STRINGS_UNESCAPE), unescape(m[2], STRINGS_UNESCAPE));
  return out;
}

// ── .po ───────────────────────────────────────────────────────────────────────

function poUnquote(s) {
  s = strip(s);
  if (s.length >= 2 && s[0] === '"' && s[s.length - 1] === '"') return s.slice(1, -1);
  return s;
}

function poRecords(text) {
  const records = [];
  let cur = null;
  let field = null;
  let idx = 0;
  const start = () => {
    cur = { ctx: null, id: null, idPlural: null, strs: new Map() };
    records.push(cur);
  };
  for (const line of splitlines(text)) {
    const s = strip(line);
    if (!s || s.startsWith("#")) continue;
    if (s.startsWith("msgctxt ")) {
      start();
      field = "ctx";
      cur.ctx = unescape(poUnquote(s.slice(8)), PO_UNESCAPE);
    } else if (s.startsWith("msgid_plural ")) {
      field = "id_plural";
      if (cur !== null) cur.idPlural = unescape(poUnquote(s.slice(13)), PO_UNESCAPE);
    } else if (s.startsWith("msgid ")) {
      if (cur === null || field !== "ctx" || cur.id !== null) start();
      field = "id";
      cur.id = unescape(poUnquote(s.slice(6)), PO_UNESCAPE);
    } else if (s.startsWith("msgstr[")) {
      const close = s.indexOf("]");
      idx = close > 7 && /^[0-9]+$/.test(s.slice(7, close)) ? parseInt(s.slice(7, close), 10) : 0;
      field = "str";
      if (cur !== null) cur.strs.set(idx, unescape(poUnquote(s.slice(close + 1)), PO_UNESCAPE));
    } else if (s.startsWith("msgstr ")) {
      field = "str";
      idx = 0;
      if (cur !== null) cur.strs.set(0, unescape(poUnquote(s.slice(7)), PO_UNESCAPE));
    } else if (s.startsWith('"') && s.endsWith('"') && s.length >= 2 && cur !== null && field) {
      const chunk = unescape(s.slice(1, -1), PO_UNESCAPE);
      if (field === "ctx") cur.ctx = (cur.ctx || "") + chunk;
      else if (field === "id") cur.id = (cur.id || "") + chunk;
      else if (field === "id_plural") cur.idPlural = (cur.idPlural || "") + chunk;
      else cur.strs.set(idx, (cur.strs.get(idx) ?? "") + chunk);
    }
  }
  return records;
}

// The number of forms gettext needs per language. Only languages with three or more are
// enforced: those are where a header copied from an English file ("nplurals=2") is certainly wrong.
const NPLURALS = { ru: 3, uk: 3, hr: 3, sr: 3, pl: 3, cs: 3, sk: 3, ro: 3, lt: 3, lv: 3, sl: 4, ar: 6 };

// Keys are the msgid (with ⟦ctx:…⟧ for a msgctxt, ⟦plural⟧ for the plural form, ⟦dup:n⟧ for a
// repeat). As a target, an empty msgstr is absent, and msgstr[2..n] are kept as ⟦plural:2⟧… with
// a note for a plural that has fewer forms than its header declares.
function readPo(text, role = "source", lang = "") {
  const records = poRecords(text);
  const out = new Flat();
  const seen = new Map();
  let header = null;

  const uniq = (base) => {
    if (!seen.has(base)) {
      seen.set(base, 0);
      return base;
    }
    seen.set(base, seen.get(base) + 1);
    return key(base, `dup:${seen.get(base)}`);
  };

  for (const r of records) {
    const mid = r.id;
    if (mid === "" && header === null) {
      header = r.strs.get(0) || "";
      continue;
    }
    if (mid === null || mid === "") continue;
    const base = r.ctx ? key(mid, `ctx:${r.ctx}`) : mid;
    const s0 = r.strs.get(0);
    const s1 = r.strs.get(1);
    const target = role === "target";
    if (r.idPlural === null) {
      const k = uniq(base);
      if (s0 || !target) out.set(k, s0 ? s0 : mid);
      continue;
    }
    const k0 = uniq(base);
    const k1 = key(k0, "plural");
    out.countOptional.add(k0);
    if (s0 || !target) out.set(k0, s0 ? s0 : mid);
    if (s1 || !target) out.set(k1, s1 ? s1 : r.idPlural);
    if (target) {
      for (const n of [...r.strs.keys()].filter((i) => i >= 2).sort((a, b) => a - b)) {
        if (r.strs.get(n)) out.set(key(k0, `plural:${n}`), r.strs.get(n));
      }
    }
  }
  if (role === "target") poPluralNotes(out, records, header, lang);
  return out;
}

const NPLURALS_RE = rx("nplurals\\s*=\\s*(\\d+)");

function poPluralNotes(out, records, header, lang) {
  let declared = null;
  const m = NPLURALS_RE.exec(header || "");
  if (m) declared = parseInt(m[1], 10);
  const baseLang = (lang || "").replace(/_/g, "-").split("-")[0].toLowerCase();
  const need = NPLURALS[baseLang];
  if (declared !== null && need && declared < need) {
    out.notes.push({
      key: "", severity: "error",
      msg: `Plural-Forms declares nplurals=${declared}, but ${baseLang} needs ${need} plural forms — the extra forms can never be selected`,
    });
  }
  if (!declared || declared <= 2) return;
  for (const r of records) {
    if (r.idPlural === null || !r.id) continue;
    const absent = [];
    for (let i = 2; i < declared; i++) if (!r.strs.get(i)) absent.push(i);
    if (absent.length && r.strs.get(0) && r.strs.get(1)) {
      const chars = [...r.id];
      const shown = chars.length <= 50 ? r.id : chars.slice(0, 47).join("") + "...";
      const forms = absent.map((i) => `msgstr[${i}]`).join(", ");
      out.notes.push({ key: shown, severity: "error", msg: `the file declares ${declared} plural forms, this entry has no ${forms}` });
    }
  }
}

// ── XML (Android, XLIFF, RESX) ────────────────────────────────────────────────

const XML_DTD_RE = /<!(DOCTYPE|ENTITY)/i;
const TAG_RE = rx("</?[a-zA-Z0-9][\\w:-]*(?:\\s[^<>]*?)?/?>", "g");

// the text as expat could see it: as is, and the same bytes read as UTF-16 either way round
function expatViews(text) {
  const raw = Buffer.from(text, "utf8");
  const le = raw.subarray(0, raw.length - (raw.length % 2)).toString("utf16le");
  const be = Buffer.from(raw.subarray(0, raw.length - (raw.length % 2))).swap16().toString("utf16le");
  return [text, le, be];
}

function xmlRoot(text, label) {
  for (const view of expatViews(text)) {
    if (XML_DTD_RE.test(view)) throw new FormatError(`${label} must not contain DOCTYPE or ENTITY declarations`);
  }
  try {
    return xml.parse(text);
  } catch (e) {
    if (e instanceof xml.XmlError) throw new FormatError(`Invalid ${label} XML: ${e.message}`);
    throw e;
  }
}

const localTag = (tag) => (typeof tag === "string" ? tag.slice(tag.lastIndexOf("}") + 1) : tag);

function stripNs(root) {
  for (const el of root.iter()) {
    el.tag = localTag(el.tag);
    for (const k of [...el.attrib.keys()]) {
      if (k.includes("}")) {
        const v = el.attrib.get(k);
        el.attrib.delete(k);
        el.attrib.set(k.split("}").slice(1).join("}"), v);
      }
    }
  }
}

const ANDROID_UNESCAPE_RE = rx("\\\\(.)", "g");

const androidUnescape = (s) => s.replace(ANDROID_UNESCAPE_RE, (_, c) => (c === "n" ? "\n" : c));

function androidTextProtect(seg) {
  seg = seg.replace(/&(?=(?:lt|gt|amp);)/g, "&amp;");
  return seg.replace(TAG_RE, (m) => m.replace(/</g, "&lt;").replace(/>/g, "&gt;"));
}

function androidInner(el) {
  const parts = [androidTextProtect(androidUnescape(el.text || ""))];
  for (const child of el.children) {
    const tail = child.tail;
    child.tail = null;
    try {
      parts.push(xml.tostring(child));
    } finally {
      child.tail = tail;
    }
    parts.push(androidTextProtect(androidUnescape(tail || "")));
  }
  return strip(parts.join(""));
}

const CLDR_ORDER = ["zero", "one", "two", "few", "many", "other"];

// printf-family specifier
const SPEC_RE = rx("%(?:\\d+\\$)?[-+ #0]*[\\d.*]*(?:hh|h|ll|l|q|L|z|t|j)?[@dioupxXeEfgGcsSaAn%]", "g");
const INT_CONV = new Set("diuoxX");

// An arm whose only printf specifier is an integer ("%d files") is written with ICU's own `#`
// for the number: many correct translations leave the number out of the singular.
export function loneCountToHash(text) {
  const found = [...text.matchAll(SPEC_RE)].filter((m) => m[0] !== "%%");
  if (found.length === 1 && INT_CONV.has(found[0][0].slice(-1))) {
    const m = found[0];
    return text.slice(0, m.index) + "#" + text.slice(m.index + m[0].length);
  }
  return text;
}

function balanced(text) {
  let depth = 0;
  for (const ch of text) {
    if (ch === "{") depth++;
    if (ch === "}") depth--;
    if (depth < 0) return false;
  }
  return depth === 0;
}

// <string>, <plurals> and <string-array>. Each <plurals> is ONE ICU string, with a lone `%d`
// written `#`.
function readAndroid(text) {
  const root = xmlRoot(text, "Android XML");
  if (root.tag !== "resources") throw new FormatError("Android XML root element must be <resources>");
  const out = new Flat();
  for (const el of root.children) {
    if (String(el.get("translatable", "true")).toLowerCase() === "false") continue;
    const name = strip(el.get("name", ""));
    if (!name) continue;
    if (el.tag === "string") out.set(name, androidInner(el));
    else if (el.tag === "plurals") {
      const items = new Map();
      for (const item of el.children) {
        if (item.tag !== "item") continue;
        const qty = strip(item.get("quantity", ""));
        const value = androidInner(item);
        if (qty && value) items.set(qty, value);
      }
      const texts = new Map(CLDR_ORDER.filter((c) => items.has(c)).map((c) => [c, items.get(c)]));
      if (texts.has("other") && [...texts.values()].every(balanced) && texts.size === items.size) {
        const arms = CLDR_ORDER.filter((c) => texts.has(c)).map((c) => `${c} {${loneCountToHash(texts.get(c))}}`).join(" ");
        out.set(key(name, "plural"), `{count, plural, ${arms}}`);
      } else for (const [qty, value] of items) out.set(key(name, `plural:${qty}`), value);
    } else if (el.tag === "string-array") {
      el.children.filter((it) => it.tag === "item").forEach((item, i) => out.set(key(name, `array:${i}`), androidInner(item)));
    }
  }
  return out;
}

function innerXml(el) {
  return strip([el.text || "", ...el.children.map((c) => xml.tostring(c))].join(""));
}

const UNTRANSLATED_STATES = new Set(["new", "needs-translation", "needs-adaptation", "needs-l10n"]);

// XLIFF 1.2: {trans-unit id: <source>}; as a target, {id: <target>} for the units that have one.
function readXliff(text, role = "source") {
  const root = xmlRoot(text, "XLIFF");
  stripNs(root);
  if (localTag(root.tag) !== "xliff") throw new FormatError("XLIFF root element must be <xliff>");
  if (strip(root.get("version") || "").startsWith("2"))
    throw new FormatError("XLIFF 2.0 is not supported yet — export XLIFF 1.2 (<trans-unit> with <source>/<target>)");
  const out = new Flat();
  for (const fel of root.children) {
    if (localTag(fel.tag) !== "file") continue;
    for (const tu of fel.iter()) {
      if (localTag(tu.tag) !== "trans-unit") continue;
      const uid = tu.get("id");
      if (uid === null) continue;
      let srcEl = null;
      let tgtEl = null;
      for (const child of tu.children) {
        const lt = localTag(child.tag);
        if (lt === "source") srcEl = child;
        else if (lt === "target") tgtEl = child;
      }
      if (srcEl === null) continue;
      let value;
      if (role === "source") value = innerXml(srcEl);
      else {
        value = tgtEl !== null ? innerXml(tgtEl) : "";
        if (tgtEl !== null && UNTRANSLATED_STATES.has((tgtEl.get("state") || "").toLowerCase())) value = "";
      }
      if (value) out.set(uid, value);
    }
  }
  return out;
}

function readResx(text) {
  const root = xmlRoot(text, "RESX");
  stripNs(root);
  if (localTag(root.tag) !== "root") throw new FormatError("RESX root element must be <root>");
  const out = new Flat();
  for (const el of root.children) {
    if (localTag(el.tag) !== "data") continue;
    const name = el.get("name");
    if (!name || el.get("type") || el.get("mimetype")) continue;
    const valueEl = el.children.find((c) => localTag(c.tag) === "value");
    out.set(name, (valueEl ? valueEl.text : el.text) || "");
  }
  return out;
}

// ── .properties ───────────────────────────────────────────────────────────────

const PROP_ESCAPES = { n: "\n", t: "\t", r: "\r", f: "\f" };

function propertiesUnescape(s) {
  const out = [];
  const n = s.length;
  let i = 0;
  while (i < n) {
    const c = s[i];
    if (c === "\\" && i + 1 < n) {
      const nxt = s[i + 1];
      if (nxt === "u" && i + 6 <= n) {
        const code = /^[0-9a-fA-F]{4}$/.test(s.slice(i + 2, i + 6)) ? parseInt(s.slice(i + 2, i + 6), 16) : null;
        if (code !== null) {
          if (code >= 0xd800 && code <= 0xdbff && s.slice(i + 6, i + 8).toLowerCase() === "\\u") {
            const lowHex = s.slice(i + 8, i + 12);
            const low = /^[0-9a-fA-F]{4}$/.test(lowHex) ? parseInt(lowHex, 16) : null;
            if (low !== null && low >= 0xdc00 && low <= 0xdfff) {
              out.push(String.fromCodePoint(0x10000 + ((code - 0xd800) << 10) + (low - 0xdc00)));
              i += 12;
              continue;
            }
          }
          out.push(String.fromCharCode(code));
          i += 6;
          continue;
        }
      }
      out.push(Object.hasOwn(PROP_ESCAPES, nxt) ? PROP_ESCAPES[nxt] : nxt);
      i += 2;
      continue;
    }
    out.push(c);
    i++;
  }
  return out.join("");
}

function propIsContinuation(s) {
  let cnt = 0;
  let j = s.length - 1;
  while (j >= 0 && s[j] === "\\") {
    cnt++;
    j--;
  }
  return cnt % 2 === 1;
}

function propSplitKv(s) {
  let i = 0;
  const n = s.length;
  const keyChars = [];
  while (i < n) {
    const c = s[i];
    if (c === "\\" && i + 1 < n) {
      keyChars.push(c, s[i + 1]);
      i += 2;
      continue;
    }
    if (" \t\f=:".includes(c)) break;
    keyChars.push(c);
    i++;
  }
  const rawKey = keyChars.join("");
  while (i < n && " \t\f".includes(s[i])) i++;
  if (i < n && "=:".includes(s[i])) {
    i++;
    while (i < n && " \t\f".includes(s[i])) i++;
  }
  return [rawKey, s.slice(i)];
}

function readProperties(text) {
  const out = new Flat();
  const lines = text.split("\n");
  let i = 0;
  const n = lines.length;
  while (i < n) {
    const line = rstrip(lines[i], "\r");
    const stripped = lstrip(line, " \t\f");
    if (stripped === "" || "#!".includes(stripped[0])) {
      i++;
      continue;
    }
    let logical = stripped;
    while (propIsContinuation(logical) && i + 1 < n) {
      i++;
      logical = logical.slice(0, -1) + lstrip(rstrip(lines[i], "\r"), " \t\f");
    }
    i++;
    const [rawKey, rawVal] = propSplitKv(logical);
    out.set(propertiesUnescape(rawKey), propertiesUnescape(rawVal));
  }
  return out;
}

// ── CSV (one file, one column per language) ───────────────────────────────────

const CSV_FIELD_LIMIT = 131072;
const CSV_FORMULA_RE = /^'*[=+\-@\t\r]/;

function csvUnguard(value) {
  const v = value || "";
  return v.startsWith("'") && CSV_FORMULA_RE.test(v.slice(1)) ? v.slice(1) : v;
}

// Python's csv.reader with the default (excel) dialect
function csvRows(text) {
  const rows = [];
  let row = [];
  let field = [];
  let state = "START_RECORD";
  const save = () => {
    row.push(field.join(""));
    field = [];
  };
  const add = (c) => {
    if (field.length >= CSV_FIELD_LIMIT) throw new FormatError(`field larger than field limit (${CSV_FIELD_LIMIT})`);
    field.push(c);
  };
  const feed = (c) => {
    // c === null is the end of a line
    switch (state) {
      case "START_RECORD":
        if (c === null) {
          rows.push([]);
          return;
        }
        if (c === "\n" || c === "\r") {
          state = "EAT_CRNL";
          return;
        }
        state = "START_FIELD";
      // falls through
      case "START_FIELD":
        if (c === "\n" || c === "\r" || c === null) {
          save();
          state = c === null ? "END" : "EAT_CRNL";
        } else if (c === '"') state = "IN_QUOTED_FIELD";
        else if (c === ",") save();
        else {
          add(c);
          state = "IN_FIELD";
        }
        return;
      case "IN_FIELD":
        if (c === "\n" || c === "\r" || c === null) {
          save();
          state = c === null ? "END" : "EAT_CRNL";
        } else if (c === ",") {
          save();
          state = "START_FIELD";
        } else add(c);
        return;
      case "IN_QUOTED_FIELD":
        if (c === null) return;
        if (c === '"') state = "QUOTE_IN_QUOTED_FIELD";
        else add(c);
        return;
      case "QUOTE_IN_QUOTED_FIELD":
        if (c === '"') {
          add(c);
          state = "IN_QUOTED_FIELD";
        } else if (c === ",") {
          save();
          state = "START_FIELD";
        } else if (c === "\n" || c === "\r" || c === null) {
          save();
          state = c === null ? "END" : "EAT_CRNL";
        } else {
          add(c);
          state = "IN_FIELD";
        }
        return;
      case "EAT_CRNL":
        if (c === "\n" || c === "\r") return;
        if (c === null) {
          state = "END";
          return;
        }
        throw new FormatError("new-line character seen in unquoted field - do you need to open the file with newline=''?");
      default:
    }
  };
  const flushRow = () => {
    if (state === "END") {
      rows.push(row);
      row = [];
      field = [];
      state = "START_RECORD";
    }
  };
  // lines end with "\n" (that is how a StringIO is iterated); each line is closed by an end marker
  const lines = text.split(/(?<=\n)/);
  for (const line of lines) {
    for (const c of line) feed(c);
    feed(null);
    flushRow();
  }
  if (state === "IN_QUOTED_FIELD" || field.length) {
    save();
    rows.push(row);
  }
  return rows;
}

// [source column name, Map(column name -> Flat)]. The first column is the key, the second the
// source text, every later column a language. A blank cell is absent.
function readCsv(text) {
  const rows = csvRows(text).filter((r) => r.some((c) => strip(c || "")));
  if (rows.length < 2) throw new FormatError("a CSV needs a header row and a key column + a source-text column");
  const header = rows[0].map((h) => strip(h || ""));
  if (header.length < 2 || !header[1]) throw new FormatError("the CSV header must name the source-text column (second column)");
  const byCol = new Map();
  for (const h of header.slice(1)) if (h) byCol.set(h, new Flat());
  for (const r of rows.slice(1)) {
    if (!r.length || !strip(r[0] || "")) continue;
    const k = csvUnguard(strip(r[0]));
    header.slice(1).forEach((name, i) => {
      const col = i + 1;
      if (name && col < r.length && strip(r[col] || "")) byCol.get(name).set(k, csvUnguard(r[col]));
    });
  }
  return [header[1], byCol];
}

// ── YAML, Fluent, Markdown ────────────────────────────────────────────────────

const LOCALE_ROOT_RE = rx("^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,4})*$");

// The document without its locale root (`en:` / `de:` / `pt-BR:`), leaves flattened.
function readYaml(text) {
  let data;
  try {
    data = yaml.load(text);
  } catch (e) {
    if (e instanceof yaml.YamlUnsupported)
      throw new FormatError(`${e.message} (kaeris check reads the YAML that locale files use; it will not guess at the rest)`);
    if (e instanceof yaml.YamlError) throw new FormatError(`Invalid YAML: ${e.message}`);
    throw e;
  }
  if (data.size === 1) {
    const [k, v] = data.entries().next().value;
    if (LOCALE_ROOT_RE.test(k) && v instanceof Map) data = v;
  }
  return new Flat(flattenAll(data));
}

function readFluent(text) {
  const [flat, problems] = ftl.parse(text);
  const out = new Flat(flat);
  out.notes = problems.map((p) => ({ key: "", severity: "warn", msg: p }));
  return out;
}

const readMarkdown = (text) => new Flat(parseMarkdown(text.replace(/\r\n/g, "\n")));

// ── the door ──────────────────────────────────────────────────────────────────

// One file, one language, as {key: text}. `role` is "target" for a translation.
export function readOne(path, role = "source", lang = "") {
  const kind = kindOf(path);
  let text;
  try {
    text = readText(path);
  } catch (e) {
    if (e instanceof ValueError) throw new FormatError(`cannot read ${path}: ${e.message}`);
    throw e;
  }
  try {
    if (kind === "strings") return readStrings(text);
    if (kind === "po") return readPo(text, role, lang);
    if (kind === "yaml") return readYaml(text);
    if (kind === "android") return readAndroid(text);
    if (kind === "xliff") return readXliff(text, role);
    if (kind === "properties") return readProperties(text);
    if (kind === "resx") return readResx(text);
    if (kind === "ftl") return readFluent(text);
    if (kind === "markdown") return readMarkdown(text);
  } catch (e) {
    if (e instanceof RangeError) throw new FormatError("the file is nested deeper than the readers handle");
    throw e;
  }
  throw new FormatError(`${path}: no reader for this format`);
}

// [source language, Map(language -> Flat)] for a file that holds all of them.
export function readMerged(path) {
  const kind = kindOf(path);
  let text;
  try {
    text = readText(path);
  } catch (e) {
    if (e instanceof ValueError) throw new FormatError(`cannot read ${path}: ${e.message}`);
    throw e;
  }
  if (kind === "csv") return readCsv(text);
  return xc.load(path);
}
