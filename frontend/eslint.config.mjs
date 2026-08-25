// @ts-check
import nextCoreWebVitals from 'eslint-config-next/core-web-vitals';
import nextTypescript from 'eslint-config-next/typescript';

// eslint-config-next 16 ships native flat configs (CJS default export).
const asArray = (mod) => {
  const value = mod?.default ?? mod;
  return Array.isArray(value) ? value : [value];
};

const eslintConfig = [
  ...asArray(nextCoreWebVitals),
  ...asArray(nextTypescript),
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
