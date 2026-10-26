// Nested objects and lists -> {dotted key: leaf}, the way the server flattens them: list items
// are addressed by index (`budgets.0`), every scalar leaf is kept (the checks look at strings only).

import { ValueError } from "./errors.js";

export const MAX_NEST_DEPTH = 50;

export class TooDeep extends ValueError {}

function tooDeep(key, depth) {
  const k = [...key].slice(0, 80).join("");
  return new TooDeep(
    `"${k}" is nested ${depth} levels deep — deeper than the ${MAX_NEST_DEPTH} ` +
      "levels KAERIS handles. Flatten that part of the file (or split it) and try again.",
  );
}

function* leaves(obj, prefix = "", depth = 1) {
  const items = obj instanceof Map ? obj.entries() : obj.entries();
  for (const [k, v] of items) {
    const key = prefix ? `${prefix}.${k}` : String(k);
    if (v instanceof Map || Array.isArray(v)) {
      if (depth >= MAX_NEST_DEPTH) throw tooDeep(key, depth + 1);
      yield* leaves(v, key, depth + 1);
    } else yield [key, v];
  }
}

export function flattenAll(obj) {
  const out = new Map();
  for (const [k, v] of leaves(obj)) out.set(k, v);
  return out;
}
