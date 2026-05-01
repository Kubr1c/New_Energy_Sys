import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import {
  Coin,
  DataAnalysis,
  DataLine,
  Document,
  Expand,
  Fold,
  Histogram,
  Loading,
  Lock,
  Setting,
  Sunny,
  Sunrise,
  SwitchButton,
  TrendCharts,
  User,
  WarningFilled,
} from '@element-plus/icons-vue'
import './styles/global.css'
import './utils/echarts-theme.js'

const app = createApp(App)

const icons = {
  Coin,
  DataAnalysis,
  DataLine,
  Document,
  Expand,
  Fold,
  Histogram,
  Loading,
  Lock,
  Setting,
  Sunny,
  Sunrise,
  SwitchButton,
  TrendCharts,
  User,
  WarningFilled,
}

// Keep global icon registration explicit. The navigation and KPI metadata still
// resolve icons by name, but the bundle no longer imports every Element Plus icon.
for (const [name, component] of Object.entries(icons)) {
  app.component(name, component)
}

app.use(router)
app.use(ElementPlus, { size: 'default' })
app.mount('#app')
