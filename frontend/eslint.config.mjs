// @ts-check
import path from 'path';
import { fileURLToPath } from 'url';
import { FlatCompat } from '@eslint/eslintrc';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// eslint-config-next 14.2.35（与 Next.js 14.2.35 配套）仍是 legacy (eslintrc)
// 格式。用 FlatCompat 将其转换成 ESLint 9 flat config；extends 项与旧的
// .eslintrc.json 完全等价（next/core-web-vitals + next/typescript）。
const compat = new FlatCompat({ baseDirectory: __dirname });

const eslintConfig = [
  ...compat.extends('next/core-web-vitals', 'next/typescript'),
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
      // 与旧 .eslintrc.json 保持一致：未使用变量降为 warning（CI 用
      // --max-warnings 0 把关），显式 any 放行。
      // _ 前缀是项目既有约定（mock 构造参数、drain generator 等故意保留的
      // 绑定），沿用 typescript-eslint 的 _ 忽略模式。
      '@typescript-eslint/no-unused-vars': [
        'warn',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
        },
      ],
      '@typescript-eslint/no-explicit-any': 'off',
      // @next/eslint-plugin-next@14.2.35 的这两条规则调用 eslint 8 的
      // context.getAncestors()，在 ESLint 9 下已被移除 -> 直接抛
      // TypeError 中断整个 lint（上游 15.x 才修复）。二者均为 pages router
      // 专用（<head> 重复 / 自定义字体），本项目用 App Router，禁用是等价降级。
      '@next/next/no-duplicate-head': 'off',
      '@next/next/no-page-custom-font': 'off',
    },
  },
];

export default eslintConfig;
