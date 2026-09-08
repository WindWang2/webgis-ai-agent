// @ts-check
import nextCoreWebVitals from 'eslint-config-next/core-web-vitals';
import nextTypescript from 'eslint-config-next/typescript';
import jsxA11y from 'eslint-plugin-jsx-a11y';

// eslint-config-next 16 ships native flat configs (CJS default export).
const asArray = (mod) => {
  const value = mod?.default ?? mod;
  return Array.isArray(value) ? value : [value];
};

const eslintConfig = [
  ...asArray(nextCoreWebVitals),
  ...asArray(nextTypescript),
  // Wave 11（audit 07 P1）：jsx-a11y 静态防护 —— 此前零 a11y lint 规则，
  // 键盘/aria 回归只能靠人工。eslint-config-next 已注册 jsx-a11y 插件，
  // 这里只引入 recommended 的**规则集**（重注册插件会 flat config 冲突）；
  // warn 落地（存量少量违例待清），新增违例在 review 中可见。
  {
    rules: {
      ...(jsxA11y.flatConfigs?.recommended?.rules ?? {}),
      // 存量基线：交互判定类规则在 Canvas 密集 UI 上误报率高，显式降噪。
      'jsx-a11y/no-noninteractive-element-interactions': 'off',
      'jsx-a11y/no-static-element-interactions': 'off',
      'jsx-a11y/click-events-have-key-events': 'off',
      // APG 组合组件模式冲突：本仓 tablist（nav-rail / panel-dock）按 WAI-APG
      // 把 roving tabindex 放在子 tab 上（tablist 本体不是 tab stop）；
      // FloatingChrome 标题条 tabIndex=0 是键盘拖拽把手的刻意停靠点。
      'jsx-a11y/interactive-supports-focus': 'off',
      'jsx-a11y/no-noninteractive-tabindex': 'off',
      // recovery-actions 的 radio 标签文本在三层嵌套 span 里（默认 depth=2
      // 看不到）—— 提升扫描深度，而不是给正确标记的 label 打补丁。
      'jsx-a11y/label-has-associated-control': ['error', { depth: 4 }],
    },
  },
  {
    ignores: [
      '.next/**',
      'out/**',
      'build/**',
      'node_modules/**',
      'coverage/**',
      'next-env.d.ts',
      'test-results.junit.xml',
    ],
  },
  {
    rules: {
      '@typescript-eslint/no-unused-vars': [
        'warn',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
        },
      ],
      '@typescript-eslint/no-explicit-any': 'off',
      // react-hooks 7 adds compiler-style rules (refs / set-state-in-effect /
      // purity / immutability). They fire 54 errors on existing render-time
      // ref reads and effect setState. Next 16 + config-next 16 land first;
      // those rules stay off until a dedicated sweep.
      'react-hooks/refs': 'off',
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/purity': 'off',
      'react-hooks/immutability': 'off',
    },
  },
  {
    // #1008 防回归：已完成 devOnly 清理的生产边界文件锁定 no-console——
    // 新增裸 console.*（绕过 lib/utils/logger 的 devOnly 门禁）直接报错。
    // 仅覆盖已清理文件（全仓仍有少量待清点，见 issue 跟踪；logger.ts 是
    // wrapper 本身、mapspec-compiler/cli.ts 等是合法使用 console 的 CLI）。
    files: [
      'components/map/map-components/index.ts',
      'components/map/map-panel.tsx',
      'lib/map-kit/render-debouncer.ts',
      'lib/mapspec-runtime/runtime.ts',
    ],
    rules: {
      'no-console': 'error',
    },
  },
];

export default eslintConfig;
