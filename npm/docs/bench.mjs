// Builds one locale set (1 source + 20 targets, N keys, a few planted faults) and times commands
// over it, wall time of a whole process as CI pays for it. Run in the folder of the data:
//   node docs/bench.mjs DIR KEYS RUNS "label::command with args" "label2::other command"
// The commands run with DIR as the working directory; the locale files are DIR/locales/*.json.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const dir = process.argv[2];
const KEYS = Number(process.argv[3] || 1000);
const LANGS = ["de", "fr", "es", "it", "pt", "nl", "pl", "ru", "uk", "cs", "sv", "da", "fi", "tr", "ja", "ko", "zh", "ar", "he", "hi"];
fs.rmSync(dir, { recursive: true, force: true });
fs.mkdirSync(path.join(dir, "locales"), { recursive: true });

let seed = 1;
const rnd = () => (seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296;
const words = ["save", "cancel", "delete", "account", "settings", "profile", "welcome", "back", "next", "search", "open", "close", "error", "retry", "loading"];
const sentence = (n) => Array.from({ length: n }, () => words[Math.floor(rnd() * words.length)]).join(" ");
const TRANSLATED = Object.fromEntries(words.map((w) => [w, w + "en"]));
const src = {};
for (let g = 0; g < KEYS / 20; g++) {
  const group = {};
  for (let i = 0; i < 20; i++) {
    const r = rnd();
    group[`k${i}`] =
      r < 0.15 ? `Hello {name}, ${sentence(3)}` :
      r < 0.25 ? `{count, plural, one {# ${words[i % 15]}} other {# ${words[i % 15]}s}}` :
      r < 0.30 ? `%s: ${sentence(2)}` :
      sentence(2 + Math.floor(rnd() * 5));
  }
  src[`section${g}`] = group;
}
fs.writeFileSync(path.join(dir, "locales", "en.json"), JSON.stringify(src, null, 2));
const flat = (o, p = "") => Object.entries(o).flatMap(([k, v]) => (typeof v === "object" ? flat(v, `${p}${k}.`) : [[`${p}${k}`, v]]));
for (const lang of LANGS) {
  const tr = JSON.parse(JSON.stringify(src));
  for (const [k, v] of flat(src)) {
    const [g, i] = k.split(".");
    tr[g][i] = v.replace(/[a-z]{3,}/g, (m) => (m === "name" ? m : TRANSLATED[m] || m));
  }
  // planted faults: a missing key, a renamed placeholder, a lost plural arm
  delete tr.section0.k0;
  tr.section1.k1 = "Hallo {nom}";
  fs.writeFileSync(path.join(dir, "locales", `${lang}.json`), JSON.stringify(tr, null, 2));
}
console.log(`${LANGS.length} languages x ${flat(src).length} keys in ${dir}`);

const runs = Number(process.argv[4] || 10);
const specs = process.argv.slice(5).map((s) => s.split("::"));
for (const [label, cmd] of specs) {
  const parts = cmd.split(" ");
  const t = [];
  let code;
  for (let i = 0; i < runs + 1; i++) {
    const t0 = process.hrtime.bigint();
    const r = spawnSync(parts[0], parts.slice(1), { cwd: dir, encoding: "utf8", env: { ...process.env, NO_COLOR: "1", KAERIS_NO_UPDATE_CHECK: "1", CI: "1" } });
    const ms = Number(process.hrtime.bigint() - t0) / 1e6;
    code = r.status;
    if (i > 0) t.push(ms); // the first run warms the disk cache
  }
  t.sort((a, b) => a - b);
  console.log(`${label.padEnd(28)} median ${t[Math.floor(t.length / 2)].toFixed(0)} ms  min ${t[0].toFixed(0)}  max ${t[t.length - 1].toFixed(0)}  exit ${code}`);
}
