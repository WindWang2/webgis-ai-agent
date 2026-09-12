/**
 * scan-lib.mjs — 裸 CJK 扫描器（no-raw-cjk 守卫的实现核心，ADR-0144 / P2）。
 *
 * 职责：对给定目录集合做轻量词法扫描，产出两类「裸 CJK」发现：
 *   1. JSX 位置（isJsxPosition(kind) === true）：
 *      - jsx-text ：JSX 子文本节点（`>中文<`）。被 `{表达式}` 切开的文本段
 *        各自计一条（`产物（{n}）` → `产物（` 与 `）` 两条，全角括号本身
 *        属于中文文案，用户看得见）。
 *      - jsx-attr ：JSX 属性的字符串字面量（`aria-label="清空搜索"`）。
 *   2. obj:<key> ：对象字面量「文案键」的字符串值（`label: '等待中'`），
 *      无论出现在模块级词典、JSX 子表达式还是属性表达式里。
 *
 * v1 明确不守卫（见白名单 _meta.note 与后续扩面计划）：
 *   - 注释（行/块，含 JSX 属性位与花括号注释）
 *   - 表达式内的字符串/模板字面量（`{cond ? 'A' : 'B'}`、toast 消息、
 *     canvas 文案等非 JSX 直出文案）
 *   - lib/** 的 obj:*（测试侧按路径豁免）
 *
 * 实现：单遍状态机（代码 / 行注释 / 块注释 / 字符串 / 模板串 / JSX 标签 /
 * JSX 文本），无第三方依赖，行号 1-based，路径为相对 root 的 posix 风格。
 *
 * CLI（自检用）：node scripts/i18n/scan-lib.mjs [root] [dirs...] [--json]
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, sep } from 'node:path';

/**
 * CJK 判定：CJK 统一表意文字（含扩展 A / 兼容区）+ 全角标点。
 * 全角括号、顿号、句读等（\u3000-\u303f、\uff00-\uffef）是中文文案不可分割的
 * 部分 —— 例如 `产物（{n}）` 在表达式后残留的 `）` 同样会渲染给用户。
 */
const CJK_CHAR = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]/;

/** 扫描的源码扩展名 */
const EXTS = new Set(['.ts', '.tsx', '.js', '.jsx']);
/** 跳过的目录与文件（测试/声明/产物不属产品代码面） */
const SKIP_DIRS = new Set(['node_modules', '.next', 'dist', 'build', 'coverage', '.git']);
const SKIP_FILE = /(\.test\.|\.spec\.|\.stories\.)|\.(d)\.ts$/;

/**
 * 「文案键」白名单：对象字面量中这些键名的字符串值按 UI 文案对待。
 * 枚举/语义类键（type/status/kind/id/color/tone/stage...）是分类标记而非
 * 面向用户的文案，不在此列。
 */
const COPY_KEYS = new Set([
  'label', 'title', 'description', 'placeholder', 'text', 'message',
  'content', 'caption', 'hint', 'tooltip',
]);

/** JSX 位置三类 kind */
export function isJsxPosition(kind) {
  return kind === 'jsx-text' || kind === 'jsx-attr' || kind === 'jsx-expr';
}

// ---------------------------------------------------------------------------
// 词法扫描
// ---------------------------------------------------------------------------

/**
 * 单文件扫描，产出候选发现（含上下文标记，供归类）。
 * @returns {Array<{line:number, cls:string, text:string, ctx:string, key:string}>}
 *   cls ∈ jsx-text | jsx-attr | expr-str | expr-tpl | obj
 */
export function scanCandidates(src) {
  const out = [];
  const n = src.length;
  let i = 0;
  let line = 1;

  // 帧类型：code(root/expr/template) / tag / text
  const stack = [{ type: 'code', parent: 'root' }];

  const hasCJK = (s) => CJK_CHAR.test(s);

  // 代码中 `<` 是否开启 JSX：取决于前一个有效 token（泛型/比较号不开启）
  const JSX_PREV_KEYWORDS = new Set([
    'return', 'do', 'else', 'new', 'typeof', 'void', 'delete', 'instanceof',
    'in', 'of', 'case', 'yield', 'await', 'default', 'throw',
  ]);
  const jsxStartAllowed = () => {
    let j = i - 1;
    while (j >= 0 && /\s/.test(src[j])) j--;
    if (j < 0) return true;
    // 前一个词是 JS 关键字（return <div>…）
    let k = j;
    while (k >= 0 && /\w/.test(src[k])) k--;
    const word = src.slice(k + 1, j + 1);
    if (word && JSX_PREV_KEYWORDS.has(word)) return true;
    return !/[)\]}\w"'\`]/.test(src[j]); // 标识符/字面量/闭括号后是比较或泛型
  };

  while (i < n) {
    const frame = stack[stack.length - 1];

    // ---------------- JSX 文本节点 ----------------
    if (frame.type === 'text') {
      const start = i;
      let sawCJK = false;
      let cjkLine = line;
      while (i < n) {
        const ch = src[i];
        if (ch === '\n') { line++; i++; continue; }
        if (ch === '<' || ch === '{') break;
        if (!sawCJK && CJK_CHAR.test(ch)) { sawCJK = true; cjkLine = line; }
        i++;
      }
      if (sawCJK) {
        out.push({
          line: cjkLine, cls: 'jsx-text',
          text: src.slice(start, i).trim().slice(0, 120),
          ctx: 'children', key: '',
        });
      }
      if (i >= n) break;
      if (src[i] === '{') {
        i++;
        stack.push({ type: 'code', parent: 'expr', inAttr: false, start: i, strCount: 0 });
      } else if (src[i] === '<') {
        if (src[i + 1] === '/') {
          i += 2;
          while (i < n && src[i] !== '>') { if (src[i] === '\n') line++; i++; }
          i++; // '>'
          stack.pop(); // text
          stack.pop(); // host tag
        } else {
          i++;
          stack.push({ type: 'tag' });
        }
      }
      continue;
    }

    // ---------------- JSX 标签内部 ----------------
    if (frame.type === 'tag') {
      const ch = src[i];
      if (ch === '\n') { line++; i++; continue; }
      if (ch === ' ' || ch === '\t' || ch === '\r') { i++; continue; }
      if (src.startsWith('//', i)) { while (i < n && src[i] !== '\n') i++; continue; }
      if (src.startsWith('/*', i)) {
        i += 2;
        while (i < n && !src.startsWith('*/', i)) { if (src[i] === '\n') line++; i++; }
        i += 2;
        continue;
      }
      if (ch === '>') {
        i++;
        frame.selfClosed = false;
        stack.push({ type: 'text' });
        continue;
      }
      if (ch === '/' && src[i + 1] === '>') { i += 2; stack.pop(); continue; }
      if (ch === '{') { // 属性展开 {...expr}
        i++;
        stack.push({ type: 'code', parent: 'expr', inAttr: true, start: i, strCount: 0 });
        continue;
      }
      // 泛型/类型参数误判（如 <T,>）：放弃标签解释，退回外层
      if (ch === ',' || ch === '(' || ch === ')' || ch === ';') { stack.pop(); continue; }
      if (/[\w.:$-]/.test(ch)) {
        const nameStart = i;
        while (i < n && /[\w.:$-]/.test(src[i])) i++;
        const name = src.slice(nameStart, i);
        let j = i;
        while (j < n && src[j] !== '\n' && /\s/.test(src[j])) j++;
        if (src[j] === '=') {
          j++;
          while (j < n && src[j] !== '\n' && /\s/.test(src[j])) j++;
          const vq = src[j];
          if (vq === '"' || vq === "'") {
            i = j + 1;
            const valStart = i;
            const valLine = line;
            let saw = false;
            while (i < n && src[i] !== vq) {
              if (src[i] === '\\') { i += 2; continue; }
              if (src[i] === '\n') { line++; i++; continue; }
              if (!saw && CJK_CHAR.test(src[i])) { saw = true; }
              i++;
            }
            const val = src.slice(valStart, i);
            if (saw) out.push({ line: valLine, cls: 'jsx-attr', text: `${name}=${val}`.slice(0, 140), ctx: 'attr', key: name });
            i++; // 收尾引号
          } else if (vq === '{') {
            i = j + 1;
            stack.push({ type: 'code', parent: 'expr', inAttr: true, start: i, strCount: 0 });
          } else {
            i = j; // 裸布尔属性
          }
        }
        continue;
      }
      i++;
      continue;
    }

    // ---------------- 模板字符串静态段 ----------------
    if (frame.type === 'template') {
      const segStart = i;
      const segLine = line;
      let saw = false;
      while (i < n) {
        if (src[i] === '\\') { i += 2; continue; }
        if (src[i] === '`') break;
        if (src.startsWith('${', i)) break;
        if (src[i] === '\n') line++;
        if (!saw && CJK_CHAR.test(src[i])) saw = true;
        i++;
      }
      const val = src.slice(segStart, i);
      if (saw && CJK_CHAR.test(val) && frame.ctx !== 'code') {
        out.push({ line: segLine, cls: 'expr-tpl', text: val.trim().slice(0, 120), ctx: frame.ctx, key: '' });
      }
      if (i >= n) break;
      if (src[i] === '`') { i++; stack.pop(); continue; }
      // '${'：插值表达式
      i += 2;
      stack.push({ type: 'code', parent: 'template', inAttr: frame.ctx === 'attr', start: i, strCount: 0 });
      continue;
    }

    // ---------------- 代码 ----------------
    const ch = src[i];

    if (src.startsWith('//', i)) { while (i < n && src[i] !== '\n') i++; continue; }
    if (src.startsWith('/*', i)) {
      i += 2;
      while (i < n && !src.startsWith('*/', i)) { if (src[i] === '\n') line++; i++; }
      i += 2;
      continue;
    }

    // 字符串
    if (ch === '"' || ch === "'") {
      const q = ch;
      const openIdx = i;
      const strLine = line;
      i++;
      const valStart = i;
      while (i < n && src[i] !== q) {
        if (src[i] === '\\') { i += 2; continue; }
        if (src[i] === '\n') break; // 未闭合容错（行号由主循环处理）
        i++;
      }
      const val = src.slice(valStart, i);
      if (i < n && src[i] === q) i++;
      recordString(frame, val, strLine, 'str', openIdx);
      continue;
    }

    // 模板字符串
    if (ch === '`') {
      const below = frame;
      stack.push({ type: 'template', ctx: below.parent === 'expr' ? (below.inAttr ? 'attr' : 'children') : 'code' });
      i++;
      continue;
    }

    // 正则字面量（启发式：前一有效字符非标识符/字面量/闭括号时按正则读）
    if (ch === '/' && jsxStartAllowed()) {
      let j = i + 1;
      let inClass = false;
      let ok = false;
      while (j < n && src[j] !== '\n') {
        if (src[j] === '\\') { j += 2; continue; }
        if (inClass) { if (src[j] === ']') inClass = false; }
        else if (src[j] === '[') inClass = true;
        else if (src[j] === '/') { ok = true; break; }
        j++;
      }
      if (ok) {
        i = j + 1;
        while (i < n && /[gimsuyvd]/.test(src[i])) i++;
        continue;
      }
    }

    // JSX 开启
    if (ch === '<' && jsxStartAllowed()) {
      const nxt = src[i + 1];
      if (/[A-Za-z>]/.test(nxt)) {
        i++;
        stack.push({ type: 'tag' });
        continue;
      }
    }

    // 花括号配平（expr/template 帧边界）
    if (ch === '{' && frame.parent !== 'root') { frame.depth = (frame.depth ?? 0) + 1; i++; continue; }
    if (ch === '}' && frame.parent !== 'root') {
      if ((frame.depth ?? 0) > 0) { frame.depth--; i++; continue; }
      stack.pop(); // 表达式/模板插值结束
      i++;
      continue;
    }

    if (ch === '\n') line++;
    i++;
  }

  /** 字符串候选归类：obj 文案键（任意上下文）/ JSX 表达式内字符串 */
  function recordString(frame, val, strLine, kind, openIdx) {
    if (!hasCJK(val)) return;
    // property-value 位置（key: 'value'）的文案键，任意上下文都算（模块级
    // 词典、JSX 表达式内的内联数组 alike）
    const key = objKeyBefore(src, openIdx);
    if (key !== null) {
      const ctx = frame.parent === 'root' ? 'code' : frame.inAttr ? 'attr' : 'children';
      out.push({ line: strLine, cls: 'obj', text: val.trim().slice(0, 120), ctx, key });
    }
    if (frame.parent === 'expr') {
      frame.strCount++;
      out.push({
        line: strLine, cls: 'expr-str',
        text: val.trim().slice(0, 120),
        ctx: frame.inAttr ? 'attr' : 'children', key: '',
      });
    }
  }

  return out;
}

/**
 * 回看 src[openIdx]（字符串开引号）之前的 property 键：`ident:` 形态，且 ident
 * 之前是 `{ , ( [`（property 位置）→ 返回键名；否则 null。
 * 三元 `cond ? a : '中文'` 的冒号前有 `?`，不会误判。
 */
function objKeyBefore(src, openIdx) {
  let j = openIdx - 1;
  while (j >= 0 && /\s/.test(src[j])) j--;
  if (src[j] !== ':') return null;
  j--;
  while (j >= 0 && /\s/.test(src[j])) j--;
  const e = j;
  while (j >= 0 && /[\w$]/.test(src[j])) j--;
  if (e === j) return null;
  const name = src.slice(j + 1, e + 1);
  if (/^\d/.test(name)) return null;
  while (j >= 0 && /\s/.test(src[j])) j--;
  if (j >= 0 && !'[{,('.includes(src[j])) return null;
  return name;
}

// ---------------------------------------------------------------------------
// 目录遍历与发现归类
// ---------------------------------------------------------------------------

function listFiles(root, rel, acc) {
  const abs = join(root, rel);
  let st;
  try { st = statSync(abs); } catch { return acc; }
  if (st.isDirectory()) {
    let entries;
    try { entries = readdirSync(abs); } catch { return acc; }
    for (const name of entries.sort()) {
      if (SKIP_DIRS.has(name)) continue;
      listFiles(root, rel ? `${rel}/${name}` : name, acc);
    }
  } else if (st.isFile()) {
    const dot = rel.lastIndexOf('.');
    if (dot < 0 || !EXTS.has(rel.slice(dot))) return acc;
    if (SKIP_FILE.test(rel)) return acc;
    acc.push(rel);
  }
  return acc;
}

/**
 * 扫描 root 下指定目录集合，返回裸 CJK 发现。
 * @param {string} root 仓库前端根（如 frontend/）
 * @param {string[]} dirs 相对 root 的目录名（如 ['components','app','lib']）
 * @returns {Array<{file:string, line:number, kind:string, text:string}>}
 */
export function collectFindings(root, dirs) {
  const findings = [];
  const files = dirs.flatMap((d) => listFiles(root, d, []));
  for (const rel of files) {
    let src;
    try { src = readFileSync(join(root, rel), 'utf8'); } catch { continue; }
    for (const cand of scanCandidates(src)) {
      const kind = finalKind(cand);
      if (!kind) continue;
      findings.push({
        file: rel.split(sep).join('/'),
        line: cand.line,
        kind,
        text: cand.text,
      });
    }
  }
  findings.sort((a, b) =>
    a.file.localeCompare(b.file) || a.line - b.line || a.kind.localeCompare(b.kind) || a.text.localeCompare(b.text)
  );
  return findings;
}

/** 调试/校准用：返回未归类的原始候选流（含 cls/ctx/key） */
export function collectCandidates(root, dirs) {
  const cands = [];
  const files = dirs.flatMap((d) => listFiles(root, d, []));
  for (const rel of files) {
    let src;
    try { src = readFileSync(join(root, rel), 'utf8'); } catch { continue; }
    for (const cand of scanCandidates(src)) {
      cands.push({ file: rel.split(sep).join('/'), ...cand });
    }
  }
  cands.sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line);
  return cands;
}

/**
 * 候选 → 守卫 kind。v1 守卫范围（与白名单 _meta 一致）：
 *   - JSX 文本节点 / 引号属性值：始终守卫
 *   - 对象字面量文案键（label/title/description/...）：守卫（lib 由测试侧豁免）
 *   - 表达式内的字符串/模板字面量（expr-str / expr-tpl）：仅记录，不进入守卫
 *     （canvas 文案、toast 消息等非 JSX 直出文案，列入后续版本扩面）
 */
function finalKind(cand) {
  switch (cand.cls) {
    case 'jsx-text': return 'jsx-text';
    case 'jsx-attr': return 'jsx-attr';
    case 'obj': {
      if (!COPY_KEYS.has(cand.key)) return '';
      return `obj:${cand.key}`;
    }
    default: return ''; // expr-str / expr-tpl：v1 不守卫
  }
}

// ---------------------------------------------------------------------------
// CLI 自检入口
// ---------------------------------------------------------------------------

if (process.argv[1] && process.argv[1].endsWith('scan-lib.mjs')) {
  const asJson = process.argv.includes('--json');
  const args = process.argv.slice(2).filter((a) => a !== '--json');
  const root = args[0] ? join(process.cwd(), args[0]) : process.cwd();
  const dirs = args.length > 1 ? args.slice(1) : ['components', 'app', 'lib'];
  const found = collectFindings(root, dirs);
  if (asJson) {
    console.log(JSON.stringify(found, null, 2));
  } else {
    const byFile = new Map();
    for (const f of found) byFile.set(f.file, (byFile.get(f.file) ?? 0) + 1);
    for (const [file, count] of [...byFile].sort()) console.log(`${String(count).padStart(4)}  ${file}`);
    console.log(`total: ${found.length} findings in ${byFile.size} files`);
  }
}
