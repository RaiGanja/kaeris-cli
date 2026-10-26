// Just enough XML for Android resources, XLIFF 1.2 and .resx: a parser that builds the same
// tree Python's ElementTree does (text, tail, namespace-qualified tags as {uri}name) and a
// serializer that writes children back the way ElementTree.tostring does, because the readers
// compare that text. No DTD/entity declarations are read at all.

import { ValueError } from "./errors.js";

export class XmlError extends ValueError {}

const XML_NS = "http://www.w3.org/XML/1998/namespace";
const NAME = /[\p{L}_:][\p{L}\p{N}_:.\-·\p{M}]*/uy;
const BAD_CHAR = /[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]/u;
const PREDEFINED = { lt: "<", gt: ">", amp: "&", quot: '"', apos: "'" };

export class Element {
  constructor(tag, attrib = new Map()) {
    this.tag = tag;
    this.attrib = attrib;
    this.text = null;
    this.tail = null;
    this.children = [];
  }
  get(name, dflt = null) {
    return this.attrib.has(name) ? this.attrib.get(name) : dflt;
  }
  *iter() {
    yield this;
    for (const c of this.children) yield* c.iter();
  }
}

export function parse(input) {
  const s = input.replace(/\r\n?/g, "\n");
  let pos = 0;

  const where = (p) => {
    const before = s.slice(0, p);
    const line = before.split("\n").length;
    return `line ${line}, column ${p - (before.lastIndexOf("\n") + 1)}`;
  };
  const fail = (msg, p = pos) => {
    throw new XmlError(`${msg}: ${where(p)}`);
  };
  const invalid = (p = pos) => fail("not well-formed (invalid token)", p);

  function entity(at) {
    // s[at] === "&"; returns [text, next]
    const end = s.indexOf(";", at);
    if (end === -1 || end - at > 40) return invalid(at);
    const body = s.slice(at + 1, end);
    if (body[0] === "#") {
      const hex = body[1] === "x";
      const digits = body.slice(hex ? 2 : 1);
      if (!(hex ? /^[0-9a-fA-F]+$/ : /^[0-9]+$/).test(digits)) return invalid(at);
      const cp = parseInt(digits, hex ? 16 : 10);
      const ok =
        cp === 9 || cp === 10 || cp === 13 || (cp >= 0x20 && cp <= 0xd7ff) ||
        (cp >= 0xe000 && cp <= 0xfffd) || (cp >= 0x10000 && cp <= 0x10ffff);
      if (!ok) fail("reference to invalid character number", at);
      return [String.fromCodePoint(cp), end + 1];
    }
    if (body in PREDEFINED) return [PREDEFINED[body], end + 1];
    if (!/^[\p{L}_:][\p{L}\p{N}_:.\-]*$/u.test(body)) return invalid(at);
    return fail("undefined entity", at);
  }

  function chars(from, to) {
    const m = BAD_CHAR.exec(s.slice(from, to));
    if (m) invalid(from + m.index);
  }

  // Text up to the next "<" with entities resolved.
  function text() {
    let out = "";
    let i = pos;
    for (;;) {
      let j = i;
      while (j < s.length && s[j] !== "<" && s[j] !== "&") j++;
      chars(i, j);
      const run = s.slice(i, j);
      const bad = run.indexOf("]]>");
      if (bad !== -1) invalid(i + bad);
      out += run;
      if (s[j] === "&") {
        const [t, next] = entity(j);
        out += t;
        i = next;
        continue;
      }
      pos = j;
      return out;
    }
  }

  function attrValue() {
    const q = s[pos];
    if (q !== '"' && q !== "'") invalid();
    pos++;
    let out = "";
    for (;;) {
      if (pos >= s.length) fail("unclosed token");
      const c = s[pos];
      if (c === q) {
        pos++;
        return out;
      }
      if (c === "<") invalid();
      if (c === "&") {
        const [t, next] = entity(pos);
        out += t;
        pos = next;
        continue;
      }
      if (BAD_CHAR.test(c)) invalid();
      out += c === "\n" || c === "\t" ? " " : c;
      pos++;
    }
  }

  function name() {
    NAME.lastIndex = pos;
    const m = NAME.exec(s);
    if (!m) return invalid();
    pos += m[0].length;
    return m[0];
  }

  const ws = () => {
    while (pos < s.length && " \t\n".includes(s[pos])) pos++;
  };

  function skipMisc(allowText) {
    // whitespace, comments and processing instructions outside the root element
    for (;;) {
      ws();
      if (s.startsWith("<!--", pos)) {
        const end = s.indexOf("-->", pos + 4);
        if (end === -1) fail("unclosed token");
        pos = end + 3;
      } else if (s.startsWith("<?", pos)) {
        const end = s.indexOf("?>", pos + 2);
        if (end === -1) fail("unclosed token");
        pos = end + 2;
      } else return;
    }
    void allowText;
  }

  function resolve(prefix, local, scope, isAttr) {
    if (prefix === "xml") return `{${XML_NS}}${local}`;
    if (prefix) {
      const uri = scope.get(prefix);
      if (uri === undefined || uri === "") return fail("unbound prefix");
      return `{${uri}}${local}`;
    }
    if (isAttr) return local;
    const uri = scope.get("");
    return uri ? `{${uri}}${local}` : local;
  }

  function startTag(parentScope) {
    // at "<" followed by a name
    pos++;
    const qname = name();
    const raw = [];
    for (;;) {
      const before = pos;
      ws();
      if (s[pos] === ">" || s.startsWith("/>", pos)) break;
      if (pos === before) invalid();
      if (pos >= s.length) fail("unclosed token");
      const an = name();
      ws();
      if (s[pos] !== "=") invalid();
      pos++;
      ws();
      raw.push([an, attrValue()]);
    }
    const scope = new Map(parentScope);
    for (const [k, v] of raw) {
      if (k === "xmlns") scope.set("", v);
      else if (k.startsWith("xmlns:")) scope.set(k.slice(6), v);
    }
    const split = (n) => {
      const i = n.indexOf(":");
      return i === -1 ? ["", n] : [n.slice(0, i), n.slice(i + 1)];
    };
    const [p, l] = split(qname);
    const el = new Element(resolve(p, l, scope, false));
    for (const [k, v] of raw) {
      if (k === "xmlns" || k.startsWith("xmlns:")) continue;
      const [ap, al] = split(k);
      const key = resolve(ap, al, scope, true);
      if (el.attrib.has(key)) fail("duplicate attribute");
      el.attrib.set(key, v);
    }
    const empty = s.startsWith("/>", pos);
    pos += empty ? 2 : 1;
    return { el, qname, scope, empty };
  }

  if (s.startsWith("<?xml", pos) && /\s/.test(s[5] ?? "")) {
    const end = s.indexOf("?>");
    if (end === -1) fail("unclosed token");
    pos = end + 2;
  }
  skipMisc();
  if (pos >= s.length) fail("no element found");
  if (s[pos] !== "<") fail("syntax error");
  if (s.startsWith("<!", pos)) invalid();

  let root = null;
  const stack = [];
  let target = null; // where the next run of text goes: [element, "text"|"tail"]
  const put = (t) => {
    if (!t || !target) return;
    const [el, slot] = target;
    el[slot] = (el[slot] ?? "") + t;
  };

  for (;;) {
    if (!stack.length && root) {
      skipMisc();
      if (pos < s.length) fail("junk after document element");
      return root;
    }
    if (pos >= s.length) fail("no element found");
    if (s[pos] !== "<") {
      if (!stack.length) fail("syntax error");
      put(text());
      continue;
    }
    if (s.startsWith("<!--", pos)) {
      const end = s.indexOf("-->", pos + 4);
      if (end === -1) fail("unclosed token");
      if (s.slice(pos + 4, end).includes("--")) invalid(pos + 4 + s.slice(pos + 4, end).indexOf("--"));
      pos = end + 3;
      continue;
    }
    if (s.startsWith("<![CDATA[", pos)) {
      if (!stack.length) invalid();
      const end = s.indexOf("]]>", pos + 9);
      if (end === -1) fail("unclosed CDATA section");
      chars(pos + 9, end);
      put(s.slice(pos + 9, end));
      pos = end + 3;
      continue;
    }
    if (s.startsWith("<?", pos)) {
      const end = s.indexOf("?>", pos + 2);
      if (end === -1) fail("unclosed token");
      pos = end + 2;
      continue;
    }
    if (s.startsWith("</", pos)) {
      const open = stack.pop();
      if (!open) invalid();
      const at = pos;
      pos += 2;
      const n = name();
      ws();
      if (s[pos] !== ">") invalid();
      if (n !== open.qname) fail("mismatched tag", at + 2);
      pos++;
      target = [open.el, "tail"];
      continue;
    }
    if (s.startsWith("<!", pos)) invalid();
    const at = pos;
    const tag = startTag(stack.length ? stack[stack.length - 1].scope : new Map());
    if (stack.length) {
      stack[stack.length - 1].el.children.push(tag.el);
    } else if (root) fail("junk after document element", at);
    else root = tag.el;
    if (tag.empty) target = [tag.el, "tail"];
    else {
      stack.push(tag);
      target = [tag.el, "text"];
    }
  }
}

// ── writing back, as ElementTree.tostring(el, encoding="unicode") does ──────────────

const escapeText = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const escapeAttr = (t) =>
  t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;")
    .replace(/\r/g, "&#13;").replace(/\n/g, "&#10;").replace(/\t/g, "&#09;");

const KNOWN_PREFIXES = new Map([
  ["http://www.w3.org/XML/1998/namespace", "xml"],
  ["http://www.w3.org/1999/xhtml", "html"],
  ["http://www.w3.org/1999/02/22-rdf-syntax-ns#", "rdf"],
  ["http://schemas.xmlsoap.org/wsdl/", "wsdl"],
  ["http://www.w3.org/2001/XMLSchema", "xs"],
  ["http://www.w3.org/2001/XMLSchema-instance", "xsi"],
  ["http://purl.org/dc/elements/1.1/", "dc"],
]);

export function tostring(elem) {
  const qnames = new Map();
  const namespaces = new Map();
  const add = (q) => {
    if (qnames.has(q)) return;
    if (q[0] === "{") {
      const cut = q.lastIndexOf("}");
      const uri = q.slice(1, cut);
      const local = q.slice(cut + 1);
      let prefix = namespaces.get(uri);
      if (prefix === undefined) {
        prefix = KNOWN_PREFIXES.get(uri);
        if (prefix === undefined) prefix = `ns${namespaces.size}`;
        if (prefix !== "xml") namespaces.set(uri, prefix);
      }
      qnames.set(q, prefix ? `${prefix}:${local}` : local);
    } else qnames.set(q, q);
  };
  for (const e of elem.iter()) {
    add(e.tag);
    for (const k of e.attrib.keys()) add(k);
  }

  const out = [];
  const write = (e, top) => {
    const tag = qnames.get(e.tag);
    out.push("<" + tag);
    if (e.attrib.size || (top && namespaces.size)) {
      if (top) {
        const byPrefix = [...namespaces.entries()].sort((a, b) => (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0));
        for (const [uri, p] of byPrefix) out.push(` xmlns${p ? ":" + p : ""}="${escapeAttr(uri)}"`);
      }
      for (const [k, v] of e.attrib) out.push(` ${qnames.get(k)}="${escapeAttr(v)}"`);
    }
    if (e.text || e.children.length) {
      out.push(">");
      if (e.text) out.push(escapeText(e.text));
      for (const c of e.children) write(c, false);
      out.push(`</${tag}>`);
    } else out.push(" />");
    if (e.tail) out.push(escapeText(e.tail));
  };
  write(elem, true);
  return out.join("");
}
