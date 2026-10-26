#!/usr/bin/env node
import { main } from "../src/cli.js";

try {
  process.exitCode = main(process.argv.slice(2));
} catch (e) {
  // A crash must not look like a clean run, and must not look like "a translation is broken"
  // (exit 1) either: 2 is "the check itself could not be done".
  process.stderr.write(`✗ kaeris check stopped on an internal error: ${e && e.message ? e.message : e}\n`);
  if (process.env.KAERIS_DEBUG && e && e.stack) process.stderr.write(e.stack + "\n");
  process.exitCode = 2;
}
