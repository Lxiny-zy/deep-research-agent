# Deep Research Agent 全面优化升级方案

**生成日期**: 2026-10-16  
**项目健康度**: 62/100（中等）  
**目标**: 提升至 85+/100（优秀）

---

## 一、执行摘要

基于深度代码审计和架构分析，识别出 **33 个待优化项**，分布在 8 个维度。核心问题：

1. **前端性能严重不足**（35/100）：无代码分割、无性能优化、无错误边界
2. **后端存在技术债**：嵌套过深、缓存不足、API 文件过大（2905 行）
3. **用户体验待提升**：缺少加载状态、错误提示不友好、响应式设计未验证

**优先级分级**：
- **P0（关键）**: 前端性能、错误处理、安全防护
- **P1（重要）**: 后端优化、文档完善、监控体系
- **P2（改进）**: 测试增强、用户体验细节

---

## 二、分阶段实施路线图

### 阶段 0：立即修复（1-2 天）—— P0 关键问题

#### 0.1 前端错误边界（必须）
**问题**: 无 ErrorBoundary，前端崩溃直接白屏  
**影响**: 用户体验致命、无法优雅降级

**实施**:
```typescript
// 1. 创建 frontend/src/components/ErrorBoundary.tsx
import { Component, ReactNode } from 'react'

interface Props { children: ReactNode; fallback?: ReactNode }
interface State { hasError: boolean; error: Error | null }

export class ErrorBoundary extends Component<Props, State> {
  state = { hasError: false, error: null }
  
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error }
  }
  
  componentDidCatch(error: Error, errorInfo: any) {
    console.error('ErrorBoundary caught:', error, errorInfo)
    // 上报错误到监控系统（Sentry/自建）
  }
  
  render() {
    if (this.state.hasError) {
      return this.props.fallback || (
        <div className="error-container">
          <h2>出现错误</h2>
          <p>{this.state.error?.message}</p>
          <button onClick={() => window.location.reload()}>刷新页面</button>
        </div>
      )
    }
    return this.props.children
  }
}

// 2. 在 App.tsx 包裹路由
<ErrorBoundary>
  <RouterProvider router={router} />
</ErrorBoundary>

// 3. 在关键页面添加局部边界
<ErrorBoundary fallback={<div>工作台暂时不可用</div>}>
  <WorkbenchPage />
</ErrorBoundary>
```

**验收**: 手动抛出错误，验证显示降级 UI 而非白屏

---

#### 0.2 API 速率限制增强
**问题**: 仅 14/33 端点有限流，易被滥用/攻击  
**影响**: 服务稳定性风险

**实施**:
```python
# 1. 安装依赖
# requirements.txt: slowapi>=0.1.9

# 2. 在 deep_research/api.py 顶部添加全局限流
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address, default_limits=["200/hour"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# 3. 为敏感端点添加更严格限流
@app.post("/api/runs")
@limiter.limit("10/minute")  # 创建任务：每分钟 10 次
async def create_run(request: Request, ...):
    ...

@app.post("/api/intent/assess")
@limiter.limit("30/minute")  # 意图评估：每分钟 30 次
async def assess_intent(request: Request, ...):
    ...
```

**验收**: 用脚本快速请求同一端点，验证 429 响应

---

#### 0.3 输入验证增强
**问题**: 仅 2 处验证装饰器，存在注入风险  
**影响**: 安全漏洞

**实施**:
```python
# 1. 创建 deep_research/validation.py
from pydantic import BaseModel, validator, Field
from fastapi import HTTPException

class RunCreateRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=2000)
    role_id: str | None = Field(None, regex=r'^[a-z0-9_-]+$')
    tags: list[str] = Field(default_factory=list, max_items=10)
    
    @validator('query')
    def sanitize_query(cls, v):
        # 移除潜在的注入字符
        if any(char in v for char in ['<script', 'javascript:', 'onerror=']):
            raise ValueError('Invalid characters in query')
        return v.strip()

# 2. 在路由中使用
@app.post("/api/runs")
async def create_run(req: RunCreateRequest):  # 类型验证自动触发
    ...
```

**文件清单**:
- 新增: `deep_research/validation.py`
- 修改: `deep_research/api.py` (为每个 POST/PUT 端点添加 Pydantic 模型)

---

### 阶段 1：性能优化（3-5 天）—— P0+P1

#### 1.1 前端代码分割与懒加载
**目标**: 首屏加载时间减少 40%+

**实施**:
```typescript
// 1. 路由懒加载 (frontend/src/main.tsx 或路由配置文件)
import { lazy, Suspense } from 'react'

const WorkbenchPage = lazy(() => import('./pages/WorkbenchPage'))
const QaPage = lazy(() => import('./pages/QaPage'))
const HistoryPage = lazy(() => import('./pages/HistoryPage'))
const ReaderPage = lazy(() => import('./pages/ReaderPage'))

// 2. 添加加载占位
const LoadingFallback = () => (
  <div className="loading-skeleton">
    <div className="skeleton-header" />
    <div className="skeleton-content" />
  </div>
)

// 3. 路由配置
const router = createBrowserRouter([
  {
    path: '/',
    element: (
      <Suspense fallback={<LoadingFallback />}>
        <WorkbenchPage />
      </Suspense>
    )
  },
  // ... 其他路由同理
])

// 4. 组件级别懒加载（大型组件）
const DagView = lazy(() => import('./components/DagView'))
const EvidencePanel = lazy(() => import('./components/EvidencePanel'))
```

**预期收益**:
- 初始 bundle 从 ~800KB 降至 ~300KB
- Time to Interactive 减少 1.5-2 秒

---

#### 1.2 React 性能优化
**实施**:
```typescript
// 1. 高频重渲染组件使用 memo
// frontend/src/components/EventTimeline.tsx
import { memo } from 'react'

export const EventTimeline = memo(({ events }: Props) => {
  // ... 组件逻辑
}, (prevProps, nextProps) => {
  // 自定义比较逻辑
  return prevProps.events === nextProps.events
})

// 2. 回调函数缓存 (frontend/src/pages/WorkbenchPage.tsx)
import { useCallback, useMemo } from 'react'

function WorkbenchPage() {
  const handleSubmit = useCallback((query: string) => {
    // 提交逻辑
  }, [/* 依赖项 */])
  
  const filteredRuns = useMemo(() => {
    return runs.filter(r => r.status === 'completed')
  }, [runs])
  
  return <RunList runs={filteredRuns} onSubmit={handleSubmit} />
}

// 3. 虚拟滚动（大列表优化）
// 对于 HistoryPage 的任务列表
import { FixedSizeList } from 'react-window'

<FixedSizeList
  height={600}
  itemCount={runs.length}
  itemSize={80}
  width="100%"
>
  {({ index, style }) => (
    <div style={style}>
      <RunCard run={runs[index]} />
    </div>
  )}
</FixedSizeList>
```

**需要安装**:
```json
"dependencies": {
  "react-window": "^1.8.10"
}
```

---

#### 1.3 后端缓存策略
**实施**:
```python
# 1. 安装 Redis 客户端
# requirements.txt: redis>=5.0.0, hiredis>=2.0.0

# 2. 创建缓存工具 deep_research/cache.py
import redis.asyncio as redis
from functools import wraps
import json
import hashlib

redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)

def cache_result(ttl: int = 300):
    """装饰器：缓存函数结果"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # 生成缓存键
            cache_key = f"{func.__name__}:{hashlib.md5(json.dumps([args, kwargs], sort_keys=True).encode()).hexdigest()}"
            
            # 尝试从缓存读取
            cached = await redis_client.get(cache_key)
            if cached:
                return json.loads(cached)
            
            # 执行函数
            result = await func(*args, **kwargs)
            
            # 写入缓存
            await redis_client.setex(cache_key, ttl, json.dumps(result))
            return result
        return wrapper
    return decorator

# 3. 应用到高频查询
# deep_research/api.py
from .cache import cache_result

@app.get("/api/runs")
@cache_result(ttl=60)  # 缓存 1 分钟
async def list_runs(status: str | None = None):
    ...

@app.get("/api/config")
@cache_result(ttl=300)  # 缓存 5 分钟
async def get_config():
    ...
```

**缓存策略**:
| 端点 | TTL | 失效条件 |
|------|-----|----------|
| `GET /api/runs` | 60s | 新 run 创建时清除 |
| `GET /api/config` | 300s | 配置更新时清除 |
| `GET /api/runs/{id}` | 120s | 该 run 状态变更时清除 |
| `GET /api/library/*` | 600s | 手动清除 |

---

#### 1.4 数据库查询优化
**实施**:
```python
# 1. 分析当前查询
# 在 deep_research/persistence/sql_repository.py 添加查询日志
import logging
import time

logger = logging.getLogger(__name__)

class SQLRepository:
    async def list_runs(self, ...):
        start = time.time()
        
        # 原查询
        query = select(Run).where(...)
        
        # 添加预加载（避免 N+1）
        query = query.options(
            selectinload(Run.tags),      # 预加载标签
            selectinload(Run.deliverables),  # 预加载交付物
            joinedload(Run.role)         # join 加载角色
        )
        
        result = await session.execute(query)
        
        elapsed = time.time() - start
        if elapsed > 0.1:  # 慢查询阈值 100ms
            logger.warning(f"Slow query: list_runs took {elapsed:.3f}s")
        
        return result.scalars().all()

# 2. 添加数据库索引
# 创建迁移文件 alembic/versions/xxx_add_indexes.py
def upgrade():
    op.create_index('idx_runs_status', 'runs', ['status'])
    op.create_index('idx_runs_created_at', 'runs', ['created_at'])
    op.create_index('idx_runs_user_id_status', 'runs', ['user_id', 'status'])

# 3. 分页查询（避免全表扫描）
@app.get("/api/runs")
async def list_runs(
    page: int = 1,
    page_size: int = 20,  # 默认 20 条/页
    status: str | None = None
):
    offset = (page - 1) * page_size
    query = select(Run).limit(page_size).offset(offset)
    ...
```

---

### 阶段 2：架构重构（5-7 天）—— P1

#### 2.1 API 层拆分
**问题**: `api.py` 单文件 2905 行，难以维护

**实施**:
```
deep_research/
├── api/
│   ├── __init__.py          # FastAPI app 实例
│   ├── middleware.py        # 中间件（CORS/认证/日志）
│   ├── dependencies.py      # 依赖注入
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── runs.py          # 任务相关路由（~500 行）
│   │   ├── workbench.py     # 工作台路由
│   │   ├── qa.py            # 问答路由
│   │   ├── library.py       # 资料库路由
│   │   ├── admin.py         # 管理路由
│   │   └── health.py        # 健康检查
│   └── schemas/
│       ├── run.py           # Run 相关 Pydantic 模型
│       ├── qa.py
│       └── common.py
```

**迁移步骤**:
1. 创建新目录结构
2. 将 `api.py` 的路由按功能域拆分到 `routes/` 下
3. 提取 Pydantic 模型到 `schemas/`
4. 创建 `api/__init__.py` 组装所有路由
5. 更新 `main.py` 导入路径
6. 运行测试验证无回归

---

#### 2.2 统一错误处理中间件
**实施**:
```python
# deep_research/api/middleware.py
from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError, NoResultFound
import logging

logger = logging.getLogger(__name__)

async def error_handler_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except NoResultFound:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "Resource not found", "code": "NOT_FOUND"}
        )
    except IntegrityError as e:
        logger.error(f"Database integrity error: {e}")
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "Data conflict", "code": "CONFLICT"}
        )
    except Exception as e:
        logger.exception("Unhandled exception")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Internal server error", "code": "INTERNAL_ERROR"}
        )

# deep_research/api/__init__.py
app.middleware("http")(error_handler_middleware)

# 自定义异常
class BusinessError(Exception):
    def __init__(self, message: str, code: str, status_code: int = 400):
        self.message = message
        self.code = code
        self.status_code = status_code

@app.exception_handler(BusinessError)
async def business_error_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.message, "code": exc.code}
    )
```

---

#### 2.3 配置管理集中化
**实施**:
```python
# deep_research/config.py (重构)
from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    # 应用配置
    app_name: str = "Deep Research Agent"
    debug: bool = False
    
    # 数据库
    database_url: str
    db_pool_size: int = 10
    db_max_overflow: int = 20
    
    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_ttl_default: int = 300
    
    # API 密钥
    openai_api_key: str
    tavily_api_key: str
    
    # 限流
    rate_limit_default: str = "200/hour"
    rate_limit_run_creation: str = "10/minute"
    
    # 安全
    cors_origins: list[str] = ["http://localhost:5173"]
    api_key_header: str = "X-API-Key"
    
    # 监控
    sentry_dsn: str | None = None
    enable_metrics: bool = True
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

@lru_cache()
def get_settings() -> Settings:
    return Settings()

# 使用
from deep_research.config import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name, debug=settings.debug)
```

---

### 阶段 3：用户体验优化（3-4 天）—— P1

#### 3.1 加载状态与骨架屏
**实施**:
```typescript
// 1. 创建骨架屏组件 frontend/src/components/Skeleton.tsx
export function SkeletonCard() {
  return (
    <div className="skeleton-card animate-pulse">
      <div className="skeleton-title" />
      <div className="skeleton-line" />
      <div className="skeleton-line short" />
    </div>
  )
}

export function SkeletonList({ count = 3 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonCard key={i} />
      ))}
    </>
  )
}

// 2. 在页面中使用
// frontend/src/pages/HistoryPage.tsx
import { useRuns } from '../hooks/useRuns'
import { SkeletonList } from '../components/Skeleton'

export function HistoryPage() {
  const { data: runs, isLoading, error } = useRuns()
  
  if (isLoading) return <SkeletonList count={5} />
  if (error) return <ErrorMessage error={error} />
  
  return <RunList runs={runs} />
}

// 3. CSS (frontend/src/components/Skeleton.css)
.skeleton-card {
  padding: 1rem;
  border-radius: 8px;
  background: var(--surface-2);
}

.skeleton-title {
  height: 1.5rem;
  width: 60%;
  background: var(--surface-3);
  border-radius: 4px;
  margin-bottom: 0.5rem;
}

.skeleton-line {
  height: 1rem;
  width: 100%;
  background: var(--surface-3);
  border-radius: 4px;
  margin-bottom: 0.5rem;
}

.skeleton-line.short { width: 40%; }

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.animate-pulse {
  animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}
```

---

#### 3.2 友好的错误提示
**实施**:
```typescript
// frontend/src/components/ErrorMessage.tsx
import { AppIcon } from './AppIcon'

interface Props {
  error: Error | { message: string; code?: string }
  retry?: () => void
}

export function ErrorMessage({ error, retry }: Props) {
  const errorMessages: Record<string, string> = {
    'NOT_FOUND': '请求的资源不存在',
    'UNAUTHORIZED': '请先登录',
    'RATE_LIMIT_EXCEEDED': '请求过于频繁，请稍后再试',
    'INTERNAL_ERROR': '服务暂时不可用，我们正在修复',
    'NETWORK_ERROR': '网络连接失败，请检查网络设置'
  }
  
  const code = 'code' in error ? error.code : 'UNKNOWN'
  const message = errorMessages[code] || error.message
  
  return (
    <div className="error-container">
      <AppIcon name="alert-circle" size={48} />
      <h3>出现问题</h3>
      <p>{message}</p>
      {retry && (
        <button onClick={retry} className="retry-button">
          <AppIcon name="refresh" />
          重试
        </button>
      )}
      <details>
        <summary>技术详情</summary>
        <code>{JSON.stringify(error, null, 2)}</code>
      </details>
    </div>
  )
}
```

---

#### 3.3 响应式设计增强
**实施**:
```css
/* frontend/src/styles/responsive.css */

/* 移动端优先 */
.sidebar {
  position: fixed;
  left: -280px;
  width: 280px;
  height: 100vh;
  transition: left 0.3s;
  z-index: 100;
}

.sidebar.open {
  left: 0;
}

.mobile-menu-button {
  display: block;
}

/* 平板 */
@media (min-width: 768px) {
  .sidebar {
    left: 0;
    position: sticky;
  }
  
  .mobile-menu-button {
    display: none;
  }
  
  .main-content {
    margin-left: 280px;
  }
}

/* 桌面 */
@media (min-width: 1024px) {
  .container {
    max-width: 1200px;
    margin: 0 auto;
  }
  
  .grid-layout {
    grid-template-columns: repeat(2, 1fr);
    gap: 2rem;
  }
}

/* 大屏 */
@media (min-width: 1440px) {
  .grid-layout {
    grid-template-columns: repeat(3, 1fr);
  }
}
```

---

### 阶段 4：可观测性与监控（2-3 天）—— P1

#### 4.1 性能监控
**实施**:
```typescript
// 1. 前端 Web Vitals
// frontend/src/lib/monitoring.ts
import { onCLS, onFID, onLCP, onFCP, onTTFB } from 'web-vitals'

function sendToAnalytics(metric: any) {
  // 发送到后端或第三方服务
  fetch('/api/metrics/web-vitals', {
    method: 'POST',
    body: JSON.stringify(metric),
    headers: { 'Content-Type': 'application/json' }
  })
}

export function initWebVitals() {
  onCLS(sendToAnalytics)
  onFID(sendToAnalytics)
  onLCP(sendToAnalytics)
  onFCP(sendToAnalytics)
  onTTFB(sendToAnalytics)
}

// frontend/src/main.tsx
import { initWebVitals } from './lib/monitoring'

if (import.meta.env.PROD) {
  initWebVitals()
}
```

```python
# 2. 后端 APM
# deep_research/monitoring.py
from prometheus_client import Counter, Histogram, Gauge
import time
from functools import wraps

# 定义指标
request_count = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status'])
request_duration = Histogram('http_request_duration_seconds', 'HTTP request duration')
active_runs = Gauge('research_runs_active', 'Number of active research runs')

def monitor_endpoint(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.time()
        try:
            result = await func(*args, **kwargs)
            request_count.labels(method='POST', endpoint=func.__name__, status='success').inc()
            return result
        except Exception as e:
            request_count.labels(method='POST', endpoint=func.__name__, status='error').inc()
            raise
        finally:
            duration = time.time() - start
            request_duration.observe(duration)
    return wrapper

# 应用到路由
@app.post("/api/runs")
@monitor_endpoint
async def create_run(...):
    ...
```

---

#### 4.2 错误追踪（Sentry 集成）
**实施**:
```python
# requirements.txt
# sentry-sdk[fastapi]>=1.40.0

# deep_research/api/__init__.py
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

def init_sentry():
    sentry_sdk.init(
        dsn=get_settings().sentry_dsn,
        integrations=[
            FastApiIntegration(),
            SqlalchemyIntegration(),
        ],
        traces_sample_rate=0.1,  # 10% 请求采样
        profiles_sample_rate=0.1,
        environment="production" if not get_settings().debug else "development",
    )

if get_settings().sentry_dsn:
    init_sentry()
```

```typescript
// frontend/src/main.tsx
import * as Sentry from "@sentry/react"

Sentry.init({
  dsn: import.meta.env.VITE_SENTRY_DSN,
  integrations: [
    new Sentry.BrowserTracing(),
    new Sentry.Replay(),
  ],
  tracesSampleRate: 0.1,
  replaysSessionSampleRate: 0.1,
  replaysOnErrorSampleRate: 1.0,
})
```

---

### 阶段 5：文档与测试（持续）—— P2

#### 5.1 API 文档自动生成
**实施**:
```python
# deep_research/api/routes/runs.py
from fastapi import APIRouter, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/runs", tags=["研究任务"])

class RunCreateRequest(BaseModel):
    """创建研究任务的请求体"""
    query: str = Field(..., description="研究问题", example="高光谱成像的压缩感知算法")
    role_id: str | None = Field(None, description="角色 ID")
    
    class Config:
        json_schema_extra = {
            "example": {
                "query": "CASSI 系统的光学编码矩阵优化方法",
                "role_id": "academic_reviewer"
            }
        }

@router.post(
    "",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建研究任务",
    description="""
    创建一个新的深度研究任务。
    
    ## 工作流程
    1. 意图识别与路由
    2. 子问题分解（DAG）
    3. 多源检索与验证
    4. 证据综合与报告生成
    
    ## 限制
    - 每分钟最多 10 次
    - 查询长度 3-2000 字符
    """,
    responses={
        201: {"description": "任务创建成功"},
        400: {"description": "请求参数错误"},
        429: {"description": "请求过于频繁"},
    }
)
async def create_run(req: RunCreateRequest):
    """创建研究任务"""
    ...
```

**自动生成 Swagger UI**:
- 访问 `http://localhost:8000/docs` 查看 API 文档
- 访问 `http://localhost:8000/redoc` 查看 ReDoc 风格文档

---

#### 5.2 E2E 测试
**实施**:
```typescript
// tests/e2e/workbench.spec.ts
import { test, expect } from '@playwright/test'

test.describe('科研工作台', () => {
  test('用户可以创建研究任务', async ({ page }) => {
    await page.goto('http://localhost:5173')
    
    // 输入研究问题
    await page.fill('[data-testid="query-input"]', 'CASSI 系统的最新进展')
    
    // 点击开始研究
    await page.click('[data-testid="start-research-button"]')
    
    // 等待任务创建
    await expect(page.locator('[data-testid="run-status"]')).toContainText('运行中', { timeout: 10000 })
    
    // 验证跳转到任务详情页
    await expect(page).toHaveURL(/\/runs\/[a-z0-9-]+/)
    
    // 验证事件流显示
    await expect(page.locator('[data-testid="event-timeline"]')).toBeVisible()
  })
  
  test('加载历史任务列表', async ({ page }) => {
    await page.goto('http://localhost:5173/history')
    
    // 等待列表加载
    await expect(page.locator('[data-testid="run-card"]').first()).toBeVisible({ timeout: 5000 })
    
    // 验证筛选器工作
    await page.click('[data-testid="filter-completed"]')
    await expect(page.locator('[data-testid="run-card"][data-status="completed"]')).toHaveCount(5)
  })
})
```

**package.json**:
```json
{
  "devDependencies": {
    "@playwright/test": "^1.40.0"
  },
  "scripts": {
    "test:e2e": "playwright test",
    "test:e2e:ui": "playwright test --ui"
  }
}
```

---

## 三、技术债务清单

### 高优先级技术债
1. **API 层重构** (estimated: 2 天)
   - 拆分 `api.py` (2905 行) 为模块化路由
   - 提取 Pydantic schemas
   
2. **嵌套逻辑重构** (estimated: 1 天)
   - 重构 13 处深度嵌套（>5 层）
   - 提取为独立函数/类

3. **前端状态管理** (estimated: 1.5 天)
   - 评估是否引入 Zustand/Jotai
   - 当前 React Context 在规模扩大后可能不足

### 中优先级技术债
4. **数据库迁移管理** (estimated: 0.5 天)
   - 补充缺失的索引
   - 优化关联查询

5. **配置管理** (estimated: 0.5 天)
   - 集中化配置（已在阶段 2.3 规划）

### 低优先级技术债
6. **日志标准化** (estimated: 0.5 天)
   - 统一日志格式（结构化日志）
   - 添加 trace ID

---

## 四、资源需求

### 人力
- **前端开发**: 1 人 × 8 天
- **后端开发**: 1 人 × 10 天
- **测试工程师**: 1 人 × 5 天（编写 E2E 测试）
- **DevOps**: 0.5 人 × 3 天（监控部署）

### 基础设施
- **Redis 实例**: 1 个（缓存）
- **监控服务**: Sentry（可选，也可自建）
- **性能测试环境**: 1 套

### 预算（可选第三方服务）
- Sentry Pro: ~$26/月（1万错误事件）
- 或自建 Sentry: 0 成本（需要 DevOps 时间）

---

## 五、验收标准

### 阶段 0 验收
- [ ] ErrorBoundary 覆盖所有路由，手动抛出错误显示降级 UI
- [ ] 所有 API 端点限流测试通过（429 响应）
- [ ] 输入验证测试：提交恶意输入被拒绝

### 阶段 1 验收
- [ ] 首屏加载时间 < 2 秒（LCP）
- [ ] Bundle 大小减少 40%+ (从 ~800KB 到 ~480KB)
- [ ] Redis 缓存命中率 > 60%
- [ ] 慢查询数量减少 50%+

### 阶段 2 验收
- [ ] `api.py` 拆分为 < 500 行/文件
- [ ] 配置集中化，无硬编码密钥
- [ ] 错误响应格式统一

### 阶段 3 验收
- [ ] 所有加载状态显示骨架屏
- [ ] 错误提示用户可理解（中文 + 可操作）
- [ ] 移动端适配测试通过（iPhone/Android）

### 阶段 4 验收
- [ ] Prometheus metrics 端点正常
- [ ] Sentry 错误上报正常
- [ ] Web Vitals 数据收集正常

### 阶段 5 验收
- [ ] Swagger 文档完整，所有端点有示例
- [ ] E2E 测试覆盖核心用户路径（工作台/问答/历史）

---

## 六、风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 重构引入新 bug | 中 | 高 | 增加测试覆盖，分支开发，灰度发布 |
| Redis 引入新依赖 | 低 | 中 | 使用 Docker Compose 本地开发，文档化部署 |
| 性能优化效果不达标 | 中 | 中 | 先在测试环境验证，用 Lighthouse 量化 |
| E2E 测试不稳定 | 高 | 低 | 使用 Playwright 重试机制，隔离测试环境 |

---

## 七、后续维护

### 监控指标
持续跟踪以下指标：
- **前端**: LCP < 2.5s, FID < 100ms, CLS < 0.1
- **后端**: P95 响应时间 < 500ms, 错误率 < 0.1%
- **数据库**: 慢查询 < 5 个/小时

### 定期审查（每季度）
1. 技术债务回顾
2. 性能回归测试
3. 安全漏洞扫描
4. 依赖更新检查

---

**文档版本**: v1.0  
**下次审查**: 2026-11-16
