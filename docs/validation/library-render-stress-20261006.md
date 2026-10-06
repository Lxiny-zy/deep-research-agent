# Library 渲染异常定向复验

**10 次均未复现原异常；原异常的根因仍未确认，不能据此宣称已经修复。**

本轮在升级工作树中连续运行 10 次以下筛选，每次启动独立 pytest 进程，使用
`D:/tmp/lb-load-01` 至 `D:/tmp/lb-load-10` 的独立短临时目录。执行期间主会话另行运行
第二轮全量与 PostgreSQL 验证，因此与这些宿主负载重叠。

```powershell
python -m pytest -q --log-cli-level=WARNING --basetemp=D:/tmp/lb-load-01 tests/test_library_task_paths.py -k "inline and autoResearch"
```

各次命令仅替换 basetemp 序号；设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，
保存完整 UTF-8 日志。此用例及 `scenario_app` 没有启用 `cooperative_render`，也没有
替换 renderer；LLM/search 使用既有固定测试实现，渲染经过生产默认 `RenderProcess`
真实子进程。每轮后另行以 SQLite 只读连接检查其 `render_job` 状态。

结果：

- 10/10 次通过，每次选中 1 项、排除 7 项。
- 共 20 个持久化渲染任务，全部 `done`、`attempts=1`、`stalls=0`、`error=null`。
- 没有渲染失败诊断日志，也没有其他 WARNING/ERROR 输出。
- 每次含进程启动与落盘核对的耗时为 12.538–13.362 秒，总计 128.407 秒。
- 全部 SQL 核对成功，没有依靠隐含渲染重试获得测试通过。

完整日志与逐轮 SQL 状态为 `library-render-stress-01.log/.json` 至
`library-render-stress-10.log/.json`；机器可读汇总见
[library-render-stress-summary-20261006.json](library-render-stress-summary-20261006.json)。

原异常记录仍是首次全量的任务 `ed651533-427f-4d2d-97a5-b3adb4176eab`，其旧记录只有
泛化后的 RuntimeError，没有可供追溯的堆栈。新安全诊断可以定位后续复现，无法恢复
已丢失的原堆栈。本轮没有异常可供进一步归因或修复。

已修复的 lifespan 测试中，遗漏的 `late_worker` 只等待并设置测试事件，没有直接调用
渲染器或数据库。当前没有证据将该遗漏与原 library 异常建立因果关系。

每轮是独立 pytest 进程，因此本复验覆盖重复真实渲染及当前宿主负载，但不复现原
全量进程在此用例之前的所有内存、事件循环或 fixture 历史。这一限制必须与第二轮
完整测试的结果一起保留，不能把 10 次未复现作为“偶发异常已修复”的证明。
