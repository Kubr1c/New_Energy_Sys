# 前端生产化整改交接锚点

## 阶段结论

当前 `frontend/` 已完成可演示的 Vue 3 + Vite 可视化平台，覆盖登录、系统总览、模型对比、调度仿真、策略治理、数据探索和报告浏览。验证结果如下：

- 前端构建：`npm run build` 通过，成功生成 `frontend/dist/`。
- 后端契约：登录、配置、模型、预测、治理、敏感性、特征、数据质量、任务、报告列表等 12 个关键接口返回 `200`。
- 阶段定位：满足桌面端 Demo 和答辩演示的基础要求，不满足生产可交付要求。

```mermaid
flowchart TD
    A["当前前端 Demo"] --> B["构建通过"]
    A --> C["核心 API 可用"]
    A --> D["主要页面已覆盖"]
    B --> E["可演示"]
    C --> E
    D --> E
    E --> F{"是否可生产交付?"}
    F -->|否| G["B. 生产化整改"]
    G --> H["部署"]
    G --> I["安全"]
    G --> J["XSS"]
    G --> K["响应式"]
    G --> L["错误态"]
    G --> M["测试"]
```

## 路线选择

| 方案 | 内容 | 优点 | 缺点 | 推荐 |
|---|---|---|---|---|
| A. Demo 固化 | 只修启动说明、API 地址和少量文案 | 成本最低，适合短期展示 | 安全、XSS、测试债务仍在 | 不推荐 |
| B. 生产化整改 | 修部署、安全、XSS、响应式、错误态、测试 | 交付风险最低，后续扩展基础稳定 | 需要集中处理工程质量 | 推荐 |
| C. 继续堆功能 | 新增图表、筛选、导出、更多页面 | 视觉内容更丰富 | 会放大已有架构和安全问题 | 不推荐 |

Pitfall: 如果跳过 B 直接做 C，部署和安全问题会在演示环境、答辩环境或服务器部署时集中暴露，修复成本高于现在。

## B. 生产化整改详细计划

### 1. 部署配置

- 将 `frontend/src/utils/api.js` 的默认 `baseURL` 从 `http://localhost:8000` 调整为相对路径 `/api`。
- 保留 `VITE_API_BASE` 覆盖能力，但仅作为显式部署配置使用。
- 补充 `frontend/.env.example`，至少包含：
  - `VITE_API_BASE=/api`
  - `VITE_APP_ENV=development`
- 检查 `frontend/vite.config.js`，确保开发环境 `/api` 代理仍指向 `http://localhost:8000`。
- 验证两种模式：
  - 开发：`npm run dev` 访问 `http://localhost:3000`，请求走 Vite proxy。
  - 生产：FastAPI 挂载 `frontend/dist/`，浏览器请求同源 `/api`。

Pitfall: 不能让生产包继续内置 `http://localhost:8000`，否则部署到服务器后会访问终端用户本机。

### 2. 安全

- 后端 `src/new_energy_sys/api/auth.py`：
  - 生产环境必须设置 `NES_JWT_SECRET`，禁止继续使用默认 demo secret。
  - 将 demo 账号策略显式标记为开发/演示模式；生产模式下从环境变量或受控配置读取账号。
- 后端 `src/new_energy_sys/api/main.py`：
  - 将 CORS `allow_origins=["*"]` 改为从环境变量读取白名单，例如 `NES_CORS_ORIGINS`。
  - 生产环境禁止 `allow_credentials=True` 与通配 origin 同时存在。
- 前端 `Login.vue`：
  - 移除页面明文显示 `admin/admin123`、`guest/guest123`。
  - 登录失败时显示统一错误，不暴露后端内部细节。
- 文档补充 demo 与 production 启动差异。

Pitfall: 只改前端隐藏账号不等于安全；默认 secret、默认用户和宽 CORS 必须一起收敛。

### 3. Markdown XSS

- 为报告渲染引入 HTML 清洗库，推荐 `dompurify`。
- `ReportViewer.vue` 中将 `marked(mdContent)` 的结果先经过 sanitize，再传给 `v-html`。
- 默认禁止危险标签和属性：
  - 禁止 `script`、`iframe`、`object`、`embed`。
  - 禁止 `on*` 事件属性。
  - 限制 `href/src` 协议为安全协议。
- 增加最小回归用例，覆盖：
  - `<script>alert(1)</script>` 不执行且不渲染。
  - `<img src=x onerror=alert(1)>` 事件属性被移除。
  - 普通标题、表格、代码块、引用正常渲染。

Pitfall: `marked` 负责 Markdown 转 HTML，不负责安全清洗；只靠“报告来自本地文件”不能作为长期安全边界。

### 4. 响应式

- 为全局布局增加断点：
  - `>= 1200px`：保持当前桌面大屏布局。
  - `768px - 1199px`：侧边栏压缩，图表从两列变一列。
  - `< 768px`：侧边栏改为顶部/抽屉式导航，KPI 卡片单列或双列。
- 重点修复页面：
  - `App.vue`：侧边栏、Header、内容区间距。
  - `OverviewDashboard.vue`：KPI 四列、主图 + 侧栏布局。
  - `ModelComparison.vue` / `DispatchSimulation.vue` / `GovernanceAnalysis.vue`：图表两列布局。
  - `DataExplorer.vue`：质量卡片四列与任务卡片。
  - `ReportViewer.vue`：左侧阶段列表和报告正文。
  - `Login.vue`：登录卡片宽度、边距和小屏高度。
- 图表容器使用稳定高度和 `min-width: 0`，避免 ECharts 在 grid/flex 下溢出。

Pitfall: 只加 `overflow-x: auto` 是兜底，不是响应式；核心信息布局必须在窄屏下重新编排。

### 5. 错误态与用户反馈

- 在 `frontend/src/utils/api.js` 增加统一错误归一化函数，输出稳定结构：
  - `status`
  - `message`
  - `requestId` 可选
  - `isAuthError`
- 各页面补齐 4 种状态：
  - `loading`
  - `error`
  - `empty`
  - `ready`
- 页面级错误展示要求：
  - 数据加载失败显示可读原因和“重试”按钮。
  - 空数据明确说明当前模块没有可展示数据。
  - 任务提交失败显示权限或命令错误，不只写 `console.error`。
- 401 继续清理 token 并跳转登录页；403 显示权限不足，不应表现为通用失败。

Pitfall: 只在 Axios interceptor 中统一弹窗会导致页面状态混乱；页面仍需要持有自己的 loading/error/empty 状态。

### 6. 测试与验收

- `frontend/package.json` 增加脚本：
  - `lint`
  - `test`
  - `test:e2e`
  - `check`
- 推荐测试组合：
  - ESLint：基础静态检查。
  - Vitest：工具函数、Markdown sanitize、API 错误归一化。
  - Playwright：登录、路由跳转、关键图表可见、报告页渲染、移动端布局 smoke。
- 后端增加 API smoke 脚本或测试：
  - 登录成功。
  - 未授权访问返回 401。
  - 关键只读接口返回非空或合理空值。
  - guest 提交任务返回 403。
- 验收命令：
  - `npm run build`
  - `npm run check`
  - `$env:PYTHONPATH='src'; python -m py_compile src\new_energy_sys\api\main.py src\new_energy_sys\api\auth.py src\new_energy_sys\api\data_loader.py src\new_energy_sys\api\tasks.py`

Pitfall: 当前只有构建验证，无法覆盖 XSS、权限、移动端和接口错误；没有测试就不能判断整改是否回归。

## 交接验收标准

- 生产构建中不再出现硬编码 `http://localhost:8000`。
- 生产模式缺少 `NES_JWT_SECRET` 时后端启动失败或明确拒绝使用默认 secret。
- CORS origin 可配置，生产模式不使用 `*`。
- Markdown 报告渲染经过 sanitize，并有 XSS 回归测试。
- 主要页面在桌面、平板、手机宽度下无关键内容遮挡或横向溢出。
- API 失败时页面显示错误态和重试入口。
- `npm run build`、前端检查脚本、后端 API smoke 均通过。

Pitfall: 验收必须同时看代码、构建、运行态和浏览器截图；只看 `npm run build` 通过不足以判定生产化完成。

## Frontend-B 推进记录 - 2026-04-26

### 已完成

- 部署配置：
  - `frontend/src/utils/api.js` 默认 `baseURL` 已改为 `/api`，保留 `VITE_API_BASE` 显式覆盖能力。
  - 新增 `frontend/.env.example`，包含 `VITE_API_BASE=/api` 和 `VITE_APP_ENV=development`。
  - API 客户端兼容既有 `/api/...` 调用，避免生产请求生成双重 API 路径。
- 安全：
  - `src/new_energy_sys/api/auth.py` 在生产环境强制要求非默认 `NES_JWT_SECRET`。
  - 生产环境强制要求 `NES_USERS_JSON`，开发/答辩模式才允许 demo 用户。
  - `src/new_energy_sys/api/main.py` 从 `NES_CORS_ORIGINS` 读取 CORS 白名单，生产环境禁止 `*`。
  - `Login.vue` 移除页面明文账号密码提示，并将登录失败归一为稳定错误文案。
- Markdown XSS：
  - 新增 `frontend/src/utils/markdown.js`，执行 `marked.parse()` 后通过 `DOMPurify.sanitize()` 清洗再进入 `v-html`。
  - 禁止 `script/iframe/object/embed` 等危险标签，并限制危险属性。
- 响应式：
  - `App.vue`、`Login.vue`、`OverviewDashboard.vue`、`ModelComparison.vue`、`DispatchSimulation.vue`、`GovernanceAnalysis.vue`、`DataExplorer.vue`、`ReportViewer.vue` 已补充 tablet/mobile 断点。
- 检查与 smoke：
  - 新增 `frontend/scripts/static-check.mjs`，覆盖硬编码 localhost、Markdown sanitize、API 错误归一化等静态门禁。
  - 新增 `scripts/api_smoke_frontend_contract.py`，覆盖未授权 401、登录成功、关键接口 200、guest 提交任务 403。

### 已验证

- `cd frontend; npm run lint`
- `cd frontend; npm run check`
- `cd frontend; npm run build`
- `$env:PYTHONPATH='src'; python -m py_compile src\new_energy_sys\api\main.py src\new_energy_sys\api\auth.py src\new_energy_sys\api\data_loader.py src\new_energy_sys\api\tasks.py`
- `$env:PYTHONPATH='src'; python scripts\api_smoke_frontend_contract.py`
- 生产环境负向验证：
  - 缺少非默认 `NES_JWT_SECRET` 时，`new_energy_sys.api.auth` 拒绝导入。
  - 缺少 `NES_USERS_JSON` 时，生产认证模块拒绝导入。
  - `NES_CORS_ORIGINS='*'` 时，生产 API 入口拒绝导入。
- 生产构建与源码检查未发现 `http://localhost:8000` 或双重 API 路径。

### 剩余项

- 尚未接入真实浏览器截图验收；移动端布局目前为 CSS 断点级整改，仍需浏览器运行态 smoke。
- 页面级 loading/error/empty/retry 只完成 API 客户端归一化、登录页和报告页重点整改，其他图表页仍需补齐完整错误态。
- Playwright E2E 未配置，不能把当前检查视为完整端到端测试。

Pitfall: 本轮已处理最高风险安全边界，但“可生产交付”仍需要浏览器截图、移动端交互和页面级错误态闭环；不要仅凭构建通过宣布 Frontend-B 全部完成。

## Frontend-B Pass 2 推进记录 - 2026-04-26

### 已完成

- 页面级状态：
  - 新增 `frontend/src/components/PageState.vue`，统一承载 `loading/error/empty/retry`。
  - `OverviewDashboard.vue`、`ModelComparison.vue`、`DispatchSimulation.vue`、`GovernanceAnalysis.vue`、`DataExplorer.vue` 已接入可见错误态、空态和重试入口。
  - `DataExplorer.vue` 的任务提交失败从 `console.error` 改为页面可见错误提示，能区分 403 等权限失败。
- 静态门禁：
  - `frontend/scripts/static-check.mjs` 新增核心图表页 `PageState` 和 `@retry` 检查，防止后续回退到只写控制台错误。
- 运行态验收：
  - 使用 in-app browser 访问 `http://localhost:3000`，完成登录、总览、模型对比、调度仿真、策略治理、数据探索、报告浏览路由 smoke。
  - 当前浏览器视口触发 `< 768px` 移动端断点；核心路由均可见，控制台无 error。
  - 报告页移动端左侧阶段列表长文本已改为省略，不再撑出阶段列表横向滚动。

### 已验证

- `cd frontend; npm run lint`
- `cd frontend; npm run build`
- `$env:PYTHONPATH='src'; python scripts\api_smoke_frontend_contract.py`
- 浏览器 smoke：
  - `#/` 可见 `PV 功率预测 vs 实际`
  - `#/models` 可见 `模型排行榜 Model Leaderboard`
  - `#/dispatch` 可见 `策略评分对比`
  - `#/governance` 可见 `储能配置 Pareto 分析`
  - `#/data` 可见 `特征重要性 Top 20`
  - `#/reports` 可见 `实验阶段 Stages`
  - 控制台 error 数量为 `0`

### 剩余项

- 尚未沉淀 Playwright E2E 脚本；当前浏览器 smoke 是人工/工具执行结果，不是 CI 可复用产物。
- 构建仍提示大 chunk warning，主要来自 ECharts/Element Plus；不阻塞生产化安全边界，但后续可做路由级 code-splitting 优化。
- 报告正文中的超长代码块和表格仍保留局部横向滚动，这是技术报告内容的合理兜底，不再让整页布局横向溢出。

Pitfall: 页面状态已闭环到主要图表页，但 E2E 尚未自动化；后续若进入交付验收，应优先把本轮浏览器 smoke 固化为 Playwright，而不是继续靠人工截图。

## Frontend-B Pass 3 推进记录 - 2026-04-26

### 已完成
- E2E 自动化：
  - 新增 `frontend/playwright.config.js`，统一管理本地后端 `uvicorn` 与 Vite dev server 启动。
  - 新增 `frontend/tests/e2e/frontend.spec.js`，覆盖登录、核心路由、移动端横向溢出、Markdown 报告 XSS 净化、guest 提交任务 403 可见错误。
  - `frontend/package.json` 新增 `test:e2e` 脚本，`@playwright/test` 已加入 devDependencies。
- 本机浏览器复用：
  - 配置优先读取 `NES_E2E_BROWSER_PATH`。
  - 未设置环境变量时自动使用系统 Chrome/Edge，例如 `C:\Program Files\Google\Chrome\Application\chrome.exe`。
  - 避免强制下载 Playwright-managed Chromium，适配当前 Windows 本地环境。
- 静态门禁：
  - `frontend/scripts/static-check.mjs` 新增 `test:e2e`、Playwright webServer、XSS Probe、guest 权限失败覆盖检查。
  - `.gitignore` 新增 `playwright-report` 与 `test-results`，避免测试产物污染仓库。

### 已验证
- `cd frontend; npm run lint`
- `cd frontend; npm run build`
- `cd frontend; npm run test:e2e`
- E2E 结果：`4 passed`，项目名 `system-chromium`，使用本机 Chrome。

### 剩余项
- `npm run build` 仍提示大 chunk warning，主要来自 ECharts/Element Plus；这是性能优化项，不阻塞当前 Frontend-B 生产硬化验收。
- `npm run check` 仍保持为静态门禁；当前 Windows 沙箱下把 Vite build 串进 `check` 曾触发 `spawn EPERM`，构建验收继续单独执行 `npm run build`。

Pitfall: E2E 已完成主链路自动化，但本机默认依赖已安装的 Chrome/Edge；如果迁移到 CI 或新机器，必须安装浏览器或设置 `NES_E2E_BROWSER_PATH`，否则会回到浏览器二进制缺失问题。
