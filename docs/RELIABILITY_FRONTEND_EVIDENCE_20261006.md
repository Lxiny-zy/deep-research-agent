# 科研交付与问答可靠性：前端验收记录

工作树：`D:/Cursor-edit/Project_test/deep-research-agent-upgrade`，本阶段仅本地实现和验证，未推送或部署。

## 实现与接口

- **D1**：`useRunDocument` 当前版本在窗口重新聚焦和网络重连时刷新，显式版本拥有独立缓存键。`RunPage` 仅在正文确实来自结构化快照时将 `content_version` 交给 `ReportActions`。所有报告格式提交固定版本的渲染操作；结果的 `X-Content-Version` 与回执比较，下载字节按 `X-Content-SHA256` 再核验。409 不会静默转成本地下载。交付文件始终带固定交付版本，并用报告 `source_version` 与交付 `input_version` 显示是否同源。
- **D2 前端**：交付项 `available=false` 禁用该文件的下载与预览，显示完整性错误；其他完好项仍可操作。未提供交付版本的旧登记不可隐式下载最新版本。
- **D3**：首次交付、单格式修复和所有报告导出使用持久渲染回执。浏览器按凭据身份摘要、run、操作类别保存请求，刷新或断网后恢复原 request_id 或查询原 operation_id。服务端终态错误保留为错误，只有显式再次提交才创建新请求；未知状态与网络错误不会被当作成功。收到回执后检查 run、kind、request_id。
- **U1/U2**：结构化表格标题、单元格、单位、注释和图注复用安全数学插件。bar、grouped_bar、dot、scatter、line 五种 SVG 图形只读取源表 numeric 字段；缺失值不补零，折线不跨缺失观测连线，异单位系列不混在同一轴。所有图形保留完整源数据表。表、图注和数据后备引用共用正文 `EvidencePanel`；这里只具有来源位置绑定，界面明确区分它与逐句核验绑定。缺映射引用不可点击。API 文档适配器保留 bibliography、final_validation、support_id。
- **U3**：显式离线副本文件名以 `-offline.md` 结尾，文件内包含内容源版本、核验状态、缺失范围的机器可读元数据和可读说明。缺失范围包括证据附录、来源快照、结构化图表、交付清单和文件 hash 核验。
- **Q2 前端**：普通问答和精读都可停止当前轮，调用 `POST .../requests/{request_id}/cancel` 后才断开当前流。历史保留；cancelled 是终态。只有 `recovery.available=true` 才提供“继续未完成的回答”，原 query、sources、project_id、revision_message_id 保持不变，添加 resume_message_id 并创建新 request_id；恢复范围失效的 409 不自动重试。

## 验证证据

完整前端验证：父恢复入口修正后最终 `npm test` 为 **66 文件 / 496 项通过**；`npm run build` 和 `npm run lint` 通过。额外边界覆盖跨登录身份的回执隔离、失败终态不自动新建操作、损坏文件不阻止其他完好文件，以及两页恢复后不再显示父轮重复继续入口。最终日志位于 `docs/validation/frontend-final.log`、`frontend-build-final.log`、`frontend-lint-final.log`。后端 API、数据库、渲染 worker 的验证由对应后端验收记录覆盖。

浏览器使用 playwright-cli 独立会话 `scientific-upgrade`，开发端口 `5182`。验收页使用真实生产组件；测试数据为明确标记的本地合成科学数据，恢复/取消的 HTTP 响应由浏览器受控 fixture 提供，没有请求研究模型或外部论文。开发预览路由仅在 `import.meta.env.DEV` 时注册。

| 场景 | 结果与文件 |
| --- | --- |
| 科学内容，1440 / 820 / 390 宽 | 三种尺寸均无页面横向溢出；每页 13 个公式渲染节点、5 个有效数据点、2 个源数据表。`scientific-1440.png`、`scientific-820.png`、`scientific-390.png` |
| 手机图与宽表滚动 | 可聚焦区域支持 ArrowRight；图宽 260、内容 560、末端 scrollLeft 300；表宽 258、内容 600、末端 342。清楚提示横向滑动与方向键；`scientific-390-scrolled.png` |
| 图形与证据 | 五种图形点数与源数据一致，负数保持负数、缺失不补零；Enter 打开正确原始引文，Escape 后焦点回到原引用；引用 9 无映射时不可点击。`scientific-evidence.png`、`browser-scientific.log` |
| 丢失首次导出响应 → 刷新 | 同一 request_id 重放、查询持久回执、固定版本与字节 hash 校验后下载；`delivery-lost-response.png`、`fixed-version.md`、`browser-reconnect.log` |
| 版本冲突与离线副本 | 409 明确显示；用户显式选择后下载带元数据的离线副本；`delivery-version-conflict.png`、`offline-copy.md` |
| 问答和精读停止与继续 | 每页一次 cancel，原轮保留，新 request_id 继续相同材料范围，完成后刷新不重发；`qa-stopped.png`、`qa-resumed.png`、`reader-stopped.png`、`reader-resumed.png`、`browser-qa.log` |

上述相对证据文件均位于 `artifacts/reliability-ui/`，属于本地验收产物，不作为产品源文件。

后续截图审阅修正：父轮已有继续请求时，不再展示重复的“继续未完成的回答”；依据完整会话中的子轮区分正在处理、已有后续回答、后续轮次尚未完成。错误或再次停止的子轮仍可从其可用阶段继续。两页新增 12 个状态用例，连同既有消息组件共 3 文件 / 44 项通过，TypeScript 和针对性 lint 通过；`qa-resumed.png`、`reader-resumed.png` 与 `browser-qa.log` 已重放更新，并断言完成后不存在重复继续按钮。

## 重复执行

在新工作树前端运行 `npm ci`，以 `npm run dev -- --host 127.0.0.1 --port 5182 --strictPort` 启动本地服务。创建 `artifacts/reliability-ui`，在仓库根目录执行：

```powershell
playwright-cli -s=scientific-upgrade open http://127.0.0.1:5182/preview/scientific-document
playwright-cli -s=scientific-upgrade run-code --filename=frontend/scripts/verify-scientific-ui.js
playwright-cli -s=scientific-upgrade run-code --filename=frontend/scripts/verify-delivery-reconnect.js
playwright-cli -s=scientific-upgrade run-code --filename=frontend/scripts/verify-qa-recovery.js
playwright-cli -s=scientific-upgrade close
```

脚本中的证据目录固定为本工作树路径；搬迁工作树时需对应修改。浏览器用例覆盖实际组件和受控网络失效，不能替代真实 API/worker 故障、生产数据或真实科研结论的验收。
