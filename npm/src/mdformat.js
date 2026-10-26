// Markdown and MDX, read the way the server reads them: the document is cut into numbered
// segments (`md.0001`, `md.0002`, ...) and the runs that must survive (inline code, URLs, tags)
// become `{0}`, `{1}`... placeholders. Only the reading half exists here, so nothing is kept
// of the skeleton the writer needs.
import { rx, strip, lstrip, rstrip } from "./py.js";

const FRONT_MATTER_CONFIG = new Set([
  "slug", "id", "uuid", "key", "date", "pubdate", "publishdate", "published", "updated",
  "updateddate", "lastmod", "created", "expirydate", "draft", "layout", "template", "type",
  "kind", "variant", "permalink", "url", "href", "link", "path", "src", "srcset", "image",
  "images", "img", "cover", "thumbnail", "icon", "avatar", "logo", "video", "audio", "file",
  "lang", "locale", "language", "translationkey", "order", "weight", "sort", "priority",
  "position", "index", "color", "colour", "theme", "class", "classname", "style", "css",
  "status", "canonical", "redirect", "redirect_from", "aliases", "tags", "categories",
  "author", "authors", "email", "twitter", "github", "width", "height", "format", "mime",
]);
const CONFIG_SUFFIX_RE = rx(
  "(?:^|[_-])(?:id|url|href|src|slug|date|path)$|[a-z](?:Id|Url|Href|Src|Slug|Date|Path)$",
);
const FM_SETTING_RE = rx(
  "^(?:[a-z0-9][a-z0-9_-]*|[./~]\\S*|[a-z][a-z0-9+.-]*://\\S+|\\d{4}-\\d\\d(?:-\\d\\d)?\\S*" +
    "|[\\w.-]+\\.[A-Za-z0-9]{2,5}|[-+]?\\d[\\d_.,]*)$",
);
const FM_KEY_RE = rx(
  "^(\\s*)((?:-\\s+)?)([A-Za-z0-9_.$@-]+|\"[^\"]*\"|'[^']*')(\\s*:)(?=\\s|$)(\\s*)(.*)$",
);
const FM_ITEM_RE = rx("^(\\s*)(-)(\\s+)(.*)$");
const BLOCK_SCALAR_RE = rx("^[|>][-+]?\\d?$");

const FENCE_RE = rx("^(\\s{0,3})(`{3,}|~{3,})(.*)$");
const HEADING_RE = rx("^(\\s{0,3}#{1,6}\\s+)(.*?)(\\s*\\{#[^}]*\\}\\s*)?$");
const SETEXT_RE = rx("^\\s{0,3}(=+|-+)\\s*$");
const LIST_RE = rx("^(\\s*(?:[-*+]|\\d{1,9}[.)])\\s+)(.*)$");
const QUOTE_RE = rx("^(\\s{0,3}>+\\s?)(.*)$");
const TABLE_SEP_RE = rx("^\\s*\\|?\\s*:?-{2,}:?\\s*(\\|\\s*:?-{2,}:?\\s*)*\\|?\\s*$");
const REF_DEF_RE = rx("^\\s{0,3}\\[[^\\]]+\\]:\\s+\\S+");
const HR_RE = rx("^\\s{0,3}([-*_])(\\s*\\1){2,}\\s*$");
const MDX_STMT_RE = rx("^\\s*(import|export)\\s");
const HTML_OPEN_RE = rx("^\\s{0,3}<[A-Za-z/!]");
const INDENTED_CODE_RE = rx("^(?: {4}|\\t)");

// Runs inside a segment that must reach the output byte-identical. The earlier alternatives win.
const PROTECT_RE = rx(
  "(?P<code>``[^`]+``|`[^`\\n]+`)" +
    "|(?P<autolink><https?://[^>\\s]+>)" +
    "|(?P<linkurl>(?<=\\])\\([^)\\s]*(?:\\s+\"[^\"]*\")?\\))" +
    "|(?P<reflink>(?<=\\])\\[[^\\]]*\\])" +
    "|(?P<tag></?[A-Za-z][A-Za-z0-9.-]*(?:\\s[^<>]*)?/?>)" +
    "|(?P<expr>\\{[^{}\\n]*\\})" +
    "|(?P<url>https?://[^\\s)\\]]+)" +
    "|(?P<entity>&[a-zA-Z]+;|&#\\d+;)",
  "g",
);
const WORD_CHAR_RE = /[\p{L}\p{Nl}\p{No}]/u; // Python's [^\W\d_]

function protect(text) {
  let n = 0;
  return text.replace(PROTECT_RE, () => `{${n++}}`);
}

function isProse(line) {
  if (!strip(line)) return false;
  return !(
    HR_RE.test(line) || REF_DEF_RE.test(line) || MDX_STMT_RE.test(line) ||
    HTML_OPEN_RE.test(line) || INDENTED_CODE_RE.test(line) || SETEXT_RE.test(line)
  );
}

class Builder {
  constructor() {
    this.flat = new Map();
    this.n = 0;
  }

  lit() {}

  // A translatable segment — or a literal, if there is nothing to translate in it.
  seg(text) {
    const masked = protect(text);
    if (!WORD_CHAR_RE.test(masked.replace(/\{/g, " ").replace(/\}/g, " "))) return;
    this.n++;
    this.flat.set("md." + String(this.n).padStart(4, "0"), masked);
  }
}

function isConfigKey(key) {
  key = strip(key, "\"'");
  return key.startsWith("_") || FRONT_MATTER_CONFIG.has(key.toLowerCase()) || CONFIG_SUFFIX_RE.test(key);
}

// decoded value of one YAML scalar token
function unquote(token) {
  if (token.length >= 2 && token[0] === token[token.length - 1] && token[0] === '"') {
    try {
      return JSON.parse(token);
    } catch {
      return token.slice(1, -1);
    }
  }
  if (token.length >= 2 && token[0] === token[token.length - 1] && token[0] === "'") return token.slice(1, -1).replace(/''/g, "'");
  return token;
}

// A plain scalar ends where ` #` starts a comment; a quoted one ends at its quote.
function splitComment(value) {
  let m;
  if (value[0] === '"') {
    m = /^"(?:[^"\\]|\\[^\n])*"/.exec(value);
    return m ? [value.slice(0, m[0].length), value.slice(m[0].length)] : [value, ""];
  }
  if (value[0] === "'") {
    m = /^'(?:[^']|'')*'/.exec(value);
    return m ? [value.slice(0, m[0].length), value.slice(m[0].length)] : [value, ""];
  }
  m = rx("\\s#").exec(value);
  return m ? [value.slice(0, m.index), value.slice(m.index)] : [value, ""];
}

function scalar(b, token, skip) {
  const decoded = unquote(token);
  if (skip || !strip(decoded) || FM_SETTING_RE.test(decoded)) return;
  b.seg(decoded);
}

// Top-level comma-separated parts of a one-line flow collection, or null if it nests.
function flowItems(inner) {
  const parts = [];
  let quote = "";
  let cur = "";
  for (const ch of inner) {
    if (quote) {
      cur += ch;
      if (ch === quote) quote = "";
      continue;
    }
    if ((ch === '"' || ch === "'") && !strip(cur)) quote = ch;
    else if ("[]{}".includes(ch)) return null;
    else if (ch === ",") {
      parts.push(cur);
      cur = "";
      continue;
    }
    cur += ch;
  }
  if (quote) return null;
  parts.push(cur);
  return parts;
}

// A one-line `[a, b]` or `{k: v, k2: v2}`. false if it is not one we can split safely.
function flow(b, value, skip, hard) {
  const isMap = value.startsWith("{");
  const items = flowItems(value.slice(1, -1));
  if (items === null) return false;
  for (const item of items) {
    const body = strip(item);
    if (isMap && body) {
      const m = rx("^([^:]+?)(\\s*:\\s*)(.*)$").exec(body);
      if (m && m[3]) scalar(b, m[3], hard || isConfigKey(m[1]));
    } else if (body) scalar(b, body, skip);
  }
  return true;
}

function value(b, v, skip, hard) {
  let [token] = splitComment(v);
  token = rstrip(token);
  const closing = { "[": "]", "{": "}" }[token[0]];
  if (closing && token.endsWith(closing) && flow(b, token, skip, hard)) return;
  if (token.length && "[{&*!|>%@`".includes(token[0])) return;
  scalar(b, token, skip);
}

// YAML front matter if present. Returns the number of lines consumed.
function frontMatter(lines, b) {
  if (!lines.length || !["---", "+++"].includes(rstrip(lines[0]))) return 0;
  const delim = rstrip(lines[0]);
  let end = null;
  for (let i = 1; i < lines.length; i++) {
    if (rstrip(lines[i]) === delim) {
      end = i;
      break;
    }
  }
  if (end === null) return 0;
  if (delim === "+++") return end + 1;
  // Open keys: [indent of their children, is configuration, is a `_` service section].
  const parents = [];
  let j = 1;
  while (j < end) {
    const raw = lines[j];
    const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
    const stripped = strip(line);
    if (!stripped || stripped.startsWith("#")) {
      j++;
      continue;
    }
    const indent = line.length - lstrip(line).length;
    const m = FM_KEY_RE.exec(line);
    const item = m ? null : FM_ITEM_RE.exec(line);
    if (m) {
      const keyIndent = indent + m[2].length;
      while (parents.length && parents[parents.length - 1][0] > (m[2] ? indent + 1 : indent)) parents.pop();
      const hard = parents.some((p) => p[2]) || strip(m[3], "\"'").startsWith("_");
      const skip = hard || isConfigKey(m[3]);
      const v = m[6];
      if (BLOCK_SCALAR_RE.test(strip(v))) {
        j++;
        const start = j;
        while (j < end && (!strip(lines[j]) || lines[j].length - lstrip(lines[j]).length > keyIndent)) j++;
        while (j > start && !strip(lines[j - 1])) j--;
        block(b, lines.slice(start, j), strip(v)[0], skip);
        continue;
      }
      if (strip(v)) value(b, v, skip, hard);
      else parents.push([keyIndent + 1, skip, hard]);
    } else if (item) {
      while (parents.length && parents[parents.length - 1][0] > indent + 1) parents.pop();
      const hard = parents.some((p) => p[2]);
      const skip = hard || Boolean(parents.length && parents[parents.length - 1][1]);
      value(b, item[4], skip, hard);
    }
    j++;
  }
  return end + 1;
}

// A `|` or `>` block scalar: one segment.
function block(b, body, style, skip) {
  if (!body.length || skip) return;
  const indent = Math.min(...body.filter((l) => strip(l)).map((l) => l.length - lstrip(l).length));
  let text = body.map((l) => rstrip(l.slice(indent), "\r")).join("\n");
  if (style === ">") text = text.replace(rx("(?<=\\S)\\n(?=\\S)", "g"), " ");
  b.seg(text);
}

// Flatten a Markdown/MDX document into translatable segments.
export function parseMarkdown(text) {
  const lines = text.split("\n");
  const b = new Builder();
  let i = frontMatter(lines, b);

  while (i < lines.length) {
    const line = lines[i];

    const fence = FENCE_RE.exec(line);
    if (fence) {
      const marker = fence[2];
      i++;
      while (i < lines.length) {
        const closing = FENCE_RE.exec(lines[i]);
        i++;
        if (closing && closing[2][0] === marker[0] && closing[2].length >= marker.length && !strip(closing[3])) break;
      }
      continue;
    }

    if (MDX_STMT_RE.test(line) || HTML_OPEN_RE.test(line)) {
      while (i < lines.length && strip(lines[i])) i++;
      continue;
    }

    if (!strip(line) || HR_RE.test(line) || REF_DEF_RE.test(line) || SETEXT_RE.test(line) || INDENTED_CODE_RE.test(line)) {
      i++;
      continue;
    }

    const heading = HEADING_RE.exec(line);
    if (heading) {
      b.seg(heading[2]);
      i++;
      continue;
    }

    if (line.includes("|") && strip(line).startsWith("|")) {
      if (TABLE_SEP_RE.test(line)) {
        i++;
        continue;
      }
      for (const cell of line.split("|")) if (strip(cell)) b.seg(strip(cell));
      i++;
      continue;
    }

    const quote = QUOTE_RE.exec(line);
    if (quote && strip(quote[2])) {
      b.seg(quote[2]);
      i++;
      continue;
    }

    const item = LIST_RE.exec(line);
    if (item) {
      b.seg(item[2]);
      i++;
      continue;
    }

    // Paragraph: consecutive prose lines, translated as one unit.
    const para = [];
    while (
      i < lines.length && isProse(lines[i]) && !FENCE_RE.test(lines[i]) && !HEADING_RE.test(lines[i]) &&
      !LIST_RE.test(lines[i]) && !QUOTE_RE.test(lines[i]) && !strip(lines[i]).startsWith("|")
    ) {
      para.push(lines[i]);
      i++;
    }
    if (para.length) b.seg(para.join("\n"));
    else i++;
  }
  return b.flat;
}
