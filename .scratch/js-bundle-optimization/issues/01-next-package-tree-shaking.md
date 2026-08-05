# 01 — Next.js 模块级树摇（Tree-Shaking）优化

**What to build:** 在 `frontend/next.config.mjs` 中配置 `experimental.optimizePackageImports`，实现对重型依赖包 (`lucide-react`, `recharts`, `framer-motion`, `@dnd-kit/core`) 的按需函数级打包，削减 80 KB 静态代码包。

**Blocked by:** None — can start immediately

**Status:** done — verified 2026-08-05 (code + tests)

- [x] frontend/next.config.mjs 中配置 optimizePackageImports 白名单
- [x] npm run build 编译成功且无配置警告
