import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  // Geliştirme sunucusu: arayüz 5173'te, API 8000'de. `/api` istekleri
  // proxy'den geçince kod üretimdekiyle AYNI yolu kullanıyor (aynı kaynak),
  // yani "bende çalışıyordu" farkı oluşmuyor.
  server: {
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  plugins: [react()],
})
