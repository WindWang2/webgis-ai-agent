import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  transpilePackages: ["react-map-gl"],
  output: "standalone",
  experimental: {
    optimizePackageImports: ["lucide-react", "recharts", "framer-motion"],
  },
  // audit ISSUE-021（#1348）：此前应用层零安全响应头。
  // CSP 取值保守但有效：script 仅 self+inline（Next 引导脚本与 HMR 需要
  // unsafe-inline/eval）；img/connect 放开 http(s) —— 底图瓦片供应商
  // 由运行时配置决定不可穷举；object/embedding 与跨站 frame 一律封死。
  // #1635：本 CSP 是全栈唯一 CSP 来源 —— nginx（deploy/nginx/nginx.conf 与
  // docker-compose.prod.secure.yml 内联副本）不再注入 CSP/X-Frame-Options。
  // 多个 CSP 响应头会被浏览器全部强制执行（等效取交集），边缘再叠一份更严
  // 策略会把这里的放宽（connect-src data: 等）在生产栈完全中和，造成
  // dev 生效、生产被静默阻断的分叉。
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          {
            key: "Content-Security-Policy",
            value: [
              "default-src 'self'",
              "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
              "style-src 'self' 'unsafe-inline'",
              "img-src 'self' data: blob: http: https:",
              "connect-src 'self' data: http: https: ws: wss:",
              "worker-src 'self' blob:",
              "font-src 'self' data:",
              "object-src 'none'",
              "base-uri 'self'",
              "frame-ancestors 'none'",
              "form-action 'self'",
            ].join("; "),
          },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Strict-Transport-Security",
            value: "max-age=31536000; includeSubDomains",
          },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=(self)",
          },
        ],
      },
    ]
  },
}

export default nextConfig
