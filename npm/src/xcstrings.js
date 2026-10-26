// An Xcode String Catalog (.xcstrings) for `check`: ONE file holding the source language and
// every translation, with the same key encoding and ICU rendering of plurals as the server.
import { readText } from "./encoding.js";
import { ValueError } from "./errors.js";
import { parse } from "./json.js";
import { rx, strip, isDict } from "./py.js";

const OPEN = "⟦";
const CLOSE = "⟧";
// printf-family specifier: %lld, %@, %d, %1$lld, %.2f — what Xcode substitutes the count into.
const SPEC_RE = rx("%(?:\\d+\\$)?[-+ #0]*[\\d.*]*(?:hh|h|ll|l|q|L|z|t|j)?[@dioupxXeEfgGcsSaAn%]", "g");

export const key = (base, ...parts) => base + parts.map((p) => `${OPEN}${p}${CLOSE}`).join("");

const get = (d, k) => (isDict(d) ? d.get(k) : undefined);
const truthy = (v) => (v instanceof Map ? v.size > 0 : Array.isArray(v) ? v.length > 0 : Boolean(v));
const orEmpty = (v) => (truthy(v) ? v : new Map());

// the specifier standing for the COUNT: the one printf token every arm shares
function countSpec(armTexts) {
  let common = null;
  for (const text of armTexts) {
    const found = new Set(text.match(SPEC_RE) ?? []);
    common = common === null ? found : new Set([...common].filter((x) => found.has(x)));
  }
  return common && common.size === 1 ? [...common][0] : null;
}

function icuFromPlural(plural) {
  const unit = (n) => get(orEmpty(get(orEmpty(n), "stringUnit")), "value");
  const texts = [...plural.values()].map(unit).filter((t) => typeof t === "string");
  if (!texts.length) return null;
  const spec = countSpec(texts);
  const arms = [];
  for (const [cat, node] of plural) {
    const val = unit(node);
    if (typeof val === "string") arms.push(`${cat} {${spec ? val.split(spec).join("#") : val}}`);
  }
  return arms.length ? "{count, plural, " + arms.join(" ") + "}" : null;
}

function flattenLocalization(base, loc, out, prefix = []) {
  const unit = get(orEmpty(get(loc, "stringUnit")), "value");
  if (typeof unit === "string" && strip(unit)) out.set(key(base, ...prefix), unit);
  const variations = orEmpty(get(loc, "variations"));
  const plural = get(variations, "plural");
  if (isDict(plural)) {
    const icu = icuFromPlural(plural);
    if (icu) out.set(key(base, ...prefix, "plural"), icu);
  }
  const device = orEmpty(get(variations, "device"));
  for (const [dev, node] of device) {
    if (isDict(node)) flattenLocalization(base, node, out, [...prefix, `device:${dev}`]);
  }
  for (const [name, node] of orEmpty(get(loc, "substitutions"))) {
    if (isDict(node)) flattenLocalization(base, node, out, [...prefix, `sub:${name}`]);
  }
}

// [source_lang, Map(lang -> Map(key -> text))] for every language present in the catalog.
// The source language always appears, even for keys with no localization at all.
export function load(path) {
  const doc = parse(readText(path));
  if (!isDict(doc) || !isDict(doc.get("strings"))) throw new ValueError("not a String Catalog: no 'strings' object");
  const srcLang = truthy(doc.get("sourceLanguage")) ? doc.get("sourceLanguage") : "en";

  const langs = new Set([srcLang]);
  for (const entry of doc.get("strings").values()) {
    if (isDict(entry)) for (const l of orEmpty(get(entry, "localizations")).keys()) langs.add(l);
  }
  const byLang = new Map([...langs].map((l) => [l, new Map()]));
  for (const [k, entry] of doc.get("strings")) {
    if (!isDict(entry) || entry.get("shouldTranslate") === false) continue;
    const locs = orEmpty(get(entry, "localizations"));
    for (const lang of langs) {
      const loc = locs.get(lang);
      if (isDict(loc)) flattenLocalization(k, loc, byLang.get(lang));
      else if (lang === srcLang && !truthy(locs.get(srcLang))) byLang.get(lang).set(k, k);
    }
  }
  return [srcLang, byLang];
}
