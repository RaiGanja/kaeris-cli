// check — the "i18n firewall": a local, static comparison of a source (base-language) locale
// file against each target locale file. No network. Exits non-zero (see cli.js) when a locale
// is untranslated or a translated string lost or gained a placeholder.
import * as det from "./detectors.js";
import { flattenAll } from "./flatten.js";
import { loneCountToHash } from "./formats.js";
import { rx, strip, sorted, setDiff, setEq } from "./py.js";

// Percent-style placeholders (positional and %@ are tried before the bare %s/%d/... class)
const PERCENT_RE = rx("%\\d+\\$[@a-zA-Z]|%@|%[sdifxocegpnXOCEGP%]", "g");
// ${name} template-literal placeholders, scanned (and masked) before the brace scanner so a
// ${x} span is not also counted as a bare {x}: they are different families.
const DOLLAR_RE = rx("\\$\\{[\\w\\d]+\\}", "g");

// Balanced top-level {...} spans: plain named ({name}), doubled ({{name}}) and ICU
// ({count, plural, one {# item} other {# items}}) as one opaque span each.
function braceSpans(text) {
  const spans = [];
  let i = 0;
  const n = text.length;
  while (i < n) {
    if (text[i] === "{") {
      let depth = 1;
      let j = i + 1;
      while (j < n && depth > 0) {
        if (text[j] === "{") depth++;
        else if (text[j] === "}") depth--;
        j++;
      }
      if (depth === 0) {
        spans.push([i, j]);
        i = j;
        continue;
      }
    }
    i++;
  }
  return spans;
}

// A brace span reduced to the argument name: '{{name}}' -> '{name}', '{count, plural, ...}' -> '{count}'.
function braceIdentity(spanText) {
  let inner = strip(spanText, "{}");
  while (inner.startsWith("{") && inner.endsWith("}")) inner = inner.slice(1, -1);
  const name = strip(inner.split(",")[0]);
  return "{" + name + "}";
}

// The set of placeholder identities found in a string.
export function findPlaceholders(text) {
  if (typeof text !== "string") return new Set();
  const found = new Set();
  const chars = text.split("");
  const mask = (from, to) => {
    for (let i = from; i < to; i++) chars[i] = " ";
  };
  for (const m of text.matchAll(DOLLAR_RE)) {
    found.add(m[0]);
    mask(m.index, m.index + m[0].length);
  }
  const masked = chars.join("");
  const spans = braceSpans(masked);
  for (const [s, e] of spans) found.add(braceIdentity(masked.slice(s, e)));
  for (const [s, e] of spans) mask(s, e);
  for (const m of chars.join("").matchAll(PERCENT_RE)) found.add(m[0]);
  return found;
}

// A .po plural has msgstr[0], [1] and, for languages with more forms, [2]... Only the first two
// have a counterpart in the source; the rest are read against its plural form.
const EXTRA_FORM_RE = rx("⟦plural:\\d+⟧$");

// The source key this target key is compared against: itself, or for an extra plural form the
// source's plural form. null when there is none.
function sourceTwin(key, sourceFlat) {
  if (sourceFlat.has(key)) return key;
  const twin = key.replace(EXTRA_FORM_RE, "⟦plural⟧");
  return twin !== key && sourceFlat.has(twin) ? twin : null;
}

// [key, source value, target value] for every key both sides have, in the source's order, then
// the extra plural forms the target has.
function* pairs(sourceFlat, targetFlat, optional = new Set()) {
  for (const [key, srcVal0] of sourceFlat) {
    if (targetFlat.has(key)) {
      let srcVal = srcVal0;
      let tgtVal = targetFlat.get(key);
      if (optional.has(key) && typeof srcVal === "string" && typeof tgtVal === "string") {
        srcVal = loneCountToHash(srcVal);
        tgtVal = loneCountToHash(tgtVal);
      }
      yield [key, srcVal, tgtVal];
    }
  }
  for (const [key, tgtVal] of targetFlat) {
    const twin = sourceTwin(key, sourceFlat);
    if (twin !== null && twin !== key) yield [key, sourceFlat.get(twin), tgtVal];
  }
}

// [missing, extra, placeholder issues] for one target locale.
export function diffLocale(sourceFlat, targetFlat, pair = true, optional = new Set()) {
  const missing = sorted([...sourceFlat.keys()].filter((k) => !targetFlat.has(k)));
  const extra = sorted([...targetFlat.keys()].filter((k) => sourceTwin(k, sourceFlat) === null));
  const issues = [];
  if (!pair) return [missing, extra, issues];
  for (const [key, srcVal, tgtVal] of pairs(sourceFlat, targetFlat, optional)) {
    if (typeof srcVal !== "string" || typeof tgtVal !== "string") continue;
    const srcPh = findPlaceholders(srcVal);
    const tgtPh = findPlaceholders(tgtVal);
    if (!setEq(srcPh, tgtPh)) issues.push({ key, missing: sorted(setDiff(srcPh, tgtPh)), added: sorted(setDiff(tgtPh, srcPh)) });
  }
  return [missing, extra, issues];
}

const onlyText = (flat) => new Map([...flat].filter(([, v]) => typeof v === "string"));

// Run the check across every target language. `loadTarget(lang)` returns the parsed locale for
// that language, or null if the file does not exist. Returns exactly what --json prints.
export function checkLocales(sourceObj, langs, loadTarget, glossary = null) {
  const sourceFlat = flattenAll(sourceObj);
  const optional = sourceObj.countOptional ?? new Set();

  const result = {
    ok: true,
    missing: new Map(),
    extra: new Map(),
    placeholder_issues: [],
    missing_files: [],
    faults: [],
    warnings: [],
  };

  for (const lang of langs) {
    const targetObj = loadTarget(lang);
    if (targetObj === null) {
      result.missing_files.push(lang);
      result.missing.set(lang, sorted(sourceFlat.keys()));
      result.ok = false;
      continue;
    }
    const targetFlat = flattenAll(targetObj);
    const pair = !targetObj.skipPairwise;
    for (const note of targetObj.notes ?? []) {
      const rec = { lang, key: note.key ?? "", msg: note.msg };
      if (note.severity === "error") {
        result.faults.push({ ...rec, severity: "error" });
        result.ok = false;
      } else result.warnings.push({ ...rec, severity: "warn" });
    }
    const [missing, extra, issues] = diffLocale(sourceFlat, targetFlat, pair, optional);
    if (missing.length) {
      result.missing.set(lang, missing);
      result.ok = false;
    }
    if (extra.length) result.extra.set(lang, extra);
    for (const issue of issues) result.placeholder_issues.push({ lang, ...issue });
    if (issues.length) result.ok = false;
    // Per-string detectors on shared keys (RED faults + YELLOW warnings).
    if (pair) {
      for (const [key, srcVal, tgtVal] of pairs(sourceFlat, targetFlat, optional)) {
        if (typeof srcVal !== "string" || typeof tgtVal !== "string") continue;
        if (strip(srcVal) && !strip(tgtVal)) {
          result.warnings.push({ lang, key, severity: "warn", msg: "empty translation — the source has text" });
          continue;
        }
        for (const f of det.stringFaults(srcVal, tgtVal, lang, glossary)) {
          const rec = { lang, key, msg: f.msg };
          if (f.severity === det.ERROR) {
            result.faults.push({ ...rec, severity: "error" });
            result.ok = false;
          } else result.warnings.push({ ...rec, severity: "warn" });
        }
      }
    }
    // File-level detectors (overflow / register / consistency) — YELLOW. They read text, and a
    // locale legitimately holds numbers, booleans, lists and nulls.
    if (pair) {
      for (const f of det.fileFaults(onlyText(sourceFlat), onlyText(targetFlat), lang))
        result.warnings.push({ lang, msg: f.msg, severity: "warn" });
    }
  }
  return result;
}
