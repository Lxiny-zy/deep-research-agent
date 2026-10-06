# N7 Linux 节点标识检查修复

2026-10-06，发布前 Linux 实际渲染验证。

原镜像 `deep-research-agent:release-de0e0c642897` 的分支 PNG 导出被节点完整性检查拒绝。复现显示 PDF 中全部节点存在，但 Noto CJK/PyMuPDF 把标题空格提取为 NBSP（`U+00A0`），旧逻辑只匹配普通 ASCII 空格。

最小修复位于 `mindmap_delivery.py`：按行首“节点＋空白＋完整数字路径＋空白＋中点”解析标题集合，兼容 Unicode 空白。全部必需路径仍必须存在；`0.10` 不会满足 `0.1`。每页 `page_node_starts` 使用相同结果，正文中的普通提及不冒充标题。没有删除节点、跳过完整性门或改变科学核验策略。

在原 Linux 镜像中仅挂载修复后的模块，使用原容量夹具实际生成 **28 节点、9 张 PNG，10.69 秒**。所有分支页节点集合与索引完全相等。已查看第一分支两页图片，中文、分式 `1/2`、完整说明及跨分支链接均可读。不同平台字体造成分页数不同，节点内容和索引仍完整。

本地受影响回归 **11 passed**，Ruff 和该模块 mypy 通过。新增回归覆盖 NBSP、换行空白、`0.1/0.10` 边界与非标题文本。

证据：

- [Linux 输出清单、SHA-256 与节点元数据](validation/n7-linux-20261006/summary.json)
- [Linux 渲染日志](validation/n7-linux-20261006/render-log.txt)
- [第一分支第一页](validation/n7-linux-20261006/sample-mindmap-b001-p001.png)
- [第一分支第二页](validation/n7-linux-20261006/sample-mindmap-b001-p002.png)
- [字体警告分阶段诊断](validation/n7-linux-20261006/font-diagnostic.txt)

`Reserved charstring byte c=0x0` 警告单独定位到既有 `Document.subset_fonts()` 阶段；最小中文 PDF 随后成功返回，约 20 MB，表明字体子集化未能缩小 Noto CFF 字体。当前 PNG 视觉和节点校验通过。此次没有改字体代码或屏蔽警告，最终 1 GiB 容量复测仍需覆盖这部分中间 PDF 开销；主任务负责新镜像与完整应用容量复测。原失败容量容器保留。
