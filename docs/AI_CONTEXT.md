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
- Windows 使用 PyInstaller 构建可执行文件。
- 测试主要使用 Python `unittest`。

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
- 插件之间不得直接依赖。
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
list_commands()
get_command(command)
get_command_schema(command)
run(command, params)
get_task(task_id)
get_task_result(task_id)
list_tasks(status, command, limit, offset)
clean_workspace(before)
commit_output(task_id, relative_path, destination)
```

`execute(command, params)` 仅作为旧 GUI 调用的兼容别名；新代码优先使用 `run`。任务状态统一使用 `PENDING`、`RUNNING`、`SUCCEEDED`、`FAILED`、`CANCELLED`、`ABANDONED`。

CLI 已形成稳定的第二阶段命令面：`plugin inspect`、`task list`、`task result`、`task export`，并支持顶层 `--json`。CLI 只能调用 Runtime，不得自行读取 SQLite、插件目录或任务结果文件。稳定退出码为：参数/用法 `2`、插件管理 `3`、任务失败 `4`、取消 `130`。

Runtime 只将脱敏参数写入任务清单和 SQLite。执行请求仍可携带插件运行所需的原始配置，但原始配置不得写入日志、报告、`manifest.json`、`result.json` 或任务历史。

## 7. 当前插件

- `data-generator`：命令 `data.mock`，生成可复现模拟数据，包含规则化输入和固定第三方行政区划资源。
- `sql-parser`：命令 `sql.parse`，解析 SQL DDL 文本，不执行 SQL。
- `evidence-tool`：命令 `evidence.inspect`、`evidence.build`，依赖部分可选库，属于已有但仍需确认优先级的能力。

新增插件必须提供清单、入口、命令 Schema、README 和必要测试。插件命令使用小写点分格式。

## 8. 插件长期规则

- 生命周期为 `init → execute → destroy`；无论成功或失败都应尽力执行 `destroy`。
- 使用 `context.logger`，不用 `print()`。
- 不调用 `sys.exit()` 结束插件任务；用户可修复错误使用 `PluginError`。
- 只能向任务 `output/` 写入并通过 SDK 登记输出文件。
- 返回 `Result`，大结果写文件，小摘要放 `data`。
- 输出文件路径必须是相对路径，不得包含绝对路径或 `..`。
- 通过 Schema 描述输入，业务层仍需做范围和资源校验。
- 不把密码、令牌、连接串、真实个人信息放入样例、日志、测试夹具或报告。
- 数据生成必须可复现；唯一性仅在用户显式声明且当前任务范围内保证。
- 证件/身份类模拟内容必须带 `TEST DATA ONLY` / `测试数据` 标识，不能用于真实认证。
- SQL Parser 只解析文本；任何插件都不得静默执行用户输入的 SQL、脚本或外部命令。

## 9. UI 长期规则

- UI 是 Runtime 的展示层，不是第二套业务层。
- 保护的是 Runtime/SDK/Result 契约，不是当前 GUI 的布局、颜色或组件。
- 当前旧 UI 可以整体重做。
- 推荐以“工具目录 → 执行工作区 → 任务详情/历史”为主路径。
- 每个任务都要有 Loading、Empty、Success、Warning、Error、Cancelled 等明确状态；状态不能只用颜色表达。
- 复杂参数由 Schema 驱动；高级参数和原始日志按需展开。
- GUI 必须展示任务 ID、插件版本、脱敏参数、输出文件和错误建议。
- UI Agent 不应在 GUI 中重新实现文件写入、任务状态机或插件业务。

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
