# Evidence Tool

Evidence Tool 将 Excel 测试用例与截图组合为 Word 测试证据报告，并通过 TestBox 任务工作区保留输入摘要、日志和输出。

它使用 TestBox 标准运行依赖（`openpyxl`、`python-docx`、Pillow），因此通过 `pip install testbox` 或 `pip install -e .` 安装 TestBox 后即可执行；无需在运行时安装插件依赖。由于会回写 Excel 和续写 Word，Core 会串行执行该插件。

## 命令

- `evidence.inspect`：自动识别前 15 行内的表头，兼容中英文别名、合并单元格和已执行状态，输出待执行项 JSON。
- `evidence.build`：截图通过 Excel 行号稳定关联；每个测试名称生成或追加一个 Word，验证点只写一次，并可输出已回写状态的 Excel 副本。

```text
testbox run evidence.inspect --input ./cases.xlsx
testbox run evidence.build --input ./cases.xlsx --screenshots '["./1.png","./2.png"]' --row-indexes '[2,3]'
```

桌面端使用 `python -m testbox.gui` 启动，可完成列映射、逐项执行、跳过/结束、F8 截图、完整标注、Excel 状态回写和 Word 报告续写。系统截图权限由操作系统管理；TestBox 不上传图片或用例。
