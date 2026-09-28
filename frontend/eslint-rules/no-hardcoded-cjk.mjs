/**
 * no-hardcoded-cjk — ESLint CJK 门禁（issue #1436 防回归，G13）。
 *
 * 守卫语义与 test/i18n/no-raw-cjk.test.ts（scan-lib.mjs + 配额白名单）严格对齐，
 * 从「测试期守卫」升格为「lint 硬门禁」（pnpm run lint = --max-warnings 0）：
 *
 *   错误层（与 vitest 守卫 v1 同语义）：
 *     - jsx-text       JSX 文本节点（`>中文<`）
 *     - jsx-attr       JSX 属性的**引号**字符串字面量（`aria-label="清空搜索"`）
 *     - obj            对象字面量文案键的字符串值（`label: '加载中'`，lib/** 豁免）
 *
 *   配额棘轮（与白名单机制衔接）：
 *     - 白名单内存量文件：按源码位置序放行前 N 条（N = 白名单配额），超出即报；
 *       vitest 守卫的陈旧检查强制「配额 === 实际」，只能向下修剪。
 *     - 白名单外干净文件：零容忍，任何一条都报。
 *     - G02 键化归零删除白名单条目后，对应文件自动并入零容忍区。
 *
 *   v1 边界（不报错，与 scan-lib.mjs finalKind 一致，归 G02 后续扩面）：
 *     - JSX 表达式内部的字符串/模板串（`{cond ? '加载中' : '...'}`、
 *       属性位模板串、toast 消息、canvas 文案等 538 处存量，见 G02 扩面计划）
 *     - lib/** 的 obj 文案键与非文案字符串
 *
 *   豁免（结构性，无需规则参与）：
 *     - 注释：AST 天然不含注释节点
 *     - 正则字面量：Literal 的 value 非 string，天然跳过
 *     - messages/**、scripts/**：门禁 files 范围只含 components/app/lib
 *     - 个案硬编码：行内 `// eslint-disable-next-line local/no-hardcoded-cjk` + 理由
 *
 * 配额来源：test/i18n/no-raw-cjk.whitelist.json（单一事实来源，不新增配置面）。
 *
 * 对齐口径：与 scan-lib.mjs 是**实证对齐**（当前树 35 文件 209 条逐文件相等、
 * 0 错位），非逐构造等价——本规则在 computed/引号键、类字段文案键、unicode
 * 转义串等 parser 级场景是 scan-lib 的严格超集（更严方向，新代码早期可见）；
 * 已知反向缺口仅剩语句块+标号语句等构造性 case，由 vitest 守卫兜底。
 */
import { readFileSync } from 'node:fs';
import { dirname, isAbsolute, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

/** CJK 判定：与 scan-lib.mjs 的 CJK_CHAR 完全一致（表意文字 + 扩展 A + 兼容区 + 全角标点） */
const CJK_CHAR = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]/;

/** 「文案键」：与 scan-lib.mjs 的 COPY_KEYS 一致（枚举/语义类键不在此列） */
const COPY_KEYS = new Set([
  'label', 'title', 'description', 'placeholder', 'text', 'message',
  'content', 'caption', 'hint', 'tooltip',
]);

/** 规则文件位于 <frontend>/eslint-rules/，仓库前端根即其上级目录 */
const FRONTEND_ROOT = dirname(dirname(fileURLToPath(import.meta.url)));

const WHITELIST_PATH = join(FRONTEND_ROOT, 'test', 'i18n', 'no-raw-cjk.whitelist.json');

/** 白名单文件 → 允许残留配额（进程内缓存） */
let whitelistCache;
function loadWhitelistFiles() {
  whitelistCache ??= (() => {
    try {
      return JSON.parse(readFileSync(WHITELIST_PATH, 'utf8')).files ?? {};
    } catch {
      // 白名单缺失时按「全部干净文件」处理（零容忍）；白名单随仓库提交，正常不存在该分支
      return {};
    }
  })();
  return whitelistCache;
}

function keyNameOf(propertyParent) {
  const key = propertyParent.key;
  if (!key) return null;
  if (key.type === 'Identifier') return key.name;
  if (key.type === 'Literal') return String(key.value);
  return null;
}

/** 文案键值位的字符串字面量：`label: '加载中'` / 类字段 `label = '加载中'` / 接口同形 */
function isObjCopyKeyValue(parent, node) {
  if (!parent) return false;
  const isPropertyLike =
    parent.type === 'Property' ||
    parent.type === 'PropertyDefinition' ||
    parent.type === 'PropertySignature';
  if (!isPropertyLike) return false;
  // 必须处于 value 位（key 是另一个节点），且键名在文案键白名单内
  if (parent.value !== node) return false;
  const name = keyNameOf(parent);
  return name !== null && COPY_KEYS.has(name);
}

function posixRel(absolutePath) {
  const rel = isAbsolute(absolutePath) ? relative(FRONTEND_ROOT, absolutePath) : absolutePath;
  return rel.split(sep).join('/');
}

const rule = {
  meta: {
    type: 'problem',
    docs: {
      description:
        'Disallow hardcoded CJK text in user-visible string positions (i18n regression gate, issue #1436)',
    },
    schema: [
      {
        type: 'object',
        properties: {
          // 测试注入用：与 no-raw-cjk.whitelist.json 的 files 段同形
          whitelistFiles: { type: 'object' },
        },
        additionalProperties: false,
      },
    ],
    messages: {
      hardcodedCjk:
        'Hardcoded CJK text ({{kind}}) is user-visible copy: route it through next-intl (messages/en-US|zh-CN + useTranslations), see issue #1436. If it must stay, add `// eslint-disable-next-line local/no-hardcoded-cjk` with a justification.',
    },
  },

  create(context) {
    const physicalPath = context.physicalPath ?? context.filename;
    const rel = posixRel(physicalPath);

    // 门禁语义只对 components/app/lib 内的文件生效（messages/scripts 不在
    // eslint.config.mjs 的 files 范围内，这里是规则级的第二道防线）
    if (!/^(components|app|lib)\//.test(rel)) return {};

    const options = context.options?.[0] ?? {};
    const whitelistFiles = options.whitelistFiles ?? loadWhitelistFiles();
    const quota = whitelistFiles[rel];
    const isLegacyFile = typeof quota === 'number' && quota >= 0;
    // lib/** 的 obj 文案键在 vitest 守卫侧豁免（scan-lib v1 边界），保持一致
    const objGuarded = !rel.startsWith('lib/');

    const findings = [];

    const report = (finding) => {
      context.report({
        node: finding.node,
        messageId: 'hardcodedCjk',
        data: { kind: finding.kind },
      });
    };

    return {
      JSXText(node) {
        if (CJK_CHAR.test(node.value)) findings.push({ node, kind: 'jsx-text' });
      },

      Literal(node) {
        if (typeof node.value !== 'string' || !CJK_CHAR.test(node.value)) return;
        const parent = node.parent;
        if (parent?.type === 'JSXAttribute' && parent.value === node) {
          findings.push({ node, kind: 'jsx-attr' });
          return;
        }
        if (objGuarded && isObjCopyKeyValue(parent, node)) {
          findings.push({ node, kind: 'obj' });
        }
        // 其余（表达式内字符串、lib 文案键、模块级字典等）：v1 边界，只记录不报错
      },

      'Program:exit'() {
        if (isLegacyFile) {
          // 存量文件：按白名单配额放行前 N 条（按源码位置序），超出即报
          const ordered = [...findings].sort((a, b) => a.node.range[0] - b.node.range[0]);
          for (const finding of ordered.slice(quota)) report(finding);
        } else {
          // 干净文件：零容忍
          for (const finding of findings) report(finding);
        }
      },
    };
  },
};

export default rule;
