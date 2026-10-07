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
.venv/bin/python -m pip install -e '.[desktop,evidence]'
.venv/bin/python -m testbox.gui
```

也可执行 `testbox gui` 或安装后使用 `testbox-gui`。截图仅保存在本地任务工作区；macOS 首次截图时需要授予屏幕录制权限。

## Python 发行包安装

在新虚拟环境中安装实际下载的 wheel（将文件名替换为目标版本）；仅 CLI 不需要桌面依赖：

```text
python -m pip install ./testbox-X.Y.Z-py3-none-any.whl
# GUI 和证据流程
python -m pip install './testbox-X.Y.Z-py3-none-any.whl[desktop,evidence]'
testbox --json plugin list
```

wheel 包含九个官方插件（十一个命令）及其 Schema、配置、固定数据和 GUI 图标，可以从任意工作目录启动。任务与用户插件不写入 Python 安装目录：Windows 使用 `%LOCALAPPDATA%\TestBox`，其他平台使用 `${XDG_DATA_HOME:-~/.local/share}/testbox`。源码/可编辑安装仍使用源码项目的 `plugins/` 和 `workspace/`；显式 `Runtime(root)` 保持独立目录行为。

安装后验证可在源码仓库执行以下脚本，`--python` 应指向刚创建的虚拟环境（Windows 为 `.venv\Scripts\python.exe`）：

```text
python scripts/smoke_installed.py --python /path/to/clean/venv/bin/python
# 已安装 desktop/evidence extra 时
python scripts/smoke_installed.py --python /path/to/clean/venv/bin/python --gui --evidence
```

脚本从空工作目录和隔离子进程启动，使用临时用户数据；GUI 为 offscreen 验证，Evidence 使用合成 Excel/图片，不代替真实屏幕录制权限或 Windows 安装器验收。

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

源码运行的工作区默认保存在 `workspace/<task-id>/`。测试可用 `python3 -m unittest discover -s tests` 执行。

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

CLI 包不携带 PySide6/Qt 桌面运行时，适合命令行任务；GUI 包单独携带桌面端。两者可同时安装，并共享 `%LOCALAPPDATA%\TestBox\` 下的用户插件、工作区和任务历史；卸载任一产品不会删除这些用户数据。安装目录不得与共享用户数据或另一产品重叠；增量安装失败会显示更新器诊断并停止，更新器会尝试恢复旧文件。为避免旧版卸载规则误删用户文件，完整重装会重置卸载日志；未登记的旧文件可能保留，不会自动递归清理。

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

发布的独立 `data-generator`、`sql-parser`、`sql-select`、`evidence-tool`、`data-preview`、`data-compare`、`schema-diff`、`data-check` 与 `office-convert` ZIP 是插件包，可额外安装或覆盖升级插件；它们不与 Windows 程序合并为同一个下载文件。Windows 发行版使用 PyInstaller `onedir`，用户应运行 EXE 安装程序而不是直接解压绿色包。

GitHub Actions 发布规则：每次推送到 `main` 或 `master`，都会自动创建一个正式 Release。工作流以最新 `vX.Y.Z` 标签为基准将补丁版本加 `0.0.1`，自动同步 `pyproject.toml`、运行时版本和四个 Windows 安装器版本、创建对应标签并发布构建产物。首次自动发布使用仓库声明的版本号。Pull Request 只执行验证，不会发布 Release。

### Windows 原生安装器验收

CI 在编译完整安装器后调用 `scripts/smoke_windows_installers.py`，执行安装、运行、合成增量更新、锁文件故障回滚、重装和卸载，并检查用户数据、未登记文件及另一组件未被破坏。失败时保留进程/Inno 日志和 `summary.json` 到 `testbox-windows-installer-smoke` 构建产物。

该入口**仅允许无既有 TestBox 注册的 GitHub-hosted Windows 临时 runner**，拒绝本机和 self-hosted runner。它不删除工作区或按进程名称杀进程；超时仅终止本次启动的进程树，退出时只卸载路径仍与本次测试一致的组件。合成增量包验证机制，不代替相邻正式版本兼容性；未实际执行流水线前不能宣称 Windows 原生验收通过。

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

## 多格式解析预览、数据比对、结构比对与质量检查

GUI 选择 `data.compare`、`sql.diff`、`data.check` 或 `data.preview` 后，先导入文件（结构比对也可粘贴多行 SQL），在“导入与解析预览”调整编码、列分隔符、记录分隔符、表头、Excel Sheet、JSON 数据路径。点击相应侧的解析预览；调整后旧预览失效，再次点击重跑。解析配置同步到正式任务，预览与比对使用相同解析器。预览产生独立可追溯任务，只展示前20行，但解析校验基于全部有界输入。

- `data.preview`：CSV/TSV、分隔TXT、JSON/JSONL、XLSX/XLSM解析与标准化样本。
- `data.compare`：位置、单/组合主键、无序多重集合比对；列映射、忽略列、严格类型与显式数值容差；输出JSON/CSV/Markdown。
- `sql.preview` / `sql.diff`：有验证覆盖的CREATE TABLE、单表SELECT投影、显式列INSERT，以及结构清单；未知/不支持信息不可当成一致。不执行SQL。
- `data.check`：必填、组合唯一、类型、枚举、范围、长度、跨字段比较、行数；错误/警告分级，声明式规则，不自动回写。

```sh
# TXT：多字符列分隔符、自定义记录分隔符
python -m testbox.cli run data.preview --set input=sample.txt --set 'options={"format":"txt","delimiter":"||","record_separator":"<EOR>"}'

# CSV 与 JSON 按业务主键比对，显式转换 id 类型
python -m testbox.cli run data.compare --set left=expected.csv --set right=actual.json --set mode=key --set 'keys=["id"]' --set 'left_normalize={"types":{"id":"integer"}}'

# 直接粘贴 SQL 的结构比对（SELECT 未声明类型/约束时结论可为 inconclusive）
python -m testbox.cli run sql.diff --set 'left_text=CREATE TABLE t (id INTEGER);' --set 'right_text=SELECT id FROM t;'

# 质量检查
python -m testbox.cli run data.check --set input=actual.json --set 'rules=[{"type":"required","fields":["id"]},{"type":"unique","fields":["id"]}]'
```

`success` 表示任务完成，不代表业务数据一致或质量达标；分别看 `equal`、`verdict`、`passed`。非法解析/超限直接失败；明细截断不改变全量计数。空白记录默认按共享解析配置跳过，可显式关闭；JSON缺失字段保持缺失。所有输入原文件不变，预览与报告可能包含原始值，日志脱敏不等于产物自动脱敏。

Excel需要已有可选openpyxl（evidence extra）；不重新计算公式，缓存值缺失会告警。旧XLS、固定宽TXT、目录/多Sheet批量、任意日期格式、完整复杂SQL/跨方言语义等价尚未支持。完整参数、规则和边界见各插件README。

## 旧版 Office 格式转换

独立插件 `office-convert`：`.xls → .xlsx`、`.doc → .docx`、`.ppt → .pptx`。GUI选择 `office.convert`，在“单文件”或“批量文件”中选一种，混合旧格式可以放在同一批任务。输出写入任务 `output/converted/`，编号避免同名来源覆盖；原文件不变，需通过任务导出保存到目标位置。

```sh
python -m testbox.cli --json run office.inspect
python -m testbox.cli --json run office.convert --set input=legacy.xls
python -m testbox.cli --json run office.convert --set 'inputs=["legacy.xls","legacy.doc","slides.ppt"]' --set continue_on_error=true
```

需要本机已有可用 LibreOffice/soffice，不随TestBox打包、不自动安装。先运行 `office.inspect` 检查。找不到引擎时可在项目/用户 `config.yaml` 设置可信的 `soffice_path`，或环境变量 `TESTBOX_OFFICE_CONVERT_SOFFICE_PATH`；不通过CLI输入任意外部命令或参数。

每文件默认60秒、批次总时限240秒；最多100个文件，单文件50MiB、累计输入100MiB；单产物100MiB、整批转换产物400MiB。默认遇错继续，关闭后未处理项标为skipped。部分成功保留成功产物并报告不完整；查看 `data.summary` 的 complete、succeeded_count、failed_count、skipped_count（也保留data顶层摘要），不只看任务status。全部失败不会报告成功。

仅接受旧Office容器，不靠改扩展名转换。输出验证相应OOXML结构。加密/损坏、不支持格式、引擎异常或未产出均明确失败。宏不迁移；公式、排版、字体、图形与嵌入对象可能因转换器而变化，关键文档仍需人工核验。不驱动用户正在运行的Microsoft Office，不修改用户Office配置；独立配置/禁宏外链措施不是恶意文档安全沙箱，请仅转换可信本地文件。详见插件README。
