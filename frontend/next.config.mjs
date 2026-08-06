import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  transpilePackages: ["react-map-gl", "maplibre-gl"],
  output: "standalone",
  eslint: {
    // next 14 的内置 lint（next lint / next build 内部）依赖 eslint 8 的
    // useEslintrc 选项，在 ESLint 9 下会抛错。lint 已迁移到独立 flat config
    // （eslint.config.mjs）+ CI 的 ESLint gate，构建阶段不再重复跑 lint。
    ignoreDuringBuilds: true,
  },
  experimental: {
    optimizePackageImports: ["lucide-react", "recharts", "framer-motion", "@dnd-kit/core"],
  },
}

export default nextConfig