/**
 * extract.mjs — #1436 裸 CJK 键化草稿工具（配合 scan-lib / no-raw-cjk）。
 *
 * 用法：
 *   node scripts/i18n/extract.mjs [--files a.tsx,b.tsx] [--ns project] [--json]
 *
 * 输出：每个守卫发现一条建议键（slug），以及按文件聚合的草稿 catalog（zh）。
 * 真正改代码仍按 wave 模式手改 / 脚本 apply；本工具负责盘点与键名草案。
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { collectFindings, isJsxPosition } from './scan-lib.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const FRONTEND = join(__dirname, '..', '..');

const CJK = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]/;

function slugify(text, used) {
  const base = text
    .replace(CJK, '')
    .replace(/[^a-zA-Z0-9]+/g, ' ')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 4)
    .map((w, i) => (i === 0 ? w.toLowerCase() : w[0].toUpperCase() + w.slice(1).toLowerCase()))
    .join('') || 'copy';
  let key = base.slice(0, 40);
  let n = 2;
  while (used.has(key)) {
    key = `${base.slice(0, 36)}${n++}`;
  }
  used.add(key);
  return key;
}

function parseArgs(argv) {
  const out = { files: null, ns: 'draft', json: false };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--json') out.json = true;
    else if (a === '--ns') out.ns = argv[++i];
    else if (a === '--files') out.files = new Set(argv[++i].split(',').map((s) => s.trim()).filter(Boolean));
    else if (a === '--help') out.help = true;
  }
  return out;
}

const args = parseArgs(process.argv);
if (args.help) {
  console.log('Usage: node scripts/i18n/extract.mjs [--files path,path] [--ns name] [--json]');
  process.exit(0);
}

const whitelist = JSON.parse(readFileSync(join(FRONTEND, 'test/i18n/no-raw-cjk.whitelist.json'), 'utf8'));
const all = collectFindings(FRONTEND, ['components', 'app', 'lib']);
const guarded = all.filter(
  (f) => isJsxPosition(f.kind) || (f.kind.startsWith('obj:') && !f.file.startsWith('lib/')),
);

const targets = args.files
  ? guarded.filter((f) => args.files.has(f.file))
  : guarded.filter((f) => whitelist.files[f.file] != null);

const byFile = new Map();
for (const f of targets) {
  const list = byFile.get(f.file) ?? [];
  list.push(f);
  byFile.set(f.file, list);
}

const used = new Set();
const catalog = {};
const plan = [];

for (const [file, list] of [...byFile.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
  const entries = [];
  for (const f of list) {
    const attr = f.kind === 'jsx-attr' && f.text.includes('=') ? f.text.split('=')[0] : null;
    const raw = f.kind === 'jsx-attr' && f.text.includes('=') ? f.text.slice(f.text.indexOf('=') + 1) : f.text;
    const key = slugify(raw, used);
    catalog[key] = raw.replace(/\s+/g, ' ').trim();
    entries.push({ line: f.line, kind: f.kind, attr, text: raw, key });
  }
  plan.push({
    file,
    whitelist: whitelist.files[file] ?? null,
    actual: list.length,
    entries,
  });
}

if (args.json) {
  console.log(JSON.stringify({ ns: args.ns, catalog, plan }, null, 2));
} else {
  console.log(`# extract draft ns=${args.ns} files=${plan.length} keys=${Object.keys(catalog).length}`);
  for (const p of plan) {
    console.log(`\n## ${p.file} (wl=${p.whitelist} actual=${p.actual})`);
    for (const e of p.entries) {
      console.log(`  L${e.line} [${e.kind}] ${e.key} ← ${JSON.stringify(e.text)}`);
    }
  }
  console.log('\n# catalog draft');
  console.log(JSON.stringify(catalog, null, 2));
}
