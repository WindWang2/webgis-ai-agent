import type { Config } from "tailwindcss";
import { dirname } from "path";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  transpilePackages: ["react-map-gl"],
};
export default config;
