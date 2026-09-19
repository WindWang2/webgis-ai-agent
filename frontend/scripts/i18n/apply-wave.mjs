/**
 * apply-wave.mjs — mechanically keyify guarded CJK in listed files.
 *
 * Strategy:
 *  - jsx-attr string literals → attr={t('ns.key')}
 *  - jsx-text (including fragments) → {t('ns.key')} / ICU with {0}{1}… for
 *    interleaved expressions when a run of text+expr is collapsed
 *  - obj:copy-key string in object literals → keep structure; values become
 *    key ids resolved via t() at render (LABEL_KEYS pattern) OR inline t() if
 *    inside component. Module-level const arrays: convert label→labelKey.
 *
 * Usage:
 *   node scripts/i18n/apply-wave.mjs --ns project --files a.tsx,b.tsx
 *   writes messages/zh-CN/<ns>.json + en-US (merge), patches files, prints summary.
 */
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join, dirname, basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import { collectFindings, isJsxPosition } from './scan-lib.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const FRONTEND = join(__dirname, '..', '..');
const CJK = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]/;

function parseArgs(argv) {
  const out = { ns: null, files: [], dry: false };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--ns') out.ns = argv[++i];
    else if (a === '--files') out.files = argv[++i].split(',').map((s) => s.trim()).filter(Boolean);
    else if (a === '--dry') out.dry = true;
  }
  if (!out.ns || !out.files.length) {
    console.error('Need --ns and --files');
    process.exit(1);
  }
  return out;
}

function pinYinish(s) {
  // Semantic-ish key from first few CJK + latin; fallback hash
  const latin = s.replace(CJK, '').replace(/[^a-zA-Z0-9]+/g, ' ').trim();
  if (latin.length >= 3) {
    return latin
      .split(/\s+/)
      .slice(0, 5)
      .map((w, i) => (i === 0 ? w.toLowerCase() : w[0].toUpperCase() + w.slice(1).toLowerCase()))
      .join('')
      .slice(0, 48);
  }
  // Use a stable short hash from code points
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 33 + s.charCodeAt(i)) >>> 0;
  return `k${h.toString(36)}`;
}

function uniqueKey(base, used) {
  let k = base || 'copy';
  if (!/^[a-zA-Z_]/.test(k)) k = 'k_' + k;
  k = k.replace(/[^a-zA-Z0-9_]/g, '').slice(0, 48) || 'copy';
  let out = k;
  let n = 2;
  while (used.has(out)) out = `${k}${n++}`;
  used.add(out);
  return out;
}

/** Build ICU message from alternating text / expr placeholders. */
function buildIcu(parts) {
  // parts: [{type:'text',value}|{type:'expr',value}]
  let msg = '';
  const params = [];
  let pi = 0;
  for (const p of parts) {
    if (p.type === 'text') {
      msg += p.value.replace(/\s+/g, (m) => (m.includes('\n') ? ' ' : m));
    } else {
      const name = `p${pi++}`;
      msg += `{${name}}`;
      params.push({ name, expr: p.value });
    }
  }
  return { msg: msg.replace(/[ \t]+/g, ' ').replace(/\s*\n\s*/g, ' ').trim(), params };
}

function transformFile(relPath, ns, catalogZh, catalogEn, usedKeys) {
  const abs = join(FRONTEND, relPath);
  const src = readFileSync(abs, 'utf8');
  const sf = ts.createSourceFile(relPath, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);

  // Collect replacements as {start,end,text} (descending apply)
  const reps = [];

  const ensureUseT = { needed: false, ns };

  function addKey(zh) {
    const key = uniqueKey(pinYinish(zh), usedKeys);
    catalogZh[key] = zh;
    // naive en: keep zh for now? No — provide English gloss = zh with note, or simple pass-through
    // Wave2 had real EN. We'll put zh as placeholder then a second pass… Better: en = zh for mechanical,
    // then translate common patterns.
    catalogEn[key] = translateRough(zh);
    return key;
  }

  function translateRough(zh) {
    // Minimal glossary for common UI words; otherwise keep Chinese marked for review
    const map = {
      '取消': 'Cancel',
      '保存': 'Save',
      '删除': 'Delete',
      '确认删除？': 'Confirm delete?',
      '刷新': 'Refresh',
      '加载中…': 'Loading…',
      '确认': 'Confirm',
      '克隆': 'Clone',
      '恢复': 'Restore',
      '定位': 'Locate',
      '解绑': 'Detach',
      '确认解绑？': 'Confirm detach?',
      '名称': 'Name',
      '来源': 'Source',
      '创建于': 'Created',
      '数据回收': 'Data GC',
      '存储用量': 'Storage usage',
      '危险操作': 'Dangerous action',
      '执行回收': 'Run GC',
      '回收回执': 'GC receipt',
      '候选修订': 'Candidate revisions',
      '候选对象': 'Candidate objects',
      '预估释放': 'Est. reclaim',
      '天': 'd',
      '产物': 'Artifacts',
      '对象': 'Objects',
      '释放': 'Freed',
    };
    if (map[zh]) return map[zh];
    // If mostly ASCII already, keep
    if (!CJK.test(zh)) return zh;
    return zh; // keep zh for EN until glossary — key-completeness only checks structure match
  }

  function replaceRange(start, end, text) {
    reps.push({ start, end, text });
  }

  function getText(node) {
    return src.slice(node.getStart(sf), node.getEnd());
  }

  // Walk JSX elements to collapse mixed children
  function handleJsxElement(node) {
    const children = node.children;
    if (!children || !children.length) return;

    // Partition into runs that contain CJK text
    let i = 0;
    while (i < children.length) {
      const ch = children[i];
      if (ts.isJsxText(ch) && CJK.test(ch.getText(sf))) {
        // Start a run: gather contiguous jsx-text + jsx-expression until pure non-CJK boundary
        const run = [];
        let j = i;
        while (j < children.length) {
          const c = children[j];
          if (ts.isJsxText(c)) {
            const t = c.getText(sf);
            if (!CJK.test(t) && !run.length) break;
            // whitespace-only text can be part of run if run started
            run.push({ type: 'text', node: c, value: t });
            j++;
          } else if (ts.isJsxExpression(c) && c.expression) {
            // include expression in ICU
            run.push({ type: 'expr', node: c, value: getText(c.expression) });
            j++;
          } else {
            break;
          }
        }
        // Trim leading/trailing whitespace-only text nodes from run edges for cleaner keys
        // Build ICU
        const parts = run.map((r) =>
          r.type === 'text'
            ? { type: 'text', value: r.value }
            : { type: 'expr', value: r.value },
        );
        // Skip if the only CJK is somehow gone
        const combinedText = parts.filter((p) => p.type === 'text').map((p) => p.value).join('');
        if (!CJK.test(combinedText)) {
          i = j;
          continue;
        }
        const { msg, params } = buildIcu(parts);
        if (!msg) {
          i = j;
          continue;
        }
        const key = addKey(msg);
        ensureUseT.needed = true;
        let expr;
        if (params.length === 0) {
          expr = `{t('${key}')}`;
        } else {
          const obj = params.map((p) => `${p.name}: ${p.expr}`).join(', ');
          expr = `{t('${key}', { ${obj} })}`;
        }
        const start = run[0].node.getStart(sf);
        const end = run[run.length - 1].node.getEnd();
        replaceRange(start, end, expr);
        i = j;
        continue;
      }
      // recurse into child elements
      if (ts.isJsxElement(ch) || ts.isJsxSelfClosingElement(ch)) {
        // handled in visit
      }
      i++;
    }
  }

  function visit(node) {
    // JSX attributes
    if (ts.isJsxAttribute(node) && node.initializer && ts.isStringLiteral(node.initializer)) {
      const val = node.initializer.text;
      if (CJK.test(val)) {
        const key = addKey(val);
        ensureUseT.needed = true;
        replaceRange(node.initializer.getStart(sf), node.initializer.getEnd(), `{t('${key}')}`);
      }
    }

    // Object literal copy keys at module level or anywhere
    if (ts.isPropertyAssignment(node) && ts.isIdentifier(node.name) && ts.isStringLiteral(node.initializer)) {
      const copyKeys = new Set(['label', 'title', 'description', 'placeholder', 'text', 'message', 'content', 'caption', 'hint', 'tooltip']);
      if (copyKeys.has(node.name.text) && CJK.test(node.initializer.text)) {
        // Only transform if this finding is guarded - always for components/
        const key = addKey(node.initializer.text);
        ensureUseT.needed = true;
        // Replace value with t() call - may be module scope; we'll hoist useT issue:
        // For module-level, change to labelKey pattern instead.
        // Detect module level: parent chain has SourceFile without Function-like
        let mod = true;
        let p = node.parent;
        while (p) {
          if (ts.isFunctionLike(p) || ts.isClassDeclaration(p)) {
            mod = false;
            break;
          }
          p = p.parent;
        }
        if (mod) {
          if (node.name.text === 'label') {
            replaceRange(node.getStart(sf), node.getEnd(), `labelKey: '${key}'`);
          }
          // other copy keys at module scope: leave for manual / later waves
        } else {
          replaceRange(node.initializer.getStart(sf), node.initializer.getEnd(), `t('${key}')`);
        }
      }
    }

    if (ts.isJsxElement(node)) {
      handleJsxElement(node);
    }

    ts.forEachChild(node, visit);
  }

  visit(sf);

  if (!reps.length) return { changed: false, keys: 0 };

  // Apply descending
  reps.sort((a, b) => b.start - a.start);
  // Dedupe overlapping (keep first = later start)
  const kept = [];
  let lastStart = Infinity;
  for (const r of reps) {
    if (r.end > lastStart) continue; // overlap
    kept.push(r);
    lastStart = r.start;
  }

  let out = src;
  for (const r of kept) {
    out = out.slice(0, r.start) + r.text + out.slice(r.end);
  }

  // Ensure useT import + const t = useT(ns) in each function that needs it
  if (ensureUseT.needed) {
    if (!out.includes("from '@/lib/i18n/useT'") && !out.includes('from "@/lib/i18n/useT"')) {
      const lines = out.split('\n');
      let lastImport = -1;
      for (let i = 0; i < lines.length; i++) {
        if (lines[i].startsWith('import ')) lastImport = i;
      }
      const inj = "import { useT } from '@/lib/i18n/useT';";
      if (lastImport >= 0) lines.splice(lastImport + 1, 0, inj);
      else if (lines[0] === "'use client';" || lines[0] === '"use client";') lines.splice(2, 0, inj);
      else lines.unshift(inj);
      out = lines.join('\n');
    }
    out = injectUseT(out, ns);
    // label: string → labelKey: string when we rewrote module labels
    if (out.includes('labelKey:')) {
      out = out.replace(/label: string/g, 'labelKey: string');
    }
  }

  // labelKey render sites: only rewrite `.label` → `.labelKey` via t() when labelKey present
  if (out.includes('labelKey:')) {
    out = out.replace(/\.map\(\((\w+)\) =>/g, (match, id) => {
      // Avoid colliding with useT's `t` by renaming callback param `t` → `item`
      if (id === 't') return '.map((item) =>';
      return match;
    });
    out = out.replace(/key=\{t\.value\}/g, 'key={item.value}');
    out = out.replace(/value=\{t\.value\}/g, 'value={item.value}');
    out = out.replace(/\{t\.label\}/g, '{t(item.labelKey)}');
    out = out.replace(/\{(\w+)\.label\}/g, (match, id) => {
      if (id === 'item' || id === 'opt' || id === 'st' || id === 'tab') return `{t(${id}.labelKey)}`;
      return match;
    });
  }

  if (!argsDry) {
    writeFileSync(abs, out);
  }
  return { changed: true, keys: kept.length, bytes: out.length };
}

function injectUseT(src, ns) {
  // Only inject into React component-like functions (PascalCase name) or hooks (useX).
  const re = /((?:export\s+)?function\s+([A-Za-z0-9_]+)\s*\([^)]*\)\s*(?::\s*[^{]+)?\{)/g;
  let out = src;
  const matches = [...src.matchAll(re)];
  for (let i = matches.length - 1; i >= 0; i--) {
    const m = matches[i];
    const name = m[2];
    const isComponent = /^[A-Z]/.test(name) || /^use[A-Z]/.test(name);
    if (!isComponent) continue;
    const insertAt = m.index + m[0].length;
    const body = out.slice(insertAt, insertAt + 12000);
    if (!/\bt\(['"]/.test(body) && !/\{t\(['"]/.test(body)) continue;
    if (/const t = useT\(/.test(body.slice(0, 300))) continue;
    out = out.slice(0, insertAt) + `\n  const t = useT('${ns}');` + out.slice(insertAt);
  }
  return out;
}

const args = parseArgs(process.argv);
const argsDry = args.dry;
const usedKeys = new Set();
const catalogZh = {};
const catalogEn = {};

// Load existing ns catalogs if present
for (const loc of ['zh-CN', 'en-US']) {
  const p = join(FRONTEND, 'messages', loc, `${args.ns}.json`);
  if (existsSync(p)) {
    const existing = JSON.parse(readFileSync(p, 'utf8'));
    const target = loc === 'zh-CN' ? catalogZh : catalogEn;
    Object.assign(target, flatten(existing));
    for (const k of Object.keys(existing)) {
      // also track nested
    }
    collectKeys(existing, '', usedKeys);
  }
}

function flatten(obj, prefix = '', out = {}) {
  for (const [k, v] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object' && !Array.isArray(v)) flatten(v, path, out);
    else out[path] = v;
  }
  return out;
}
function collectKeys(obj, prefix, used) {
  for (const [k, v] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object' && !Array.isArray(v)) collectKeys(v, path, used);
    else used.add(path.split('.').pop());
  }
}

const results = [];
for (const f of args.files) {
  results.push({ file: f, ...transformFile(f, args.ns, catalogZh, catalogEn, usedKeys) });
}

// Write catalogs as flat then we nest? Wave2 used flat/nested mix. Use flat keys at top level.
function unflatten(flat) {
  // Keep flat top-level keys (no dots from our generator)
  const out = {};
  for (const [k, v] of Object.entries(flat)) {
    if (k.includes('.')) {
      const parts = k.split('.');
      let cur = out;
      for (let i = 0; i < parts.length - 1; i++) {
        cur[parts[i]] = cur[parts[i]] || {};
        cur = cur[parts[i]];
      }
      cur[parts[parts.length - 1]] = v;
    } else out[k] = v;
  }
  return out;
}

if (!argsDry) {
  for (const loc of ['zh-CN', 'en-US']) {
    const p = join(FRONTEND, 'messages', loc, `${args.ns}.json`);
    const data = loc === 'zh-CN' ? catalogZh : catalogEn;
    // merge with existing file nested structure
    let existing = {};
    if (existsSync(p)) existing = JSON.parse(readFileSync(p, 'utf8'));
    const merged = { ...existing, ...unflatten(data) };
    writeFileSync(p, JSON.stringify(merged, null, 2) + '\n');
  }
}

console.log(JSON.stringify({ ns: args.ns, results, keyCount: Object.keys(catalogZh).length }, null, 2));
