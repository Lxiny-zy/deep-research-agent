# 07 — Example Plan.json

这是 Vela 平台实际生成的 plan.json（3 步调研任务），作为你复现时的格式参考。

```json
{
  "title": "2026用户态—内核态通信调研",
  "resource_plan": {
    "needs_confirmation": false,
    "max_gpu": "none",
    "max_gpu_count": 0,
    "note": ""
  },
  "steps": [
    {
      "id": "collect-current-evidence",
      "name": "检索最新证据",
      "prompt": "本步骤只做防御性、合规的证据检索与边界界定，不实现或指导隐蔽信道、无痕通信、Rootkit、EDR/审计绕过或规避检测。先读 `.claude/skills/academic-search-v2/SKILL.md`、`.claude/skills/academic-search-v2/deep-read/SKILL.md`、`.claude/skills/_shared/references/long-task-windows.md`。围绕截至 2026-08-29 的用户态—内核态通信机制，分别覆盖 Linux 与 Windows，检索 2025—2026 最新官方文档、安全公告、内核/驱动演进资料与同行评议研究；区分可审计的正规机制（如系统调用/设备接口、netlink、IOCTL、共享环形缓冲区、eBPF/perf 等类别）和高风险的未文档化滥用，但对后者只记录威胁类别、检测信号与缓解措施，不给出实现步骤、代码、规避技巧或可直接操作的接口组合。普通资料使用 WebSearch/WebFetch，抓到来源后立即增量写入 bibliography。将研究边界、系统范围、检索式、纳入/排除标准、来源日期与可信度写入 `work/user-kernel-communication-2026/explore/scope-and-method.md`；把逐条可追溯证据写入 `work/user-kernel-communication-2026/explore/source-map.md`；参考文献写入 `work/user-kernel-communication-2026/explore/bibliography.bib`。至少形成一份能独立使用的阶段性来源地图。",
      "reset": true,
      "status": "pending"
    },
    {
      "id": "compare-defensive-options",
      "name": "比较安全方案",
      "prompt": "读 `.claude/skills/research-explorer/SKILL.md`、`.claude/skills/image-gen/SKILL.md`，并读取 `work/user-kernel-communication-2026/explore/scope-and-method.md`、`source-map.md`、`bibliography.bib`。将用户所说"最为隐蔽、无痕、最低检测面"改写为可防御、可验证的工程目标：最小新增攻击面、最少不必要遥测、最小权限、可审计、稳定、版本兼容且不依赖规避检测。构建 Linux 与 Windows 分开的候选机制矩阵，至少比较权限边界、内核改动量、接口稳定性、内存安全、审计/遥测可见性、性能、部署维护、失陷影响和适用场景；所有评价须回链到上一步证据。输出 `work/user-kernel-communication-2026/explore/comparison-matrix.md`、`work/user-kernel-communication-2026/explore/recommendation-draft.md` 与 `work/user-kernel-communication-2026/explore/limitations.md`。",
      "reset": true,
      "status": "pending"
    },
    {
      "id": "deliver-defensive-report",
      "name": "交付调研报告",
      "prompt": "读取 `work/user-kernel-communication-2026/explore/` 下全部已完成证据、比较矩阵、建议、局限和合格图片。形成中文调研报告，内容覆盖最新进展、Linux/Windows 分类比较、推荐架构、迁移/部署决策树、证据等级、局限和参考文献。以 Markdown 为单一内容真源，先写 `work/user-kernel-communication-2026/explore/user-kernel-communication-2026-report.md`，再同源交付 `output/user-kernel-communication-2026/explore/user-kernel-communication-2026-report.md`、`.docx`、`.pdf`、`.html` 四份且语义一致。",
      "reset": true,
      "status": "pending"
    }
  ]
}
```

## 分析要点

1. **意图改写**：用户说"最为隐蔽/无痕/最低检测面"，Planner 将其改写为"最小新增攻击面、最少不必要遥测、最小权限、可审计"——安全重定向
2. **步数**：3 步（检索 → 比较 → 交付），符合"默认 3-8 步"规则
3. **skill 引用**：每条 prompt 都点名了具体要读的 SKILL.md 文件
4. **产物路径**：每条 prompt 都写清了输出文件路径
5. **reset: true**：所有步骤独立 context，通过磁盘产物传递
6. **安全边界**：第一条 prompt 开头就有"不实现或指导..."的合规声明
