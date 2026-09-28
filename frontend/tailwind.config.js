/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          dark: '#0B132B',
          panel: '#1C2541',
          card: '#151E38',
          border: '#334155',
          cyan: '#00F2FE',
          blue: '#38BDF8',
          accent: '#00C6FF'
        }
      }
    },
  },
  plugins: [],
}
