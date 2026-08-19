import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  transpilePackages: ["react-map-gl", "maplibre-gl"],
  output: "standalone",
  experimental: {
    optimizePackageImports: ["lucide-react", "recharts", "framer-motion", "@dnd-kit/core"],
  },
}

export default nextConfig