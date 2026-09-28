# 项目执行器契约

生产执行器为 `deep_research/agents/plan_executor.py`，契约校验在
`deep_research/plan_contract.py`。本文件说明真实行为，不提供另一套示意执行器。

每次调用由当前任务、当前 step.prompt、显式技能和依赖交接组成。
`reset` 保留兼容字段，执行始终使用新上下文；磁盘交接不会因 reset 而消失。
上下文只包含依赖图上的祖先步骤，不读取所有工作区文件。
缺失、失败、部分完成、二进制文件和被截断的节选会明确标注。

## 模型响应

单文件步骤默认可直接返回文件文本；JSON 文件必须是严格 JSON。
多文件步骤或 `metadata.result_contract: "research-step-v1"` 必须返回：

```json
{
  "contract_version": 1,
  "status": "partial",
  "summary": "已完成证据比较，部分全文未取得",
  "artifacts": [
    {"path":"work/research-topic/review/assessment.md","content":"# 证据比较\n\n已确认事实及来源、不同解释、失败路线和未验证内容。"},
    {"path":"work/research-topic/review/gaps.json","content":"{\"gaps\":[\"缺少全文\"]}"}
  ],
  "gaps": ["无法核对部分研究的实验条件"],
  "next_actions": ["取得全文后复核实验条件"]
}
```

`done` 必须提供所有必需文件且没有未解决缺口。
`partial` 必须提供至少一个有用文件和具体缺口，可省略暂时无法生成的文件。
所有路径必须已声明；禁止重复路径、空内容、无效 JSON、NaN、重复 JSON 键和代码块包裹。
支持 Markdown、JSON、TXT、HTML、CSV/TSV、TeX/Bib、Python/R/SQL、YAML 文本；
写出代码文件不代表执行了代码。其他格式应走注册操作或报告导出。

## 执行与恢复

1. 预检输出类型、路径、技能及依赖文件完整性。
2. 保存 running 状态，调用模型。重试策略由步骤 resource 和工作流执行。
3. 保存原始响应到 `work/<slug>/executor-journal/<step-id>/<attempt>.txt`。
4. 整批检查响应；不将错误 JSON 包装成成功文件。
5. 原子写入各文件，最后写入包含哈希、摘要、缺口、下一步建议的完成记录。
6. 将完成/部分完成状态提交给黑板和计划检查点。

如果模型完成并落盘后、工作流检查点提交前中断，重跑同一步会检查输入指纹和文件哈希，
匹配时复用完成记录。输入变化时重新执行；文件损坏时报错，不把损坏文件当证据。
写入多个文件不是数据库事务：完成记录是整步提交标志；失败尝试留下的文件不作为成功交接。
取消或超时记录 interrupted，恢复从步骤边界开始。模型尚未返回的 token 不提供流式落盘保证。

模型不能自行宣布存储、权限或格式错误已经修复。框架只在现有权限和预算内重试。
