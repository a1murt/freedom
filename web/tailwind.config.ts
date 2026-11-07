import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

const c = (v: string) => `rgb(var(${v}) / <alpha-value>)`;

export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: c("--bg"),
        surface: c("--surface"),
        "surface-2": c("--surface-2"),
        line: c("--line"),
        text: c("--text"),
        "text-2": c("--text-2"),
        accent: c("--accent"),
        "accent-hover": c("--accent-hover"),
        "accent-subtle": c("--accent-subtle"),
        selection: c("--selection"),
        "accent-fg": c("--accent-fg"),
        green: {
          50: "var(--green-50)",
          100: "var(--green-100)",
          200: "var(--green-200)",
          300: "var(--green-300)",
          400: "var(--green-400)",
          500: "var(--green-500)",
          600: "var(--green-600)",
          700: "var(--green-700)",
          800: "var(--green-800)",
          900: "var(--green-900)",
        },
        deep: c("--deep"),
        tint: c("--tint"),
        success: c("--success"),
        warning: c("--warning"),
        critical: c("--critical"),
        info: c("--info"),
      },
      fontFamily: {
        sans: ['-apple-system', '"SF Pro Text"', '"SF Pro Display"', "system-ui", "sans-serif"],
        display: ['-apple-system', '"SF Pro Display"', '"SF Pro Text"', "system-ui", "sans-serif"],
        mono: ['"SF Mono"', "ui-monospace", "Menlo", "monospace"],
      },
      borderRadius: { sm: "6px", DEFAULT: "6px", md: "10px", lg: "14px" },
      fontSize: {
        display: ["40px", { lineHeight: "44px", fontWeight: "600" }],
        h1: ["32px", { lineHeight: "36px", fontWeight: "600" }],
        h2: ["24px", { lineHeight: "28px", fontWeight: "600" }],
        h3: ["18px", { lineHeight: "24px", fontWeight: "600" }],
        body: ["15px", { lineHeight: "22px" }],
        caption: ["13px", { lineHeight: "18px" }],
        label: ["12px", { lineHeight: "16px", fontWeight: "500", letterSpacing: "0.04em" }],
        mono: ["13px", { lineHeight: "18px" }],
      },
      boxShadow: {
        float: "0 12px 40px -12px rgb(0 0 0 / 0.45), 0 0 0 1px rgb(var(--line))",
        glow: "0 0 120px -20px rgb(var(--accent) / 0.35)",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0", transform: "translateY(2px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        pulseSoft: { "0%,100%": { opacity: "1" }, "50%": { opacity: "0.55" } },
      },
      animation: { "fade-in": "fade-in 160ms ease-out", "pulse-soft": "pulseSoft 1.6s ease-in-out infinite" },
    },
  },
  plugins: [animate],
} satisfies Config;
