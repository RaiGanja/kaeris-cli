// os.path and glob as Python has them, because the checks print and compare paths and the
// layout rules (en.json -> de.json, values -> values-de, app_en.arb -> app_de.arb) are written
// against that behaviour. Windows and POSIX differ the way they do in Python.
import fs from "node:fs";
import path from "node:path";

export const isWin = process.platform === "win32";
export const sep = isWin ? "\\" : "/";
export const altsep = isWin ? "/" : null;
const SEPS = isWin ? "\\/" : "/";
const flavor = isWin ? path.win32 : path.posix;

export function splitdrive(p) {
  if (!isWin) return ["", p];
  if (p.length >= 2 && p[1] === ":") return [p.slice(0, 2), p.slice(2)];
  const norm = p.replace(/\//g, "\\");
  if (norm.startsWith("\\\\") && norm[2] !== "\\") {
    // UNC: \\host\share
    const hostEnd = norm.indexOf("\\", 2);
    if (hostEnd === -1) return ["", p];
    const shareEnd = norm.indexOf("\\", hostEnd + 1);
    const cut = shareEnd === -1 ? p.length : shareEnd;
    return [p.slice(0, cut), p.slice(cut)];
  }
  return ["", p];
}

export function split(p) {
  const [d, rest] = splitdrive(p);
  let i = rest.length;
  while (i && !SEPS.includes(rest[i - 1])) i--;
  let head = rest.slice(0, i);
  const tail = rest.slice(i);
  const trimmed = head.replace(new RegExp(`[${isWin ? "\\\\/" : "/"}]+$`), "");
  head = trimmed || head;
  return [d + head, tail];
}

export const basename = (p) => split(p)[1];
export const dirname = (p) => split(p)[0];

export function splitext(p) {
  let sepIndex = -1;
  for (const c of SEPS) sepIndex = Math.max(sepIndex, p.lastIndexOf(c));
  const dotIndex = p.lastIndexOf(".");
  if (dotIndex > sepIndex) {
    for (let i = sepIndex + 1; i < dotIndex; i++) if (p[i] !== ".") return [p.slice(0, dotIndex), p.slice(dotIndex)];
  }
  return [p, ""];
}

const isAbs = (p) => {
  const [, rest] = splitdrive(p);
  return rest.length > 0 && SEPS.includes(rest[0]);
};

export function join(a, ...rest) {
  let result = a;
  for (const b of rest) {
    if (isAbs(b) || (isWin && splitdrive(b)[0])) {
      result = b;
    } else if (!result || SEPS.includes(result[result.length - 1]) || (isWin && result.endsWith(":"))) {
      result += b;
    } else result += sep + b;
  }
  return result;
}

export function normpath(p) {
  if (p === "") return ".";
  let n = flavor.normalize(p);
  if (n.length > 1 && SEPS.includes(n[n.length - 1]) && !/^([A-Za-z]:)?[\\/]$/.test(n)) n = n.slice(0, -1);
  return n;
}

export const abspath = (p) => flavor.resolve(p);

export function relpath(p, start = ".") {
  const a = abspath(p);
  const b = abspath(start);
  if (isWin && splitdrive(a)[0].toLowerCase() !== splitdrive(b)[0].toLowerCase()) throw new Error("path is on mount");
  return flavor.relative(b, a) || ".";
}

export function isfile(p) {
  try {
    return fs.statSync(p).isFile();
  } catch {
    return false;
  }
}

// ── glob ──────────────────────────────────────────────────────────────────────

const hasMagic = (s) => /[*?[]/.test(s);

// fnmatch.translate for one path component
function fnmatchRe(pat) {
  let out = "";
  for (let i = 0; i < pat.length; i++) {
    const c = pat[i];
    if (c === "*") out += "[^]*";
    else if (c === "?") out += "[^]";
    else if (c === "[") {
      let j = i + 1;
      if (pat[j] === "!") j++;
      if (pat[j] === "]") j++;
      while (j < pat.length && pat[j] !== "]") j++;
      if (j >= pat.length) out += "\\[";
      else {
        let stuff = pat.slice(i + 1, j).replace(/\\/g, "\\\\");
        i = j;
        if (stuff[0] === "!") stuff = "^" + stuff.slice(1);
        else if (stuff[0] === "^") stuff = "\\" + stuff;
        out += `[${stuff.replace(/]/g, "\\]")}]`;
      }
    } else out += c.replace(/[.+^${}()|\\/]/g, "\\$&");
  }
  return new RegExp(`^${out}$`, isWin ? "i" : "");
}

function listdir(dir) {
  try {
    return fs.readdirSync(dir || ".");
  } catch {
    return [];
  }
}

const isDir = (p) => {
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
};

function exists(p) {
  try {
    fs.lstatSync(p);
    return true;
  } catch {
    return false;
  }
}

function glob1(dirname, pattern) {
  let names = listdir(dirname);
  if (!pattern.startsWith(".")) names = names.filter((n) => !n.startsWith("."));
  const re = fnmatchRe(pattern);
  return names.filter((n) => re.test(n));
}

function* rlistdir(dirname) {
  const names = listdir(dirname).filter((n) => !n.startsWith("."));
  for (const x of names) {
    yield x;
    const p = dirname ? join(dirname, x) : x;
    if (isDir(p)) for (const y of rlistdir(p)) yield join(x, y);
  }
}

function glob2(dirname, pattern) {
  const out = [pattern.slice(0, 0)];
  for (const x of rlistdir(dirname)) out.push(x);
  return out;
}

// glob.glob(pattern, recursive=True)
export function glob(pattern) {
  const [dirname0, basename0] = split(pattern);
  const dirname = dirname0;
  const base = basename0;
  if (!hasMagic(pattern)) {
    if (base) return exists(pattern) ? [pattern] : [];
    return isDir(dirname) ? [pattern] : [];
  }
  if (!dirname) {
    if (base === "**") return glob2(dirname, base);
    return glob1(dirname, base);
  }
  const dirs = dirname !== pattern && hasMagic(dirname) ? glob(dirname) : [dirname];
  const out = [];
  for (const d of dirs) {
    let names;
    if (hasMagic(base)) names = base === "**" ? glob2(d, base) : glob1(d, base);
    else names = exists(join(d, base)) ? [base] : [];
    for (const n of names) out.push(join(d, n));
  }
  return out;
}
