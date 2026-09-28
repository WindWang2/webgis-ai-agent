/**
 * no-hardcoded-cjk 规则单测（G13 / issue #1436 防回归门禁）。
 *
 * 验证矩阵：
 *   正例（报错）：JSX 文本 / 引号属性 / 文案键对象值 / 白名单配额突破 / 干净文件零容忍
 *   反例（不报）：纯 ASCII / 注释（行、块、JSX 花括号）/ 正则字符类 /
 *                messages+scripts 范围外 / lib 文案键豁免 / JSX 表达式内字符串与
 *                属性位·文案键模板串（v1 边界，归 G02 扩面）/ 枚举/语义类键 /
 *                eslint-disable 行内豁免
 *
 * 用 Linter API 直跑（不经过子进程），配置形状与 eslint.config.mjs 的接线一致。
 */
import { describe, expect, it } from 'vitest';
import { resolve } from 'node:path';
import { Linter } from 'eslint';
import rule from '../../eslint-rules/no-hardcoded-cjk.mjs';

const linter = new Linter({ configType: 'flat' });

/**
 * 与 eslint.config.mjs 中门禁块同形的最小配置。
 * 不传 whitelistFiles 时规则走真实白名单加载路径（loadWhitelistFiles）。
 */
function gateConfig(whitelistFiles?: Record<string, number>) {
  return [
    {
      files: ['**/*.{js,jsx,ts,tsx}'],
      languageOptions: {
        ecmaVersion: 'latest',
        sourceType: 'module',
        parserOptions: { ecmaFeatures: { jsx: true } },
      },
      plugins: { local: { rules: { 'no-hardcoded-cjk': rule } } },
      rules: {
        'local/no-hardcoded-cjk': whitelistFiles
          ? ['error', { whitelistFiles }]
          : ['error'],
      },
    },
  ];
}

function cjkReports(code: string, filename: string, whitelistFiles?: Record<string, number>) {
  const messages = linter.verify(code, gateConfig(whitelistFiles), filename);
  return messages.filter((m) => m.ruleId === 'local/no-hardcoded-cjk');
}

function kindsOf(reports: ReturnType<typeof cjkReports>) {
  return reports.map((r) => (r.message.match(/\(([^)]+)\)/) ?? [])[1]);
}

describe('no-hardcoded-cjk 正例（裸 CJK 必须报错）', () => {
  it('JSX 文本节点报错', () => {
    const reports = cjkReports(
      "export default () => (\n  <div>\n    图层列表\n  </div>\n);\n",
      'components/foo/foo-panel.tsx',
    );
    expect(reports).toHaveLength(1);
    expect(kindsOf(reports)).toEqual(['jsx-text']);
  });

  it('引号 JSX 属性报错（字符串 props）', () => {
    const reports = cjkReports(
      "export default () => <input aria-label=\"清空搜索\" placeholder=\"键入过滤词\" />;\n",
      'components/foo/search-field.tsx',
    );
    expect(reports).toHaveLength(2);
    expect(kindsOf(reports)).toEqual(['jsx-attr', 'jsx-attr']);
  });

  it('对象文案键报错（label/title/... 值位）', () => {
    const reports = cjkReports(
      "export const statusMap = {\n  pending: { label: '等待中', description: '任务排队' },\n};\n",
      'components/foo/status.tsx',
    );
    expect(reports).toHaveLength(2);
    expect(kindsOf(reports)).toEqual(['obj', 'obj']);
  });

  it('干净文件零容忍：任意一条守卫类都报错', () => {
    const reports = cjkReports(
      "export default () => <span>加载完成</span>;\n",
      'app/geoai/clean-check.tsx',
      {},
    );
    expect(reports).toHaveLength(1);
  });

  it('白名单配额突破：超出部分报错（含绝对路径物理文件名）', () => {
    // 注意：vitest jsdom 下 new URL(x, import.meta.url).pathname 会解析到
    // http://localhost 基准（丢主机名），必须用 import.meta.dirname + resolve
    const absFile = resolve(import.meta.dirname, '../../components/foo/quota-check.tsx');
    const code =
      "export default () => <div>\n  <span>第一条</span>\n  <span>第二条</span>\n</div>;\n";
    const within = cjkReports(code, absFile, { 'components/foo/quota-check.tsx': 2 });
    expect(within).toHaveLength(0);
    const breach = cjkReports(code, absFile, { 'components/foo/quota-check.tsx': 1 });
    expect(breach).toHaveLength(1);
  });
});

describe('no-hardcoded-cjk 反例（不得误报）', () => {
  it('纯 ASCII 不报错', () => {
    const reports = cjkReports(
      "export const re = /^[a-z]+$/i;\nexport default () => <div className=\"ok\">Total 42 items</div>;\n",
      'components/foo/plain.tsx',
    );
    expect(reports).toHaveLength(0);
  });

  it('注释中的 CJK 不报错（行注释 / 块注释 / JSX 花括号注释）', () => {
    const reports = cjkReports(
      [
        "// 图层面板负责渲染图层列表",
        "/* 状态映射：待处理 / 进行中 / 已完成 */",
        "export default () => (",
        "  <div>",
        "    {/* 渲染中文注释不应报错 */}",
        "    <span aria-hidden>ok</span>",
        "  </div>",
        ");",
        "",
      ].join('\n'),
      'components/foo/commented.tsx',
    );
    expect(reports).toHaveLength(0);
  });

  it('正则字面量中的 CJK 字符类不报错', () => {
    const reports = cjkReports(
      "export const cjkRe = /[一-鿿\\u3000-\\u303f]+/g;\nexport const tagRe = /<[^>]*中文[^>]*>/;\n",
      'lib/foo/cjk-regex.ts',
    );
    expect(reports).toHaveLength(0);
  });

  it('lib/** 的对象文案键豁免（scan-lib v1 边界一致）', () => {
    const reports = cjkReports(
      "export const canvasCopy = { label: '比例尺', tooltip: '双击缩放' };\n",
      'lib/map-kit/copy.ts',
    );
    expect(reports).toHaveLength(0);
  });

  it('messages/**、scripts/** 不在门禁 files 范围（配置级第二道防线）', () => {
    const reports = cjkReports(
      "export default () => <div>词条目录内不属于门禁范围</div>;\n",
      'messages/should-not-be-linted.tsx',
    );
    expect(reports).toHaveLength(0);
    const scriptReports = cjkReports(
      "console.log('i18n 工具自身输出豁免');\n",
      'scripts/i18n/tool-output.mjs',
    );
    expect(scriptReports).toHaveLength(0);
  });

  it('JSX 表达式内字符串不报错（v1 边界，归 G02 扩面）', () => {
    const reports = cjkReports(
      "export default ({ busy }) => (\n  <span>{busy ? '加载中' : '加载完成'}</span>\n);\n",
      'components/foo/expr-boundary.tsx',
    );
    expect(reports).toHaveLength(0);
  });

  it('属性位/文案键的模板串不报错（v1 边界：表达式内字面量，归 G02 扩面）', () => {
    const attrTpl = cjkReports(
      "const n = 3;\nexport default () => <Btn label={`已选 ${n} 项`} />;\n",
      'components/foo/selection.tsx',
    );
    expect(attrTpl).toHaveLength(0);
    const objTpl = cjkReports(
      "export const hints = { tooltip: `双击缩放地图` };\n",
      'components/foo/hints.ts',
    );
    expect(objTpl).toHaveLength(0);
  });

  it('枚举/语义类键（type/status/id...）不视为文案，值可含 CJK 不报错', () => {
    const reports = cjkReports(
      "export const kinds = { type: '矢量图层', status: '已发布', id: 'lc-01' };\n",
      'components/foo/semantic-keys.ts',
    );
    expect(reports).toHaveLength(0);
  });

  it('行内 eslint-disable 提供个案豁免通道（JSX 内用花括号注释形态）', () => {
    const reports = cjkReports(
      [
        "export default () => (",
        "  <div>",
        "    {/* eslint-disable-next-line local/no-hardcoded-cjk -- 与后端协议回显串逐字对齐 */}",
        "    <div>协议原样回显文本</div>",
        "  </div>",
        ");",
        "",
      ].join('\n'),
      'components/foo/protocol-echo.tsx',
    );
    expect(reports).toHaveLength(0);
  });
});

describe('no-hardcoded-cjk 配额语义（与 vitest 守卫衔接）', () => {
  it('存量文件配额内不报错；新增即报（只减不增棘轮）', () => {
    const code =
      "export default () => <div aria-label=\"既有文案\">既有文本</div>;\n";
    const legacy = { 'components/foo/legacy.tsx': 2 };
    expect(cjkReports(code, 'components/foo/legacy.tsx', legacy)).toHaveLength(0);
    const grown =
      "export default () => (<><div aria-label=\"既有文案\">既有文本</div><p>新增一条</p></>);\n";
    expect(cjkReports(grown, 'components/foo/legacy.tsx', legacy)).toHaveLength(1);
  });

  it('真实白名单加载路径：存量配额文件单条不报错（同时守护白名单文件在位）', () => {
    // 不注入 options → 规则实读 test/i18n/no-raw-cjk.whitelist.json；
    // market-tab.tsx 配额 15，单条 jsx-text 在配额内。若白名单文件丢失或
    // 移位（规则回退到零容忍），本用例即红。
    const reports = cjkReports(
      "export default () => <span>白名单加载路径覆盖</span>;\n",
      'components/sidebar/market/market-tab.tsx',
    );
    expect(reports).toHaveLength(0);
  });
});
