import { ValueError } from "./errors.js";

// An order-preserving JSON reader (objects come back as Map, so a key such as "10" does not
// jump ahead of "a") whose errors read like Python's json module, plus the writer for the report.

export class JSONError extends ValueError {
  constructor(msg, doc, pos) {
    const line = doc.slice(0, pos).split("\n").length;
    const col = pos - doc.lastIndexOf("\n", pos - 1);
    super(`${msg}: line ${line} column ${col} (char ${pos})`);
    this.name = "JSONError";
  }
}

const WS = new Set([" ", "\t", "\n", "\r"]);
const ESC = { '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t" };
const NUM = /-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?/y;

export function parse(doc) {
  let i = 0;
  const skip = () => {
    while (i < doc.length && WS.has(doc[i])) i++;
  };

  function str() {
    const begin = i - 1;
    let out = "";
    for (;;) {
      let j = i;
      while (j < doc.length) {
        const c = doc.charCodeAt(j);
        if (c === 0x22 || c === 0x5c || c < 0x20) break;
        j++;
      }
      out += doc.slice(i, j);
      i = j;
      if (i >= doc.length) throw new JSONError("Unterminated string starting at", doc, begin);
      const ch = doc[i];
      if (ch === '"') {
        i++;
        return out;
      }
      if (ch !== "\\") throw new JSONError("Invalid control character at", doc, i);
      const e = doc[i + 1];
      if (e === undefined) throw new JSONError("Unterminated string starting at", doc, begin);
      if (e !== "u") {
        if (!(e in ESC)) throw new JSONError("Invalid \\escape", doc, i);
        out += ESC[e];
        i += 2;
        continue;
      }
      const hex = doc.slice(i + 2, i + 6);
      if (!/^[0-9a-fA-F]{4}$/.test(hex)) throw new JSONError("Invalid \\uXXXX escape", doc, i + 1);
      out += String.fromCharCode(parseInt(hex, 16));
      i += 6;
    }
  }

  function value(depth) {
    if (depth > 900) throw new ValueError("nested too deeply: the JSON is deeper than this reader follows");
    const c = doc[i];
    if (c === '"') {
      i++;
      return str();
    }
    if (c === "{") {
      const start = i;
      i++;
      const out = new Map();
      skip();
      if (doc[i] === "}") {
        i++;
        return out;
      }
      for (;;) {
        skip();
        if (doc[i] !== '"')
          throw new JSONError("Expecting property name enclosed in double quotes", doc, i);
        i++;
        const k = str();
        skip();
        if (doc[i] !== ":") throw new JSONError("Expecting ':' delimiter", doc, i);
        i++;
        skip();
        out.set(k, value(depth + 1));
        skip();
        if (doc[i] === "}") {
          i++;
          return out;
        }
        if (doc[i] !== ",") throw new JSONError("Expecting ',' delimiter", doc, i);
        const comma = i;
        i++;
        skip();
        if (doc[i] === "}")
          throw new JSONError("Illegal trailing comma before end of object", doc, comma);
        void start;
      }
    }
    if (c === "[") {
      i++;
      const out = [];
      skip();
      if (doc[i] === "]") {
        i++;
        return out;
      }
      for (;;) {
        skip();
        out.push(value(depth + 1));
        skip();
        if (doc[i] === "]") {
          i++;
          return out;
        }
        if (doc[i] !== ",") throw new JSONError("Expecting ',' delimiter", doc, i);
        const comma = i;
        i++;
        skip();
        if (doc[i] === "]")
          throw new JSONError("Illegal trailing comma before end of array", doc, comma);
      }
    }
    if (doc.startsWith("null", i)) return (i += 4), null;
    if (doc.startsWith("true", i)) return (i += 4), true;
    if (doc.startsWith("false", i)) return (i += 5), false;
    if (doc.startsWith("NaN", i)) return (i += 3), NaN;
    if (doc.startsWith("Infinity", i)) return (i += 8), Infinity;
    if (doc.startsWith("-Infinity", i)) return (i += 9), -Infinity;
    NUM.lastIndex = i;
    const m = NUM.exec(doc);
    if (m) {
      i += m[0].length;
      return Number(m[0]);
    }
    throw new JSONError("Expecting value", doc, i);
  }

  skip();
  const v = value(0);
  skip();
  if (i !== doc.length) throw new JSONError("Extra data", doc, i);
  return v;
}

function quote(s, ensureAscii) {
  let out = '"';
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    const ch = s[i];
    if (ch === '"') out += '\\"';
    else if (ch === "\\") out += "\\\\";
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch === "\t") out += "\\t";
    else if (ch === "\b") out += "\\b";
    else if (ch === "\f") out += "\\f";
    else if (c < 0x20 || (ensureAscii && c > 0x7f)) out += "\\u" + c.toString(16).padStart(4, "0");
    else out += ch;
  }
  return out + '"';
}

// json.dumps(value, indent=N, ensure_ascii=...) for strings, numbers, booleans, null,
// arrays, Map and plain objects.
export function dumps(v, indent = 2, ensureAscii = false, level = 0) {
  if (v === null || v === undefined) return "null";
  if (typeof v === "string") return quote(v, ensureAscii);
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : Number.isNaN(v) ? "NaN" : v > 0 ? "Infinity" : "-Infinity";
  if (typeof v === "boolean") return v ? "true" : "false";
  const pad = " ".repeat(indent * (level + 1));
  const end = " ".repeat(indent * level);
  if (Array.isArray(v)) {
    if (!v.length) return "[]";
    return "[\n" + v.map((x) => pad + dumps(x, indent, ensureAscii, level + 1)).join(",\n") + "\n" + end + "]";
  }
  const entries = v instanceof Map ? [...v.entries()] : Object.entries(v);
  if (!entries.length) return "{}";
  return (
    "{\n" +
    entries.map(([k, x]) => `${pad}${quote(String(k), ensureAscii)}: ${dumps(x, indent, ensureAscii, level + 1)}`).join(",\n") +
    "\n" + end + "}"
  );
}
