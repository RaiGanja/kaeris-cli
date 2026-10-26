// Small helpers that make JavaScript string handling behave like the Python the checks
// were written in: its whitespace set, its code-point ordering, its rounding, its regex
// classes (\w, \d, \s and \b are Unicode-aware there, ASCII-only here).

// Python's str.isspace() set: JS's \s minus U+FEFF plus U+001C-001F and U+0085.
const SPACE = "\\t-\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const WORD = "\\p{L}\\p{N}_";
const BOUND = `(?:(?<=[${WORD}])(?![${WORD}])|(?<![${WORD}])(?=[${WORD}]))`;

// Python regex source -> a JS RegExp with the same meaning (always /u).
export function rx(src, flags = "") {
  if (src.startsWith("(?i)")) {
    src = src.slice(4);
    flags += "i";
  }
  // Python's [^\W\d_]: a word character that is not a decimal digit or an underscore
  src = src.split("[^\\W\\d_]").join("[\\p{L}\\p{Nl}\\p{No}]");
  let out = "";
  let inClass = false;
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (c === "\\") {
      const n = src[i + 1];
      i++;
      if (n === "w") out += inClass ? WORD : `[${WORD}]`;
      else if (n === "d") out += "\\p{Nd}";
      else if (n === "s") out += inClass ? SPACE : `[${SPACE}]`;
      else if (n === "S") {
        if (inClass) throw new Error("\\S in a class");
        out += `[^${SPACE}]`;
      } else if (n === "W") {
        if (inClass) throw new Error("\\W in a class");
        out += `[^${WORD}]`;
      } else if (n === "b") {
        if (inClass) throw new Error("\\b in a class");
        out += BOUND;
      } else out += "\\" + n;
      continue;
    }
    if (inClass) {
      if (c === "]") inClass = false;
      out += c;
      continue;
    }
    if (c === "[") {
      inClass = true;
      out += c;
      if (src[i + 1] === "^") {
        out += "^";
        i++;
      }
      if (src[i + 1] === "]") {
        out += "\\]";
        i++;
      }
      continue;
    }
    if (c === ".") {
      out += "[^\\n]";
      continue;
    }
    if (c === "(" && src.startsWith("(?P<", i)) {
      out += "(?<";
      i += 3;
      continue;
    }
    out += c;
  }
  return new RegExp(out, [...new Set(flags + "u")].join(""));
}

export function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
}

export function isSpaceCode(c) {
  return (
    (c >= 9 && c <= 13) || (c >= 0x1c && c <= 0x20) || c === 0x85 || c === 0xa0 ||
    c === 0x1680 || (c >= 0x2000 && c <= 0x200a) || c === 0x2028 || c === 0x2029 ||
    c === 0x202f || c === 0x205f || c === 0x3000
  );
}

export function isSpace(ch) {
  return ch.length > 0 && isSpaceCode(ch.charCodeAt(0)) && ch.length === 1;
}

export function lstrip(s, chars) {
  let i = 0;
  if (chars === undefined) while (i < s.length && isSpaceCode(s.charCodeAt(i))) i++;
  else while (i < s.length && chars.includes(s[i])) i++;
  return s.slice(i);
}

export function rstrip(s, chars) {
  let j = s.length;
  if (chars === undefined) while (j > 0 && isSpaceCode(s.charCodeAt(j - 1))) j--;
  else while (j > 0 && chars.includes(s[j - 1])) j--;
  return s.slice(0, j);
}

export function strip(s, chars) {
  return lstrip(rstrip(s, chars), chars);
}

// str.split() with no argument
export function splitWs(s) {
  const out = [];
  let i = 0;
  const n = s.length;
  while (i < n) {
    while (i < n && isSpaceCode(s.charCodeAt(i))) i++;
    if (i >= n) break;
    let j = i;
    while (j < n && !isSpaceCode(s.charCodeAt(j))) j++;
    out.push(s.slice(i, j));
    i = j;
  }
  return out;
}

// len() in code points
export function plen(s) {
  let n = s.length;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) {
      const d = s.charCodeAt(i + 1);
      if (d >= 0xdc00 && d <= 0xdfff) {
        n--;
        i++;
      }
    }
  }
  return n;
}

// the first code point of s as a string ("" for "")
export function first(s) {
  if (!s) return "";
  const cp = s.codePointAt(0);
  return String.fromCodePoint(cp);
}

const fix = (c) => (c >= 0xe000 ? c - 0x800 : c >= 0xd800 ? c + 0x2000 : c);

// Python orders strings by code point, JS by UTF-16 unit; they differ for astral characters.
export function cmp(a, b) {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const x = a.charCodeAt(i);
    const y = b.charCodeAt(i);
    if (x !== y) return fix(x) - fix(y);
  }
  return a.length - b.length;
}

export function sorted(iter) {
  return [...iter].sort(cmp);
}

// round() with no digits: halves go to the even neighbour
export function pyRound(x) {
  if (Math.abs(x % 1) === 0.5) return 2 * Math.round(x / 2);
  return Math.round(x);
}

export function setDiff(a, b) {
  const out = new Set();
  for (const x of a) if (!b.has(x)) out.add(x);
  return out;
}

export function setEq(a, b) {
  if (a.size !== b.size) return false;
  for (const x of a) if (!b.has(x)) return false;
  return true;
}

export function isLowerChar(ch) {
  return /^\p{Lowercase}/u.test(ch);
}

export function isAlnumChar(ch) {
  return /^[\p{L}\p{N}]/u.test(ch);
}

// All matches as arrays: [whole, ...groups], with .index
export function findAll(re, text) {
  return [...text.matchAll(re.global ? re : new RegExp(re.source, re.flags + "g"))];
}

export class Counter extends Map {
  get(k) {
    return super.get(k) ?? 0;
  }
  add(k, n = 1) {
    this.set(k, this.get(k) + n);
  }
}

export const isDict = (x) => x instanceof Map;

// Python's repr() of a str: quotes chosen like Python does, unprintable characters escaped.
const UNPRINTABLE = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}\p{Zs}]/u;

export function reprStr(s) {
  const q = s.includes("'") && !s.includes('"') ? '"' : "'";
  let out = q;
  for (const ch of s) {
    const cp = ch.codePointAt(0);
    if (ch === "\\") out += "\\\\";
    else if (ch === q) out += "\\" + q;
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch === "\t") out += "\\t";
    else if (ch !== " " && UNPRINTABLE.test(ch)) {
      out += cp < 0x100 ? "\\x" + cp.toString(16).padStart(2, "0")
        : cp < 0x10000 ? "\\u" + cp.toString(16).padStart(4, "0")
        : "\\U" + cp.toString(16).padStart(8, "0");
    } else out += ch;
  }
  return out + q;
}

// Python's repr() of a list of strings: ['a', 'b']
export const reprList = (items) => "[" + items.map(reprStr).join(", ") + "]";
