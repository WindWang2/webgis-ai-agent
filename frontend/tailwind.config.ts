import type { Config } from "tailwindcss";
import typography from "@tailwindcss/typography";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        /* ── Deep Space HUD Palette ── */
        "ds-black": "#0a0a0b",
        "ds-surface": "rgba(14, 14, 16, 0.72)",
        "ds-glass": "rgba(255, 255, 255, 0.04)",

        /* Action colors */
        "hud-cyan": "#00f2ff",
        "hud-green": "#00ff41",
        "hud-orange": "#ff5f00",
        "hud-red": "#ff2d55",

        /* Neutral tints */
        "hud-muted": "rgba(255, 255, 255, 0.45)",
        "hud-dim": "rgba(255, 255, 255, 0.12)",
        "hud-border": "rgba(255, 255, 255, 0.08)",

        /* Legacy compat (CSS vars) */
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
        display: ["Orbitron", "Rajdhani", "sans-serif"],
      },
      boxShadow: {
        "glow-cyan": "0 0 20px rgba(0, 242, 255, 0.35), 0 0 60px rgba(0, 242, 255, 0.10)",
        "glow-green": "0 0 16px rgba(0, 255, 65, 0.30)",
        "glow-orange": "0 0 16px rgba(255, 95, 0, 0.30)",
        "glow-sm": "0 0 8px rgba(0, 242, 255, 0.20)",
        "hud": "0 8px 32px rgba(0, 0, 0, 0.60), inset 0 1px 0 rgba(255, 255, 255, 0.05)",
      },
      backdropBlur: {
        hud: "20px",
      },
      animation: {
        "pulse-glow": "pulse-glow 2.5s ease-in-out infinite",
        "scan-line": "scan-line 3s linear infinite",
        "fade-in": "fade-in 0.4s ease-out",
        "slide-up": "slide-up 0.5s cubic-bezier(0.16, 1, 0.3, 1)",
        "slide-right": "slide-right 0.5s cubic-bezier(0.16, 1, 0.3, 1)",
        "radar": "radar 2s ease-out",
      },
      keyframes: {
        "pulse-glow": {
          "0%, 100%": { boxShadow: "0 0 12px rgba(0, 242, 255, 0.15)" },
          "50%": { boxShadow: "0 0 28px rgba(0, 242, 255, 0.45)" },
        },
        "scan-line": {
          "0%": { transform: "translateY(-100%)", opacity: "0.6" },
          "100%": { transform: "translateY(100vh)", opacity: "0" },
        },
        "fade-in": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "slide-up": {
          "0%": { opacity: "0", transform: "translateY(24px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "slide-right": {
          "0%": { opacity: "0", transform: "translateX(-24px)" },
          "100%": { opacity: "1", transform: "translateX(0)" },
        },
        "radar": {
          "0%": { opacity: "0", transform: "scale(0.3)" },
          "50%": { opacity: "0.8" },
          "100%": { opacity: "1", transform: "scale(1)" },
        },
      },
      backgroundImage: {
        "grid-hud":
          "linear-gradient(rgba(0, 242, 255, 0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(0, 242, 255, 0.03) 1px, transparent 1px)",
      },
    },
  },
  plugins: [typography],
};
export default config;
