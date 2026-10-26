// Mozilla Fluent (.ftl) read without Mozilla's parser: the part of Fluent that translation files
// use, producing the same keys as the Python reader. See kaeris/ftl.py for the key scheme.
import { reprStr, rx, strip, lstrip, rstrip, isSpace } from "./py.js";

const ORDER = ["zero", "one", "two", "few", "many", "other"];
const COUNTING = new Set(["zero", "one", "two", "few", "many"]);
const FOLD_MARK = "⟦plural⟧";

const ENTRY_RE = rx("^(-?[A-Za-z][A-Za-z0-9_-]*)[ \\t]*=(.*)$");
const ATTR_RE = rx("^[ \\t]+\\.([A-Za-z][A-Za-z0-9_-]*)[ \\t]*=(.*)$");
const VARIANT_RE = rx("^[ \\t]*(\\*?)\\[[ \\t]*([^\\]\\s][^\\]]*?)[ \\t]*\\][ \\t]*(.*)$");
const SIMPLE_VAR_RE = rx("\\{ (\\$[A-Za-z][\\w-]*) \\}", "g");

class FtlError extends Error {}

// Depth of `{` nesting at the start of every line, string literals ("...") ignored.
function scan(text) {
  const depths = [0];
  let depth = 0;
  let inStr = false;
  for (let k = 0; k < text.length; k++) {
    const c = text[k];
    if (inStr) {
      if (c === "\\") k++;
      else if (c === '"') inStr = false;
    } else if (c === '"' && depth) inStr = true;
    else if (c === "{") depth++;
    else if (c === "}") depth = Math.max(depth - 1, 0);
    else if (c === "\n") depths.push(depth);
  }
  return depths;
}

function dedent(lines, depths) {
  let common = null;
  const n = Math.min(lines.length, depths.length);
  for (let i = 0; i < n; i++) {
    const line = lines[i];
    if (depths[i] === 0 && strip(line)) {
      const indent = line.length - lstrip(line, " \t").length;
      common = common === null ? indent : Math.min(common, indent);
    }
  }
  common = common || 0;
  const out = [];
  for (let i = 0; i < n; i++) {
    const ln = lines[i];
    const d = depths[i];
    out.push(d === 0 && strip(ln) ? ln.slice(common) : d ? ln : "");
  }
  return out;
}

function patternText(first, rest) {
  const raw = [first, ...rest].join("\n");
  const depths = scan(raw);
  const lines = raw.split("\n");
  const head = lstrip(lines[0], " \t");
  const body = dedent(lines.slice(1), depths.slice(1));
  const out = [...(strip(head) ? [head] : []), ...body];
  while (out.length && !strip(out[0]) && !strip(head)) out.shift();
  return rstrip(out.join("\n"), " \t\n");
}

// [["text", s] | ["ph", inner]] — top-level `{ ... }` placeables and the text between.
function split(text) {
  const out = [];
  let buf = [];
  let k = 0;
  while (k < text.length) {
    const c = text[k];
    if (c !== "{") {
      buf.push(c);
      k++;
      continue;
    }
    let depth = 1;
    let j = k + 1;
    let inStr = false;
    while (j < text.length && depth) {
      const d = text[j];
      if (inStr) {
        if (d === "\\") j++;
        else if (d === '"') inStr = false;
      } else if (d === '"') inStr = true;
      else if (d === "{") depth++;
      else if (d === "}") depth--;
      j++;
    }
    if (depth) throw new FtlError("a { that never closes");
    if (buf.length) {
      out.push(["text", buf.join("")]);
      buf = [];
    }
    out.push(["ph", text.slice(k + 1, j - 1)]);
    k = j;
  }
  if (buf.length) out.push(["text", buf.join("")]);
  return out;
}

function topLevelArrow(inner) {
  let depth = 0;
  let inStr = false;
  for (let k = 0; k < inner.length - 1; k++) {
    const c = inner[k];
    if (inStr) {
      if (c === "\\") k++;
      else if (c === '"') inStr = false;
    } else if (c === '"') inStr = true;
    else if (c === "{") depth++;
    else if (c === "}") depth--;
    else if (depth === 0 && c === "-" && inner[k + 1] === ">") return k;
  }
  return -1;
}

// How the placeable is written once Fluent's own serializer has had it.
function normExpr(expr) {
  const out = [];
  let inStr = false;
  expr = strip(expr);
  const last = () => out[out.length - 1];
  for (let k = 0; k < expr.length; k++) {
    const c = expr[k];
    if (inStr) {
      out.push(c);
      if (c === "\\" && k + 1 < expr.length) {
        out.push(expr[k + 1]);
        k++;
      } else if (c === '"') inStr = false;
    } else if (c === '"') {
      inStr = true;
      out.push(c);
    } else if (isSpace(c)) {
      if (out.length && last() !== " " && last() !== "(") out.push(" ");
    } else if (c === ",") {
      while (out.length && last() === " ") out.pop();
      out.push(", ");
      while (k + 1 < expr.length && isSpace(expr[k + 1])) k++;
    } else if (c === ":") {
      while (out.length && last() === " ") out.pop();
      out.push(": ");
      while (k + 1 < expr.length && isSpace(expr[k + 1])) k++;
    } else if (c === ")") {
      while (out.length && last() === " ") out.pop();
      out.push(c);
    } else out.push(c);
  }
  return strip(out.join(""));
}

// [[key, isDefault, pattern text]] from what follows `->` inside a placeable.
function variants(body) {
  const lines = body.split("\n");
  const marks = [];
  lines.forEach((ln, n) => {
    const m = VARIANT_RE.exec(ln);
    if (m) marks.push([n, m]);
  });
  if (!marks.length) throw new FtlError("a selector with no [variant]");
  return marks.map(([n, m], idx) => {
    const end = idx + 1 < marks.length ? marks[idx + 1][0] : lines.length;
    return [m[2], Boolean(m[1]), patternText(m[3], lines.slice(n + 1, end))];
  });
}

// The pattern as Fluent's parser lists it: text runs and placeables, in order.
function elements(text) {
  const els = [];
  for (const [kind, value] of split(text)) {
    if (kind === "text") els.push(["text", value]);
    else if (topLevelArrow(value) >= 0) {
      const arrow = topLevelArrow(value);
      els.push(["select", variants(value.slice(arrow + 2)).map(([k, d, t]) => [k, d, elements(t)])]);
    } else els.push(["ph", normExpr(value)]);
  }
  return els;
}

const inline = (els) => els.map(([k, v]) => (k === "text" ? v : "{ " + v + " }")).join("");

function walk(els, path, out) {
  if (!els.some(([k]) => k === "select")) {
    const text = inline(els);
    if (strip(text)) out.set(path, text);
    return;
  }
  els.forEach(([kind, value], i) => {
    if (kind === "select") {
      for (const [key, , sub] of value) walk(sub, `${path}$${i}.${key}`, out);
    } else if (kind === "text" && strip(value)) out.set(`${path}~${i}`, value);
  });
}

// Paths ('emails$0') of selectors whose variants are plain text and whose keys are the CLDR
// plural categories.
function pluralPrefixes(entries) {
  const found = new Set();
  const visit = (els, path) => {
    els.forEach(([kind, value], i) => {
      if (kind !== "select") return;
      const keys = new Set(value.map(([k]) => k));
      const named = new Set([...keys].filter((k) => /^[A-Za-z][\w-]*$/.test(k)));
      const simple = !value.some(([, , sub]) => sub.some(([sk]) => sk === "select"));
      const inOrder = [...named].every((k) => ORDER.includes(k));
      const counting = [...named].some((k) => COUNTING.has(k));
      if (keys.size === named.size && inOrder && counting && simple) found.add(`${path}$${i}`);
      for (const [k, , sub] of value) visit(sub, `${path}$${i}.${k}`);
    });
  };
  for (const [path, els] of entries) visit(els, path);
  return found;
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

function fold(flat, prefixes) {
  const members = new Map();
  for (const key of flat.keys()) {
    const dot = key.lastIndexOf(".");
    const head = dot === -1 ? "" : key.slice(0, dot);
    const cat = key.slice(dot + 1);
    if (prefixes.has(head) && ORDER.includes(cat)) {
      if (!members.has(head)) members.set(head, new Map());
      members.get(head).set(cat, key);
    }
  }
  const groups = new Map();
  for (const [head, cats] of members) {
    const texts = new Map();
    for (const [c, k] of cats) texts.set(c, flat.get(k).replace(SIMPLE_VAR_RE, (_, v) => "{" + v.slice(1) + "}"));
    if (!cats.has("other") || ![...texts.values()].every(balanced)) continue;
    const arms = ORDER.filter((c) => texts.has(c)).map((c) => `${c} {${texts.get(c)}}`).join(" ");
    groups.set(head + FOLD_MARK, [new Set(cats.values()), `{count, plural, ${arms}}`, cats.values().next().value]);
  }
  if (!groups.size) return flat;
  const firstKey = new Map();
  const gone = new Set();
  for (const [name, [m, , f]] of groups) {
    firstKey.set(f, name);
    for (const k of m) gone.add(k);
  }
  const out = new Map();
  for (const [key, value] of flat) {
    if (firstKey.has(key)) out.set(firstKey.get(key), groups.get(firstKey.get(key))[1]);
    else if (!gone.has(key)) out.set(key, value);
  }
  return out;
}

// ([key -> text], [problems])
export function parse(text) {
  let flat = new Map();
  const entries = [];
  const problems = [];
  const lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const m = ENTRY_RE.exec(lines[i]);
    if (!m) {
      const l = lines[i];
      if (strip(l) && !l.startsWith("#") && !(l.length && isSpace(l[0]))) {
        const head = [...l].slice(0, 40).join("");
        problems.push(`line ${i + 1}: not a Fluent message (${reprStr(head)})`);
      }
      i++;
      continue;
    }
    const name = m[1];
    const first = m[2];
    const start = i;
    i++;
    const rest = [];
    while (i < lines.length && (!strip(lines[i]) || " \t".includes(lines[i][0]))) {
      rest.push(lines[i]);
      i++;
    }
    while (rest.length && !strip(rest[rest.length - 1])) rest.pop();
    const depths = scan([first, ...rest].join("\n"));
    const blocks = [["", first, []]];
    rest.forEach((line, idx) => {
      const a = depths[idx + 1] === 0 ? ATTR_RE.exec(line) : null;
      if (a) blocks.push([a[1], a[2], []]);
      else blocks[blocks.length - 1][2].push(line);
    });
    for (const [attr, head, body] of blocks) {
      const path = attr ? `${name}.${attr}` : name;
      let els;
      try {
        els = elements(patternText(head, body));
      } catch (e) {
        if (!(e instanceof FtlError)) throw e;
        problems.push(`line ${start + 1}: ${name}: ${e.message}`);
        continue;
      }
      if (els.length) {
        entries.push([path, els]);
        walk(els, path, flat);
      }
    }
  }
  flat = fold(flat, pluralPrefixes(entries));
  return [flat, problems];
}
