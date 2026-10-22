# 项目优化快速启动指南

> 10 分钟了解如何开始优化，适合团队成员快速上手

---

## 📋 前置准备

### 1. 阅读诊断报告
**必读**: `docs/COMPREHENSIVE_UPGRADE_PLAN.md` 第一章节"执行摘要"

**核心问题**:
- 前端性能不足（35/100）→ 无代码分割、无优化
- 后端技术债 → API 文件过大、缓存不足
- 用户体验待提升 → 无加载状态、错误提示不友好

### 2. 环境准备

```bash
# 1. 确保依赖已安装
cd D:\Cursor-edit\Project_test\deep-research-agent

# 后端依赖
pip install -r requirements.txt

# 前端依赖
cd frontend
npm install
```

### 3. 本地 Redis（用于缓存优化）

```bash
# 方式 1：Docker（推荐）
docker run -d --name redis-cache -p 6379:6379 redis:7-alpine

# 方式 2：Windows 安装包
# 下载：https://github.com/tporadowski/redis/releases
# 启动：redis-server
```

验证：
```bash
redis-cli ping
# 输出：PONG
```

---

## 🚀 从哪里开始？

### 前端开发者 → 阶段 0.1 + 阶段 1

**立即做**（30 分钟）:
1. 添加错误边界（避免白屏）
2. 实现代码分割（提升加载速度）

```bash
# 文件清单
frontend/src/components/ErrorBoundary.tsx  # 新建
frontend/src/App.tsx                        # 修改：包裹错误边界
frontend/src/main.tsx                       # 修改：懒加载路由
```

**参考**: `COMPREHENSIVE_UPGRADE_PLAN.md` 阶段 0.1 和阶段 1.1

---

### 后端开发者 → 阶段 0.2 + 阶段 1.3

**立即做**（45 分钟）:
1. 添加 API 限流（防止滥用）
2. 实现 Redis 缓存（减少数据库压力）

```bash
# 安装依赖
pip install slowapi redis hiredis

# 文件清单
deep_research/cache.py          # 新建
deep_research/api.py            # 修改：添加限流和缓存
```

**参考**: `COMPREHENSIVE_UPGRADE_PLAN.md` 阶段 0.2 和阶段 1.3

---

### 全栈/架构师 → 阶段 2

**稍后做**（2-3 天）:
1. 拆分 API 层（2905 行 → 模块化）
2. 统一错误处理
3. 配置集中化

**参考**: `COMPREHENSIVE_UPGRADE_PLAN.md` 阶段 2

---

## ✅ 第一周目标

### Day 1-2：立即修复（阶段 0）
- [x] 前端错误边界
- [x] API 限流
- [x] 输入验证

**验收**: 运行 `docs/IMPLEMENTATION_CHECKLIST.md` 阶段 0 的验收测试

### Day 3-5：性能优化（阶段 1）
- [x] 前端代码分割
- [x] React 性能优化
- [x] 后端缓存
- [x] 数据库查询优化

**验收**:
```bash
# 前端性能测试
cd frontend
npm run build
# 查看 dist/ 大小，应减少 40%+

# 使用 Lighthouse
npx lighthouse http://localhost:5173 --view

# 后端缓存测试
# 查看 Redis 命令统计
redis-cli INFO stats | grep keyspace_hits
```

---

## 🛠️ 开发工作流

### 1. 选择任务
打开 `docs/IMPLEMENTATION_CHECKLIST.md`，选择一个未完成的任务

### 2. 创建分支
```bash
git checkout -b feature/error-boundary
# 或
git checkout -b perf/code-splitting
```

### 3. 实施 + 测试
参考 `COMPREHENSIVE_UPGRADE_PLAN.md` 对应章节的实施代码

### 4. 验收
执行对应阶段的验收标准

### 5. 提交
```bash
git add .
git commit -m "feat: add ErrorBoundary to prevent white screen

- Create ErrorBoundary component
- Wrap App root with error boundary
- Add fallback UI for errors

Ref: COMPREHENSIVE_UPGRADE_PLAN.md Stage 0.1"

git push origin feature/error-boundary
```

### 6. PR + 代码审查
创建 PR，链接到 `IMPLEMENTATION_CHECKLIST.md` 中的任务

---

## 📊 如何衡量进度？

### 自动化指标

#### 前端性能（Lighthouse）
```bash
cd frontend
npm run build
npx serve dist

# 在另一个终端
npx lighthouse http://localhost:3000 --only-categories=performance --output=json --output-path=./lighthouse-report.json

# 目标：
# - Performance Score: > 85
# - LCP: < 2.5s
# - FID: < 100ms
```

#### 后端性能（Apache Bench）
```bash
# 测试 API 响应时间
ab -n 1000 -c 10 http://localhost:8000/api/runs

# 目标：
# - P95 响应时间: < 500ms
```

#### 缓存命中率
```bash
redis-cli INFO stats | grep -E "keyspace_hits|keyspace_misses"

# 计算命中率：hits / (hits + misses)
# 目标：> 60%
```

### 手动检查
- [ ] 前端首屏加载是否 < 2 秒？
- [ ] 错误时是否显示友好提示而非白屏？
- [ ] API 快速请求是否触发 429？
- [ ] 移动端布局是否正常？

---

## 🆘 常见问题

### Q1: Redis 连接失败
**症状**: `redis.exceptions.ConnectionError`

**解决**:
```bash
# 检查 Redis 是否运行
docker ps | grep redis
# 或
redis-cli ping

# 检查 .env 配置
cat .env | grep REDIS
# 应该包含：REDIS_URL=redis://localhost:6379/0
```

### Q2: 前端 build 失败
**症状**: `vite build` 报错

**解决**:
```bash
# 清除缓存
rm -rf node_modules/.vite
rm -rf dist

# 重新安装依赖
npm install

# 检查 TypeScript 错误
npm run type-check
```

### Q3: API 限流不生效
**症状**: 快速请求无 429 响应

**解决**:
```python
# 确认 slowapi 已安装
pip list | grep slowapi

# 检查 api.py 中是否正确配置
# 确保 app.state.limiter = limiter
# 确保端点有 @limiter.limit() 装饰器
```

### Q4: 数据库迁移失败
**症状**: `alembic upgrade head` 报错

**解决**:
```bash
# 查看当前版本
alembic current

# 回滚到上一个版本
alembic downgrade -1

# 重新应用
alembic upgrade head

# 如果持续失败，检查迁移脚本
cat alembic/versions/最新文件.py
```

---

## 📞 获取帮助

### 文档索引
- **完整方案**: `docs/COMPREHENSIVE_UPGRADE_PLAN.md`
- **实施清单**: `docs/IMPLEMENTATION_CHECKLIST.md`
- **本指南**: `docs/QUICK_START.md`

### 优先级参考
1. **P0（必须做）**: 阶段 0（错误边界、限流、验证）
2. **P1（重要）**: 阶段 1（性能）+ 阶段 2（架构）
3. **P2（改进）**: 阶段 3（用户体验）+ 阶段 4（监控）

### 资源链接
- React 性能优化: https://react.dev/learn/render-and-commit
- FastAPI 最佳实践: https://fastapi.tiangolo.com/async/
- Redis 缓存策略: https://redis.io/docs/manual/patterns/

---

## 🎯 第一个任务建议

**如果你是前端开发者**，从这里开始：
```bash
# 1. 创建错误边界
touch frontend/src/components/ErrorBoundary.tsx

# 2. 参考 COMPREHENSIVE_UPGRADE_PLAN.md 阶段 0.1
# 3. 复制代码模板并调整
# 4. 在 App.tsx 中使用
# 5. 手动测试：throw new Error('test')
```

**如果你是后端开发者**，从这里开始：
```bash
# 1. 安装限流库
pip install slowapi

# 2. 参考 COMPREHENSIVE_UPGRADE_PLAN.md 阶段 0.2
# 3. 在 api.py 顶部添加 limiter
# 4. 为 POST 端点添加装饰器
# 5. 测试：curl 快速请求同一端点
```

---

**祝优化顺利！有问题随时查阅完整方案文档。**

最后更新：2026-10-16
