// A reader for the YAML that locale files are written in: nested mappings, block and inline
// sequences, plain / 'single' / "double" quoted scalars (also across lines), `|` and `>` block
// scalars, one-line or multi-line `[a, b]` / `{a: b}`, comments. Scalars are typed the way
// PyYAML types them (YAML 1.1: `yes` is a boolean, `~` is null), mapping keys stay text.
// What it does not read it refuses by name instead of guessing.
import { rx, strip, lstrip, rstrip } from "./py.js";
import { ValueError } from "./errors.js";

export class YamlUnsupported extends ValueError {}
export class YamlError extends ValueError {}

const BLOCK_RE = rx("^([|>])([-+]?)(\\d?)([-+]?)\\s*(?:#.*)?$");
const INT_RE = /^[-+]?(?:0|[1-9][0-9_]*)$/;
const HEX_RE = /^[-+]?0x[0-9a-fA-F_]+$/;
const OCT_RE = /^[-+]?0[0-7_]+$/;
const BIN_RE = /^[-+]?0b[01_]+$/;
const FLOAT_RE = new RegExp(
  "^[-+]?(?:[0-9][0-9_]*)?\\.[0-9_]+(?:[eE][-+]?[0-9]+)?$" +
    "|^[-+]?[0-9][0-9_]*(?:\\.[0-9_]*)?[eE][-+]?[0-9]+$" +
    "|^[-+]?[0-9][0-9_]*\\.$",
);
const FLOAT_VALUE_RE = /^[-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?$/;
const INF_RE = /^[-+]?\.(?:inf|Inf|INF)$|^\.(?:nan|NaN|NAN)$/;
const DATE_RE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;
const SEXAGESIMAL_RE = /^[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+$/;
const NULLS = new Set(["~", "null", "Null", "NULL", ""]);
const TRUE = new Set(["yes", "Yes", "YES", "true", "True", "TRUE", "on", "On", "ON"]);
const FALSE = new Set(["no", "No", "NO", "false", "False", "FALSE", "off", "Off", "OFF"]);
const ESCAPES = {
  "0": "\0", a: "\x07", b: "\b", t: "\t", "\t": "\t", n: "\n", v: "\v", f: "\f", r: "\r",
  e: "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\", N: "\x85", _: "\xa0", L: "\u{2028}", P: "\u{2029}",
};

const validDate = (t) => {
  const [y, m, d] = t.split("-").map(Number);
  if (y < 1 || m < 1 || m > 12 || d < 1) return false;
  const leap = y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
  return d <= [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1];
};

const SPLIT_QUOTED_RE = rx("^\\s*:(?:\\s+(.*))?$");
const SPLIT_PLAIN_RE = rx("^(.*?[^\\s])\\s*:(?:\\s+(.*))?$");
const COMMENT_RE = rx("\\s#");

// The value of a plain scalar. Only its type matters downstream (a string is checked, a number,
// boolean, null or date is not), so non-strings come back as a marker of the right kind.
export function resolve(token) {
  if (NULLS.has(token)) return null;
  if (TRUE.has(token)) return true;
  if (FALSE.has(token)) return false;
  const clean = token.replace(/_/g, "");
  if (INT_RE.test(token)) return 1;
  if (OCT_RE.test(token)) return 1;
  if (HEX_RE.test(token)) return /[0-9a-fA-F]/.test(clean.replace(/^[-+]?0x/, "")) ? 1 : token;
  if (BIN_RE.test(token)) return /[01]/.test(clean.replace(/^[-+]?0b/, "")) ? 1 : token;
  if (SEXAGESIMAL_RE.test(token)) return 1;
  if (FLOAT_RE.test(token)) return FLOAT_VALUE_RE.test(clean) ? 1.5 : token;
  if (INF_RE.test(token)) return 1.5;
  if (DATE_RE.test(token)) return validDate(token) ? { date: token } : token;
  return token;
}

class Reader {
  constructor(text) {
    const raw = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    this.lines = []; // [indent or null for blank, content, lineno, raw line]
    let started = false;
    for (let n = 1; n <= raw.length; n++) {
      let line = raw[n - 1];
      if (line.startsWith("%")) throw new YamlUnsupported(`line ${n}: YAML directives (%...) are not read`);
      if (rstrip(line) === "---" || line.startsWith("--- ")) {
        if (started) throw new YamlUnsupported(`line ${n}: several YAML documents in one file are not read`);
        started = true;
        const rest = strip(line.slice(3));
        if (!rest || rest.startsWith("#")) continue;
        line = rest;
      } else if (rstrip(line) === "...") break;
      started = started || Boolean(strip(line) && !lstrip(line).startsWith("#"));
      const stripped = strip(line);
      if (!stripped || stripped.startsWith("#")) {
        this.lines.push([null, "", n, line]);
        continue;
      }
      const indent = line.length - lstrip(line, " ").length;
      if (line.slice(indent, indent + 1) === "\t") throw new YamlError(`line ${n}: a tab used as indentation`);
      this.lines.push([indent, rstrip(line.slice(indent)), n, line]);
    }
  }

  skipBlank(i) {
    while (i < this.lines.length && this.lines[i][0] === null) i++;
    return i;
  }

  block(i, depth = 0) {
    if (depth > 200) throw new YamlError("nested too deeply");
    i = this.skipBlank(i);
    if (i >= this.lines.length) return [null, i];
    const [indent, content] = this.lines[i];
    if (content === "-" || content.startsWith("- ")) return this.sequence(i, indent, depth);
    return this.mapping(i, indent, depth);
  }

  sequence(i, indent, depth) {
    const out = [];
    for (;;) {
      i = this.skipBlank(i);
      if (i >= this.lines.length) break;
      const [ind, content, n] = this.lines[i];
      if (ind !== indent || !(content === "-" || content.startsWith("- "))) {
        if (ind > indent) throw new YamlError(`line ${n}: unexpected indentation`);
        break;
      }
      let rest = content.slice(1);
      const gap = rest.length - lstrip(rest, " ").length;
      rest = lstrip(rest, " ");
      if (!rest || rest.startsWith("#")) {
        const j = this.skipBlank(i + 1);
        let value;
        if (j < this.lines.length && this.lines[j][0] > indent) [value, i] = this.block(j, depth + 1);
        else [value, i] = [null, i + 1];
        out.push(value);
        continue;
      }
      this.lines[i] = [indent + 1 + gap, rest, n, this.lines[i][3]];
      let value;
      if (this.splitKey(rest, n) !== null || rest === "-" || rest.startsWith("- ")) [value, i] = this.block(i, depth + 1);
      else [value, i] = this.scalarValue(i, rest, indent);
      out.push(value);
    }
    return [out, i];
  }

  // [key, rest] when `content` is `key: rest` or `key:`, else null.
  splitKey(content, n) {
    if (content.startsWith("? ")) throw new YamlUnsupported(`line ${n}: complex mapping keys (? ...) are not read`);
    if (content[0] === '"' || content[0] === "'") {
      const end = Reader.end(content, content[0]);
      if (end === -1) return null;
      const m = SPLIT_QUOTED_RE.exec(content.slice(end));
      if (!m) return null;
      return [this.quoted(content.slice(0, end), n), m[1] || ""];
    }
    if (content[0] === "[" || content[0] === "{") return null;
    const m = SPLIT_PLAIN_RE.exec(content);
    if (!m || COMMENT_RE.test(m[1])) return null;
    return [m[1], m[2] || ""];
  }

  mapping(i, indent, depth) {
    const out = new Map();
    for (;;) {
      i = this.skipBlank(i);
      if (i >= this.lines.length) break;
      const [ind, content, n] = this.lines[i];
      if (ind < indent) break;
      if (ind > indent) throw new YamlError(`line ${n}: unexpected indentation`);
      if (content === "-" || content.startsWith("- ")) break;
      const pair = this.splitKey(content, n);
      if (pair === null) throw new YamlError(`line ${n}: expected 'key: value', found ${pyRepr([...content].slice(0, 40).join(""))}`);
      const key = pair[0];
      let rest = pair[1];
      if (key === "<<") throw new YamlUnsupported(`line ${n}: YAML merge keys (<<) are not read`);
      if ("&*!".includes(content[0])) throw new YamlUnsupported(`line ${n}: anchors, aliases and tags on a key are not read`);
      rest = strip(rest);
      let value;
      if (!rest || rest.startsWith("#")) {
        const j = this.skipBlank(i + 1);
        if (j < this.lines.length && this.lines[j][0] > indent) [value, i] = this.block(j, depth + 1);
        else if (j < this.lines.length && this.lines[j][0] === indent && (this.lines[j][1] === "-" || this.lines[j][1].startsWith("- ")))
          [value, i] = this.sequence(j, indent, depth + 1);
        else [value, i] = [null, i + 1];
      } else [value, i] = this.scalarValue(i, rest, indent);
      out.set(key, value);
    }
    return [out, i];
  }

  // The value that starts on line `i` after `key:` or `- `; returns [value, next line].
  scalarValue(i, rest, indent) {
    const n = this.lines[i][2];
    if (rest[0] === "&" || rest[0] === "*") throw new YamlUnsupported(`line ${n}: YAML anchors and aliases (&x, *x) are not read`);
    if (rest[0] === "!") throw new YamlUnsupported(`line ${n}: YAML tags (!!type) are not read`);
    if (BLOCK_RE.test(rest)) return this.blockScalar(i, rest, indent);
    if (rest[0] === "[" || rest[0] === "{") return this.flow(i, rest);
    if (rest[0] === '"' || rest[0] === "'") return this.quotedValue(i, rest);
    const comment = COMMENT_RE;
    const cut = (t) => {
      const m = comment.exec(t);
      return m ? t.slice(0, m.index) : t;
    };
    let text = rstrip(cut(rest));
    i++;
    for (;;) {
      let j = i;
      while (j < this.lines.length && this.lines[j][0] === null) j++;
      if (j < this.lines.length && this.lines[j][0] > indent && this.splitKey(this.lines[j][1], this.lines[j][2]) === null) {
        text += " " + strip(cut(this.lines[j][1]));
        i = j + 1;
        continue;
      }
      break;
    }
    return [resolve(text), i];
  }

  blockScalar(i, header, indent) {
    const m = BLOCK_RE.exec(header);
    const style = m[1];
    const chomp = m[2] || m[4];
    const explicit = m[3] ? parseInt(m[3], 10) : 0;
    i++;
    const body = [];
    while (i < this.lines.length) {
      const [ind, , , rawLine] = this.lines[i];
      if (ind !== null && ind <= indent) break;
      body.push(rawLine);
      i++;
    }
    let blanks = 0;
    while (body.length && !strip(body[body.length - 1])) {
      body.pop();
      blanks++;
    }
    let lead;
    if (explicit) lead = indent + explicit;
    else {
      const ind = body.filter((l) => strip(l)).map((l) => l.length - lstrip(l, " ").length);
      lead = ind.length ? Math.min(...ind) : 0;
    }
    const rows = body.map((l) => (strip(l) ? l.slice(lead) : ""));
    let text;
    if (style === "|") text = rows.join("\n");
    else {
      text = "";
      let prev = null;
      for (const row of rows) {
        if (prev === null) text = row;
        else if (row === "") text += "\n";
        else if (prev === "") text += row;
        else if (row.startsWith(" ") || prev.startsWith(" ")) text += "\n" + row;
        else text += " " + row;
        prev = row;
      }
    }
    if (chomp === "+") text += "\n".repeat(blanks + 1);
    else if (chomp !== "-" && rows.length) text += "\n";
    return [text, i];
  }

  quoted(token, n) {
    const q = token[0];
    const inner = token.slice(1, -1);
    if (q === "'") return inner.replace(/''/g, "'");
    const out = [];
    let k = 0;
    while (k < inner.length) {
      const c = inner[k];
      if (c !== "\\") {
        out.push(c);
        k++;
        continue;
      }
      k++;
      const e = inner.slice(k, k + 1);
      if (e === "x" || e === "u" || e === "U") {
        const width = { x: 2, u: 4, U: 8 }[e];
        const digits = inner.slice(k + 1, k + 1 + width);
        const v = /^[0-9a-fA-F_]+$/.test(digits.replace(/^[-+]/, "")) && digits.length ? parseInt(digits.replace(/_/g, ""), 16) : NaN;
        if (Number.isNaN(v) || v > 0x10ffff) throw new YamlError(`line ${n}: bad escape \\${e}${digits}`);
        out.push(String.fromCodePoint(v));
        k += 1 + width;
      } else if (e !== "" && Object.hasOwn(ESCAPES, e)) {
        out.push(ESCAPES[e]);
        k++;
      } else throw new YamlError(`line ${n}: unknown escape \\${e}`);
    }
    return out.join("");
  }

  quotedValue(i, rest) {
    const n = this.lines[i][2];
    const q = rest[0];
    let text = rest;
    let j = i;
    while (Reader.end(text, q) === -1) {
      j++;
      if (j >= this.lines.length) throw new YamlError(`line ${n}: a quoted string that never closes`);
      text += "\n" + strip(this.lines[j][3]);
    }
    const end = Reader.end(text, q);
    let token = text.slice(0, end);
    const tail = strip(text.slice(end));
    if (tail && !tail.startsWith("#")) throw new YamlError(`line ${n}: unexpected text after the closing quote: ${pyRepr([...tail].slice(0, 30).join(""))}`);
    if (token.includes("\n")) token = q + Reader.fold(token.slice(1, -1), q) + q;
    return [this.quoted(token, n), j + 1];
  }

  // A quoted scalar written over several lines: a line break is a space, a blank line is a
  // line break, and a trailing backslash joins lines without either.
  static fold(inner, q) {
    if (q === '"') inner = inner.replace(/\\\n[ \t]*/g, "");
    const parts = inner.split("\n");
    let out = rstrip(parts[0], " \t");
    let pending = 0;
    for (let part of parts.slice(1)) {
      part = strip(part, " \t");
      if (!part) {
        pending++;
        continue;
      }
      out += pending ? "\n".repeat(pending) : " ";
      out += part;
      pending = 0;
    }
    return out;
  }

  // Index just past the quote that closes the string opening `text`, or -1.
  static end(text, q) {
    let j = 1;
    while (j < text.length) {
      if (text[j] === "\\" && q === '"') {
        j += 2;
        continue;
      }
      if (text[j] === q) {
        if (q === "'" && text.slice(j + 1, j + 2) === "'") {
          j += 2;
          continue;
        }
        return j + 1;
      }
      j++;
    }
    return -1;
  }

  flow(i, rest) {
    const n = this.lines[i][2];
    let text = rest;
    let j = i;
    while (!Reader.balanced(text)) {
      j++;
      if (j >= this.lines.length) throw new YamlError(`line ${n}: a [ or { that never closes`);
      text += " " + this.lines[j][1];
    }
    const [value, pos] = this.flowValue(text, 0, n);
    const tail = strip(text.slice(pos));
    if (tail && !tail.startsWith("#")) throw new YamlError(`line ${n}: unexpected text after the closing bracket: ${pyRepr([...tail].slice(0, 30).join(""))}`);
    return [value, j + 1];
  }

  static balanced(text) {
    let depth = 0;
    let q = "";
    for (let k = 0; k < text.length; k++) {
      const c = text[k];
      if (q) {
        if (c === "\\" && q === '"') k++;
        else if (c === q) q = "";
      } else if (c === '"' || c === "'") q = c;
      else if (c === "[" || c === "{") depth++;
      else if (c === "]" || c === "}") depth--;
    }
    return depth <= 0;
  }

  static ws(s, p) {
    while (p < s.length && (s[p] === " " || s[p] === "\t")) p++;
    return p;
  }

  flowValue(s, p, n) {
    p = Reader.ws(s, p);
    if (p >= s.length) throw new YamlError(`line ${n}: a flow collection ends too early`);
    if (s[p] === "[") {
      const out = [];
      p++;
      for (;;) {
        p = Reader.ws(s, p);
        if (s.slice(p, p + 1) === "]") return [out, p + 1];
        let value;
        [value, p] = this.flowValue(s, p, n);
        out.push(value);
        p = Reader.ws(s, p);
        if (s.slice(p, p + 1) === ",") p++;
        else if (s.slice(p, p + 1) !== "]") throw new YamlError(`line ${n}: expected , or ] in a flow list`);
      }
    }
    if (s[p] === "{") {
      const out = new Map();
      p++;
      for (;;) {
        p = Reader.ws(s, p);
        if (s.slice(p, p + 1) === "}") return [out, p + 1];
        let key;
        [key, p] = this.flowScalar(s, p, n, ":,}", false);
        p = Reader.ws(s, p);
        let value = null;
        if (s.slice(p, p + 1) === ":") {
          p = Reader.ws(s, p + 1);
          if (![",", "}"].includes(s.slice(p, p + 1))) [value, p] = this.flowValue(s, p, n);
        }
        out.set(key, value);
        p = Reader.ws(s, p);
        if (s.slice(p, p + 1) === ",") p++;
        else if (s.slice(p, p + 1) !== "}") throw new YamlError(`line ${n}: expected , or } in a flow mapping`);
      }
    }
    return this.flowScalar(s, p, n, ",]}", true);
  }

  flowScalar(s, p, n, stops, typed) {
    if (["&", "*", "!"].includes(s.slice(p, p + 1))) throw new YamlUnsupported(`line ${n}: anchors, aliases and tags are not read`);
    if (['"', "'"].includes(s.slice(p, p + 1))) {
      const end = Reader.end(s.slice(p), s[p]);
      if (end === -1) throw new YamlError(`line ${n}: a quoted string that never closes`);
      return [this.quoted(s.slice(p, p + end), n), p + end];
    }
    let k = p;
    while (k < s.length && !stops.includes(s[k]) && !(s[k] === ":" && ["", " "].includes(s.slice(k + 1, k + 2)))) k++;
    const token = strip(s.slice(p, k));
    return [typed ? resolve(token) : token, k];
  }
}

// Python's repr() of a short string
function pyRepr(s) {
  const q = s.includes("'") && !s.includes('"') ? '"' : "'";
  let out = q;
  for (const ch of s) {
    if (ch === "\\") out += "\\\\";
    else if (ch === q) out += "\\" + q;
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch === "\t") out += "\\t";
    else if (/[\x00-\x1f\x7f]/.test(ch)) out += "\\x" + ch.charCodeAt(0).toString(16).padStart(2, "0");
    else out += ch;
  }
  return out + q;
}

// The document as values. The root must be a mapping.
export function load(text) {
  const reader = new Reader(text);
  let value;
  let i;
  try {
    [value, i] = reader.block(0);
  } catch (e) {
    if (e instanceof RangeError) throw new YamlError("nested too deeply");
    throw e;
  }
  i = reader.skipBlank(i);
  if (i < reader.lines.length) throw new YamlError(`line ${reader.lines[i][2]}: content this reader could not place`);
  if (!(value instanceof Map)) throw new YamlError("the YAML root must be a mapping");
  return value;
}
