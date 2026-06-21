/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui"],
        mono: ["JetBrains Mono", "ui-monospace", "Menlo"],
      },
      colors: {
        ink: {
          50: "#f6f7f9",
          100: "#e9ecf1",
          200: "#cdd3de",
          300: "#a3acbb",
          400: "#6b7484",
          500: "#4b5360",
          600: "#383e49",
          700: "#2a2f37",
          800: "#1d2128",
          900: "#10131a",
        },
        accent: {
          400: "#8ab4ff",
          500: "#5b8dff",
          600: "#3b6ee0",
        },
      },
    },
  },
  plugins: [],
};
