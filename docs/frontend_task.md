# 前端可视化平台 — 执行任务清单

## Phase 1: 后端 API
- [x] 创建 `src/new_energy_sys/api/` 目录结构
- [x] 实现 FastAPI 入口 (`main.py`) + CORS
- [x] 实现数据加载器 (`data_loader.py`) — CSV/JSON/Parquet 读取 + 缓存
- [x] 实现只读 API 路由 (`routes.py`) — 模型指标、预测、调度、治理、敏感性
- [x] 实现认证模块 (`auth.py`) — JWT 登录
- [x] 实现 CLI 触发端点 (`tasks.py`) — 异步调用训练/调度仿真

## Phase 2: 前端脚手架
- [x] Vite + Vue 3 项目初始化
- [x] 设计系统 CSS (variables + global + glass)
- [x] ECharts 暗色主题注册
- [x] Vue Router 路由配置
- [x] Axios API 封装 + 拦截器
- [x] 侧边导航 + 主布局 App.vue

## Phase 3: 登录页
- [x] Login.vue — 深色粒子背景 + 玻璃态登录卡

## Phase 4: 系统总览大屏
- [x] OverviewDashboard.vue — KPI + 站点信息 + 预测曲线 + 技术路线

## Phase 5: 预测模型对比
- [x] ModelComparison.vue — 排行榜 + 雷达图 + 柱状图

## Phase 6: 储能调度 + 策略治理
- [x] DispatchSimulation.vue — 策略卡片 + 评分对比 + 雷达图
- [x] GovernanceAnalysis.vue — Pareto散点 + 热力图 + 配置表

## Phase 7: 数据 + 报告
- [x] DataExplorer.vue — 数据质量 + 特征重要性 + 任务触发器
- [x] ReportViewer.vue — Markdown 报告浏览器

## Phase 8: 集成验证
- [x] API 全端点验证（config/models/governance/sensitivity/features/reports 全部返回数据）
- [x] 浏览器端到端测试（登录 → 总览 → 模型对比 → 调度仿真）
## Phase 9: 阶段评估与生产化整改路线

### 当前完成度
- [x] `frontend/` 新增 Vue 3 + Vite 前端工程，包含登录、总览、模型对比、调度仿真、策略治理、数据探索、报告浏览 7 个主要视图。
- [x] 前端生产构建验证通过：`npm run build` 成功生成 `frontend/dist/`。
- [x] 后端 API 契约抽检通过：登录、配置、模型指标、预测、治理、敏感性、特征、数据质量、任务、报告列表等 12 个关键接口返回 `200`。
- [x] 阶段定位明确：当前版本满足“桌面端演示 Demo”要求，但暂不满足“生产可交付”要求。

### 主要风险
- [ ] 部署配置：`frontend/src/utils/api.js` 默认指向 `http://localhost:8000`，会绕过 Vite `/api` 代理，生产部署后容易访问用户本机 localhost。
- [ ] 安全：后端 CORS 当前放开 `*`，JWT secret 存在默认值，演示账号密码写在源码和登录页中。
- [ ] XSS：`ReportViewer.vue` 使用 `marked()` + `v-html` 直接渲染 Markdown，缺少 HTML sanitize。
- [ ] 响应式：多个页面仍采用桌面固定栅格，缺少移动端/窄屏断点。
- [ ] 错误态：多数接口失败只 `console.error`，缺少页面级错误提示、空状态和重试入口。
- [ ] 测试：`package.json` 仅有 `dev/build/preview`，缺少 lint、单元测试、E2E 或接口契约测试脚本。

### 下一阶段推荐路线：B. 生产化整改
- [ ] 修部署：统一 API baseURL 策略，开发环境走 `/api` 代理，生产环境使用同源 `/api` 或显式 `VITE_API_BASE`，并补充 `.env.example`。
- [ ] 修安全：强制生产环境配置 `NES_JWT_SECRET`，收敛 CORS 白名单，移除登录页明文账号提示，区分 demo 与 production 凭据策略。
- [ ] 修 XSS：为 Markdown 渲染增加 HTML 清洗，默认禁止危险标签/属性，并增加报告渲染安全测试。
- [ ] 修响应式：为 App 外壳、KPI 栅格、图表行、报告页和登录页补齐 tablet/mobile 断点。
- [ ] 修错误态：统一 Axios 错误处理，页面增加 loading/error/empty/retry 状态，不再只依赖控制台。
- [ ] 补测试：增加 lint、生产构建、API smoke、关键页面 E2E 和 Markdown XSS 回归测试。

Pitfall: 不要在继续堆新图表前跳过该阶段；部署、安全和 XSS 问题一旦进入答辩或生产演示环境，会比功能缺口更容易造成阻塞。
