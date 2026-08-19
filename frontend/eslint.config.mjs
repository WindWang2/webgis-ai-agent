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
];

export default eslintConfig;
