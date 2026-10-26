// The JavaScript port against the answers of the Python original (see gen_baselines.py): the same
// project directories must give the same exit code, the same --json report, the same human
// report and the same SARIF file; the same strings must give the same faults.
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, test } from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { fileFaults, stringFaults } from "../src/detectors.js";
import { dumps, parse } from "../src/json.js";

const run = promisify(execFile);
const here = path.dirname(fileURLToPath(import.meta.url));
const bin = path.join(here, "..", "bin", "kaeris.js");
const norm = (s) => s.replace(/\\/g, "/");
const ordered = (text) => dumps(parse(text), 1);

const cases = fs.readdirSync(path.join(here, "corpus")).sort();

describe("check: the corpus", { concurrency: 8 }, () => {
  for (const name of cases) {
    test(name, async () => {
      const spec = parse(fs.readFileSync(path.join(here, "corpus", name, "case.json"), "utf8")).get("args");
      const want = parse(fs.readFileSync(path.join(here, "baseline", `${name}.json`), "utf8"));
      const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kaeris-parity-"));
      try {
        fs.cpSync(path.join(here, "corpus", name), dir, { recursive: true });
        fs.rmSync(path.join(dir, "case.json"));
        const go = async (extra) => {
          try {
            const r = await run(process.execPath, [bin, "check", ...spec, ...extra], { cwd: dir, env: { ...process.env, NO_COLOR: "1" } });
            return { code: 0, stdout: r.stdout, stderr: r.stderr };
          } catch (e) {
            if (typeof e.code !== "number") throw e;
            return { code: e.code, stdout: e.stdout, stderr: e.stderr };
          }
        };

        const asJson = await go(["--json"]);
        const wantJson = want.get("json");
        assert.equal(asJson.code, wantJson.get("code"), `exit code (--json)\n${asJson.stderr}`);
        const body = wantJson.get("stdout");
        if (body === null) assert.equal(asJson.stdout.trim(), "");
        else assert.equal(ordered(asJson.stdout), dumps(body, 1), "--json report");
        assert.equal(norm(asJson.stderr), wantJson.get("stderr"), "stderr (--json)");

        const human = await go([]);
        const wantHuman = want.get("human");
        assert.equal(human.code, wantHuman.get("code"), `exit code\n${human.stderr}`);
        assert.equal(human.stdout, wantHuman.get("stdout"));
        assert.equal(norm(human.stderr), wantHuman.get("stderr"), "human report");

        const sarifAt = spec.indexOf("--sarif");
        if (sarifAt !== -1) {
          const file = path.join(dir, spec[sarifAt + 1]);
          const wantSarif = want.get("sarif");
          assert.ok(fs.existsSync(file), "SARIF file written");
          assert.equal(dumps(parse(fs.readFileSync(file, "utf8")), 1), dumps(wantSarif, 1), "SARIF");
        }
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });
  }
});

const { pairs, files } = (() => {
  const doc = parse(fs.readFileSync(path.join(here, "pairs.json"), "utf8"));
  return { pairs: doc.get("pairs"), files: doc.get("files") };
})();

const asPlain = (faults) => faults.map((f) => ({ severity: f.severity, msg: f.msg }));
const expected = (list) => list.map((f) => ({ severity: f.get("severity"), msg: f.get("msg") }));

describe("detectors: string by string", () => {
  test(`${pairs.length} pairs`, () => {
    const bad = [];
    for (const p of pairs) {
      const got = asPlain(stringFaults(p.get("src"), p.get("tgt"), p.get("lang"), p.get("glossary")));
      const want = expected(p.get("expect"));
      try {
        assert.deepEqual(got, want);
      } catch {
        bad.push({ src: p.get("src"), tgt: p.get("tgt"), lang: p.get("lang"), got, want });
      }
    }
    assert.equal(bad.length, 0, `${bad.length} of ${pairs.length} differ, first:\n${JSON.stringify(bad.slice(0, 3), null, 1)}`);
  });
});

describe("detectors: whole files", () => {
  test(`${files.length} files`, () => {
    const bad = [];
    for (const f of files) {
      const src = new Map(f.get("src"));
      const tgt = new Map(f.get("tgt"));
      const got = asPlain(fileFaults(src, tgt, f.get("lang")));
      const want = expected(f.get("expect"));
      try {
        assert.deepEqual(got, want);
      } catch {
        bad.push({ lang: f.get("lang"), got, want });
      }
    }
    assert.equal(bad.length, 0, `${bad.length} of ${files.length} differ, first:\n${JSON.stringify(bad.slice(0, 2), null, 1)}`);
  });
});
