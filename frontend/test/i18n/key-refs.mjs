/**
 * key-refs.mjs — 「代码引用键 ⊆ catalog」契约测试的扫描核心（#1748）。
 *
 * 背景：translator.ts 的 onError 对 MISSING_MESSAGE 静默回落（缺键渲染成
 * 键路径本身），所以「代码里 t('x.y') 引用的键是否真的存在于 catalog」
 * 只能靠测试兜底。此前没有任何测试做这件事 —— 删掉一个 catalog 条目只会
 * 让界面多出一行 `chat.foo.bar`，CI 全绿（translator.ts:27 声称由
 * key-completeness.test.ts 兜住，实际那个文件只比对 catalog 内部一致性）。
 *
 * 扫描面：components/app/lib 的 .ts/.tsx（与 scripts/i18n/scan-lib.mjs 同款
 * SKIP_FILE：测试/声明/story 不属产品代码面）。
 *
 * 每个 `t('literal')` 调用点分三类：
 *   resolved   —— translator 绑定可静态定位（`import { t } from '@/lib/i18n/t'`
 *                 含 `as tNow` 别名 / `const t = useT('ns')` / `useT()` /
 *                 `const { t } = useI18n()`），键解析为全键，交给调用方对照
 *                 catalog 校验；
 *   unresolved —— 调用点无法静态判定：translator 作为参数传给模块级 helper
 *                 （`function label(t: TranslateFn)`，namespace 不在同一作用域），
 *                 或 `useT(动态ns)`。数量由测试侧按文件配额棘轮锁定，
 *                 盲区不许静默扩大；
 *   missed     —— 模板串/变量键（`t(\`ns.${x}\`)`）天然不可静态判定，
 *                 本扫描器不计数；基线见 test/i18n/key-completeness.test.ts
 *                 的盲区说明（2026-10-20：42 处 / 25 文件）。
 *
 * 实现：单遍词法（注释抹空格、保留行号）+ 正则定位绑定与调用点，无第三方
 * 依赖；行号 1-based，路径为相对 root 的 posix 风格。
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';

/** 扫描的源码扩展名 */
const EXTS = /\.(tsx?|jsx?)$/;
/** 跳过的目录（与 scripts/i18n/scan-lib.mjs 一致） */
const SKIP_DIRS = new Set(['node_modules', '.next', 'dist', 'build', 'coverage', '.git']);
/** 测试/声明/story 不属产品代码面（同 scan-lib.mjs 的 SKIP_FILE） */
const SKIP_FILE = /(\.test\.|\.spec\.|\.stories\.)|\.d\.ts$/;

/** i18n 入口模块（按 import 来源识别 translator 绑定，兼容 @/ 与相对路径） */
const ENTRY_T = /(^|\/)lib\/i18n\/t$/;
const ENTRY_USE_T = /(^|\/)lib\/i18n\/useT$/;
const ENTRY_USE_I18N = /(^|\/)lib\/i18n\/i18n-provider$/;

/**
 * 注释抹成空格（保留换行以维持行号）：字符串/模板串内容原样保留，
 * 因此 `t('a.b')` 字面量仍可被后续正则捕获，而注释里的示例写法不会
 * 污染结果（把注释算成引用会造出永不存在的键）。
 */
function stripComments(src) {
  let out = '';
  let i = 0;
  const n = src.length;
  let state = 'code';
  while (i < n) {
    const c = src[i];
    const c2 = src[i + 1];
    if (state === 'code') {
      if (c === '/' && c2 === '/') {
        state = 'line';
        out += '  ';
        i += 2;
        continue;
      }
      if (c === '/' && c2 === '*') {
        state = 'block';
        out += '  ';
        i += 2;
        continue;
      }
      if (c === "'" || c === '"' || c === '`') {
        state = c;
        out += c;
        i += 1;
        continue;
      }
      out += c;
      i += 1;
      continue;
    }
    if (state === 'line') {
      if (c === '\n') {
        state = 'code';
        out += '\n';
      } else {
        out += ' ';
      }
      i += 1;
      continue;
    }
    if (state === 'block') {
      if (c === '*' && c2 === '/') {
        state = 'code';
        out += '  ';
        i += 2;
        continue;
      }
      out += c === '\n' ? '\n' : ' ';
      i += 1;
      continue;
    }
    // 字符串 / 模板串：内容原样透传，只找结束引号（转义跳过）
    out += c;
    if (c === '\\') {
      out += src[i + 1] ?? '';
      i += 2;
      continue;
    }
    if (c === state) state = 'code';
    i += 1;
  }
  return out;
}

/** `import { a as b, c }` 的命名导入 → [{ imported, local }]；解析不了的条目丢弃。 */
function namedImports(clause) {
  return clause
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
    .map((spec) => {
      const m = spec.match(/^([A-Za-z_$][\w$]*)(?:\s+as\s+([A-Za-z_$][\w$]*))?$/);
      return m ? { imported: m[1], local: m[2] ?? m[1] } : null;
    })
    .filter(Boolean);
}

/** 一个 translator 绑定：本地名 + namespace（null = 全键，'dynamic' = 不可静态判定）。 */
function binding(name, namespace, at) {
  return { name, namespace, at };
}

/**
 * 收集文件内所有 translator 绑定（位置用于把调用点归到最近的前置绑定，
 * 这样同一文件里多个组件各用各的 namespace 也不会串）。
 */
function collectBindings(src) {
  const bindings = [];
  let useTLocal = null;

  for (const m of src.matchAll(/import\s*\{([^}]*)\}\s*from\s*['"]([^'"]+)['"]/g)) {
    const names = namedImports(m[1]);
    const from = m[2];
    if (ENTRY_T.test(from)) {
      for (const n of names) {
        if (n.imported === 't') bindings.push(binding(n.local, null, m.index));
      }
    }
    if (ENTRY_USE_T.test(from)) {
      for (const n of names) {
        if (n.imported === 'useT') useTLocal = n.local;
      }
    }
    if (ENTRY_USE_I18N.test(from)) {
      for (const n of names) {
        if (n.imported !== 'useI18n') continue;
        for (const d of src.matchAll(/\b(?:const|let)\s*\{([^}]*)\}\s*=\s*useI18n\s*\(\s*\)/g)) {
          for (const dn of namedImports(d[1])) {
            if (dn.imported === 't') bindings.push(binding(dn.local, null, d.index));
          }
        }
      }
    }
  }

  if (useTLocal !== null) {
    const useTRe = new RegExp(
      '\\b(?:const|let)\\s+([A-Za-z_$][\\w$]*)\\s*=\\s*' + useTLocal + '\\s*\\(',
      'g',
    );
    for (const m of src.matchAll(useTRe)) {
      const rest = src.slice(m.index + m[0].length);
      // useT() → 全键；useT('ns') → namespace 限定；其余（变量/模板串）→ dynamic
      if (/^\s*\)/.test(rest)) {
        bindings.push(binding(m[1], null, m.index));
        continue;
      }
      const lit = rest.match(/^\s*(['"])([^'"`]*)\1\s*\)/);
      bindings.push(binding(m[1], lit ? lit[2] : 'dynamic', m.index));
    }
  }

  return bindings;
}

function walk(dir, out) {
  for (const name of readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue;
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p, out);
    else if (EXTS.test(name) && !SKIP_FILE.test(name)) out.push(p);
  }
  return out;
}

/**
 * 扫描 root 下指定目录，产出 i18n 键引用。
 *
 * @param {string} root 前端根目录（含 components/app/lib 的那层）
 * @param {string[]} dirs 要扫描的目录名
 * @returns {{
 *   files: string[],
 *   refs: Array<{ file: string, line: number, dir: string, key: string, raw: string, namespace: string | null }>,
 *   unresolved: Array<{ file: string, line: number, key: string, reason: string }>,
 * }}
 */
export function collectKeyReferences(root, dirs = ['components', 'app', 'lib']) {
  const files = [];
  for (const d of dirs) walk(join(root, d), files);

  const refs = [];
  const unresolved = [];

  for (const file of files) {
    const rel = relative(root, file).split(sep).join('/');
    const src = stripComments(readFileSync(file, 'utf8'));

    // 行号表（失败信息要能点到行）
    const lineStarts = [0];
    for (let i = 0; i < src.length; i += 1) {
      if (src[i] === '\n') lineStarts.push(i + 1);
    }
    const lineAt = (index) => {
      let lo = 0;
      let hi = lineStarts.length - 1;
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (lineStarts[mid] <= index) lo = mid;
        else hi = mid - 1;
      }
      return lo + 1;
    };

    const bindings = collectBindings(src);
    if (bindings.length === 0) continue;

    // 调用点：NAME('literal') —— 前一个字符必须是边界（排除 x.t( 与
    // format( 之类以 t 结尾的标识符误命中）。
    const names = [...new Set(bindings.map((b) => b.name))];
    const callRe = new RegExp(
      '(?:^|[^A-Za-z0-9_$.])(' + names.join('|') + ')\\s*\\(\\s*([\'"])([^\'"`\\n]*)\\2',
      'g',
    );
    for (const m of src.matchAll(callRe)) {
      const name = m[1];
      const raw = m[3];
      const at = m.index + (m[0].startsWith(name) ? 0 : 1);
      let best = null;
      for (const b of bindings) {
        if (b.name !== name) continue;
        if (b.at <= at && (best === null || b.at > best.at)) best = b;
      }
      if (best === null) {
        unresolved.push({ file: rel, line: lineAt(at), key: raw, reason: 'unbound-t' });
        continue;
      }
      if (best.namespace === 'dynamic') {
        unresolved.push({ file: rel, line: lineAt(at), key: raw, reason: 'dynamic-namespace' });
        continue;
      }
      refs.push({
        file: rel,
        line: lineAt(at),
        dir: rel.split('/')[0],
        key: best.namespace === null ? raw : `${best.namespace}.${raw}`,
        raw,
        namespace: best.namespace,
      });
    }
  }

  return { files: files.map((f) => relative(root, f).split(sep).join('/')), refs, unresolved };
}
