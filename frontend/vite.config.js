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
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
