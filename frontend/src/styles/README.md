# 样式维护

应用只从 `index.css` 加载样式，顺序是 foundation → components → layout → motion → pages → print。

| 文件 | 修改范围 |
| --- | --- |
| foundation.css | 设计变量、页面基础、原生控件 |
| components.css | 可复用控件、报告与执行状态组件 |
| layout.css | 工作台布局、导航、通用响应式规则 |
| motion.css | 动画与减少动画偏好 |
| pages.css | 研究、历史、设置、角色和工作流页面 |
| print.css | 打印排版，保持最后加载 |

历史的 15 份覆盖文件已合并为这 6 类，并删除了 715 条同条件、同选择器、同值的重复声明。迁移保持原声明顺序；不要通过继续追加全局覆盖文件来修正组件样式。在所属文件里修改已有规则，新组件尽量使用组件自己的类名。现有 `!important` 保留兼容作用，移除时需要检查该组件的焦点、窄屏和打印状态。

验证：`npm run build` 后，在仓库根目录执行 `python scripts/verify_workspace_ui.py`。样式迁移可以先生成 `--label before`，修改后运行 `--label after --compare before`，比较 6 个页面、两种宽度下的实际计算样式；截图存于 `artifacts/ui-workspace/`。
