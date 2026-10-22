# 项目优化实施检查清单

> 基于 `COMPREHENSIVE_UPGRADE_PLAN.md` 的可操作清单  
> 每完成一项在 `[ ]` 中标记 `x`

---

## ⚠️ 阶段 0：立即修复（P0 关键问题）

预计时间：1-2 天

### 0.1 前端错误边界
- [ ] 创建 `frontend/src/components/ErrorBoundary.tsx`
- [ ] 在 `App.tsx` 包裹根路由
- [ ] 在工作台页面添加局部错误边界
- [ ] 测试：手动抛出错误，验证降级 UI
- [ ] 集成错误上报（可选：Sentry）

### 0.2 API 速率限制
- [ ] 安装 `slowapi>=0.1.9`
- [ ] 在 `deep_research/api.py` 添加全局限流器
- [ ] 为 POST `/api/runs` 添加限流（10/分钟）
- [ ] 为 POST `/api/intent/assess` 添加限流（30/分钟）
- [ ] 测试：快速请求触发 429 响应

### 0.3 输入验证
- [ ] 创建 `deep_research/validation.py`
- [ ] 定义 `RunCreateRequest` Pydantic 模型
- [ ] 添加查询长度验证（3-2000 字符）
- [ ] 添加 XSS/注入字符检测
- [ ] 为所有 POST/PUT 端点添加验证模型

**阶段 0 验收**:
- [ ] ErrorBoundary 测试通过
- [ ] 限流 429 响应测试通过
- [ ] 恶意输入被正确拒绝

---

## 🚀 阶段 1：性能优化（P0+P1）

预计时间：3-5 天

### 1.1 前端代码分割
- [ ] 安装 `react-window` (可选，用于虚拟滚动)
- [ ] 在路由配置中使用 `React.lazy()` 懒加载页面：
  - [ ] WorkbenchPage
  - [ ] QaPage
  - [ ] HistoryPage
  - [ ] ReaderPage
  - [ ] LibraryPage
- [ ] 创建 `LoadingFallback` 组件
- [ ] 用 `<Suspense>` 包裹懒加载组件
- [ ] 懒加载大型组件：
  - [ ] DagView
  - [ ] EvidencePanel
  - [ ] ReportViewer

### 1.2 React 性能优化
- [ ] 识别高频重渲染组件（用 React DevTools Profiler）
- [ ] 对关键组件使用 `React.memo()`：
  - [ ] EventTimeline
  - [ ] RunCard
  - [ ] EvidenceItem
- [ ] 在 WorkbenchPage 使用 `useCallback` 缓存回调
- [ ] 在列表过滤/排序中使用 `useMemo`
- [ ] （可选）在 HistoryPage 使用 `react-window` 虚拟滚动

### 1.3 后端缓存
- [ ] 安装 `redis>=5.0.0` 和 `hiredis>=2.0.0`
- [ ] 创建 `deep_research/cache.py`
- [ ] 实现 `@cache_result` 装饰器
- [ ] 为以下端点添加缓存：
  - [ ] GET `/api/runs` (TTL: 60s)
  - [ ] GET `/api/config` (TTL: 300s)
  - [ ] GET `/api/runs/{id}` (TTL: 120s)
  - [ ] GET `/api/library/*` (TTL: 600s)
- [ ] 实现缓存失效逻辑（run 创建/更新时清除）
- [ ] 本地启动 Redis 实例（或使用 Docker Compose）

### 1.4 数据库查询优化
- [ ] 在 `deep_research/persistence/sql_repository.py` 添加查询日志
- [ ] 使用 `selectinload`/`joinedload` 避免 N+1 查询：
  - [ ] Run.tags
  - [ ] Run.deliverables
  - [ ] Run.role
- [ ] 创建数据库索引迁移：
  - [ ] `idx_runs_status`
  - [ ] `idx_runs_created_at`
  - [ ] `idx_runs_user_id_status`
- [ ] 在 `/api/runs` 添加分页（默认 20 条/页）
- [ ] 运行慢查询分析，确认优化效果

**阶段 1 验收**:
- [ ] 首屏加载 < 2s（用 Lighthouse 测试）
- [ ] Bundle 减少 40%+（查看 Vite build 输出）
- [ ] Redis 缓存命中率 > 60%（添加日志统计）
- [ ] 慢查询减少 50%+

---

## 🏗️ 阶段 2：架构重构（P1）

预计时间：5-7 天

### 2.1 API 层拆分
- [ ] 创建新目录结构 `deep_research/api/`
- [ ] 创建子目录：
  - [ ] `routes/`
  - [ ] `schemas/`
  - [ ] `middleware.py`
  - [ ] `dependencies.py`
- [ ] 拆分 `api.py` 为模块化路由：
  - [ ] `routes/runs.py` (任务管理)
  - [ ] `routes/workbench.py` (工作台)
  - [ ] `routes/qa.py` (问答)
  - [ ] `routes/library.py` (资料库)
  - [ ] `routes/admin.py` (管理)
  - [ ] `routes/health.py` (健康检查)
- [ ] 提取 Pydantic 模型到 `schemas/`：
  - [ ] `schemas/run.py`
  - [ ] `schemas/qa.py`
  - [ ] `schemas/common.py`
- [ ] 创建 `api/__init__.py` 组装路由
- [ ] 更新 `main.py` 导入
- [ ] 运行测试确认无回归

### 2.2 统一错误处理
- [ ] 创建 `deep_research/api/middleware.py`
- [ ] 实现 `error_handler_middleware`
- [ ] 处理常见异常：
  - [ ] NoResultFound → 404
  - [ ] IntegrityError → 409
  - [ ] 通用异常 → 500
- [ ] 定义 `BusinessError` 自定义异常
- [ ] 注册异常处理器到 FastAPI app
- [ ] 更新前端 API 客户端解析错误码

### 2.3 配置管理集中化
- [ ] 安装 `pydantic-settings>=2.0`
- [ ] 重构 `deep_research/config.py` 使用 `BaseSettings`
- [ ] 定义配置项：
  - [ ] 应用配置（app_name, debug）
  - [ ] 数据库配置（url, pool_size）
  - [ ] Redis 配置
  - [ ] API 密钥
  - [ ] 限流配置
  - [ ] 安全配置（CORS）
  - [ ] 监控配置（Sentry）
- [ ] 实现 `@lru_cache` 单例 `get_settings()`
- [ ] 更新 `.env.example` 包含所有配置项
- [ ] 移除代码中的硬编码配置

**阶段 2 验收**:
- [ ] `api.py` 文件不存在，拆分为 < 500 行/文件
- [ ] 配置从 `.env` 加载，无硬编码
- [ ] 错误响应格式统一（包含 code 字段）

---

## 💎 阶段 3：用户体验优化（P1）

预计时间：3-4 天

### 3.1 加载骨架屏
- [ ] 创建 `frontend/src/components/Skeleton.tsx`
- [ ] 实现 `SkeletonCard` 组件
- [ ] 实现 `SkeletonList` 组件
- [ ] 添加脉动动画 CSS
- [ ] 在以下页面使用：
  - [ ] HistoryPage (加载任务列表时)
  - [ ] LibraryPage (加载资料时)
  - [ ] QaPage (加载问答历史时)
  - [ ] WorkbenchPage (加载配置时)

### 3.2 友好错误提示
- [ ] 创建 `frontend/src/components/ErrorMessage.tsx`
- [ ] 定义错误码映射表（中文提示）：
  - [ ] NOT_FOUND
  - [ ] UNAUTHORIZED
  - [ ] RATE_LIMIT_EXCEEDED
  - [ ] INTERNAL_ERROR
  - [ ] NETWORK_ERROR
- [ ] 添加重试按钮（可选）
- [ ] 添加技术详情折叠面板
- [ ] 在所有数据请求中使用 `ErrorMessage`

### 3.3 响应式设计
- [ ] 创建 `frontend/src/styles/responsive.css`
- [ ] 移动端侧边栏改为抽屉式（< 768px）
- [ ] 添加移动菜单按钮
- [ ] 平板适配（768px-1024px）
- [ ] 桌面布局（> 1024px）
- [ ] 大屏优化（> 1440px）
- [ ] 测试主要页面在移动端显示

**阶段 3 验收**:
- [ ] 所有加载状态显示骨架屏
- [ ] 错误提示为中文且可操作
- [ ] 在 iPhone/Android 测试通过

---

## 📊 阶段 4：可观测性（P1）

预计时间：2-3 天

### 4.1 性能监控
- [ ] 安装 `web-vitals` 前端库
- [ ] 创建 `frontend/src/lib/monitoring.ts`
- [ ] 实现 Web Vitals 收集：
  - [ ] CLS
  - [ ] FID
  - [ ] LCP
  - [ ] FCP
  - [ ] TTFB
- [ ] 在 `main.tsx` 初始化监控（仅生产环境）
- [ ] 后端添加 `/api/metrics/web-vitals` 端点接收指标
- [ ] 安装 `prometheus-client` Python 库
- [ ] 创建 `deep_research/monitoring.py`
- [ ] 定义 Prometheus 指标：
  - [ ] `http_requests_total`
  - [ ] `http_request_duration_seconds`
  - [ ] `research_runs_active`
- [ ] 实现 `@monitor_endpoint` 装饰器
- [ ] 添加 `/metrics` 端点导出 Prometheus 指标

### 4.2 错误追踪
- [ ] 安装 `sentry-sdk[fastapi]` (后端)
- [ ] 安装 `@sentry/react` (前端)
- [ ] 后端初始化 Sentry：
  - [ ] FastAPI 集成
  - [ ] SQLAlchemy 集成
  - [ ] 配置采样率
- [ ] 前端初始化 Sentry：
  - [ ] BrowserTracing
  - [ ] Replay (可选)
- [ ] 配置环境变量 `SENTRY_DSN`
- [ ] 测试错误上报

**阶段 4 验收**:
- [ ] `/metrics` 端点正常返回 Prometheus 格式
- [ ] Sentry 收到测试错误
- [ ] Web Vitals 数据被记录

---

## 📚 阶段 5：文档与测试（持续）

预计时间：持续进行

### 5.1 API 文档
- [ ] 为所有路由添加 docstring
- [ ] 定义 Pydantic 响应模型
- [ ] 添加示例到 `json_schema_extra`
- [ ] 添加详细的 `description` 和 `summary`
- [ ] 定义 `responses` 错误码说明
- [ ] 访问 `/docs` 验证 Swagger UI
- [ ] 访问 `/redoc` 验证 ReDoc

### 5.2 E2E 测试
- [ ] 安装 `@playwright/test`
- [ ] 创建 `tests/e2e/workbench.spec.ts`
- [ ] 编写测试用例：
  - [ ] 创建研究任务
  - [ ] 查看任务详情
  - [ ] 加载历史列表
  - [ ] 筛选任务
  - [ ] 问答交互
- [ ] 配置 CI 运行 E2E 测试
- [ ] 生成测试报告

### 5.3 前端单元测试
- [ ] 为关键 Hook 添加测试：
  - [ ] useRuns
  - [ ] useWorkbench
  - [ ] useCatalog
- [ ] 为工具函数添加测试（`lib/` 目录）
- [ ] 提升测试覆盖率到 > 70%

**阶段 5 验收**:
- [ ] Swagger 文档完整，所有端点有示例
- [ ] E2E 测试覆盖核心用户路径
- [ ] 单元测试覆盖率 > 70%

---

## 🛡️ 可选增强项

这些项目可以在完成上述核心优化后考虑：

### 前端状态管理升级
- [ ] 评估引入 Zustand 或 Jotai
- [ ] 迁移复杂状态（如工作台草稿、全局配置）

### 离线支持
- [ ] 添加 Service Worker
- [ ] 实现离线缓存策略（Workbox）
- [ ] 添加离线提示 UI

### 国际化（i18n）
- [ ] 安装 `react-i18next`
- [ ] 提取所有 UI 文案
- [ ] 添加英文翻译

### 深色模式增强
- [ ] 验证所有组件在深色模式下的显示
- [ ] 优化颜色对比度

### 用户引导优化
- [ ] 增强 OnboardingTour
- [ ] 添加情境式帮助（tooltips）

---

## 📋 总体进度跟踪

| 阶段 | 状态 | 开始日期 | 完成日期 | 负责人 |
|------|------|----------|----------|--------|
| 阶段 0：立即修复 | ⬜ 未开始 | | | |
| 阶段 1：性能优化 | ⬜ 未开始 | | | |
| 阶段 2：架构重构 | ⬜ 未开始 | | | |
| 阶段 3：用户体验 | ⬜ 未开始 | | | |
| 阶段 4：可观测性 | ⬜ 未开始 | | | |
| 阶段 5：文档测试 | ⬜ 未开始 | | | |

**状态图例**:
- ⬜ 未开始
- 🟡 进行中
- ✅ 已完成
- 🔴 受阻

---

## 🚨 阻塞问题追踪

遇到阻塞时在此记录：

| 日期 | 阶段/任务 | 问题描述 | 解决方案 | 状态 |
|------|-----------|----------|----------|------|
| | | | | |

---

## 📝 每日进度日志

### 示例：
**2026-10-16**
- [x] 完成项目全面诊断
- [x] 生成优化方案文档
- [ ] 开始阶段 0 实施

---

**最后更新**: 2026-10-16
