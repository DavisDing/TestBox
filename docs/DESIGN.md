# TestBox 技术设计基线

> 本文件只描述项目准备如何实现，以及当前代码与目标设计的差异。
>
> 基线日期：2026-08-25。架构原则：简单优先、复用优先、先稳定本地闭环，再扩展能力。

## 1. 事实优先级与设计状态

判断项目事实时按以下顺序：

1. 本轮用户明确要求。
2. 当前代码、配置和可重复验证结果。
3. 正式需求与设计文档。
4. 历史方案和旧规范。

本文档使用以下状态：

- `CURRENT`：已在当前仓库代码或配置中确认。
- `TARGET`：作为后续实现目标保留。
- `RECOMMENDATION`：基于当前问题提出的建议，尚未改代码。
- `NEEDS_CONFIRMATION`：需要用户/产品确认。

## 2. 总体架构

TestBox 是单机 Python 应用，不采用前后端 HTTP 服务架构。

```text
CLI / PySide6 GUI
        │
        ▼
      Runtime
  ┌─────┼──────────────┐
  │     │              │
Plugin  Config       Task History
Manager Manager       (SQLite)
  │
  ▼
Plugin Host 子进程
  │
  ▼
单个插件实例
  │
  ▼
workspace/<task-id>/
  ├── input/
  ├── output/
  ├── logs/
  ├── manifest.json
  ├── result.json
  └── report.md
```

Core 负责发现、校验、参数处理、工作区、任务记录、Host 进程生命周期和结果汇总。插件负责具体业务。GUI 只调用 Runtime，不复制插件逻辑。

## 3. 技术栈

| 层 | 当前事实 | 设计基线 |
| --- | --- | --- |
| 语言 | Python，`pyproject.toml` 要求 `>=3.11` | 保持 Python 3.11+ |
| CLI | `argparse` | 保持轻量 CLI；除非确认有收益，不迁移到 Typer |
| GUI | PySide6 可选依赖 | GUI 继续复用 Runtime；视觉和页面允许重做 |
| 插件协议 | 标准输入/输出上的 JSON 请求与结果 | 保持版本化；仅在有实时进度需求时扩展 |
| 持久化 | SQLite `task_history` | 继续使用本地 SQLite，不引入服务端数据库 |
| 配置 | YAML 文件 + 环境变量；PyYAML 不可用时有简化解析兜底 | 保持简单配置合并，不增加配置中心 |
| 构建 | setuptools；Windows 使用 PyInstaller 脚本 | 保持现有构建链路 |
| 测试 | `unittest` 集成测试 | 先修复/稳定现有测试，再补充关键边界 |
| 插件产物 | ZIP，根目录直接包含 `manifest.yaml` | 保持安装前临时校验、成功后替换 |

`pyproject.toml` 声明了 `typer`、`pydantic`、`loguru`，但当前核心实现未普遍使用它们。这是依赖与实现的偏差，暂不直接删除，进入依赖收敛决策。

### 3.1 技术选型评估（2026-08-25）

结论：**总体技术方向没有根本性问题，不建议推翻重选；需要做依赖边界和契约实现方面的调整。** 当前最大的风险不是 Python、PySide6 或 SQLite 本身，而是“声明的技术栈”和“实际使用的技术”不一致，以及插件依赖、Schema 校验、运行环境边界尚未收敛。

| 选型 | 结论 | 建议 |
| --- | --- | --- |
| Python 3.11+ | 保留 | 适合本地工具、插件和跨平台脚本；通过 CI 明确实际支持的 Python 版本，不要只依赖 `>=3.11` 的宽范围声明。 |
| setuptools + `pyproject.toml` | 保留 | 项目规模不需要迁移 Poetry、uv、Hatch 等构建体系；先补齐安装后运行和打包 smoke test。 |
| `argparse` | 保留 | 当前 CLI 规模适中，迁移 Typer 不会解决现有核心问题；除非后续 CLI 子命令和自动补全需求明显增加。 |
| PySide6 | 暂时保留 | 本项目需要桌面文件、截图和本地任务能力，PySide6 比引入 Electron/WebView 更轻；应重做 GUI 模块结构，而不是更换 GUI 框架。 |
| SQLite | 保留 | 本地任务历史的规模和并发要求都适合 SQLite；补充 schema 版本、索引和迁移策略即可。 |
| Plugin Host + stdio JSON | 保留 | 子进程可隔离插件崩溃，stdio 协议简单且跨平台；实时进度/取消只有在需求确认后再扩展协议。 |
| PyYAML | 保留并收敛 | Manifest 和配置既然使用 YAML，就应将 PyYAML 作为明确运行依赖；简化解析兜底逻辑不应长期承担完整 YAML 兼容责任。 |
| 自研 Schema 校验 | 短期可用，长期需调整 | 当前插件数量少、Schema 简单时可以工作；在支持 `oneOf`、嵌套数组、条件字段和 GUI 复杂表单前，建议引入标准 `jsonschema` 校验库。 |
| `unittest` | 当前保留 | 不需要为了形式迁移；若后续需要 fixture、参数化、覆盖率和更快的 AI 回归反馈，可增加 pytest 作为测试工具，不必立即重写现有测试。 |
| 插件依赖 | 需要调整 | `openpyxl`、`python-docx`、`Pillow` 实际属于 Evidence Tool，不应被 Core 默认依赖长期持有。需要先确定插件依赖安装策略，再移动到插件独立依赖或 evidence extra。 |
| Typer/Pydantic/Loguru | 需要收敛 | 当前核心代码没有实际依赖它们。应在下一阶段确认是否使用；若不使用，应从运行时依赖中移除，避免给 Agent 造成错误架构暗示。 |

#### 不建议当前阶段引入

- FastAPI、HTTP 后端或本地服务：当前没有跨进程客户端/远程调用需求，GUI 可直接调用 Runtime。
- React + Electron/Tauri：会增加前端工程、打包和 Python Runtime 通信复杂度；除非产品明确转向 Web 技术栈，否则收益不足。
- PostgreSQL、Redis、消息队列：当前是单机工具，不需要服务端持久化和异步基础设施。
- 微服务、容器编排和插件市场：超出当前产品边界。

#### 调整优先级

1. **P0**：修复源码/安装/打包三种运行方式的导入边界和失败测试。
2. **P0**：收敛 `pyproject.toml` 中未使用的运行依赖，明确插件依赖归属。
3. **P1**：为 SQLite 增加 schema 版本和必要索引。
4. **P1**：在 GUI Schema 复杂度增加前评估 `jsonschema`，避免继续扩展自研校验器。
5. **P2**：只有确认实时任务体验后，再增加 Host 事件流、心跳和取消协议。

## 4. 项目结构

```text
TestBox/
├── testbox/
│   ├── cli.py                  # CLI 参数解析和命令分发
│   ├── gui.py                  # 当前 PySide6 GUI；后续允许重做
│   ├── sdk.py                  # 插件稳定 SDK：Context、Result、PluginError、SafeFiles
│   └── core/
│       ├── config.py           # 配置读取与合并
│       ├── history.py          # SQLite 任务历史
│       ├── host.py             # 单插件 Host 子进程入口
│       ├── manifest.py         # manifest 与 Schema 基础校验
│       ├── plugin_packages.py  # 插件打包/安装/卸载
│       └── runtime.py          # 发现、任务、工作区、Host 调度
├── plugins/
│   ├── data-generator/
│   ├── sql-parser/
│   └── evidence-tool/
├── tests/test_integration.py
├── workspace/                  # 本地任务工作区，不是源码
├── docs/
│   ├── REQUIREMENT.md
│   ├── DESIGN.md
│   ├── AI_CONTEXT.md
│   └── archive/                # 已迁移的历史文档
└── pyproject.toml
```

## 5. 模块设计

### 5.1 CLI

职责：

- 解析 `plugin`、`run`、`task`、`workspace`、`gui` 子命令。
- 读取 `--set key=value` 和 JSON 参数文件。
- 将用户可理解的任务摘要和退出码输出到终端。
- 不实现插件业务。

当前使用 `argparse`；`parse_scalar_options` 支持把 `--count 10` 等旧式参数转换为参数键值。

### 5.2 Runtime

职责：

- 定位应用根目录、插件目录和工作区。
- 发现插件、建立命令索引并记录不可用原因。
- 校验命令、Schema、参数和文件输入。
- 创建任务目录，脱敏参数并写入任务清单。
- 启动并监控 Host 子进程。
- 处理超时、异常退出、输出路径和输出大小校验。
- 写入 `result.json`、`report.md` 并更新 SQLite 历史。

Runtime 是 CLI 与 GUI 共用的唯一执行入口。

### 5.3 Plugin Manager / Manifest

`manifest.yaml` 至少描述：插件名称、版本、分类、Core 兼容范围、入口、命令、输入 Schema 和能力声明。命令名使用小写点分格式，例如 `data.mock`。

发现失败的插件不能阻塞其他插件；命令冲突必须记录为不可用。

### 5.4 Plugin Host

Host 每次只加载一个插件实例，调用：

```text
Plugin(context) -> init(context) -> execute(command, params) -> destroy()
```

当前协议事实：Core 向 Host 发送一个 JSON 请求，Host 返回一个带 `event=result` 的 JSON 响应。当前代码没有实现旧设计文档中描述的完整 `start/log/heartbeat/progress/result` 多消息流。

**RECOMMENDATION**：短期保持一次请求/一次响应，避免为尚未确认的实时进度引入复杂协议。若 GUI 确认必须显示实时进度，再以 `protocol_version` 方式增加事件消息、取消信号和心跳，并补齐测试。

### 5.5 SDK

稳定边界：

- `Context.logger`
- `Context.config`
- `Context.workspace.input_dir/output_dir`
- `Context.files`
- `Context.task.id`
- `Result`
- `PluginError`

插件不得依赖 Runtime 私有属性，不得使用 `print()`、`sys.exit()` 或写入任务目录外的路径。

### 5.6 配置

当前合并来源顺序为：用户级 `~/.testbox/config.yaml`、插件配置、项目根目录 `config.yaml`、插件专属环境变量。命令参数在 Runtime 侧作为最高优先级输入。

敏感信息不得写入配置文件明文、任务清单、日志或报告。当前配置引用/钥匙串方案尚未完整实现，不能在文档或 UI 中当作已交付能力宣传。

### 5.7 任务历史

SQLite 表 `task_history` 保存任务 ID、插件及版本、命令、脱敏参数、开始/结束时间、状态、结果路径、工作区路径、错误码、心跳字段和 Host PID。状态包括 `PENDING`、`RUNNING`、`SUCCEEDED`、`FAILED`、`CANCELLED`，启动时会尝试将已无 Host 进程的运行中任务标记为 `ABANDONED`。

## 6. 插件设计

### 6.1 插件目录

```text
plugin-name/
├── manifest.yaml
├── src/main.py
├── schemas/*.json
├── config/config.yaml        # 可选
├── data/                     # 可选、随版本固定
├── README.md
├── requirements.txt          # 有直接依赖时提供
└── tests/                    # 插件测试（目标）
```

插件之间不能直接依赖。Core/SDK 提供公共能力；插件只负责自己的输入转换、业务逻辑和输出文件生成。

### 6.2 当前插件

| 插件 | 命令 | 当前状态 |
| --- | --- | --- |
| data-generator | `data.mock` | `CURRENT`；有规则 Schema、固定行政区划数据和多种输出 |
| sql-parser | `sql.parse` | `CURRENT`；覆盖多种 DDL 解析场景并支持警告/严格模式 |
| evidence-tool | `evidence.inspect`, `evidence.build` | `CURRENT/PARTIAL`；依赖可选包，部分集成测试在当前环境跳过 |

### 6.3 插件能力与安全边界

清单声明 `concurrency`、`network`、`filesystem`、`resources`。当前插件均声明无网络且输出目录写入。能力声明用于调度和审计，但不构成操作系统安全沙箱；本地插件仍应视为可信代码。

插件安装流程：临时目录解压/复制 → 清单校验 → 替换安装目录。ZIP 路径穿越被拒绝。升级前不会直接破坏现有已启用目录。

历史设计提出“每插件独立虚拟环境、依赖哈希锁定”，但当前代码没有实现完整流程。此能力列为 `NEEDS_CONFIRMATION`，不应作为 V1 已实现事实。

## 7. 数据流

### 7.1 CLI/GUI 执行

```text
用户输入
  → CLI 或 GUI
  → Runtime 解析并校验参数
  → 文件输入复制到 task/input 并记录哈希
  → 创建 SQLite 任务记录与 manifest.json
  → 启动 Plugin Host
  → 插件读取 Context 并写 task/output
  → Host 返回 Result
  → Runtime 校验结果文件与路径
  → 写 result.json/report.md
  → 更新任务历史
  → CLI/GUI 展示摘要
```

### 7.2 结果模型

```json
{
  "status": "success | failed | cancelled",
  "message": "用户可读摘要",
  "data": {},
  "files": ["relative/path.ext"],
  "warnings": []
}
```

`data` 只放可 JSON 序列化的轻量摘要；大结果必须写入 `output/`。`files` 只能是相对任务 output 的路径。

## 8. API 与契约

本项目当前没有 HTTP API，也没有独立后端服务。以下是需要保持稳定的本地契约。

### 8.1 CLI 契约

| 命令 | 用途 |
| --- | --- |
| `testbox plugin list` | 列出可用命令和无效插件原因 |
| `testbox plugin inspect <name-or-command>` | 查看插件版本、能力和命令信息 |
| `testbox plugin validate <path>` | 校验插件清单 |
| `testbox plugin package <path> --output <zip>` | 打包插件 |
| `testbox plugin install <path> [--force]` | 安装/覆盖安装 |
| `testbox plugin uninstall <name>` | 卸载插件 |
| `testbox run <command> --set key=value` | 执行任务 |
| `testbox task list [--status] [--command] [--limit] [--offset]` | 查询任务历史 |
| `testbox task show <task-id>` | 查看任务和结果 |
| `testbox task result <task-id>` | 查看任务结果 JSON |
| `testbox task export <task-id> <relative-path> --output <destination>` | 导出任务输出文件 |
| `testbox workspace clean --before YYYY-MM-DD --confirm` | 清理旧工作区 |

所有命令支持顶层 `--json` 输出机器可读结果。错误统一输出 `{"ok": false, "error": {"code", "message"}}`；成功退出码为 0，参数/用法错误为 2，插件管理错误为 3，任务失败为 4，用户取消为 130。

### 8.2 Host 契约

请求至少包含协议版本、任务 ID、插件路径、入口、命令、参数、配置和工作区。响应包含协议版本、事件类型、任务 ID 和 Result。

Host 标准输出只能承载协议消息；诊断写入任务日志/标准错误。Unicode 通过 ASCII JSON 转义保证跨平台管道可读。

### 8.3 GUI 与 Runtime 契约

GUI 直接调用 Runtime 的任务接口，并读取任务工作区中的结果和报告。GUI 不应通过复制参数规则或直接导入插件实现业务。

## 9. 前端/GUI 设计

### 9.1 当前实现评估

当前 GUI 是一个 PySide6 单窗口：左侧按命令生成列表导航，右侧显示 Schema 表单；Evidence Tool 有一套专用截图/标注流程；任务完成后主要用消息框展示结果。

当前问题：

- 信息架构以“命令列表”为中心，缺少工具目录、任务中心和工作区概念。
- 任务过程和历史任务展示不足，不能形成持续可追溯的工作流。
- 结果主要依赖消息框，不适合查看多文件、警告、日志和报告。
- 通用命令页与 Evidence 专用页的交互模型不统一。
- 当前视觉样式偏简单的传统表单，不能体现测试工具箱的层级和效率。
- GUI 模块导入时加载 Qt，桌面依赖缺失时可能影响需要复用模块的场景；内部 Host 模式已有提前分支，但结构仍应保持清晰。

CURRENT UI 不作为必须保留的设计资产。

### 9.2 UI Agent 的重设计方向

建议采用“工具目录 + 执行工作区 + 任务详情”的主结构：

1. **工具首页**：按类别展示插件和命令，显示说明、版本、能力和最近使用。
2. **执行工作区**：左侧/上方为命令信息和参数表单，主体区域为输入与运行控制；高级参数折叠展示。
3. **任务详情**：状态、任务 ID、时间、日志摘要、警告、输出文件、报告和打开工作区动作集中展示。
4. **任务历史**：按状态、命令和时间筛选，支持重新打开详情，不默认复制参数执行。
5. **插件与设置**：先提供只读状态和路径/限制展示；安装、卸载、能力确认等危险操作单独确认。

必须设计 Loading、Empty、Error、Success、Warning、Cancelled 和权限拒绝状态。视觉风格、颜色、字体、组件细节由 UI Agent 决定；不能把旧 UI 的具体颜色和布局当作约束。

### 9.3 UI 与业务边界

- 表单展示、即时字段校验、导航和结果呈现：GUI。
- Schema 权威校验、文件暂存、任务状态、结果落盘和敏感值处理：Runtime。
- 任何文件导出或任务运行：通过 Runtime，不由 UI 私自实现。

## 10. 错误处理

错误分为：

1. 用户输入/Schema 错误：在 CLI/GUI 入口尽早提示。
2. 输入文件不存在或不可读：返回 `INPUT_NOT_FOUND` 或对应错误。
3. 插件业务错误：插件抛出 `PluginError`，由 Host 转为结构化失败。
4. Host 崩溃/协议错误/超时：Runtime 写入诊断摘要并将任务标记失败。
5. 输出非法/缺失/过大：Runtime 拒绝结果并保留任务证据。
6. 历史写入失败：不得覆盖原始任务结果，应至少记录警告（当前实现需继续验证）。

CLI/GUI 面向用户显示可理解摘要；完整堆栈只放任务日志或受控诊断，不显示敏感信息。

## 11. 安全设计

- 插件清单校验、命令唯一性和 Core 兼容性检查。
- Host 子进程隔离 Core 的异常影响，但不是恶意代码沙箱。
- 文件输入暂存并记录来源哈希。
- 输出路径限制在任务 output 目录。
- 参数脱敏后才写历史和任务清单。
- 数据生成器只输出测试数据；证件/身份图像必须有不可移除的测试水印。
- SQL Parser 不执行 SQL。
- 插件能力默认最小化；网络、外部服务和用户目录写入不应隐式开放。

## 12. 性能与资源

- 默认 Host 超时为 300 秒，Core 对输入/输出大小有上限。
- 非并发插件使用跨进程锁；当前 Evidence Tool 声明不支持并发。
- 10 万条数据是 Data Generator 目标规模，需使用真实设备做基准，不把文档数字当成已验证指标。
- 不引入队列、服务端、缓存集群或数据库服务器。

## 13. 测试关注点

### 13.1 已有测试重点

- 插件发现、清单校验和命令冲突。
- Data Generator 可复现、规则、地址筛选、输出格式和唯一性边界。
- SQL Parser 多方言、嵌套类型、注释、约束、警告和严格失败。
- Host 崩溃、超时、诊断、任务中断恢复。
- 插件打包、安装和卸载。
- 任务历史和工作区清理。

### 13.2 后续必须补齐

- 真实安装环境下 CLI 可启动和可执行，不依赖当前工作目录偶然可导入源码。
- GUI 的 Loading/Empty/Error/Success/Warning/Cancelled 状态。
- GUI 与 CLI 对同一命令、参数和结果模型的一致性。
- 文件路径穿越、超大输入/输出、损坏插件包和缺失资源。
- 敏感参数在任务清单、日志、报告、错误堆栈中的脱敏。
- Evidence Tool 在安装可选依赖时的完整集成测试。
- 取消、进程孤儿、SQLite 并发更新和重复终态保护。

## 14. 当前代码问题

### Critical

- 暂未发现会阻止核心 CLI 正常运行的必现 Critical 问题。

### High

- 全量测试在 2026-08-25 执行结果为 `49` 个测试中 `1` 个失败、`5` 个跳过。失败为 `test_cli_listing_survives_legacy_console_encoding`：子进程以临时目录为工作目录运行 `python -m testbox.cli` 时找不到 `testbox` 模块。该问题说明源码运行/安装运行边界未统一，必须在正式开发前修复或明确测试运行方式。
- 旧 Core 设计描述的多事件 Host 协议、独立插件虚拟环境、依赖哈希锁定和完整权限确认没有在当前实现中落地，文档不能继续把它们当作已实现事实。

### Medium

- GUI 当前页面结构和结果呈现较弱，且与重新定义的产品工作流不匹配。
- `pyproject.toml` 的声明依赖与实际代码使用不完全一致，增加维护和打包认知成本。
- Evidence Tool 的部分测试因当前环境未安装可选依赖而跳过，不能据此宣称完整通过。
- 当前配置解析、Schema 校验属于项目自有简化实现，尚未覆盖完整 JSON Schema 语义。

### Low

- CLI 文件和 Core 文件存在较密集的单行语句，后续修改时可在相关模块内逐步改善可读性，但不应在无业务理由时大范围重写。
- README 的历史文档链接与实际 `docs/` 目录结构不一致，归档后需要同步文档入口。

## 15. Architecture Recommendations

### Recommendation A：先修复运行基线，再做功能扩展

- Current：核心命令可在仓库根目录运行，但安装/临时工作目录场景存在模块发现失败。
- Problem：测试与发行包可能在不同导入路径下表现不同。
- Recommendation：明确“可编辑安装运行”和“打包运行”两条支持路径，补充安装后子进程 smoke test，不以修改业务逻辑掩盖环境问题。
- Reason：这是 Core、CLI、GUI 和 Host 的共同基础。
- Impact：Full-stack Agent 应先修复启动/测试基线，再接 UI。

### Recommendation B：保持轻量 Host 协议

- Current：一次 JSON 请求/一次 JSON 结果。
- Problem：旧文档规划了更复杂的实时事件协议，但当前需求尚未确认是否需要实时进度。
- Recommendation：短期保持当前协议；将进度/取消设计为明确需求后再扩展版本。
- Reason：避免没有用户价值的协议和状态机复杂化。
- Impact：GUI 第一版可采用任务运行中状态与完成后详情，不承诺实时百分比。

### Recommendation C：重做 GUI 信息架构，不修补旧布局

- Current：按命令生成的左侧列表 + 表单 + 结果消息框。
- Problem：不能承载任务历史、工作区、插件状态和多文件结果。
- Recommendation：按工具目录、执行工作区、任务详情、历史任务组织页面。
- Reason：与产品定位和长期 AI Coding 协作更一致。
- Impact：UI Agent 可重建 `testbox/gui.py` 或拆分 GUI 模块；Runtime/SDK 契约保持稳定。

### Recommendation D：暂不引入数据库服务器或微服务

- Current：SQLite 已足够承载本地任务历史。
- Problem：引入服务端会增加部署和调试成本。
- Recommendation：继续本地单进程/子进程架构。
- Reason：用户目标是本地工具，现有数据量和并发没有提出更高要求。
- Impact：架构简单，后续若出现多用户/远程执行需求再重新评估。

### Recommendation E：收敛依赖和契约

- Current：声明依赖多于实际使用，Schema/配置/Host 协议都有自有简化实现。
- Problem：AI Agent 容易依据依赖名误判项目能力。
- Recommendation：下一阶段建立“实际使用依赖清单”和“契约测试”，确认后再删除或保留依赖。
- Reason：减少隐性环境差异。
- Impact：可能影响打包文件和 CI，但不改变产品功能。

## 16. Design Decisions

1. **本地单机优先**：不引入微服务、队列、远程数据库或 Kubernetes。
2. **Runtime 单一事实源**：CLI 和 GUI 必须复用同一执行、任务和结果模型。
3. **插件子进程执行**：插件异常不应直接破坏 Core；但明确这不是安全沙箱。
4. **任务工作区优先**：所有输入暂存、输出、日志、结果和报告围绕任务目录组织。
5. **Schema 驱动参数**：命令参数以插件 JSON Schema 为权威来源，GUI 表单由其生成。
6. **当前 UI 可推翻**：旧 UI 不作为兼容约束，只保护 Runtime/SDK/结果契约。

## 17. Design Deviations

| 历史设计/文档说法 | 当前事实 | 基线处理 |
| --- | --- | --- |
| GUI 属于后续范围/仅规范 | 当前仓库已有 PySide6 GUI 和 Evidence 专用流程 | 保留代码事实，但 GUI 作为可重做实现 |
| Host 支持多事件 JSON Lines、心跳和进度 | 当前是一次 JSON 请求/一次结果响应 | 以当前轻量协议为基线，实时协议待确认 |
| 每插件独立虚拟环境和依赖哈希锁定 | 当前安装流程未实现完整隔离环境 | 记录为未来选项，不作为已交付能力 |
| CLI 示例主要使用 `--count 100` | 当前正式解析入口以 `--set key=value` 为主，同时保留标量兼容解析 | 后续统一 CLI 文档与测试 |
| PRD 将 Evidence/GUI 列为 P2 | 当前仓库已有 Evidence 实现和 GUI | 保留现状；优先级需要用户确认 |
| 旧 README 链接指向 docs 外的文件名 | 实际文档在 `docs/requirements` 与 `docs/design` | 归档后统一 README 导航 |

## 18. Open Questions

1. 是否把当前 `evidence-tool` 从兼容能力提升为下一阶段主线？
2. GUI 是否要求实时日志、实时进度和取消；如果要求，Host 协议如何版本化？
3. 是否需要真正的插件依赖隔离安装，还是继续把插件视为本地可信、共享 Python 环境？
4. 是否将 CLI 参数统一为 `--set`，并废弃/保留 `--count` 等标量语法？
5. 是否支持 macOS 与 Windows 同等完整的 GUI 发布与截图能力？
6. 是否把配置引用、钥匙串和能力确认纳入下一版本？

## 19. Agent 执行边界

- Architecture Agent：维护需求/设计/上下文，分析冲突，不直接实现业务代码。
- UI Agent：根据本设计重做 GUI 信息架构和视觉实现，不改变 Runtime/SDK 契约。
- Full-stack Agent：先修复启动、测试和契约问题，再实现确认过的功能；修改前读取三个核心 MD 和当前代码。
