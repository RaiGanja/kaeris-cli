// SARIF 2.1.0 output for `check`: findings that show up inside the pull request. Every finding is
// resolved back to the line where its key sits in the file it belongs to, and to a
// repo-relative path, which is the only shape GitHub can match to a diff.
import fs from "node:fs";
import { readText } from "./encoding.js";
import { ValueError } from "./errors.js";
import { dumps } from "./json.js";
import { splitlines } from "./formats.js";
import { escapeRe, rx } from "./py.js";
import * as pp from "./pypath.js";

const SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json";

const RULES = [
  ["missing-key", "Key missing from a locale", "A key present in the source file has no translation in this locale."],
  [
    "placeholder", "Placeholder lost or altered",
    "A placeholder ({name}, %s, %d, <b>…) present in the source is missing or changed type in the translation — this breaks formatting at runtime.",
  ],
  [
    "translation-fault", "Translation fault",
    "A deterministic defect in the translation: dropped glossary term, plural form missing for the language's CLDR rules, number or currency drift, broken entity, ICU syntax.",
  ],
  [
    "translation-warning", "Translation warning",
    "A soft signal worth a look: text left in the source language, register drift, cross-key inconsistency, or a translation long enough to overflow the UI.",
  ],
  [
    "extra-key", "Key not in the source",
    "A key exists in this locale but not in the source file — usually a leftover after a rename or deletion.",
  ],
];

// 1-based line where `key` is defined in a JSON/ARB file, or 1 if it cannot be found.
function lineOfKey(path, key) {
  if (!key) return 1;
  const leaf = key.split(".").pop();
  const pattern = rx('^\\s*"' + escapeRe(leaf) + '"\\s*:');
  try {
    const lines = splitlines(readText(path));
    for (let n = 0; n < lines.length; n++) if (pattern.test(lines[n])) return n + 1;
  } catch (e) {
    if (e instanceof ValueError || e.code) return 1;
    throw e;
  }
  return 1;
}

// Repo-relative, forward slashes.
function rel(path, root) {
  let r;
  try {
    r = pp.relpath(path, root);
  } catch {
    r = path;
  }
  return r.split(pp.sep).join("/");
}

const result = (ruleId, level, text, uri, line) => ({
  ruleId,
  level,
  message: { text },
  locations: [{ physicalLocation: { artifactLocation: { uri }, region: { startLine: Math.max(1, Math.trunc(line)) } } }],
});

// Turn a checkLocales result into a SARIF document. targetFor(lang) is the path of that
// language's file, so a finding lands on the locale that has the problem.
export function build(res, source, targetFor, root = ".") {
  const results = [];
  const add = (lang, key, rule, level, text) => {
    const p = lang ? targetFor(lang) : source;
    results.push(result(rule, level, text, rel(p, root), key ? lineOfKey(p, key) : 1));
  };

  for (const [lang, keys] of res.missing) for (const key of keys) add(lang, null, "missing-key", "error", `[${lang}] missing translation for key "${key}"`);
  for (const [lang, keys] of res.extra) for (const key of keys) add(lang, key, "extra-key", "note", `[${lang}] key "${key}" is not in the source file`);
  for (const item of res.placeholder_issues) add(item.lang, item.key, "placeholder", "error", `[${item.lang}] ${item.key}: ${item.msg || "placeholder mismatch"}`);
  for (const item of res.faults)
    add(item.lang, item.key, "translation-fault", (item.severity ?? "error") === "error" ? "error" : "warning", `[${item.lang}] ${item.key}: ${item.msg ?? ""}`);
  for (const item of res.warnings) add(item.lang, item.key, "translation-warning", "warning", `[${item.lang}] ${item.key}: ${item.msg ?? ""}`);
  for (const lang of res.missing_files)
    results.push(result("missing-key", "error", `[${lang}] no locale file found for this language`, rel(source, root), 1));

  return {
    $schema: SCHEMA,
    version: "2.1.0",
    runs: [
      {
        tool: {
          driver: {
            name: "KAERIS",
            informationUri: "https://kaeris.dev",
            rules: RULES.map(([id, name, desc]) => ({
              id,
              name,
              shortDescription: { text: name },
              fullDescription: { text: desc },
              helpUri: "https://kaeris.dev/developer.html",
            })),
          },
        },
        results,
      },
    ],
  };
}

export function write(path, doc) {
  const d = pp.dirname(pp.abspath(path));
  if (d) fs.mkdirSync(d, { recursive: true });
  fs.writeFileSync(path, dumps(doc, 1, false), "utf8");
}
