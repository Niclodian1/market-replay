import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        gray: {
          950: '#09090e',
        },
      },
      fontFamily: {
        sans: ['var(--font-syne)', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'ui-monospace', 'monospace'],
      },
      boxShadow: {
        'glow-blue': '0 0 20px rgba(59,130,246,0.12), 0 0 0 1px rgba(59,130,246,0.15)',
        'glow-green': '0 0 20px rgba(34,197,94,0.1), 0 0 0 1px rgba(34,197,94,0.12)',
        'glow-sm': '0 0 10px rgba(59,130,246,0.08)',
      },
    },
  },
  plugins: [],
}

export default config
