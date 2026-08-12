import type { Config } from "tailwindcss";
import typography from "@tailwindcss/typography";

const config: Config = {
  // Without this, Tailwind v3 defaults to `darkMode: 'media'` and every `dark:`
  // variant tracks the OS preference instead of the `dark` class that
  // app/page.tsx puts on <html>. The V4 audit found 124 such variants across 13
  // files, all of them inert under the in-app theme toggle.
  darkMode: 'class',
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        /* ── Visual System V4 semantic tokens ──
           Prefer these over raw palette classes: `bg-surface-panel` survives a
           theme flip, `bg-slate-50` does not. The values live in
           app/globals.css (`:root` and `.dark`). */
        surface: {
          canvas: "var(--surface-canvas)",
          panel: "var(--surface-panel)",
          raised: "var(--surface-raised)",
          overlay: "var(--surface-overlay)",
          sunken: "var(--surface-sunken)",
          hover: "var(--surface-hover)",
          selected: "var(--surface-selected)",
          scrim: "var(--surface-scrim)",
        },
        edge: {
          subtle: "var(--border-subtle)",
          DEFAULT: "var(--border-default)",
          strong: "var(--border-strong)",
        },
        ink: {
          DEFAULT: "var(--text-primary)",
          secondary: "var(--text-secondary)",
          muted: "var(--text-muted)",
          disabled: "var(--text-disabled)",
          "on-accent": "var(--text-on-accent)",
        },
        status: {
          accent: "var(--accent)",
          "accent-vivid": "var(--accent-vivid)",
          "accent-soft": "var(--accent-soft)",
          "accent-border": "var(--accent-border)",
          success: "var(--success)",
          "success-soft": "var(--success-soft)",
          "success-border": "var(--success-border)",
          info: "var(--info)",
          "info-soft": "var(--info-soft)",
          "info-border": "var(--info-border)",
          warning: "var(--warning)",
          "warning-soft": "var(--warning-soft)",
          "warning-border": "var(--warning-border)",
          critical: "var(--critical)",
          "critical-soft": "var(--critical-soft)",
          "critical-border": "var(--critical-border)",
          neutral: "var(--neutral)",
          "neutral-soft": "var(--neutral-soft)",
          "neutral-border": "var(--neutral-border)",
        },
        "map-chrome": {
          DEFAULT: "var(--map-chrome-bg)",
          border: "var(--map-chrome-border)",
          ink: "var(--map-chrome-text)",
          "ink-muted": "var(--map-chrome-text-muted)",
        },

        /* ── Pre-V4 aliases, kept so existing call sites keep compiling.
              They now resolve through the semantic tokens above. ── */
        "agent-bg": "var(--surface-canvas)",
        "agent-panel": "var(--surface-panel)",
        "agent-panel2": "var(--surface-raised)",
        "agent-glass": "var(--surface-overlay)",
        "agent-border": "var(--border-subtle)",
        "agent-border-mid": "var(--border-default)",

        /* Text */
        "agent-tp": "var(--text-primary)",
        "agent-ts": "var(--text-secondary)",
        "agent-tm": "var(--text-muted)",

        /* Accent */
        "agent-accent": "var(--agent-accent)",
        "agent-accent-dim": "var(--accent-soft)",
        "agent-accent-brd": "var(--accent-border)",

        /* Semantic */
        "agent-blue": "var(--info)",
        "agent-orange": "var(--warning)",
        "agent-red": "var(--critical)",

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
      /* Dense workbench type scale. The audit counted 309 raw `text-[Npx]`
         across 12 distinct values; these six steps replace them. */
      fontSize: {
        micro: ["var(--font-micro)", { lineHeight: "var(--leading-tight)" }],
        caption: ["var(--font-caption)", { lineHeight: "var(--leading-tight)" }],
        meta: ["var(--font-meta)", { lineHeight: "var(--leading-normal)" }],
        body: ["var(--font-body)", { lineHeight: "var(--leading-normal)" }],
        title: ["var(--font-title)", { lineHeight: "var(--leading-tight)" }],
        heading: ["var(--font-heading)", { lineHeight: "var(--leading-tight)" }],
      },
      /* Control/row heights and shell metrics, so `h-control-md` and `w-rail`
         replace the scattered h-6/h-7/h-8 and the hardcoded 48/42/330. */
      spacing: {
        "control-sm": "var(--control-sm)",
        "control-md": "var(--control-md)",
        "control-lg": "var(--control-lg)",
        "row-sm": "var(--row-sm)",
        "row-md": "var(--row-md)",
        "row-lg": "var(--row-lg)",
        "icon-sm": "var(--icon-sm)",
        "icon-md": "var(--icon-md)",
        "icon-lg": "var(--icon-lg)",
        panel: "var(--panel-pad)",
        topbar: "var(--topH)",
        statusbar: "var(--stH)",
        rail: "var(--railW)",
        sidebar: "var(--sw)",
      },
      borderRadius: {
        xs: "var(--radius-xs)",
        sm: "var(--radius-sm)",
        md: "var(--radius-md)",
        lg: "var(--radius-lg)",
        xl: "var(--radius-xl)",
        pill: "var(--radius-pill)",
        chrome: "var(--map-chrome-radius)",
      },
      fontFamily: {
        sans: ["DM Sans", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
      },
      boxShadow: {
        raised: "var(--elevation-raised)",
        overlay: "var(--elevation-overlay)",
        drawer: "var(--elevation-drawer)",
        chrome: "var(--map-chrome-shadow)",
        /* Pre-V4 names, re-pointed at the elevation scale. */
        "agent-sm": "var(--elevation-raised)",
        "agent-md": "var(--elevation-overlay)",
        "agent-lg": "var(--elevation-drawer)",
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
