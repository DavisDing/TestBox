# TestBox AI 长期上下文

> 所有 AI Agent 开始工作前读取。
>
> 这里只记录长期有效的项目事实、原则和边界，不记录临时任务过程。

## 1. 项目定位

TestBox 是面向测试工程师的本地化、插件化测试效能工具。它通过统一 CLI、可选桌面 GUI、任务工作区和插件 SDK，提供测试数据生成、SQL 字段解析、测试证据等能力。

它是本地工具，不是云平台、协作平台或远程执行平台。

## 2. 核心开发理念

- 简单优先。
- 实用优先。
- 先验证完整闭环，再扩展能力。
- 优先复用现有 Runtime、SDK、Schema 和工作区机制。
- 不为了“企业级”引入微服务、消息队列、Kubernetes 或不必要的数据库服务。
- 不把历史文档或当前实现未经判断地当成最终设计。
- 不编造不存在的 API、服务、表、字段或完成状态。

## 3. 技术栈与运行形态

- Python `>=3.11`。
- CLI 当前使用标准库 `argparse`。
- GUI 使用可选 PySide6。
- 本地任务历史使用 SQLite。
- 插件通过 `manifest.yaml` 发现和校验。
- 插件由 Plugin Host 子进程加载和执行。
- 插件包为 ZIP；安装前在临时目录校验。
- Windows 使用 PyInstaller 构建可执行文件；GUI 保持 `windowed` 单 EXE，冻结 GUI 的 Host 模式通过临时 UTF-8 请求/响应文件通信，避免 Windows GUI 子系统的 stdout 管道问题。
- 测试主要使用 Python `unittest`。GUI 测试仅在缺少可选 PySide6 时跳过；其他导入错误仍失败，安装 desktop extra 的环境继续执行 GUI 测试。CLI 发行测试不为收集 GUI 测试而强制安装 Qt。

`pyproject.toml` 中的依赖声明不等于代码一定使用；修改依赖前应检查实际导入和发行构建。

## 4. 重要目录

```text
TestBox/
├── testbox/           # Core、CLI、GUI、SDK
├── plugins/           # 本地插件
├── tests/             # 集成测试
├── workspace/         # 运行时任务数据，不是业务源码
└── docs/              # 三个 AI 核心文档和 archive/
```

AI 后续开发的主要知识源是：

1. `docs/REQUIREMENT.md`：做什么。
2. `docs/DESIGN.md`：怎么实现。
3. `docs/AI_CONTEXT.md`：长期记住什么。
4. 当前代码和测试：验证实际事实。

`docs/archive/` 只保存已迁移的历史资料，不作为默认设计依据。

## 5. 核心架构原则

- CLI 和 GUI 必须复用同一个 Runtime；GUI 不能复制插件执行逻辑。
- Runtime 负责插件发现、Schema 参数校验、文件暂存、任务状态、Host 调度、结果校验和工作区落盘。
- 插件只负责自身业务逻辑和输出文件。
- 插件之间不得直接依赖；下游插件通过任务工作区中的已登记输出文件消费上游产物。
- 插件只能通过 SDK 的 Context、Result 和 PluginError 使用 Core 能力。
- 任务工作区是可追溯性的基本边界：输入、输出、日志、manifest、result 和 report 都围绕任务 ID 保存。
- 当前 Host 协议是一次 JSON 请求/一次 JSON 结果响应。不要在没有需求确认前假设存在实时进度、心跳或取消事件。
- Plugin Host 是异常隔离，不是恶意代码安全沙箱；本地插件默认视为可信代码。

## 6. Core Runtime 契约

Runtime 是 CLI 与 GUI 共用的唯一业务 Facade，内部职责保持以下边界：

- `PluginManager`：发现插件、校验 manifest、建立命令索引并记录不可用原因。
- `SchemaValidator`：读取命令 Schema、应用默认值、校验类型/必填项/范围/未知参数，并返回结构化校验错误。
- `WorkspaceManager`：创建任务工作区，暂存文件输入，校验输出路径和大小，并负责用户主动导出。
- `ProcessRunner`：启动 Plugin Host 子进程，处理一次请求/一次响应、超时、协议错误和异常退出。
- `TaskHistory`：使用 SQLite 保存任务历史，维护 schema version、索引和 `ABANDONED` 任务回收。
- `Report Writer`：原子写入 `result.json`，并生成可读的 `report.md`。

运行时根目录规则：显式传入 `root` 时用于测试和嵌入场景；源码/可编辑安装从任意工作目录启动时，会回退到包含 bundled plugins 的项目根目录；冻结构建则使用可执行文件旁的 bundled plugins，并把用户安装插件和任务工作区放到用户数据目录。

Runtime 对外稳定入口包括：

```text
list_plugins()
list_unavailable_plugins()
list_commands()
get_command(command)
get_command_schema(command)
inspect_plugin(identifier)
validate_plugin(source)
preview_plugin_install(source)
package_plugin(source, destination)
install_plugin(source, force)
uninstall_plugin(name)
get_runtime_diagnostics()
validate_params(command, params)
run(command, params)
get_task(task_id)
get_task_result(task_id)
get_task_report(task_id)
get_task_log(task_id, max_chars)
list_tasks(status, command, task_id_query, started_from, started_before, limit, offset)
count_tasks(status, command, task_id_query, started_from, started_before)
clean_workspace(before)
clean_history(before)
commit_output(task_id, relative_path, destination)
commit_outputs_archive(task_id, destination)
```

`execute(command, params)` 仅作为旧 GUI 调用的兼容别名；新代码优先使用 `run`。任务状态统一使用 `PENDING`、`RUNNING`、`SUCCEEDED`、`FAILED`、`CANCELLED`、`ABANDONED`。

`clean_workspace(before)` 与 `clean_history(before)` 将用户选择的日期解释为系统本地时区的当天零点，再转换为 UTC 边界执行清理，避免本地日期与 UTC 任务 ID/时间戳混用。

CLI 已形成稳定的第二阶段命令面：`plugin inspect`、`task list`、`task result`、`task export`，并支持顶层 `--json`。CLI 只能调用 Runtime，不得自行读取 SQLite、插件目录或任务结果文件。稳定退出码为：参数/用法 `2`、插件管理 `3`、任务失败 `4`、取消 `130`。

Runtime 只将脱敏参数写入任务清单和 SQLite。执行请求仍可携带插件运行所需的原始配置，但原始配置不得写入日志、报告、`manifest.json`、`result.json` 或任务历史。

参数校验返回深拷贝后的参数与默认值，成功和失败均不修改调用方嵌套输入或 Schema 默认对象；对象/数组默认值递归应用，并支持显式 nullable 类型列表。空值仍需满足类型与 enum 约束；补默认值后的完整参数树拒绝 NaN/Infinity，包含 `additionalProperties: true`、无 items 定义的数组及空 Schema 下的自由内容。普通有限数值、null、布尔和字符串不受此检查影响。先执行已有 Schema 校验以保留声明字段的错误反馈，再检查开放值；该检查不扫描输入文件正文、插件配置或产物，也不等于完整 JSON Schema 支持。

诊断脱敏统一复用 `core.redaction.Redactor`，根据原始参数与配置中的敏感键收集值，处理日志、异常、结果摘要与 GUI 参数展示；协议身份、任务状态与产物路径不能被通用文本替换改写。此机制不是插件任意输出文件的内容扫描，也不构成恶意代码沙箱。

任务历史 schema v2 在 v1 上增量增加可空 `owner_pid`，保留已有记录；Runtime 显式记录拥有者 PID。恢复在 Host 未启动时检查 owner，Host 已启动时检查 Host，并用非阻塞任务生命周期锁保护结果收尾窗口。PID 存活检查不识别 PID 复用。工作区清理跳过被锁定任务、PENDING/RUNNING 和未知状态，历史清理只删除明确终态。

业务结果先落盘再同步历史；历史写入失败进行一次有界重试，保留原状态和产物列表，并返回可见警告。持续失败可能暂时留下未同步历史，不应宣称数据库已成功更新。

任务日志展示在有界读取后先将 CRLF/CR 统一为 LF，再按字符数截取尾部；不改写原始日志文件。

任务日志上限为 1 MiB，Host 响应上限为 8 MiB，stderr 保留最后 64 KiB。文件输入采用分块暂存与哈希，100 MiB 限制按当前任务累计计算；登记产物总量仍限 500 MiB。插件 ZIP 解压限制条目数、单项和总量，并拒绝链接与非法路径；覆盖激活失败先恢复旧目录，回滚失败则保留恢复材料，不删除旧插件。

### 6.1 Python 发行包与运行目录

- setuptools 的 `testbox_build.BundledBuildPy` 在构建输出中将官方 `plugins/` 资源装配到 `testbox/_bundled_plugins`；源码插件仍是唯一资源来源，构建不生成源码副本。`MANIFEST.in` 保留 sdist 重建所需的构建钩子和插件资源；wheel 不包含插件测试、缓存或运行工作区。
- wheel 从安装目录加载官方插件、Schema、配置、固定数据和图标，忽略无关 cwd 的插件目录。任务与用户插件放在 `_user_data_dir()`：Windows 为 `%LOCALAPPDATA%/TestBox`，其余平台为 `${XDG_DATA_HOME:-~/.local/share}/testbox`，不写入 site-packages 的任务目录。
- 源码/可编辑安装及显式 `Runtime(root)` 的目录语义不变；冻结程序继续从 `_MEIPASS/plugins` 读取官方资源。同名用户插件可覆盖官方插件；卸载用户副本后官方插件重新可用，不允许卸载官方资源。
- 安装后验证入口为 `scripts/smoke_installed.py --python <目标虚拟环境Python>`；`--gui` 和 `--evidence` 要求相应可选依赖，使用空 cwd、隔离子进程与临时用户数据。offscreen GUI 和合成证据测试不能替代真实系统权限、截图或安装器实机验证。

## 7. 当前插件

- `data-generator`：命令 `data.mock`，生成可复现模拟数据，包含规则化输入和固定第三方行政区划资源。
- `sql-parser`：命令 `sql.parse`，解析 SQL DDL 文本，不执行 SQL；输出文件名为 `<task-id>.<format>`。
- `sql-select`：命令 `sql.select`，消费 `sql.parse` 生成的 JSON、CSV、XLSX 字段清单并生成 SELECT 文本。
- `evidence-tool`：命令 `evidence.build`，在一次任务内完成 Excel 识别、截图关联、Word 报告生成和 Excel 状态回写；支持批处理和桌面交互模式，依赖部分可选库。

新增插件必须提供清单、入口、命令 Schema、README 和必要测试。插件命令使用小写点分格式。

## 8. 插件长期规则

- 生命周期为 `init → execute → destroy`；无论成功或失败都应尽力执行 `destroy`。
- 使用 `context.logger`，不用 `print()`。
- 不调用 `sys.exit()` 结束插件任务；用户可修复错误使用 `PluginError`。
- 只能向任务 `output/` 写入并通过 SDK 登记输出文件。
- 返回 `Result`，大结果写文件，小摘要放 `data`。
- SDK `SafeFiles.write_text` 保留调用方指定的换行符，不进行操作系统默认换行转换；CSV 记录分隔符和引号内换行保持原样。声明精确换行符的文本测试夹具应使用 `newline=""` 或字节写入。
- 输出文件路径必须是相对路径，不得包含绝对路径或 `..`。
- 通过 Schema 描述输入，业务层仍需做范围和资源校验。
- 不把密码、令牌、连接串、真实个人信息放入样例、日志、测试夹具或报告。
- 数据生成必须可复现；唯一性仅在用户显式声明且当前任务范围内保证。
- 证件/身份类模拟内容必须带 `TEST DATA ONLY` / `测试数据` 标识，不能用于真实认证。
- SQL Parser 只解析文本；任何插件都不得静默执行用户输入的 SQL、脚本或外部命令。旧版 Office 转换已移除，当前官方插件无 LibreOffice 执行例外。

## 9. UI 长期规则

- UI 是 Runtime 的展示层，不是第二套业务层。
- 保护的是 Runtime/SDK/Result 契约，不是当前 GUI 的布局、颜色或组件。
- 当前旧 UI 可以整体重做。
- 推荐以“工具目录 → 执行工作区 → 任务详情/历史”为主路径。
- 每个任务都要有 Loading、Empty、Success、Warning、Error、Cancelled 等明确状态；状态不能只用颜色表达。
- 复杂参数由 Schema 驱动；高级参数和原始日志按需展开。
- GUI 必须展示任务 ID、插件版本、脱敏参数、输出文件和错误建议。
- UI Agent 不应在 GUI 中重新实现文件写入、任务状态机或插件业务。
- GUI 的任务历史筛选、插件可用性与 Schema 问题、Runtime 路径和版本诊断均通过 Runtime 只读接口获取，不直连 SQLite 或自行扫描插件目录。
- GUI 安装插件前必须通过 `preview_plugin_install(source)` 使用与正式安装一致的包校验逻辑展示预览；预览不能改变用户插件目录，覆盖和卸载仍需明确确认。

## 9.1 Windows 发布与自动版本

- Windows 使用 PyInstaller `onedir` 分别构建 CLI 与 GUI：`TestBox-CLI-Install-vX.Y.Z.exe` / `TestBox-CLI-Setup-vX.Y.Z.exe` 安装到 `%LOCALAPPDATA%\Programs\TestBox CLI`；`TestBox-GUI-Install-vX.Y.Z.exe` / `TestBox-GUI-Setup-vX.Y.Z.exe` 安装到 `%LOCALAPPDATA%\Programs\TestBox GUI`。两者各有独立 updater、更新 ZIP 与 manifest，不能共用安装根目录。
- CLI 包排除 PySide6 和 `testbox.gui`；GUI 包仅显式引入实际使用的 Qt Core/Gui/Widgets/Svg 模块，避免收集不需要的 Qt WebEngine、QML、3D 等组件。两产品共享 `%LOCALAPPDATA%\TestBox` 中的用户数据，但卸载或更新不会管理该目录。
- 更新 manifest schema 为 2，并含 `component`（`cli` 或 `gui`）；更新器会拒绝跨组件更新。旧 schema 1 的合包更新不能升级分包安装，应运行相应完整安装器迁移。
- 更新器先校验 Windows 安全路径、文件清单、ZIP 内容/哈希、基线版本与组件身份；网络清单必须与包内清单一致。拒绝链接/junction 目标和覆盖/删除未登记的用户文件。全部备份完成后才修改文件；失败尽力回滚，回滚失败保留恢复材料并暴露路径。
- 完整安装器拒绝与共享用户数据及另一组件安装路径重叠（含短文件名及链接路径保护），卸载只管理本次 Inno 已登记文件，不递归删除整个安装根目录。完整重装覆盖旧卸载日志以丢弃历史递归删除规则，因此不在新包中的旧文件可能保留，不做无清单清理。增量安装器等待更新器退出并检查返回码，将 UTF-8 诊断作为失败原因，不能把更新失败显示成安装成功。Windows 原生编译和实机安装/升级/卸载仍需目标平台验证。
- `scripts/smoke_windows_installers.py` 是原生安装器验收入口，只允许无现有 TestBox 注册的 GitHub-hosted Windows 临时 runner；要求新的 `RUNNER_TEMP` 子目录。实际编译并执行完整安装器、合成增量安装器及卸载程序，校验 CLI/GUI Host、路径冲突、组件/base/hash 拒绝、锁文件回滚、用户数据/未登记文件保留和任务结果读取。日志与 JSON 摘要由 CI `always()` 上传；合成 delta 不代表相邻正式版本兼容性，配置存在不代表已验收。
- 每次推送到 `main` 或 `master` 都由 GitHub Actions 创建一个正式版本：以最新 `vX.Y.Z` 标签为基准自动增加补丁号，并同步 `pyproject.toml`、四个安装器版本、标签及 GitHub Release。首次自动发布使用声明版本。
- 自动生成的版本提交和标签由 `github-actions[bot]` 推送；PR 只执行验证，不创建 Release。

## 10. 编码规则

- 先读三个核心 MD、相关代码和测试，再修改。
- 先定位事实和影响范围，再做小范围修改。
- 优先复用，避免无关重构。
- 不删除未知代码，不修改无关文件。
- 不把 Mock 当真实数据。
- 不伪造测试结果；说明通过、失败和跳过。
- 变化涉及需求、架构、长期约束或核心契约时，更新对应文档。
- 架构不确定时标记 `NEEDS_CONFIRMATION`，不要自行拍板隐藏分歧。

## 11. 长期约束

- 本地优先，不依赖云端数据库或在线服务才能运行核心流程。
- 不执行用户提供的 SQL。
- 不将真实生产数据纳入仓库、日志、报告或测试夹具。
- 插件能力声明必须与实际行为一致；声明不是操作系统级安全隔离。
- 任务输出必须留在工作区，用户主动导出才写入选定外部路径。
- 修改 CLI、SDK、Result、manifest 或 Host 协议时必须同步检查插件和集成测试。

## 12. 重要技术决策

1. 使用本地 Runtime 作为 CLI/GUI 共同执行核心。
2. 使用 Plugin Host 子进程隔离插件异常。
3. 使用 SQLite 记录任务历史，不引入服务端数据库。
4. 使用任务工作区保存输入、输出、日志和报告。
5. 使用 JSON Schema 驱动命令参数与 GUI 表单。
6. 旧 GUI 不构成兼容约束；后续可由 UI Agent 重做。

## 13. Agent 协作规则

推荐顺序：

1. Architecture Agent：更新需求、设计和上下文，识别冲突与待确认项。
2. UI Agent：基于设计重做 GUI 信息架构和视觉，不改变 Core/SDK 契约。
3. Full-stack Agent：先修复启动/测试基线，再实现已确认功能，最后补回归测试。

每个 Agent 都必须说明：

- 读取了哪些核心文档。
- 修改了哪些文件。
- 哪些事实已验证。
- 哪些问题仍是 `NEEDS_CONFIRMATION`。
- 测试是通过、失败还是跳过。

## 14. 何时更新本文件

只在以下变化发生时更新：

- 技术栈或运行形态变化。
- Runtime、Plugin Host、SDK 或任务工作区边界变化。
- 插件长期开发规则变化。
- GUI 与 Core 的长期职责边界变化。
- 本地优先、安全或数据处理约束变化。

普通功能细节、一次性修复和临时任务计划不应写入本文件。

## 15. 多格式工具的已实现边界

- 官方目录新增 data-preview、data-compare、schema-diff、data-check；当前合计8插件9命令；旧版 Office 转换已撤除。构建hook自动收录目录，CI独立插件ZIP列表同步。
- 公共SDK新增 read_dataset/normalize_dataset，在testbox.tabular实现：CSV/TSV、分隔TXT（可多字符列/记录分隔）、JSON对象/对象数组/JSONL、XLSX/XLSM、SQL全文载体；默认严格文本，不修改来源。可显式编码、表头、Sheet、数据路径、列映射/trim/casefold/null/types，decimal精确文本+类型元数据。Excel沿用可选openpyxl，不计算公式或运行宏。资源超限失败，不返回截断数据声称complete。
- 数据结构统一columns/rows/locations/format/warnings/complete，保留缺失/null区别；字符串长度限制按解码内容，嵌套值按序列化规模限制。拒绝重复表头/JSON键、非有限值、引号不闭合/不齐列；共享读取默认跳过空白记录，可显式关闭。
- x-preview为Schema展示元数据，通用GUI面板通过真实异步Runtime/Host预览，不自行解析；配置同步正式表单，源/配置变化旧结果失效；预览留真实任务和样本产物，不替代完整比较。
- data.compare按位置/主键/多重集合，支持容差与明细上限；data.check声明式8类规则；数据差异/质量error仍可任务success，分别用equal/passed判断。sql.diff/sql.preview有限语法分析，不执行SQL；unknown不允许报告equal，使用equal/different/inconclusive业务结论。
- 暂无旧XLS、固定宽TXT、XML、批量多Sheet/目录、任意日期格式、完整SQL语义或自动清洗。Windows原生安装/增量更新/回滚本阶段用户要求跳过，配置存在不代表通过。

新增插件使用新增SDK能力，manifest Core兼容下限为1.0.16；预览传输单元格有界显示，完整采样保留在JSON产物。文本location含row（物理行）和record（解析记录序号），SQL另含单元格内位置。

## 17. 已撤除能力与通用输入策略

- 旧版 Office 转换插件已按用户决定撤除源码、专属 GUI 分支、专属测试与打包/冒烟入口；当前不提供旧 XLS/DOC/PPT 转换或 Office 引擎诊断，不依赖 LibreOffice。
- 通用 Schema `x-input-policy` 仍是显式输入暂存保护：`reject_symlinks`、`unique_sources`、`max_files` 在 resolve/copy 前核对原始来源；未声明策略的旧插件行为不变。同名同内容但不同来源不是重复输入，相关回归使用独立测试插件，不依赖已撤除业务。
- 源码撤除不删除已有任务历史、工作区、输出或用户自行安装的插件，不自动卸载系统软件；不能据此宣称用户安装的旧插件也被禁用。
