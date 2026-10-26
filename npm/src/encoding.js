// How to read a locale file somebody else wrote. The same rules as the Python package
// (BOM, UTF-16/32 with and without one, UTF-8, Latin-1 for .properties), and the same words
// in the refusal, so the two tools say one thing about one file.
import { readFileSync } from "node:fs";

import { ValueError } from "./errors.js";

export class UnknownEncoding extends ValueError {}

const hex2 = (n) => n.toString(16).toUpperCase().padStart(2, "0");

function startsWith(buf, bytes) {
  return bytes.every((b, i) => buf[i] === b);
}

// strict UTF-8; returns {text} or {start} (offset of the first byte that is not valid)
function utf8(buf) {
  let i = 0;
  const n = buf.length;
  while (i < n) {
    const b = buf[i];
    if (b < 0x80) {
      i++;
      continue;
    }
    let need;
    let min;
    let cp;
    if (b >= 0xc2 && b <= 0xdf) [need, min, cp] = [1, 0x80, b & 0x1f];
    else if (b >= 0xe0 && b <= 0xef) [need, min, cp] = [2, 0x800, b & 0x0f];
    else if (b >= 0xf0 && b <= 0xf4) [need, min, cp] = [3, 0x10000, b & 0x07];
    else return { start: i };
    for (let k = 1; k <= need; k++) {
      const c = buf[i + k];
      if (c === undefined || (c & 0xc0) !== 0x80) return { start: i };
      cp = (cp << 6) | (c & 0x3f);
    }
    if (cp < min || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) return { start: i };
    i += need + 1;
  }
  return { text: buf.toString("utf8") };
}

// UTF-16, either order; {text} or {start}
function utf16(buf, le) {
  let out = "";
  const n = buf.length;
  for (let i = 0; i < n; i += 2) {
    if (i + 1 >= n) return { start: i };
    const u = le ? buf[i] | (buf[i + 1] << 8) : (buf[i] << 8) | buf[i + 1];
    if (u >= 0xd800 && u <= 0xdbff) {
      if (i + 3 >= n) return { start: i };
      const v = le ? buf[i + 2] | (buf[i + 3] << 8) : (buf[i + 2] << 8) | buf[i + 3];
      if (v < 0xdc00 || v > 0xdfff) return { start: i };
      out += String.fromCharCode(u, v);
      i += 2;
    } else if (u >= 0xdc00 && u <= 0xdfff) {
      return { start: i };
    } else out += String.fromCharCode(u);
  }
  return { text: out };
}

function utf32(buf, le) {
  let out = "";
  for (let i = 0; i < buf.length; i += 4) {
    if (i + 3 >= buf.length) return { start: i };
    const cp = le ? buf.readUInt32LE(i) : buf.readUInt32BE(i);
    if (cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) return { start: i };
    out += String.fromCodePoint(cp);
  }
  return { text: out };
}

const BOMS = [
  [[0xef, 0xbb, 0xbf], "utf-8-sig"],
  [[0xff, 0xfe, 0x00, 0x00], "utf-32-le"],
  [[0x00, 0x00, 0xfe, 0xff], "utf-32-be"],
  [[0xff, 0xfe], "utf-16-le"],
  [[0xfe, 0xff], "utf-16-be"],
];

// UTF-16/32 without a BOM, recognised by where the NUL bytes sit
function bomlessWide(raw) {
  const head = raw.subarray(0, 4096);
  if (!head.includes(0)) return null;
  if (head.length >= 4) {
    let zeros = 0;
    for (let i = 0; i < 4; i++) if (head[i] === 0) zeros++;
    if (zeros === 3) return head[0] ? "utf-32-le" : "utf-32-be";
  }
  let even = 0;
  let odd = 0;
  for (let i = 0; i < head.length; i += 2) if (head[i] === 0) even++;
  for (let i = 1; i < head.length; i += 2) if (head[i] === 0) odd++;
  const threshold = Math.max(2, Math.floor(head.length / 8));
  if (odd >= threshold && even === 0) return "utf-16-le";
  if (even >= threshold && odd === 0) return "utf-16-be";
  return null;
}

function decodeWide(buf, enc) {
  return enc.startsWith("utf-32") ? utf32(buf, enc.endsWith("le")) : utf16(buf, enc.endsWith("le"));
}

export function decodeBytes(content, filename = "") {
  for (const [bom, enc] of BOMS) {
    if (!startsWith(content, bom)) continue;
    const body = content.subarray(bom.length);
    const r = enc === "utf-8-sig" ? utf8(body) : decodeWide(body, enc);
    if (r.text !== undefined) return r.text;
    // Python reports the offset inside what follows the BOM for UTF-8 and inside the whole
    // buffer for the wide ones, but reads the byte out of the whole buffer in both.
    const start = enc === "utf-8-sig" ? r.start : r.start + bom.length;
    const label = enc === "utf-8-sig" ? "utf-8-sig" : enc.slice(0, 6);
    throw new UnknownEncoding(
      `the file starts with a ${label} byte-order mark but is not valid ${label} ` +
        `(byte 0x${hex2(content[start] ?? 0)} at offset ${start})`,
    );
  }

  const wide = bomlessWide(content);
  if (wide) {
    const r = decodeWide(content, wide);
    if (r.text !== undefined) return r.text;
  }

  const r = utf8(content);
  if (r.text === undefined) {
    if (filename.toLowerCase().endsWith(".properties")) return content.toString("latin1");
    throw new UnknownEncoding(`byte 0x${hex2(content[r.start])} at offset ${r.start} is not valid UTF-8`);
  }
  if (r.text.includes("\x00"))
    throw new UnknownEncoding(
      "the file contains NUL bytes, so it is not text in any encoding we can read",
    );
  return r.text;
}

export function readText(path) {
  return decodeBytes(readFileSync(path), path);
}
