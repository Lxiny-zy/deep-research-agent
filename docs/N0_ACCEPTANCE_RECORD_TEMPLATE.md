# 人工验收记录模板

本模板与 N0 API 配套，工程验证不代替用户对新产物的人工评价。没有评价的项目保持“待人工验收”。

| 记录项 | 填写内容 |
| --- | --- |
| 任务 ID / 验收请求 ID | 任务 ID：；本次验收 request_id： |
| 应用版本依据 | package_version：；package_code_sha256：；revision（未提供时填未知）： |
| 输入标识 | input_id：；source_version： |
| 文档 / 交付版本 | document_version：；include_hsi_tables：；delivery_version（可选）： |
| 本次性质 | 首次登记 initial / 恢复观察 recovery；父记录 ID： |
| 首次实际结果 | 未记录 unknown / done / needs_review / error / cancelled；由人工报告，不从当前成功反推 |
| 首次登记观察 / 本次观察 | API 自动保存的 initial_status：；observed_status：；recovery_status： |
| 任务要求 | requirement_id：；要求说明： |
| 问题位置 | location_id：；正文行/表格行列/图形 ID： |
| 明确选择的问题片段 | 仅贴该位置中必要的原文，最多 1,200 字符： |
| 原文或数据依据 | evidence_id：；source_id：；来源快照 hash / 页段 locator：；选择的依据片段： |
| 人工观察 | 具体发现：；结论 pending/pass/fail/uncertain： |
| 后续任务 | N1–N11：；todo_id：；预期改动： |

后续任务默认沿用 [主计划](IMPROVEMENT_PLAN.md)：课题调研 N1、综述 N2、评审 N3、精读 N4、数据分析 N5、幻灯片 N6、
导图 N7、上下文 N8、交互/登录态 N9、调用或性能 N10、布局阅读 N11。用户可以明确调整该分类。

## 实际使用路径

所有路径都沿用现有身份认证和运行归属校验。写入需要现有研究/管理权限。

1. `GET /api/runs/{run_id}/document`：取得 `content_version`。选择 HSI 表格时，此处与后续请求
   一致传 `include_hsi_tables=true`。
2. `GET /api/runs/{run_id}/acceptance/template`：取得完整请求 schema、默认模板和分类。
3. `GET /api/runs/{run_id}/acceptance/context?version={content_version}`：取得已有要求、正文位置、
   来源标识及核验记录的关联。`not_checked` 不等于通过；顶层 `coverage_review_bound` 与
   `prose_review_bound` 仅表示相应核验记录与版本匹配，还需分别查看 `coverage_issues` / `prose_review_issues`。
   材料和覆盖状态分别呈现，不能把未读取等同于论文未报告。
4. 根据选中的 ID，读取 `/acceptance/locations/{location_id}?version=...` 或
   `/acceptance/evidence/{evidence_id}?version=...`。两者支持 offset/length，单次最多 1,200 字符。
5. `POST /api/runs/{run_id}/acceptance/records` 保存下方 JSON。
6. `GET /api/runs/{run_id}/acceptance/records/{record_id}` 查看冻结记录；末尾追加 `/package`
   下载可复现问题包。`GET /acceptance/records?limit=50&after={cursor}` 分页查看历史。

```json
{
  "request_id": "manual-review-001",
  "document_version": "从 document.content_version 复制",
  "include_hsi_tables": false,
  "phase": "initial",
  "first_attempt_status": "unknown",
  "conclusion": "pending",
  "issues": [
    {
      "location_id": "从 acceptance/context 选择",
      "requirement_ids": [],
      "source_ids": [],
      "excerpt": "从指定位置复制的必要问题片段",
      "evidence_selections": [
        {"evidence_id": "选择的证据编号", "excerpt": "原文中必要的依据片段"}
      ],
      "category": "correctness",
      "observation": "说明问题，不贴全文、全对话或凭据",
      "conclusion": "pending",
      "todo_id": "N1-review-001"
    }
  ]
}
```

恢复观察必须新建 request_id，设置 `phase=recovery` 并引用已有 `parent_record_id`。
父记录及首次实际结果不会被恢复成功覆盖。首次实际结果没有可靠记录时，保持 `unknown`；
`initial_status` 只是首次登记时的运行状态，不伪称完整执行历史。

文档变化后，旧 version 的新记录或新位置查询返回 409。已有记录、相同请求的重放和已保存问题包
继续返回原先明确选择的片段和原版本，不从新稿重新生成旧记录。原始任务和正式质量门不被人工记录改写。

问题包不默认包含完整正文、私人来源全文、全对话、模型思考或配置；只包含版本标识、所选位置、
用户输入的观察及主动选择的片段。凭据形态文本与当前认证值在选择文本中被遮盖，URL 用户信息及查询参数被剥离；
若发生遮盖，`excerpt_redacted` 标记为 true，原始片段 hash 仍用于定位，不能把遮盖后的文字当逐字原文。

`revision=null` 表示部署没有提供 `DR_APP_REVISION`。`package_code_sha256` 是当前安装 Python 包源码的
指纹，不冒充 Git commit，也不代表前端构建版本；部署需精确关联 commit 时可注入非秘密的 `DR_APP_REVISION`。

当前后端 API 与业务页面入口均已上线，版本和验证结果见 [发布记录](RELEASE.md)，页面使用说明见
[研究工作台](RESEARCH_WORKBENCH.md)。登记操作不触发新模型调用，不改变正式核验结果；
新产物的事实质量与阅读体验仍由用户逐项评价，未评价的项目保持“待人工验收”。
