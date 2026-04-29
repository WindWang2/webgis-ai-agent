import type { Config } from "tailwindcss";
import typography from "@tailwindcss/typography";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        /* ── All is Agent — Light Glass Palette ── */
        "agent-bg": "#dce8f2",
        "agent-panel": "rgba(252, 253, 254, 0.88)",
        "agent-panel2": "rgba(248, 250, 252, 0.72)",
        "agent-glass": "rgba(255, 255, 255, 0.70)",
        "agent-border": "rgba(15, 23, 42, 0.08)",
        "agent-border-mid": "rgba(15, 23, 42, 0.12)",

        /* Text */
        "agent-tp": "#0f172a",
        "agent-ts": "#475569",
        "agent-tm": "#94a3b8",

        /* Accent */
        "agent-accent": "#16a34a",
        "agent-accent-dim": "rgba(22, 163, 74, 0.08)",
        "agent-accent-brd": "rgba(22, 163, 74, 0.22)",
        "agent-accent-text": "#15803d",

        /* Semantic */
        "agent-blue": "#2563eb",
        "agent-orange": "#ea580c",
        "agent-red": "#dc2626",

        /* Legacy compat (CSS vars for shadcn) */
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
        sans: ["DM Sans", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
      },
      boxShadow: {
        "agent-sm":
          "0 1px 4px rgba(15,23,42,0.06), 0 0 0 1px rgba(15,23,42,0.04)",
        "agent-md":
          "0 4px 24px rgba(15,23,42,0.09), 0 1px 4px rgba(15,23,42,0.05)",
        "agent-lg":
          "0 8px 40px rgba(15,23,42,0.12), 0 2px 8px rgba(15,23,42,0.06)",
      },
      animation: {
        "hb-scan": "hbScan 2.2s ease-in-out infinite",
        "ring-pulse": "ringPulse 2.5s ease-out infinite",
        "ring-pulse-delay": "ringPulse 2.5s ease-out 0.8s infinite",
        "ring-pulse-delay2": "ringPulse 2.5s ease-out 1.6s infinite",
        "fade-up": "fadeUp 0.2s ease both",
        spulse: "spulse 1.6s ease-in-out infinite",
        "dot-1": "dotPulse 1.3s infinite 0s",
        "dot-2": "dotPulse 1.3s infinite 0.18s",
        "dot-3": "dotPulse 1.3s infinite 0.36s",
        "sidebar-in": "sidebarIn 0.22s cubic-bezier(0.4, 0, 0.2, 1)",
        "sidebar-out": "sidebarOut 0.22s cubic-bezier(0.4, 0, 0.2, 1)",
        "slide-from-right":
          "slideFromRight 0.22s cubic-bezier(0.4, 0, 0.2, 1)",
      },
      keyframes: {
        hbScan: {
          "0%": { left: "-30%", opacity: "0" },
          "20%": { opacity: "0.7" },
          "80%": { opacity: "0.7" },
          "100%": { left: "110%", opacity: "0" },
        },
        ringPulse: {
          "0%": {
            transform: "translate(-50%, -50%) scale(0.6)",
            opacity: "0.5",
          },
          "100%": {
            transform: "translate(-50%, -50%) scale(2.2)",
            opacity: "0",
          },
        },
        fadeUp: {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        spulse: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.3" },
        },
        dotPulse: {
          "0%, 80%, 100%": { opacity: "0.2", transform: "scale(0.75)" },
          "40%": { opacity: "1", transform: "scale(1)" },
        },
        sidebarIn: {
          from: { transform: "translateX(-100%)" },
          to: { transform: "translateX(0)" },
        },
        sidebarOut: {
          from: { transform: "translateX(0)" },
          to: { transform: "translateX(-100%)" },
        },
        slideFromRight: {
          from: { transform: "translateX(100%)" },
          to: { transform: "translateX(0)" },
        },
      },
      backgroundImage: {
        "grid-agent":
          "linear-gradient(rgba(15,23,42,0.04) 1px, transparent 1px), linear-gradient(90deg, rgba(15,23,42,0.04) 1px, transparent 1px)",
      },
    },
  },
  plugins: [typography],
};
export default config;
