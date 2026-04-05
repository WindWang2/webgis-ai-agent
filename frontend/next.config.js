/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  swcMinify: true,
  transpilePackages: ["react-map-gl", "maplibre-gl"],
}

export default nextConfig
