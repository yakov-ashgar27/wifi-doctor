/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"Cascadia Code"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      keyframes: {
        flow: { to: { backgroundPosition: '24px 0' } },
        'pulse-ring': {
          '0%': { boxShadow: '0 0 0 0 var(--ring-color)' },
          '70%': { boxShadow: '0 0 0 10px transparent' },
          '100%': { boxShadow: '0 0 0 0 transparent' },
        },
      },
      animation: {
        flow: 'flow 0.8s linear infinite',
        'flow-slow': 'flow 2.4s linear infinite',
        'pulse-ring': 'pulse-ring 1.8s ease-out infinite',
      },
    },
  },
  plugins: [],
};
