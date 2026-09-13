/**
 * svg2pdf.js ESM 子路径的显式类型声明（V11 W6.2，ADR-0166）。
 *
 * 包 main 是 UMD（peer 模式读全局 jsPDF，Vite/ESM 下 import 即炸）；
 * 运行时显式走 `dist/svg2pdf.es.js`（package.json module 字段同款构建），
 * 但包类型只声明在主入口 —— 子路径在 Next 构建的 tsc 检查下需本声明
 * （类型从主入口重导出，单一真相）。
 */
declare module 'svg2pdf.js/dist/svg2pdf.es.js' {
  export * from 'svg2pdf.js';
  export { default } from 'svg2pdf.js';
}
