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
        // Cyberpunk colors palette
        cyber: {
          black: "#0a0a0f",
          cyan: "#00f0ff",
          purple: "#8b5cf6",
          pink: "#ff00aa",
          dark: "#1a1a2e",
          surface: "rgba(30, 30, 40, 0.8)",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
        display: ["Orbitron", "Rajdhani", "sans-serif"],
      },
      boxShadow: {
        glow: "0 0 20px rgba(0, 240, 255, 0.5)",
        "glow-purple": "0 0 20px rgba(139, 92, 246, 0.5)",
        "glow-pink": "0 0 20px rgba(255, 0, 170, 0.5)",
        "glow-sm": "0 0 10px rgba(0, 240, 255, 0.3)",
      },
      animation: {
        "pulse-glow": "pulse-glow 2s ease-in-out infinite",
        "glow-border": "glow-border 3s ease-in-out infinite",
      },
      keyframes: {
        "pulse-glow": {
          "0%, 50%": { boxShadow: "0 0 20px rgba(0, 240, 255, 0.3)" },
          "100%": { boxShadow: "0 0 30px rgba(0, 240, 255, 0.6)" },
        },
        "glow-border": {
          "0%, 50%": { borderColor: "rgba(0, 240, 255, 0.3)" },
          "100%": { borderColor: "rgba(0, 240, 255, 0.8)" },
        },
      },
      backgroundImage: {
        "grid-pattern": "linear-gradient(rgba(0, 240, 255, 0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(0, 240, 255, 0.03) 1px, transparent 1px)",
      },
    },
  },
  plugins: [typography],
};
export default config;
