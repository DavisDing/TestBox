# Evidence Tool

这是 `DavisDing/screenshot-to-word` 的 TestBox 插件化重构版。原项目的桌面交互被拆成两层：

- **插件层**：负责 Excel 识别、合并单元格继承、待执行项筛选、截图校验、Word 报告追加、验证点去重和 Excel 状态回写。
- **TestBox GUI 层**：负责文件选择、任务工作区、启动交互任务时隐藏主窗口，以及结果展示与导出；插件 Host 内的 Qt 会话负责截图/标注。插件本身不上传文件，也不执行外部命令。

## 已迁移能力

- 支持 `.xlsx` 与 `.xlsm`。
- 在前 15 行内自动识别表头，提供列样例、建议映射和未识别原因。
- 支持基础用例和步骤式用例。
- 支持测试名称、验证点、步骤名称、步骤描述、预期结果、测试结果。
- 正确继承测试名称、验证点的纵向合并单元格。
- 自动跳过 `已执行 / 通过 / 完成 / 成功 / passed / done` 等状态。
- 截图按待执行项顺序或明确的 Excel 行号关联。
- 对截图进行有效图片校验。
- 每个测试名称生成一个 Word；相同验证点不会重复写入。
- 可继续追加已有报告，并输出执行过的 Excel 副本。
- 所有文件都进入 TestBox 任务 `output/`；`evidence.build` 自动完成 Excel 识别，并生成 `evidence-index.json` 作为追踪索引。

## 命令

```text
testbox run evidence.build --input ./cases.xlsx --screenshots '["./1.png","./2.png"]' --row-indexes '[2,3]'

# 桌面交互模式：逐条执行、F8 截图、标注、跳过/结束
testbox run evidence.build --input ./cases.xlsx --interactive true
```

`evidence.build` 会在同一次任务中完成表头识别、列样例和映射诊断，并将这些信息写入 `evidence-index.json`。建议传入 `row_indexes`，这样截图与 Excel 行的关联不会因中间状态变化而漂移。

## 与原桌面工具的对应关系

原项目中的逐条执行面板、F8 全屏截图、截图标注、跳过/结束交互已迁移到插件 Host 的 `interactive=true` 模式。TestBox GUI 只负责启动任务、暂时隐藏主窗口并在任务结束后恢复；插件 Host 内的 Qt 窗口负责截图与标注，避免截图包含 TestBox 主窗口。无桌面环境或没有屏幕录制权限时，应改用预先提供 screenshots 的批处理模式。

## 依赖

复用 TestBox 已有依赖：`openpyxl`、`python-docx`、`Pillow`。无需新增 Python 依赖。
