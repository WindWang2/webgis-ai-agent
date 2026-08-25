/**
 * Vite/vitest 的 `?raw` 后缀导入（以字符串形式加载源码）在 TypeScript 侧
 * 没有内建类型 —— layer-style-panel.audit840.test.tsx 等用
 * `import('...?raw')` 做源码级契约断言时 tsc 报 TS2307。此声明补齐模块
 * 形状（frontend 双 tsconfig 门禁的既有缺口，master 上被增量缓存掩盖）。
 */
declare module '*?raw' {
  const content: string;
  export default content;
}
