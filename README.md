# TestBox

TestBox 是面向测试工程师的本地化、插件化测试效能工具框架。它通过统一 CLI、任务工作区和插件 SDK，把测试数据生成、SQL 字段解析、环境检查等高频工具纳入同一套可追溯运行机制。

> 当前仓库已完成本地 CLI、桌面 GUI、插件发现与校验、Host 子进程执行、任务工作区、结构化结果/报告，以及 Data Generator、SQL Parser 和 Evidence Tool 插件。

## 文档导航

| 文档 | 用途 |
| --- | --- |
| [需求基线](./docs/REQUIREMENT.md) | 定义产品目标、用户场景、功能范围和验收标准 |
| [技术设计基线](./docs/DESIGN.md) | 定义 Runtime、CLI、插件、任务、数据流和 GUI 边界 |
| [AI 长期上下文](./docs/AI_CONTEXT.md) | 定义长期架构原则、插件规则和 Agent 协作约束 |
| [历史文档归档](./docs/archive/) | 保存已迁移的 PRD、技术设计、插件规范和 UI 规范 |

## 当前能力

- 交付本地 CLI、插件发现与运行、独立工作区、日志、结构化结果和 Markdown 报告。
- 首批 P0 插件：Data Generator、SQL Parser，以及基于字段清单生成查询语句的 SQL Select。
- 桌面 GUI 从插件 Schema 生成参数表单，并提供 Excel 用例识别、截图标注和 Word 证据报告的专用流程。
- 插件市场、AI 插件和远程执行仍不属于当前交付范围。

## 桌面端

安装桌面与证据插件依赖并启动：

```text
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[desktop]'
.venv/bin/python -m testbox.gui
```

也可执行 `testbox gui` 或安装后使用 `testbox-gui`。截图仅保存在本地任务工作区；macOS 首次截图时需要授予屏幕录制权限。

## 统一使用方式（设计目标）

```text
testbox plugin list
testbox run data.mock --count 100 --format csv --seed 10001
testbox run sql.parse --input ./schema.sql --format xlsx
testbox run sql.select --input ./workspace/<task-id>/output/<task-id>.xlsx --dialect mysql
testbox task show <task-id>
```

插件命令采用小写点分格式；每次运行由 Core 分配任务 ID，并将日志、`result.json`、报告和输出文件保存到独立工作区。实现时请优先遵循 Core 文档第 23 节与 SDK 文档第 20 节。

## 本地运行

在仓库根目录直接运行：

```text
python3 -m testbox.cli plugin list
python3 -m testbox.cli run data.mock --set count=100 --set format=csv --set seed=10001
python3 -m testbox.cli run sql.parse --set input=./schema.sql --set format=csv
```

工作区默认保存在 `workspace/<task-id>/`。测试可用 `python3 -m unittest discover -s tests` 执行。

## 插件包管理

插件包为 ZIP 文件，根目录必须直接包含 `manifest.yaml`。安装会先在临时目录完成校验，成功后才启用；卸载只接受清单名称与目录一致的已安装插件。

```text
python3 -m testbox.cli plugin package ./plugins/data-generator --output ./dist/data-generator-1.0.0.zip
python3 -m testbox.cli plugin install ./dist/data-generator-1.0.0.zip
python3 -m testbox.cli plugin uninstall data-generator
```

## Windows 使用

Windows 发行版按用途拆为**独立的 CLI 和 GUI 产品**，分别安装、分别增量更新：

| 产品 | 完整安装程序 | 增量安装程序 | 默认安装目录 |
| --- | --- | --- | --- |
| CLI | `TestBox-CLI-Install-vX.Y.Z.exe` | `TestBox-CLI-Setup-vX.Y.Z.exe` | `%LOCALAPPDATA%\Programs\TestBox CLI` |
| GUI | `TestBox-GUI-Install-vX.Y.Z.exe` | `TestBox-GUI-Setup-vX.Y.Z.exe` | `%LOCALAPPDATA%\Programs\TestBox GUI` |

CLI 包不携带 PySide6/Qt 桌面运行时，适合命令行任务；GUI 包单独携带桌面端。两者可同时安装，并共享 `%LOCALAPPDATA%\TestBox\` 下的用户插件、工作区和任务历史；卸载任一产品不会删除这些用户数据。

```powershell
# CLI
& "$env:LOCALAPPDATA\Programs\TestBox CLI\TestBox\TestBox.exe" plugin list
& "$env:LOCALAPPDATA\Programs\TestBox CLI\TestBox\TestBox.exe" run data.mock --count 100 --format csv --seed 10001

# GUI
& "$env:LOCALAPPDATA\Programs\TestBox GUI\TestBox-GUI\TestBox-GUI.exe"
```

冻结的 CLI 安装包不包含桌面端，因此 `TestBox.exe gui` 会提示安装 GUI 包；从源码或普通 Python 安装运行该子命令仍可启动桌面端。CLI 保留官方插件和 Evidence Tool 的非交互处理能力；需要 Evidence Tool 的交互式截图选择等 Qt 功能时，请使用 GUI 包。

每个产品的 Release 资产各自包含版本化更新 ZIP 和 manifest：

```text
TestBox-CLI-update-vX.Y.Z.zip
TestBox-CLI-update-manifest.json
TestBox-GUI-update-vX.Y.Z.zip
TestBox-GUI-update-manifest.json
```

增量更新只接受同一产品、对应上一正式版本的 manifest；安装目录和更新清单隔离，避免 GUI 更新删除 CLI 文件（或反向删除）。旧的“CLI + GUI 合包”不能直接用新分包增量更新；请下载对应的新版完整安装程序迁移。新安装器不会删除旧安装目录或 `%LOCALAPPDATA%\TestBox\` 用户数据。

CLI 和 GUI 安装目录分别包含 `TestBox-CLI-Updater.exe`、`TestBox-GUI-Updater.exe`。它们会按自身产品自动下载对应 manifest，校验 SHA-256 后仅替换受管文件。手动更新示例：

```text
TestBox-CLI-Updater.exe --manifest-url https://github.com/DavisDing/TestBox/releases/latest/download/TestBox-CLI-update-manifest.json
TestBox-GUI-Updater.exe --manifest-url https://github.com/DavisDing/TestBox/releases/latest/download/TestBox-GUI-update-manifest.json
```

发布的独立 `data-generator`、`sql-parser`、`sql-select` 与 `evidence-tool` ZIP 是插件包，可额外安装或覆盖升级插件；它们不与 Windows 程序合并为同一个下载文件。Windows 发行版使用 PyInstaller `onedir`，用户应运行 EXE 安装程序而不是直接解压绿色包。

GitHub Actions 发布规则：每次推送到 `main` 或 `master`，都会自动创建一个正式 Release。工作流以最新 `vX.Y.Z` 标签为基准将补丁版本加 `0.0.1`，自动同步 `pyproject.toml`、运行时版本和四个 Windows 安装器版本、创建对应标签并发布构建产物。首次自动发布使用仓库声明的版本号。Pull Request 只执行验证，不会发布 Release。

## 任务历史与清理

任务状态和摘要保存于 `workspace/task_history.sqlite3`。查看任务或删除指定日期前的工作区：

```text
testbox task show <task-id>
testbox workspace clean --before 2026-01-01 --confirm
```

## Data Generator 规则

`data.mock` 支持默认个人信息、客户/账户/产品/交易金融模板，以及 `rules`、规则集、SQL DDL、Excel 字段清单驱动的字段规则。字段唯一性由 `unique` 显式控制，只保证本次任务内不重复；地址可按全国、省、市筛选，手机号使用常用中国大陆号段。

数据可输出 JSON、CSV、XLSX、TXT、SQL 或 ZIP 包；SQL 输出支持 MySQL、PostgreSQL、SQL Server、Oracle、SQLite 的批量 `INSERT` 与安全字面量转义。

Data Generator 的大陆省市区县街道及港澳台数据引用 [modood/Administrative-divisions-of-China](https://github.com/modood/Administrative-divisions-of-China/tree/c49d495b40ac73eb1a66f6eeae5f8fd10696f035)，上游许可证为 WTFPL-2.0；固定版本、许可证和校验值保留在插件资源目录。
