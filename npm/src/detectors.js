// Offline, deterministic i18n quality detectors: the per-string and per-file checks `check` runs,
// ported from kaeris/detectors.py. Only the checking half is here (the repair helpers the
// Python module also holds are not part of `check`). Patterns and word lists come from
// tables.js, which is generated from the Python source.
import T from "./tables.js";
import {
  rx, escapeRe, strip, splitWs, plen, first, sorted, pyRound, setDiff, isLowerChar, Counter,
} from "./py.js";

export const ERROR = "error";
export const WARN = "warn";

const re = (t, flags = "") => rx(t.source, (t.i ? "i" : "") + flags);
const PH_RE = re(T.PH_RE, "g");
const ICU_HEAD_RE = rx("^" + T.ICU_HEAD_RE.source);
const ICU_SELECTOR_VAR_RE = rx("^" + T.ICU_SELECTOR_VAR_RE.source);
const MAX_ICU_DEPTH = T.MAX_ICU_DEPTH;

const allMatches = (regex, text) => [...text.matchAll(regex)].map((m) => m[0]);

// Balanced top-level {...} spans: a stack of unmatched '{' positions; a '}' that empties the
// stack closes a top-level span.
function braceSpans(text) {
  const spans = [];
  const stack = [];
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === "{") stack.push(i);
    else if (ch === "}" && stack.length) {
      const start = stack.pop();
      if (!stack.length) spans.push([start, i + 1]);
    }
  }
  return spans;
}

// Placeholders as ICU semantics define them: the argument of a plural/select block is the
// placeholder, its arms are text (recursed into), everything else is scanned flat.
function placeholderList(text, depth = 0) {
  const out = [];
  const rest = [];
  let pos = 0;
  for (const [bs, be] of braceSpans(text)) {
    const seg = text.slice(bs, be);
    const head = ICU_HEAD_RE.exec(seg);
    if (!head) continue;
    rest.push(text.slice(pos, bs));
    pos = be;
    const v = ICU_SELECTOR_VAR_RE.exec(seg);
    if (v) out.push("{" + v[1] + "}");
    const body = seg.slice(head[0].length, -1);
    if (depth < MAX_ICU_DEPTH) {
      for (const [as, ae] of braceSpans(body)) out.push(...placeholderList(body.slice(as + 1, ae - 1), depth + 1));
    }
  }
  rest.push(text.slice(pos));
  out.push(...allMatches(PH_RE, rest.join(" ")));
  return out;
}

// The text a user actually SEES: ICU markup replaced by the arm that renders longest.
function icuVisibleText(text, depth = 0) {
  if (depth >= MAX_ICU_DEPTH) return text;
  const out = [];
  let pos = 0;
  for (const [bs, be] of braceSpans(text)) {
    const seg = text.slice(bs, be);
    const head = ICU_HEAD_RE.exec(seg);
    if (!head) continue;
    out.push(text.slice(pos, bs));
    pos = be;
    const body = seg.slice(head[0].length, -1);
    const arms = braceSpans(body).map(([s, e]) => icuVisibleText(body.slice(s + 1, e - 1), depth + 1));
    let best = "";
    let bestLen = -1;
    for (const a of arms) {
      const n = plen(a);
      if (n > bestLen) [best, bestLen] = [a, n];
    }
    out.push(arms.length ? best : "");
  }
  out.push(text.slice(pos));
  return out.join("");
}

const findPlaceholders = (text) => new Set(placeholderList(text));

function lostPlaceholders(original, translated) {
  return sorted(setDiff(findPlaceholders(original), findPlaceholders(translated)));
}

// Counter addition keeps only positive counts, as Python's Counter does.
function counterAdd(a, b) {
  const out = new Counter();
  for (const k of new Set([...a.keys(), ...b.keys()])) {
    const n = a.get(k) + b.get(k);
    if (n > 0) out.set(k, n);
  }
  return out;
}

// How many times a placeholder can appear in the RENDERED string: [most, fewest].
function placeholderArity(text, depth = 0) {
  const outside = [];
  const armMax = new Counter();
  const armMin = new Counter();
  let pos = 0;
  for (const [bs, be] of braceSpans(text)) {
    const seg = text.slice(bs, be);
    const head = ICU_HEAD_RE.exec(seg);
    if (!head) continue;
    outside.push(text.slice(pos, bs));
    pos = be;
    const v = ICU_SELECTOR_VAR_RE.exec(seg);
    if (v) outside.push("{" + v[1] + "}");
    const body = seg.slice(head[0].length, -1);
    const perArm = [];
    if (depth < MAX_ICU_DEPTH) {
      for (const [as, ae] of braceSpans(body)) perArm.push(placeholderArity(body.slice(as + 1, ae - 1), depth + 1));
    }
    if (perArm.length) {
      const keys = new Set();
      for (const [mx] of perArm) for (const k of mx.keys()) keys.add(k);
      for (const k of keys) {
        armMax.add(k, Math.max(...perArm.map(([mx]) => mx.get(k))));
        armMin.add(k, Math.min(...perArm.map(([, mn]) => mn.get(k))));
      }
    }
  }
  outside.push(text.slice(pos));
  const flat = new Counter();
  for (const m of allMatches(PH_RE, outside.join(" "))) flat.add(m);
  return [counterAdd(flat, armMax), counterAdd(flat, armMin)];
}

// An invented placeholder, or one used a different number of times.
function placeholderTypeFaults(original, translated) {
  const [srcMax, srcMin] = placeholderArity(original);
  const [trMax, trMin] = placeholderArity(translated);
  const faults = [];
  for (const ph of sorted(new Set([...srcMax.keys(), ...trMax.keys()]))) {
    if (ph === "%%") continue;
    if (trMax.get(ph) > srcMax.get(ph)) {
      faults.push(
        srcMax.get(ph) === 0
          ? `invented placeholder ${ph} (not in source)`
          : `placeholder ${ph} appears ${trMax.get(ph)}× vs ${srcMax.get(ph)}× in source (duplicated)`,
      );
    } else if (srcMin.get(ph) > trMin.get(ph)) {
      if (trMax.get(ph) === 0) continue;
      faults.push(
        trMax.get(ph) !== trMin.get(ph) || srcMax.get(ph) !== srcMin.get(ph)
          ? `placeholder ${ph} is missing from some plural/select arms (${trMin.get(ph)}× in the thinnest arm vs ${srcMin.get(ph)}× in the source)`
          : `placeholder ${ph} appears ${srcMin.get(ph)}× in source but ${trMin.get(ph)}× in translation`,
      );
    }
  }
  return faults;
}

// ── numbers ───────────────────────────────────────────────────────────────────

const NUM_RUN_RE = re(T.NUM_RUN_RE, "g");
const DIGITS = new Map(Object.entries(T.DIGITS));
const NUM_SEP = T.NUM_SEP;
const MAGNITUDE_SUFFIX_RE = re(T.MAGNITUDE_SUFFIX_RE, "g");
const MAGNITUDE_WORD_RE = re(T.MAGNITUDE_WORD_RE, "g");
const CJK_MAGNITUDE_RE = re(T.CJK_MAGNITUDE_RE, "g");
const NON_DIGIT_RE = /[^0-9]/g;

const translateDigits = (run) => [...run].map((c) => DIGITS.get(c) ?? c).join("");

// Normalized numeric key: tolerates locale grouping (1,000 == 1.000 == 1 000 000) yet still
// distinguishes a decimal VALUE (9.99 != 999, 1.5 != 15).
function numKey(run) {
  const t = translateDigits(run);
  const last = Math.max(...[...NUM_SEP].map((c) => t.lastIndexOf(c)));
  if (last !== -1) {
    const head = t.slice(0, last).replace(NON_DIGIT_RE, "");
    const tail = t.slice(last + 1).replace(NON_DIGIT_RE, "");
    if (head && tail && tail.length !== 3) return head + "." + tail;
  }
  return t.replace(NON_DIGIT_RE, "");
}

// str(int(v)) when v is whole, repr(v) otherwise
function pyFloatText(v) {
  if (Number.isInteger(v)) return BigInt(v).toString();
  return String(v);
}

function magnitudeValue(number, mult) {
  const v = Number((parseFloat(number.replace(",", ".")) * mult).toFixed(6));
  return pyFloatText(v);
}

function cjkValue(m) {
  let total = 0;
  for (const [, n, unit] of m.matchAll(/(\d+(?:\.\d+)?)([億亿萬万만千천]?)/gu)) total += parseFloat(n) * (T.CJK_UNITS[unit] ?? 1);
  return pyFloatText(total);
}

// "15k", "15 tys.", "15 ألف", "1万5千": the same fifteen thousand in each language's own words.
function magnitudes(text) {
  text = text.replace(CJK_MAGNITUDE_RE, (m) => cjkValue(m));
  text = text.replace(MAGNITUDE_SUFFIX_RE, (_, n, suffix) => magnitudeValue(n, T.SUFFIX_MULT[suffix]));
  return text.replace(MAGNITUDE_WORD_RE, (...args) => {
    const g = args[args.length - 1];
    return magnitudeValue(args[1], g.b ? 1e9 : g.m ? 1e6 : 1e3);
  });
}

// [Counter of numeric VALUES, display text for each]
function extractNumbers(text) {
  const stripped = magnitudes(text.replace(PH_RE, " "));
  const counts = new Counter();
  const display = new Map();
  for (const m of allMatches(NUM_RUN_RE, stripped)) {
    const run = strip(m, " ,.'\xa0 ");
    const key = numKey(run);
    if (key) {
      counts.add(key);
      if (!display.has(key)) display.set(key, run);
    }
  }
  return [counts, display];
}

const NUMBER_WORDS = T.NUMBER_WORDS;
const NUMBER_WORD_RE = re(T.NUMBER_WORD_RE, "g");
const NUMBER_RUN_RE = re(T.NUMBER_RUN_RE, "g");
const ICU_ARM_LABEL_RE = re(T.ICU_ARM_LABEL_RE, "g");
const NUMBER_PART_WORDS = T.NUMBER_PART_WORDS;
const NUMBER_SCALES = T.NUMBER_SCALES;

// "two hundred" → 200, "ninety-nine" → 99, "three thousand five hundred" → 3500.
function numberRunValue(run) {
  let total = 0;
  let current = 0;
  for (const w of run.toLowerCase().match(/[a-z]+/g) ?? []) {
    if (w === "and") continue;
    if (Object.hasOwn(NUMBER_SCALES, w)) {
      if (NUMBER_SCALES[w] === 100) current = (current || 1) * 100;
      else {
        total += (current || 1) * NUMBER_SCALES[w];
        current = 0;
      }
    } else current += NUMBER_PART_WORDS[w];
  }
  return total + current;
}

// Numbers the ENGLISH source spells out, as their values. Used only to FORGIVE.
function spelledNumbers(text) {
  const prose = (text || "").replace(ICU_ARM_LABEL_RE, "$1$3");
  const out = new Set();
  for (const m of prose.matchAll(NUMBER_WORD_RE)) {
    const w = m[1].toLowerCase();
    if (!Object.hasOwn(NUMBER_PART_WORDS, w)) out.add(numKey(String(NUMBER_WORDS[w])));
  }
  for (const m of prose.matchAll(NUMBER_RUN_RE)) out.add(numKey(String(numberRunValue(m[0]))));
  return out;
}

const COUNTER_LANGS = new Set(T.COUNTER_LANGS);
const INDEFINITE_ARTICLE_RE = re(T.INDEFINITE_ARTICLE_RE);

// Number drift: 'Delete 5 files' -> 'Delete 50 files', a wrong price, a mangled version.
function numericFaults(original, translated, lang = "") {
  const [src, srcDisp] = extractNumbers(original);
  const [tr, trDisp] = extractNumbers(translated);
  const same = src.size === tr.size && [...src].every(([k, v]) => tr.get(k) === v);
  if (same) return [];
  const faults = [];
  let spelled = spelledNumbers(original);
  const base = (lang || "").replace(/_/g, "-").split("-")[0].toLowerCase();
  if (COUNTER_LANGS.has(base) && INDEFINITE_ARTICLE_RE.test(original)) spelled = new Set([...spelled, numKey("1")]);
  // Counter subtraction: what the translation has more of than the source
  const added = [...tr].filter(([k, v]) => v - src.get(k) > 0).map(([k]) => k).filter((k) => !spelled.has(k));
  for (const num of sorted(added)) faults.push(`number ${trDisp.get(num)} appears in the translation but not the source`);
  if (tr.size) {
    const lost = [...src].filter(([k, v]) => v - tr.get(k) > 0).map(([k]) => k);
    for (const num of sorted(lost)) faults.push(`number ${srcDisp.get(num)} from the source is missing or changed in the translation`);
  }
  return faults;
}

// ── entities, tags, line breaks ───────────────────────────────────────────────

const DOUBLE_ENC_RE = re(T.DOUBLE_ENC_RE, "g");
const BROKEN_UNICODE_ESC_RE = re(T.BROKEN_UNICODE_ESC_RE);

function counterOf(items) {
  const c = new Counter();
  for (const x of items) c.add(x);
  return c;
}

function entityFaults(original, translated) {
  const faults = [];
  const src = counterOf(allMatches(DOUBLE_ENC_RE, original));
  const tr = counterOf(allMatches(DOUBLE_ENC_RE, translated));
  const more = [...tr].filter(([k, v]) => v - src.get(k) > 0).map(([k]) => k);
  for (const ent of sorted(more)) faults.push(`double-encoded entity ${ent} — the & was re-escaped (renders literally)`);
  if (BROKEN_UNICODE_ESC_RE.test(translated) && !BROKEN_UNICODE_ESC_RE.test(original))
    faults.push("broken \\u escape — a unicode escape was mangled to non-hex");
  return faults;
}

const TAG_RE = re(T.TAG_RE, "g");
const TAG_NAME_RE = rx("^</?\\s*([a-zA-Z0-9][\\w:-]*)");

function findTags(text) {
  const out = [];
  for (const raw of allMatches(TAG_RE, text)) {
    const name = TAG_NAME_RE.exec(raw)[1];
    if (raw.startsWith("</")) out.push(`</${name}>`);
    else if (raw.trimEnd().endsWith("/>")) out.push(`<${name}/>`);
    else out.push(`<${name}>`);
  }
  return out;
}

// Tags present in `original` but missing (or fewer) in `translated`.
function lostTags(original, translated) {
  const o = new Map();
  for (const tg of findTags(original)) o.set(tg, (o.get(tg) ?? 0) + 1);
  for (const tg of findTags(translated)) if (o.has(tg)) o.set(tg, o.get(tg) - 1);
  const lost = [];
  for (const [tg, n] of o) for (let i = 0; i < n; i++) lost.push(tg);
  return sorted(lost);
}

const EMPHASIS_RE = re(T.EMPHASIS_RE, "g");

// Intact `*emphasis*`: as written, and with one phrase cut by a line break counted once.
function emphasisCounts(text) {
  const spans = [...text.matchAll(EMPHASIS_RE)];
  let joints = 0;
  for (let i = 0; i + 1 < spans.length; i++) {
    const a = spans[i];
    const b = spans[i + 1];
    const between = text.slice(a.index + a[0].length, b.index);
    if (a[1] === b[1] && between.includes("\n") && !strip(between)) joints++;
  }
  return [spans.length, spans.length - joints];
}

const STRAY_RE = rx("(?<![*\\\\])\\*{1,2}(?!\\*)", "g");

// `*` and `**` that open or close nothing.
function strayMarkers(text) {
  const covered = [...text.matchAll(EMPHASIS_RE)].map((m) => [m.index, m.index + m[0].length]);
  const out = [];
  for (const m of text.matchAll(STRAY_RE)) {
    const s = m.index;
    const e = s + m[0].length;
    if (covered.some(([a, b]) => a <= s && s < b)) continue;
    const before = s ? text[s - 1] : "\n";
    const after = e < text.length ? text[e] : "\n";
    if (strip(before) === "" && strip(after) === "") continue;
    out.push([s, m[0]]);
  }
  return out;
}

function countChar(s, ch) {
  let n = 0;
  for (let i = s.indexOf(ch); i !== -1; i = s.indexOf(ch, i + 1)) n++;
  return n;
}

// A line break lost, added or moved to an edge, and `*emphasis*` that no longer wraps text.
function lineBreakFaults(original, translated, softBreaks = false) {
  const out = [];
  const nSrc = countChar(original, "\n");
  const nTr = countChar(translated, "\n");
  if (nSrc !== nTr && !softBreaks) out.push(`line breaks changed: ${nSrc} in the source, ${nTr} in the translation`);
  else if (nSrc && (original.startsWith("\n") !== translated.startsWith("\n") || original.endsWith("\n") !== translated.endsWith("\n")))
    out.push("a line break moved to the start or end of the translation");
  const [srcEm, srcOne] = emphasisCounts(original);
  const [trEm, trOne] = emphasisCounts(translated);
  const left = new Set([srcEm, srcOne]);
  if ([trEm, trOne].some((x) => left.has(x))) {
    if (strayMarkers(translated).length > strayMarkers(original).length) out.push("emphasis *…* broken: a stray * the source does not have");
  } else if (trEm < srcEm) out.push(`emphasis *…* broken: ${srcEm} in the source, ${trEm} intact in the translation`);
  else if (trEm > srcEm) out.push(`emphasis *…* added: ${srcEm} in the source, ${trEm} in the translation`);
  return out;
}

// ── ICU plural/select validation ──────────────────────────────────────────────

const CLDR_REQUIRED = new Map(Object.entries(T.CLDR_REQUIRED).map(([k, v]) => [k, new Set(v)]));

// Top-level ICU plural/select/selectordinal blocks as [type, full segment].
function icuBlocks(text) {
  const out = [];
  for (const [s, e] of braceSpans(text)) {
    const seg = text.slice(s, e);
    const m = ICU_HEAD_RE.exec(seg);
    if (m) out.push([m[1], seg]);
  }
  return out;
}

const WORD_CHAR = /^[\p{L}\p{N}]$/u;
const isWordish = (ch) => ch === "_" || WORD_CHAR.test(ch);

// The `one` / `other` / `=0` label sitting immediately left of an arm's `{`, read backwards.
function armLabel(body, pos) {
  let i = pos;
  const prev = (k) => {
    // the code point that ends before index k
    const c = body.charCodeAt(k - 1);
    if (c >= 0xdc00 && c <= 0xdfff && k >= 2) {
      const h = body.charCodeAt(k - 2);
      if (h >= 0xd800 && h <= 0xdbff) return body.slice(k - 2, k);
    }
    return body[k - 1];
  };
  while (i > 0 && strip(prev(i)) === "" && prev(i) !== "") i -= prev(i).length;
  let j = i;
  while (j > 0 && isWordish(prev(j))) j -= prev(j).length;
  const tok = body.slice(j, i);
  if (!tok) return null;
  if (j > 0 && body[j - 1] === "=" && /^\p{Nd}+$/u.test(tok)) return "=" + tok;
  return tok;
}

// [type, category keywords, exact matches] for one ICU block.
function icuArms(seg) {
  const m = ICU_HEAD_RE.exec(seg);
  if (!m) return null;
  const body = seg.slice(m[0].length, -1);
  const keywords = new Set();
  const exacts = new Set();
  for (const [s] of braceSpans(body)) {
    const tok = armLabel(body, s);
    if (tok) (tok.startsWith("=") ? exacts : keywords).add(tok);
  }
  return [m[1], keywords, exacts];
}

function icuFaults(original, translated, lang) {
  const src = icuBlocks(original);
  const tr = icuBlocks(translated);
  const faults = [];
  if (src.length > tr.length) faults.push(`ICU plural/select construct dropped or broken (${src.length} in source, ${tr.length} in translation)`);
  const base = (lang || "").replace(/_/g, "-").split("-")[0].toLowerCase();
  for (const [typ, seg] of tr) {
    const arms = icuArms(seg);
    if (!arms) continue;
    const kws = arms[1];
    if (!kws.has("other")) faults.push(`ICU ${typ} is missing the required 'other' branch`);
    if (typ === "plural") {
      const missing = [...(CLDR_REQUIRED.get(base) ?? [])].filter((x) => !kws.has(x));
      if (missing.length) faults.push(`ICU plural is missing the ${sorted(missing).join("/")} form(s) ${base} requires`);
    }
  }
  const exactsOf = (blocks) => blocks.map(([, s]) => icuArms(s)).filter(Boolean).map((a) => a[2]);
  const union = (sets) => new Set(sets.flatMap((s) => [...s]));
  const srcEx = union(exactsOf(src));
  const trEx = union(exactsOf(tr));
  for (const ex of sorted(setDiff(srcEx, trEx))) faults.push(`ICU exact-match branch ${ex} {…} was dropped in the translation`);
  return faults;
}

// ── file-level: register, overflow, consistency ───────────────────────────────

const REGISTER_MARKERS = new Map(Object.entries(T.REGISTER_MARKERS).map(([k, [a, b]]) => [k, [re(a, "g"), re(b, "g")]]));

// A T–V language app should pick ONE register.
function registerFaults(translatedFlat, lang) {
  const base = (lang || "").replace(/_/g, "-").split("-")[0].toLowerCase();
  const markers = REGISTER_MARKERS.get(base);
  if (!markers) return [];
  const [infRe, formRe] = markers;
  const text = [...translatedFlat.values()].join("\n");
  const inf = allMatches(infRe, text).length;
  const formal = allMatches(formRe, text).length;
  if (inf >= 2 && formal >= 2) return [`file mixes informal and formal address (${inf} informal vs ${formal} formal markers) — pick one register`];
  return [];
}

const LANG_EXPANSION = T.LANG_EXPANSION;
const SHORT_LABEL_CHARS = T.SHORT_LABEL_CHARS;
const SHORT_LABEL_GROWTH = T.SHORT_LABEL_GROWTH;
const WRAPS_RE = re(T.WRAPS_RE);
const INVISIBLE_RE = /[\p{Mn}\p{Me}\p{Cf}]/u;

// Characters a reader sees: a Hindi vowel sign, a Persian ZWNJ and a Markdown `*` are code
// points with no place of their own.
function renderedLen(text) {
  let n = 0;
  for (const c of icuVisibleText(text).replace(/\*/g, "")) if (!INVISIBLE_RE.test(c)) n++;
  return n;
}

const wraps = (text) => strip(text).includes("\n") || (splitWs(text).length >= 4 && WRAPS_RE.test(strip(text)));

function computeOverflow(sourceFlat, translatedFlat, lang = "", prose = false) {
  const base = (lang || "").replace(/_/g, "-").split("-")[0].toLowerCase();
  const expected = LANG_EXPANSION[base] ?? 1.2;
  const threshold = Math.max(1.4, expected * 1.18);
  const typical = pyRound((expected - 1) * 100);
  const out = [];
  for (const [k, src] of sourceFlat) {
    const tr = translatedFlat.has(k) ? translatedFlat.get(k) : "";
    if (typeof tr !== "string" || !src) continue;
    const slen = renderedLen(src);
    const tlen = renderedLen(tr);
    if (slen === 0) continue;
    const ratio = tlen / slen;
    if (tlen - slen <= SHORT_LABEL_GROWTH) continue;
    if (prose || wraps(src)) {
      if (ratio >= Math.max(2.5, threshold)) out.push({ key: k, pct: pyRound((ratio - 1) * 100), typical });
      continue;
    }
    if (slen <= SHORT_LABEL_CHARS) {
      if (tlen - slen > SHORT_LABEL_GROWTH) out.push({ key: k, pct: pyRound((ratio - 1) * 100), typical, chars: tlen - slen, short: true });
      continue;
    }
    if (slen <= 60 && tlen >= 8 && ratio >= threshold) out.push({ key: k, pct: pyRound((ratio - 1) * 100), typical });
  }
  out.sort((a, b) => b.pct - a.pct);
  return out.slice(0, 50);
}

const LANG_SCRIPT_RE = new Map(Object.entries(T.LANG_SCRIPT_RE).map(([k, v]) => [k, re(v)]));
const URL_RE = re(T.URL_RE, "g");
const LOWERCASE_WORD_RE = re(T.LOWERCASE_WORD_RE);
const LETTER_RUN_RE = rx("[^\\W\\d_]+", "g");
const LETTER_RUN2_RE = rx("[^\\W\\d_]{2,}", "g");
const LETTER_RE = rx("[^\\W\\d_]");
const DIGITS_RE = /\p{Nd}+/gu;

function stripGlossary(s, glossary) {
  for (const g of glossary || []) if (g) s = s.replace(new RegExp(escapeRe(g), "giu"), " ");
  return s;
}

// A value the model left in the source language.
function untranslatedString(original, translated, lang, glossary = null) {
  const base = (lang || "").replace(/_/g, "-").split("-")[0].toLowerCase();
  if (base === "en") return [];
  const script = LANG_SCRIPT_RE.get(lang) ?? LANG_SCRIPT_RE.get(base);
  const sameAsSource = strip(translated) === strip(original);
  if (!sameAsSource && (!script || script.test(translated))) return [];

  let s = translated.replace(PH_RE, " ");
  s = s.replace(TAG_RE, " ");
  s = s.replace(URL_RE, " ");
  s = s.replace(DIGITS_RE, " ");
  s = stripGlossary(s, glossary);
  if (!LOWERCASE_WORD_RE.test(s)) {
    const words = allMatches(LETTER_RUN_RE, s);
    const looksLikeASentence = words.length >= 3 && words.some((w) => isLowerChar(first(w)));
    if (!(script && !script.test(translated) && looksLikeASentence)) return [];
  }
  if (script && !script.test(translated)) return ["may be untranslated — no target-language script, still reads as source text"];
  if (sameAsSource && allMatches(LETTER_RUN2_RE, s).length >= 3) return ["may be untranslated — identical to the source text"];
  return [];
}

// Source terms translated DIFFERENTLY across keys that share the exact same source text.
function computeConsistency(sourceFlat, translatedFlat) {
  const byTerm = new Map();
  const display = new Map();
  const srcDisplay = new Map();
  for (const [k, src] of sourceFlat) {
    if (typeof src !== "string") continue;
    const s = strip(src);
    if (!s || plen(s) > 60) continue;
    const tr = translatedFlat.get(k);
    if (typeof tr !== "string" || !strip(tr)) continue;
    const normS = splitWs(s.toLowerCase()).join(" ");
    const normT = splitWs(strip(tr).toLowerCase()).join(" ");
    if (!byTerm.has(normS)) byTerm.set(normS, new Map());
    if (!byTerm.get(normS).has(normT)) byTerm.get(normS).set(normT, []);
    byTerm.get(normS).get(normT).push(k);
    if (!display.has(normS)) display.set(normS, new Map());
    if (!display.get(normS).has(normT)) display.get(normS).set(normT, strip(tr));
    if (!srcDisplay.has(normS)) srcDisplay.set(normS, s);
  }
  const out = [];
  for (const [normS, variants] of byTerm) {
    if (variants.size < 2) continue;
    const keys = [];
    const variantList = [];
    for (const [normT, ks] of variants) {
      keys.push(...ks);
      variantList.push(display.get(normS).get(normT));
    }
    out.push({ term: srcDisplay.get(normS), variants: variantList, keys: sorted(keys) });
  }
  out.sort((a, b) => b.keys.length - a.keys.length);
  return out.slice(0, 30);
}

// ── currency ──────────────────────────────────────────────────────────────────

const CURRENCY_NEAR_NUMBER = T.CURRENCY_NEAR_NUMBER.map(([p, cur]) => [re(p, "g"), cur]);
const CURRENCY_ANYWHERE = T.CURRENCY_ANYWHERE.map(([p, cur]) => [re(p, "g"), cur]);

// Which currencies this string quotes a price in. Longest token wins, so R$ never registers as $.
function currenciesIn(text) {
  text = magnitudes(text || "");
  const found = new Set();
  const claimed = new Uint8Array(text.length + 1);
  for (const [r, cur] of CURRENCY_NEAR_NUMBER) {
    for (const m of text.matchAll(r)) {
      if (claimed[m.index]) continue;
      claimed.fill(1, m.index, m.index + m[0].length);
      found.add(cur);
    }
  }
  return found;
}

// Every currency the string names anywhere — symbol, ISO code or word.
function currenciesNamed(text) {
  text = text || "";
  const found = new Set();
  const claimed = new Uint8Array(text.length + 1);
  for (const [r, cur] of CURRENCY_ANYWHERE) {
    for (const m of text.matchAll(r)) {
      if (claimed[m.index]) continue;
      claimed.fill(1, m.index, m.index + m[0].length);
      found.add(cur || T.CODE_CURRENCY[m[0]] || m[0].toUpperCase());
    }
  }
  return found;
}

const setEqual = (a, b) => a.size === b.size && [...a].every((x) => b.has(x));
const union = (a, b) => new Set([...a, ...b]);

function currencyFaults(original, translated) {
  const src = currenciesIn(original);
  const tgt = currenciesIn(translated);
  if (!src.size && !tgt.size) return [];
  if (setEqual(src, tgt)) return [];
  const show = (cs) => (cs.size ? sorted(cs).join(", ") : "none");
  if (tgt.size && !src.size) return [`currency invented — the translation quotes ${show(tgt)}, the source quoted none`];
  const present = union(tgt, currenciesNamed(translated));
  const missing = setDiff(src, present);
  if (!missing.size && !setDiff(tgt, src).size) return [];
  if (missing.size && !setDiff(present, src).size) return [`currency dropped — the source priced this in ${show(src)}, the translation has no currency`];
  const shown = setDiff(present, src).size ? setDiff(present, src) : tgt;
  return [`currency changed: ${show(src)} → ${show(shown)} — this is a different price, not a translation`];
}

// ── glossary ──────────────────────────────────────────────────────────────────

const lowerLower = (s) => s.toLowerCase();

// Glossary/brand terms present in the source that did NOT survive in the translation.
function lostGlossary(original, translated, glossary) {
  if (!glossary || !glossary.length) return [];
  const tr = lowerLower(translated);
  const out = [];
  for (const term of glossary) {
    const t = strip(term || "");
    if (t && new RegExp("(?<![A-Za-z0-9_])" + escapeRe(t) + "(?![A-Za-z0-9_])", "u").test(original) && !tr.includes(lowerLower(t))) out.push(t);
  }
  return sorted(new Set(out));
}

// A locked term that survived but came back re-cased: KAERIS -> Kaeris-System.
function glossaryCaseDrift(original, translated, glossary) {
  if (!glossary || !glossary.length) return [];
  const out = [];
  for (const term of glossary) {
    const t = strip(term || "");
    if (!t) continue;
    const forms = new Set(allMatches(new RegExp(escapeRe(t), "giu"), original));
    if (!forms.size) continue;
    const seen = new RegExp(escapeRe(t), "iu").exec(translated);
    if (!seen) continue;
    const accepted = new Set();
    for (const f of forms) {
      const c = first(f);
      const tail = f.slice(c.length);
      for (const a of [f, c.toUpperCase() + tail, c.toLowerCase() + tail]) accepted.add(a);
    }
    if ([...accepted].some((a) => translated.includes(a))) continue;
    const expected = sorted(forms)[0];
    out.push(`${expected} appears as "${seen[0]}" — a kept term keeps its exact case`);
  }
  return sorted(new Set(out));
}

// A sentence whose translation is nothing but the glossary term(s).
function glossaryCollapse(original, translated, glossary) {
  if (!glossary || !glossary.length || typeof original !== "string" || typeof translated !== "string") return [];
  const terms = glossary.filter((t) => t && strip(t)).map((t) => strip(t));
  if (!terms.length) return [];
  if (allMatches(LETTER_RUN2_RE, original).length < 3) return [];
  let rest = translated;
  for (const t of terms) rest = rest.replace(new RegExp(escapeRe(t), "giu"), " ");
  rest = rest.replace(PH_RE, " ").replace(TAG_RE, " ");
  if (LETTER_RE.test(rest)) return [];
  const kept = terms.filter((t) => lowerLower(translated).includes(lowerLower(t)));
  if (!kept.length) return [];
  return [`translation collapsed to the glossary term "${kept[0]}" — the sentence is gone`];
}

// The model answered the string instead of translating it.
function answeredInsteadOfTranslating(original, translated) {
  if (typeof original !== "string" || typeof translated !== "string") return false;
  const src = strip(original);
  const tr = strip(translated);
  if (!src || !tr) return false;
  if (plen(src) > 80) return false;
  return plen(tr) >= Math.max(4 * plen(src), 120);
}

// ── the two doors ─────────────────────────────────────────────────────────────

// All per-string faults between one source value and one target value, each tagged error
// (breaks the build) or warn (advisory).
export function stringFaults(src, tgt, lang, glossary = null) {
  if (typeof src !== "string" || typeof tgt !== "string") return [];
  const out = [];
  for (const ph of lostPlaceholders(src, tgt)) out.push({ severity: ERROR, msg: `lost placeholder ${ph}` });
  for (const msg of placeholderTypeFaults(src, tgt)) out.push({ severity: ERROR, msg });
  for (const msg of numericFaults(src, tgt, lang)) out.push({ severity: ERROR, msg });
  for (const msg of entityFaults(src, tgt)) out.push({ severity: ERROR, msg });
  for (const msg of currencyFaults(src, tgt)) out.push({ severity: ERROR, msg });
  for (const msg of icuFaults(src, tgt, lang)) out.push({ severity: ERROR, msg });
  for (const tag of lostTags(src, tgt)) out.push({ severity: ERROR, msg: `lost inline tag ${tag}` });
  for (const msg of lineBreakFaults(src, tgt)) out.push({ severity: WARN, msg });
  if (glossary && glossary.length) {
    for (const term of lostGlossary(src, tgt, glossary)) out.push({ severity: ERROR, msg: `glossary term dropped: ${term}` });
    for (const msg of glossaryCollapse(src, tgt, glossary)) out.push({ severity: ERROR, msg });
    for (const msg of glossaryCaseDrift(src, tgt, glossary)) out.push({ severity: WARN, msg: `glossary case: ${msg}` });
  }
  if (answeredInsteadOfTranslating(src, tgt))
    out.push({ severity: ERROR, msg: "not a translation — the model answered the string instead of translating it" });
  for (const msg of untranslatedString(src, tgt, lang, glossary)) out.push({ severity: WARN, msg });
  return out;
}

// File-level faults over one target locale (register, consistency, overflow). All advisory.
export function fileFaults(sourceFlat, translatedFlat, lang) {
  const out = [];
  for (const msg of registerFaults(translatedFlat, lang)) out.push({ severity: WARN, msg });
  for (const item of computeConsistency(sourceFlat, translatedFlat))
    out.push({ severity: WARN, msg: `inconsistent translation of "${item.term}" across ${item.keys.length} keys: ${item.variants.join(", ")}` });
  for (const item of computeOverflow(sourceFlat, translatedFlat, lang)) {
    if (item.short) {
      out.push({ severity: WARN, msg: `${item.key}: translation +${item.chars} characters vs source — may overflow UI` });
      continue;
    }
    out.push({ severity: WARN, msg: `${item.key}: translation +${item.pct}% vs source (typical +${item.typical}%) — may overflow UI` });
  }
  return out;
}
