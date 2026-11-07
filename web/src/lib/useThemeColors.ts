import * as React from "react";

const VARS = ["bg", "surface", "surface-2", "line", "text", "text-2", "accent", "success", "warning", "critical", "info"] as const;
export type ThemeColors = Record<(typeof VARS)[number], string>;

function read(): ThemeColors {
  const cs = getComputedStyle(document.documentElement);
  const out = {} as ThemeColors;
  for (const v of VARS) {
    const raw = cs.getPropertyValue(`--${v}`).trim();
    out[v] = raw ? `rgb(${raw})` : "currentColor";
  }
  return out;
}

/** Resolved theme colors for SVG/canvas consumers (recharts) that cannot use CSS `var()` inside `rgb()` attributes. */
export function useThemeColors(): ThemeColors {
  const [colors, setColors] = React.useState<ThemeColors>(() => read());
  React.useEffect(() => {
    const obs = new MutationObserver(() => setColors(read()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);
  return colors;
}
