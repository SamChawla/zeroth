// Shared design tokens for every page.
//
// Every colour resolves to a CSS variable rather than a literal, so light and
// dark are one attribute flip on <html> and no markup carries a theme. The
// variables hold "R G B" triplets rather than hex so Tailwind's opacity
// modifiers (bg-accent/10) keep working.
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`;

tailwind.config = {
  theme: {
    extend: {
      colors: {
        bg: token("bg"),
        surface: token("surface"),
        surface2: token("surface2"),
        fg: token("fg"),
        fg2: token("fg2"),
        fg3: token("fg3"),
        edge: token("edge"),
        accent: token("accent"),
        "accent-hover": token("accent-hover"),
        success: token("success"),
        warning: token("warning"),
        danger: token("danger"),
        code: token("code"),
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      borderRadius: {
        // Per the design direction: buttons tightest, inputs mid, cards loosest.
        btn: "10px",
        input: "12px",
        card: "16px",
      },
      maxWidth: {
        content: "1180px",
      },
      transitionDuration: {
        DEFAULT: "180ms",
      },
      keyframes: {
        "fade-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "none" },
        },
      },
      animation: {
        "fade-up": "fade-up 220ms ease-out both",
      },
    },
  },
};
