import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  build: {
    chunkSizeWarningLimit: 900,
    rolldownOptions: {
      output: {
        // Split the heavy framework/UI/chart libraries away from the app entry.
        // This keeps the dashboard shell cacheable and prevents ECharts or
        // Element Plus growth from silently inflating the first application chunk.
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('echarts') || id.includes('vue-echarts')) return 'charts'
          if (id.includes('element-plus') || id.includes('@element-plus/icons-vue')) return 'element-plus'
          if (id.includes('dompurify') || id.includes('marked')) return 'markdown'
          if (id.includes('vue')) return 'vue-vendor'
          return 'vendor'
        },
      },
    },
  },
  server: {
    // Keep the development server on the IPv4 loopback address.
    // Windows can resolve "localhost" to either ::1 or 127.0.0.1 depending on
    // the caller. Binding explicitly avoids the common case where Vite listens
    // only on [::1]:3060 while Chromium tries 127.0.0.1:3060 and gets
    // ERR_CONNECTION_REFUSED.
    host: '127.0.0.1',
    port: 3060,
    proxy: {
      '/api': {
        // Match the documented FastAPI bind address. Using "localhost" here can
        // hit the same IPv6/IPv4 ambiguity as the frontend dev server.
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
