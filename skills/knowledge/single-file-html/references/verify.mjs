#!/usr/bin/env node
// Static verifier for single-file HTML.
// Usage: node verify.mjs <file.html>
// - Extracts every inline <script> (skips src= externals), syntax-checks each.
// - Runs structural sanity checks (DOCTYPE/html/head/body, balanced script/style tags).
// Exit code 0 = all good, 1 = at least one problem.

import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';

const file = process.argv[2];
if (!file) {
  console.error('usage: node verify.mjs <file.html>');
  process.exit(2);
}

let html;
try {
  html = readFileSync(file, 'utf8');
} catch (e) {
  console.error(`cannot read ${file}: ${e.message}`);
  process.exit(2);
}

const lineOf = (idx) => html.slice(0, idx).split('\n').length;
let problems = 0;
const tmp = mkdtempSync(join(tmpdir(), 'sfh-verify-'));

// ---- Tier 1a: syntax-check inline scripts ----
const re = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
let m, n = 0, checked = 0;
while ((m = re.exec(html)) !== null) {
  n++;
  const attrs = m[1], body = m[2];
  if (/\bsrc\s*=/.test(attrs)) continue; // external script, nothing to check
  checked++;
  const isModule =
    /type\s*=\s*["']module["']/.test(attrs) || /^\s*(import|export)\b/m.test(body);
  const ext = isModule ? 'mjs' : 'js';
  const p = join(tmp, `script-${n}.${ext}`);
  writeFileSync(p, body);
  const line = lineOf(m.index);
  try {
    execFileSync('node', ['--check', p], { stdio: ['ignore', 'ignore', 'pipe'] });
    console.log(`  OK   inline <script> #${n} (${isModule ? 'module' : 'script'}, ~line ${line})`);
  } catch (err) {
    problems++;
    const msg = (err.stderr ? err.stderr.toString() : err.message).trim();
    console.log(`  FAIL inline <script> #${n} (~line ${line}):\n${msg.replace(/^/gm, '       ')}`);
  }
}
if (checked === 0) console.log('  (no inline scripts to syntax-check)');

// ---- Tier 1b: structural sanity ----
const checks = [
  [/<!doctype html/i, '<!DOCTYPE html> present'],
  [/<html[\s>]/i, '<html> present'],
  [/<head[\s>]/i, '<head> present'],
  [/<body[\s>]/i, '<body> present'],
];
console.log('structure:');
for (const [rx, label] of checks) {
  const ok = rx.test(html);
  if (!ok) problems++;
  console.log(`  ${ok ? 'OK  ' : 'WARN'} ${label}`);
}
const countTag = (tag) => ({
  open: (html.match(new RegExp(`<${tag}\\b`, 'gi')) || []).length,
  close: (html.match(new RegExp(`</${tag}>`, 'gi')) || []).length,
});
for (const tag of ['script', 'style']) {
  const c = countTag(tag);
  const ok = c.open === c.close;
  if (!ok) problems++;
  console.log(`  ${ok ? 'OK  ' : 'FAIL'} <${tag}> balance (${c.open} open / ${c.close} close)`);
}

// ---- summary ----
console.log(`\n${problems === 0 ? 'PASS' : 'FAIL'}: ${checked} script(s) checked, ${problems} problem(s).`);
process.exit(problems === 0 ? 0 : 1);
