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
| 构建 | setuptools 构建钩子装配官方插件资源至 wheel；Windows 使用 PyInstaller 脚本 | 保持现有构建链路，源码与 wheel 分别验证 |
| 测试 | `unittest` 集成测试 | 先修复/稳定现有测试，再补充关键边界 |
| 插件产物 | ZIP，根目录直接包含 `manifest.yaml` | 保持安装前临时校验、成功后替换 |

当前 `pyproject.toml` 核心依赖只有 PyYAML；PySide6、Evidence 库与 Windows 构建工具分别为可选 extra。历史方案中的 Typer/Pydantic/Loguru 不再是当前声明依赖。

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

SQLite 表 `task_history` 保存任务 ID、插件及版本、命令、脱敏参数、开始/结束时间、状态、结果路径、工作区路径、错误码、心跳字段和 Host PID。状态包括 `PENDING`、`RUNNING`、`SUCCEEDED`、`FAILED`、`CANCELLED`、`ABANDONED`。schema v2 兼容迁移新增 `owner_pid`：Host 未启动时检查拥有者存活，已记录 Host 时检查 Host；Runtime 用任务生命周期锁保护暂存和结果收尾窗口，避免并发启动误回收。明确终态不被重复更新改写。

迁移使用事务，只增加可空列，不删除历史数据；未知未来版本被拒绝。上线前应备份现有 SQLite 文件。回退应用版本不应手工删除列或覆盖数据，若需要恢复 v1 数据库应停用所有 Runtime 后从备份恢复。

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

插件之间不能直接依赖。Core/SDK 提供公共能力；插件只负责自己的输入转换、业务逻辑和输出文件生成。下游插件通过用户选择的上游任务 output 文件消费产物，不导入上游插件代码。

### 6.2 当前插件

| 插件 | 命令 | 当前状态 |
| --- | --- | --- |
| data-generator | `data.mock` | `CURRENT`；有规则 Schema、固定行政区划数据和多种输出，输出文件名使用任务 ID |
| sql-parser | `sql.parse` | `CURRENT`；覆盖多种 DDL 解析场景并支持警告/严格模式，输出文件名使用任务 ID |
| sql-select | `sql.select` | `CURRENT`；消费 SQL Parser 的 JSON/CSV/XLSX 字段清单并生成 SELECT 文本 |
| evidence-tool | `evidence.build` | `CURRENT/PARTIAL`；单次任务内完成 Excel 识别、截图关联、Word/Excel 输出；安装可选依赖时有完整批处理测试，真实交互截图/权限仍需目标平台验收 |

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

以下为基线建立时的历史评估；当前 GUI 已具备工具目录、工作区、历史和插件诊断，见第 26 节。本次本地插件目录扩展以第 27 节为准，保留现有卡片、主题和执行页面，不按本节旧评估重新设计整个 GUI。

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
6. 历史写入失败：保留已经落盘的业务结果及产物登记，进行一次有界重试；返回同步失败或重试恢复警告，不改写为插件失败。持续失败时历史可能暂未同步。

CLI/GUI 面向用户显示可理解摘要；完整堆栈只放任务日志或受控诊断，不显示敏感信息。

## 11. 安全设计

- 插件清单校验、命令唯一性和 Core 兼容性检查。
- Host 子进程隔离 Core 的异常影响，但不是恶意代码沙箱。
- 文件输入暂存并记录来源哈希。
- 输出路径限制在任务 output 目录。
- 参数脱敏后才写历史和任务清单；Host 与 Runtime 统一处理日志、异常、Result 摘要和 GUI 参数展示中的已知敏感值，保留协议与产物路径身份。插件任意产物内容不由诊断脱敏机制扫描。
- 数据生成器只输出测试数据；证件/身份图像必须有不可移除的测试水印。
- SQL Parser 不执行 SQL。
- 插件能力默认最小化；网络、外部服务和用户目录写入不应隐式开放。

## 12. 性能与资源

- 默认 Host 超时为 300 秒；文件输入分块暂存并哈希，任务累计限 100 MiB；登记输出总量限 500 MiB（结束时校验，不是执行中硬磁盘配额）。
- Host 日志按 UTF-8 字节限 1 MiB，带一次截断提示；管道响应限 8 MiB，stderr 只保留最后 64 KiB。冻结 GUI 保持文件响应协议，运行中监测响应大小并有界读取；监测不能阻止两次检查之间的瞬时磁盘写入。
- 插件 ZIP 限 4096 项、单项 128 MiB、总解压 512 MiB，同时校验声明大小和实际流式读取量。链接、路径穿越和冲突路径被拒绝。覆盖激活失败恢复旧目录；恢复失败保留备份并报告人工恢复路径。
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

### 13.2 验证入口与未验收边界

- `python -m unittest discover -s tests -v`：Runtime/SDK/Host、GUI 状态契约与可选 offscreen 交互、文件额度/路径安全、插件包回滚、脱敏、SQLite 并发/迁移/终态保护；GUI/Evidence 运行测试要求相应可选依赖。
- `scripts/smoke_installed.py --python <独立虚拟环境Python>`：从空 cwd 验证实际 wheel 的命令、资源、Host、可复现生成、SQL 接力、任务读取/导出及同名插件覆盖恢复。`--evidence` 验证合成 Excel/图片到 Word 和 Excel 状态回写；`--gui` 验证安装后的 offscreen 窗口及异步执行。
- 更新器的故障注入测试覆盖备份、提交、删除和回滚失败；Windows 等待 API 的跨平台 mock 只验证调用/收尾逻辑，不代替原生进程测试。Inno 静态契约不是编译或安装器实机验收。
- CI 包含 Linux clean wheel/Evidence 与 Windows clean wheel/GUI/Evidence 入口，以及冻结程序构建后的原生安装器生命周期验收。原生入口只允许 GitHub-hosted Windows 临时 runner，记录完整安装、合成 delta（含锁文件回滚）、路径/组件/基线/hash 失败和卸载保留；日志与 JSON 摘要无论成功失败均上传。配置存在不等于对应流水线已运行，合成增量不等于相邻发布版本兼容性。
- 仍需实际执行 Windows 原生流水线并验收相邻正式版本升级、真实截图权限及交互 Evidence 流程；通用取消与后代进程管理边界需要单独确认，不承诺未实现的取消协议。

## 14. 当前代码问题与验收边界

### 已收敛的历史问题

- 2026-08-25 的 49 项测试失败/跳过记录仅代表当时环境，不应继续作为当前运行基线。
- GUI 已具有工具目录、执行表单、结果详情、历史、插件诊断和只读设置，复用同一 Runtime；新增可选 PySide6 offscreen 交互回归，不能用旧 GUI 描述判断现状。
- Evidence 批处理集成测试在安装可选依赖的环境执行；真实截图权限与交互证据流程仍需目标平台验收。
- 原始配置失败、并发启动恢复、活动任务清理、历史同步失败覆盖结果及诊断泄露均有专项回归。

### 仍需加强或确认

- Python 发行包通过构建钩子收录官方插件资源，安装后的 CLI/Host、用户插件覆盖和证据流程使用独立 smoke 入口验收；GUI offscreen 与 Windows 安装/升级/卸载实机验收必须区分，不以源码测试或构建配置存在代替。
- 通用取消、实时事件、插件独立环境和权限确认仍为 NEEDS_CONFIRMATION，当前没有新增这些协议。
- Schema 校验对输入与默认对象进行深拷贝，支持对象/数组递归默认及显式 nullable 类型列表，校验空值 enum。已有 Schema 校验后扫描补默认值的完整参数树，拒绝开放对象、嵌套数组及空 Schema 自由值中的 NaN/Infinity；仍是简化实现，不支持完整 JSON Schema，也不扫描输入文件正文、插件配置或产物。
- Host 隔离不是安全沙箱；执行后产物额度、响应文件轮询、PID 存活检查都有边界，不能宣称能限制恶意插件、杀掉全部后代或识别 PID 复用。
- 持续 SQLite 写入失败时原结果保留，但历史可能未同步；需要后续决定是否加入持久化补偿机制。

## 15. Architecture Recommendations

### Recommendation A：先修复运行基线，再做功能扩展

- Current：源码/可编辑安装支持从其他工作目录定位源码和插件；wheel 收录官方插件并使用用户数据目录。CI 配置冻结程序和干净 wheel 的独立 smoke，执行结果须以实际运行记录为准。
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

- Current：GUI 已采用工具目录、执行工作区、任务结果、历史和插件诊断。
- Problem：真实系统权限、长任务生命周期、安装后交互仍需加强验证；不应重复重写已落地页面。
- Recommendation：按工具目录、执行工作区、任务详情、历史任务组织页面。
- Reason：与产品定位和长期 AI Coding 协作更一致。
- Impact：优先补交互回归；必要改动仍保护 Runtime/SDK 契约，不因旧建议重写当前 GUI。

### Recommendation D：暂不引入数据库服务器或微服务

- Current：SQLite 已足够承载本地任务历史。
- Problem：引入服务端会增加部署和调试成本。
- Recommendation：继续本地单进程/子进程架构。
- Reason：用户目标是本地工具，现有数据量和并发没有提出更高要求。
- Impact：架构简单，后续若出现多用户/远程执行需求再重新评估。

### Recommendation E：收敛依赖和契约

- Current：依赖已收敛为 PyYAML 与可选 extra，Schema/配置/Host 仍保持轻量实现。
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

## 15. 多格式工具与解析预览实现方案

- 新增官方 data-preview、data-compare、schema-diff、data-check 插件；插件互相不导入，不连接数据库/网络。
- 可复用解析/标准化是 SDK 显式能力 `read_dataset(path, options)` / `normalize_dataset(dataset, rules)`，实现位于 `testbox.tabular`，不在 Runtime 复制业务比对。数据形状包含 columns、rows、locations、format、warnings、complete；缺失字段保留缺失，decimal标准化成精确文本并记录types。
- 解析采用有界读取。文本列/记录分隔通过引号感知扫描器解析，不split字符串；CSV/TSV单字符，TXT可多字符。JSON拒绝重复键/非有限值，点分路径不执行表达式。Excel按只读模式读取，不执行宏；使用已有可选openpyxl，没有依赖时返回明确错误，不新增Core强依赖。
- Schema的 `x-preview` 只描述预览命令与输入/配置参数映射。GUI加载通用ParsingPreviewPanel，编辑编码/分隔符/表头/Sheet/JSON路径并同步原Schema表单；预览调用异步Runtime/Host、独立任务落盘。修改输入或配置清除旧样本，执行期间变更的响应丢弃，防止展示过期结果。预览不是新的Host事件协议。
- data.compare独立实现记录对齐与差异；sql.diff/sql.preview独立实现有明确语法边界的结构抽取；data.check独立实现声明式质量规则。任务status仍沿用SDK，equal/passed/verdict作为业务结果，不把差异伪装成Host异常。
- 先完成有验收覆盖的基础闭环；结构unknown不能当missing/equal，复杂语法明确警告/inconclusive。后续支持完整SQL需要重新评估解析器依赖，不继续无限扩展正则。
- 所有报告在任务output目录，CSV保护公式前缀，默认不打印输入数据到日志。JSON/明细产物可能含用户数据，脱敏日志不等于自动脱敏产物。

## 16. 旧版 Office 转换插件（已撤除）

此前转换方案已被 2026-10-09 用户“暂时弃用，代码删除”的决定取代；当前不实施或发行该插件。撤除范围与兼容边界见第 21 节。

## 20. Windows CI Office 缺失引擎测试修复设计（历史诊断，已取代）

状态：`SUPERSEDED`。保留此前对 run #46 的诊断记录；用户随后决定撤除插件，以下夹具修复方案不再实施，也不是当前代码说明。当前方案见第 21 节。

### 20.1 目标、证据与范围

- 目标：解除 Windows `Run tests` 对打包的阻塞，保持“合法配置但引擎不存在”与“配置非法”两种结果的区分，不通过跳过测试、安装 LibreOffice 或放松执行限制制造成功。
- GitHub 核验对象：`DavisDing/TestBox` 的 `Build and release #46`，run `37614679742`，触发于 2026-10-07 19:31:39（Asia/Shanghai）。触发提交为 `ffd022c84ecf63b1cfdc89256b7eec10e7054f20`，实际 checkout 为自动版本提交 `bc9d62b9a5b9b5bc324d5dcd8abe3a2aaeb39fd5`；compare 显示后者仅修改六个版本相关文件，不修改 Office 业务或测试代码。
- Python 发行包/插件包 job `112770097704` 的测试、发行包与插件 ZIP 构建、clean wheel smoke、产物上传均已成功。Windows job `112770097647` 在测试阶段失败：614 项，3 failures，27 skipped；Windows wheel smoke、EXE 构建、安装器构建和 Release 发布尚未执行，不是已观察到的 PyInstaller/Inno 打包错误。
- 可见日志明确确认 `OfficeDistributionTests.test_package_preview_install_inspect_without_system_dependency` 返回 `CONFIG_INVALID`，消息为“Windows 引擎仅允许 native exe/com，不接受 batch 脚本”，而测试期待 `success`。
- 日志连接器的中段输出被截断，不能声称已逐条读到三项失败的完整堆栈。另两条关联路径由代码确认：GUI 缺失引擎用例同样使用无后缀路径；`CiPortabilityTests.test_office_package_fixture_closes_database_before_temporary_cleanup` 内部再次运行上述发行测试。它们与剩余两项失败相符，但完整远端失败列表仍为 `NEEDS_CONFIRMATION`。
- 本阶段仅输出设计；下一实现阶段拟修改测试夹具与新增契约回归。范围外：业务功能扩展、Core/Host 协议、GUI 视觉、依赖安装、自动版本策略、Windows 原生安装/增量/回滚验收，以及提交、推送、重跑与发布操作。

### 20.2 原因与必须保留的规则

`plugins/office-convert/src/main.py::find_engine` 的当前数据流：

```text
可信 config / TESTBOX_OFFICE_CONVERT_SOFFICE_PATH
  → 候选路径
  → Windows 后缀检查（只允许 .exe/.com，大小写不敏感）
  → resolve(strict=True) / 文件与执行权限检查
  → 返回引擎路径或 None
  → office.inspect 返回可用性；office.convert 另按缺失依赖失败
```

- Windows 后缀检查先于存在性检查，因此无后缀的 `missing-engine` / `engine-not-installed` 不是有效的“缺少引擎”夹具，而是非法配置夹具。POSIX 不走此限制，故本地测试与仅模拟换行符的回归均不能发现该差异。
- 合法 `.exe/.com` 路径不存在时：`office.inspect` 为 `success`，`available=false`，警告含 `DEPENDENCY_MISSING`；这表示诊断成功，不表示具备转换能力。`office.convert` 应以 `DEPENDENCY_MISSING` 失败。
- 非法配置（空白、非字符串、含 NUL，或 Windows 非 `.exe/.com` 后缀）应保持 `CONFIG_INVALID`，不自动降级成缺少依赖。
- 显式配置不可用时不回退到 PATH 中另一引擎。不得允许 batch 脚本、引入 shell 执行、伪造可用结果，或仅因路径尚不存在而绕过类型校验。

### 20.3 最小修改入口与职责

| 入口 | 拟修改 | 不改变的行为 |
| --- | --- | --- |
| `tests/test_office_distribution.py` | 将临时根目录下的缺失引擎夹具命名为 `missing-engine.exe`；不创建该文件 | 真实打包→安装→Runtime→Host→诊断，全部原有断言和先关闭 SQLite 的清理顺序 |
| `tests/test_gui_office_convert.py` | 将 GUI 缺失引擎夹具命名为 `engine-not-installed.exe`；不创建该文件 | 真实异步 Host、SUCCEEDED、available=false、警告/历史/报告展示；非法配置用例仍验证失败 |
| `tests/test_ci_portability.py` | 保留嵌套发行测试的清理回归；补充不依赖 Qt/LibreOffice 的引擎发现契约矩阵 | 不删除嵌套回归、不弱化结果断言，不引入业务模块或依赖 |
| `plugins/office-convert/src/main.py::find_engine` | 本问题不需要业务修改 | 后缀限制、存在性检查、显式配置优先级全部保留 |
| `.github/workflows/build.yml` | 本修复不需要修改 | Linux CLI job 不强装 Qt；Windows desktop job 继续运行 GUI；失败仍阻断发布 |

统一使用临时目录下不存在的 `.exe` 路径即可同时适配 POSIX 和 Windows；不需要按平台添加命名分支或引入共享夹具框架。临时根目录隔离确保不受 runner 上是否安装 LibreOffice 影响。

### 20.4 回归与验收

新增契约回归应独立于 `SyntheticConverterTests` 的 POSIX-only skip；不得因整个类跳过而漏测 Windows 配置语义。

| 场景 | 预期 |
| --- | --- |
| 不存在的 `.exe` / `.com` / `.EXE` 显式路径 | `find_engine` 为 None；inspect success + available=false + DEPENDENCY_MISSING；不探测、不启动转换器、不回退 PATH |
| Windows 无后缀 / `.cmd` / `.bat` 显式路径 | CONFIG_INVALID；不能因文件不存在而静默变为 DEPENDENCY_MISSING |
| Windows 存在的 `.cmd` / `.bat` 文件 | 同样 CONFIG_INVALID，不执行文件；用临时夹具，不调用 shell |
| 空白 / 非字符串 / NUL 配置 | CONFIG_INVALID，保持已有非法配置 GUI 反馈 |
| 真实发行测试与其清理回归 | 包装、安装、Host、结果历史/报告断言通过，数据库先关闭后删目录 |
| GUI 缺失引擎用例 | 原有状态/警告/结果/历史断言全部通过，不 mock 成功结果 |

跨平台逻辑探针可对**插件模块绑定的 os 引用**使用最小 Windows facade，只模拟后缀分支；不得全局 patch `os.name`，避免改变 `pathlib` 或其他模块行为。这类探针必须标注“分支模拟”，不能冒充原生 Windows 测试。

下一实现阶段使用已核实入口：

```text
.venv/bin/python -m unittest discover -s tests -p test_office_distribution.py -v
.venv/bin/python -m unittest discover -s tests -p test_ci_portability.py -v
.venv/bin/python -m unittest discover -s tests -p test_gui_office_convert.py -v
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s plugins/data-generator/tests -v
git diff --check
```

Windows CI 的 `python -m unittest discover -s tests -v` 必须通过；然后分别确认 Windows clean wheel smoke 与构建步骤是否实际成功。若出现下一阶段错误，以其新日志另行诊断，不能预先宣称安装器或发布已经恢复。

### 20.5 风险、未决项与下一阶段入口

- 当前架构诊断已执行 14 个 POSIX/Windows 后缀分支探针：原始无后缀路径在模拟 Windows 分支返回 CONFIG_INVALID，合法不存在的 `.exe/.com/.EXE` 返回缺少依赖；显式配置不回退 PATH、不执行引擎。另在本机执行发行测试 1 项、CI 可移植性 4 项、Office GUI 13 项，均通过。这些是**未修改代码时的诊断结果**，不是方案实施后的验收，也不是 Windows 原生结果。
- `NEEDS_CONFIRMATION`：完整远端三项失败的堆栈；本节已区分已见日志与代码关联，实施后仍须以新一次 Windows job 结果验收。
- `NEEDS_CONFIRMATION`：用户此前要求跳过原生安装/增量/回滚工作，但当前 workflow 仍含原生生命周期步骤。是否要停用或拆分这些步骤是独立的流水线范围决定；本次不擅自改变。
- 原 run 重跑仍使用旧提交，不能验证尚未提交的夹具修复。下一阶段先按本节完成最小实现并本地验证，再经用户授权选择 PR 或推送方式；主分支 push 会进入现有自动版本/标签/发布流程。
- AI_CONTEXT 更新建议：若长期维护平台测试规则，可加入“缺失外部引擎夹具须满足目标平台的配置语法；分支/换行模拟不能替代原生平台”。本阶段不将未实施方案写入长期现状，也不改需求文档。
- 设计交付后停止；全栈实现入口为 20.3 的两个夹具修改与 20.4 的契约回归，不需要先重构 Office 插件或安装新依赖。

## 21. 撤除旧版 Office 转换插件（2026-10-09）

用户明确要求删除代码，当前撤除范围如下：

- 删除 `plugins/office-convert` 的清单、Schema、入口和 README；删除插件专属转换、发行与 GUI 测试。动态构建 hook 继续从有效官方目录收集资源，无需新增排除名单。
- 删除 GUI 中该命令专属的提示、文件筛选、主参数分类和二选一校验；保留通用 Schema 表单、SingleFilePicker/MultiFilesPicker、解析预览与 Evidence 行为。不重新设计其他界面。
- 从 CI 独立插件 ZIP 列表、安装后 smoke、Windows installer smoke 的命令集合删除两个 Office 命令；其余构建流程和原生安装/增量/回滚策略不变。
- 发行物支持集合为 8 个官方插件、9 个命令。源码与模拟安装布局、两个 smoke 入口、CI 打包名单需一致；用专门契约回归防止遗漏。
- 保留 Runtime 通用 `x-input-policy` 安全能力，将输入策略测试改为临时独立测试插件；保留 SDK 换行符、日志尾部与缺少可选 Qt 的回归。删除 CI 可移植性测试中对被撤除发行测试的嵌套调用，不屏蔽其余真实错误。
- 不新增依赖、不改存储结构、不删除旧任务/产物，不自动删除用户数据目录的插件或卸载 LibreOffice；用户已安装插件仍遵循现有插件管理机制。停止官方发行不等于全局禁用或强制卸载。
- 验收入口为完整 unittest、数据生成独立测试、编译检查、插件 ZIP 打包校验、发行集合契约和 diff 检查。实际 wheel/EXE/安装器构建与远端 Actions 状态另行报告，不能用本机源码测试代替。

## 22. Windows Unicode 安装路径修复（2026-10-10）

### 22.1 目标与已核实证据

- 本次按用户“打包报错修复”的明确请求实施最小修复，不改变第 21 节的插件撤除决定。
- 核验对象：[Build and release #47](https://github.com/DavisDing/TestBox/actions/runs/37912471789)，触发提交 `4d48ace604c6f710fb83c3370305310cc0e9c0b1`，实际构建提交 `f92049137dbb75cb15764d63bbf03443b39881af`（自动版本 1.0.20）。Linux 发行 job 成功；Windows job `113760699823` 的 577 项测试、安装后 wheel smoke、EXE 构建及冻结 Host smoke 均成功，四个安装器也已用 Inno Setup 6.7.1 编译成功。
- 失败发生在原生生命周期验收的 `install-cli`，不是编译或缺少 Office 引擎。[诊断产物](https://github.com/DavisDing/TestBox/actions/runs/37912471789/artifacts/11606823980) 中 `install-cli-inno.log` 明确记录：安装目标为 `D:\a\_temp\testbox-windows-installer-smoke\installations\CLI 安装`，`PrepareToInstall failed: TestBox 安装目录包含无效字符。`；退出码为 7。此前两项受保护目录拒绝检查已通过，后续生命周期检查未执行，Release 发布被跳过。

### 22.2 原因、修改入口与边界

[Inno Unicode 文档](https://jrsoftware.org/ishelp/topic_unicode.htm) 明确脚本 `String` / `Char` 使用 Unicode；[脚本函数文档](https://jrsoftware.org/ishelp/topic_scriptfunctions.htm) 的 `Pos` 参数为 `AnyString`。官方 [Pascal Script runtime 源码](https://github.com/jrsoftware/issrc/blob/main/Components/UniPs/Source/uPSRuntime.pas) 按第一个参数的字符串类型选择 Unicode、Wide 或 ANSI 分支。因此 `Pos('?', InstallDir)` 等以 ASCII 字面量为首参数的检查存在 ANSI 转换风险，无法表示的路径字符可能变为问号，误判正常中文路径。源码核验使用上游 main，并非本机执行 run #47 的编译器二进制；完整修复效果仍须原生 Windows 验证。

| 入口 | 本次修改 | 保持不变 |
| --- | --- | --- |
| 四个 `installer/TestBox*.iss` 的 `InstallationPathError` | 用 `for I := 1 to Length(InstallDir)` 逐个检查 Unicode 字符，只拒绝实际 `*`、`?`、`"`；四份守卫保持一致 | 空路径/根目录拒绝，规范化、短路径、链接/junction、用户数据与另一组件目录保护，增量执行前再次校验 |
| `scripts/smoke_windows_installers.py::NativeSmoke.setup` | 运行失败或超时时，将已有 Inno 日志解码后的末尾至多 4000 个字符带入异常及失败摘要 | 非预期退出仍失败，超时仍为 TimeoutError；缺失/空日志保留原错误，读取失败附加诊断；原有清理和日志上传不变 |
| 两个 Windows 测试模块 | 新增 Unicode 守卫源码契约、多编码诊断、截尾、缺失/读取失败、超时、真实子进程失败摘要回归 | 保留所有路径保护与四份守卫一致性断言，不 mock 出安装成功 |

不新增模块或依赖，不改产品 UI、用户数据、manifest、组件身份、自动版本或发布策略；不把中文 CI 目录改成 ASCII 来绕过问题，不关闭验收门禁。`PathsOverlap` 仍使用类型明确的 String 变量，不属于此次字面量检查修改。日志截尾只限制展示长度，现有解码函数仍读取整个文件。

### 22.3 验收与下一阶段

- 本机回归入口：两个 Windows 契约/runner 测试、完整 `unittest discover -s tests -v`、数据生成独立测试、compileall 与 diff 检查。字符谓词探针是源码关联测试，不是 Pascal 执行；真实 Python 子进程只验证退出码和日志/摘要处理，不是 Windows 安装器验收。
- 原生 Windows 必须允许既有 `CLI 安装` / `GUI 安装` 目录完成安装、Host 和受管文件验证，并继续通过受保护目录拒绝、合成增量、失败回滚和卸载保留检查。合成增量通过也不能替代相邻正式发行版本兼容性验收。
- `NEEDS_CONFIRMATION`：含本次修复的新提交的四个安装器编译及原生生命周期结果。重跑旧 run 使用旧代码，不能验证未提交修复。本机不具备 Windows/Inno 执行环境，不宣称远端已恢复。
- 下一阶段须先经用户授权选择提交/推送方式，再核验对应新提交的 Actions；默认分支推送会触发现有正式发布，不在本次本地修复中执行。
- AI_CONTEXT 更新建议：验收通过后在 9.1 补充“安装目录非法字符检查使用 Unicode 字符扫描；静默安装失败摘要包含 Inno 具体诊断”，无需改动需求基线或大规模改写长期事实。

## 23. Windows 卸载空注册表项修复（2026-10-10）

### 23.1 已核实故障与范围

- 用户本次明确要求修复 Actions，允许实施与失败原因直接相关的最小安装器修复；不进入业务功能或 UI 实现。
- 核验 [Build and release #48](https://github.com/DavisDing/TestBox/actions/runs/38012967847)，触发提交 `3c33a07284a22d546aa42f1a8a4439e2184c9dd7`，Windows job `114096935435` 实际 checkout 为 `07732040869a9de0f4a65212bf7726d9cc68b737`，自动版本 `1.0.21`。Python 发行成功；Windows 四个安装器编译成功，失败位于原生生命周期验收，Release 发布跳过。
- 保存的 `testbox-windows-installer-smoke` 诊断产物 `11655486862` 中，16 项检查通过，包含中文路径安装、CLI/GUI Host、拒绝非法更新、锁文件回滚、两组件合成增量、重放拒绝及完整重装。CLI 卸载退出 0，Inno 日志记录卸载成功；随后 summary 报 `Existing cli registration is missing InstallDir`，清理阶段 GUI 卸载退出 0 后也报同类错误。这不是第 22 节中文路径问题复发。
- 当前两个完整安装器的 `[Registry]` 仅声明 `uninsdeletevalue`，卸载删除 `InstallDir` 后留下空组件项；验收脚本对存在但缺少安装路径的注册表项保持 fail-closed，导致验收失败。

### 23.2 最小方案与修改入口

- `installer/TestBoxCLI.iss` 与 `installer/TestBox.iss`：仅为现有 HKCU 组件安装路径条目添加 `uninsdeletekeyifempty`，组合为 `uninsdeletevalue uninsdeletekeyifempty`。先删除本安装器登记的路径值，仅当组件项为空才删除该项；不会递归删除父项、另一组件、额外值或子项。依据 [Inno Registry 官方文档](https://jrsoftware.org/ishelp/topic_registrysection.htm)。
- `tests/test_windows_release_contract.py`：新增两完整安装器的精确注册表条目契约，将禁止递归 `uninsdeletekey` 的旧子串断言改为完整 flag 判断，明确允许安全的 `uninsdeletekeyifempty`，仍禁止无条件删项。
- 保留 `scripts/smoke_windows_installers.py` 的注册表异常拒绝、卸载后注册检查与仅清理本次自有安装的守卫；不把缺失 `InstallDir` 一律解释为未安装，不降低 Actions 门禁。
- 不改增量安装器、共享用户数据、组件身份、版本策略或工作流，不清理用户额外注册表内容。存在额外值/子项时保留组件项是预期安全行为，不承诺适用于任意残留的自动清理。

### 23.3 验收、风险与下一阶段

- 本地执行 Windows release 契约、smoke runner 回归与完整测试；这些是源码/模拟契约，不代表 Inno 原生卸载已通过。
- 新提交在 Windows Actions 上须完成原有全部生命周期验收，尤其 `uninstall-cli-preserves-gui-and-data`、`uninstall-gui-preserves-data-and-unregistered-files`，summary 为 `passed` 且无 cleanup_errors；继续保持用户数据/未登记文件和另一组件保护。合成增量仍不代表相邻正式发行兼容性。
- **NEEDS_CONFIRMATION**：修复提交在 Windows 的真实编译、安装、升级、卸载及发布结果；本机 macOS 不提供这些平台证据。未提交补丁不能通过重跑旧 run 验证。
- 本次不自动推送默认分支（该操作会创建正式版本）；下一阶段经用户确认提交/推送方式后核验新提交的 Actions。
- AI_CONTEXT 更新建议：待 Windows 验收通过后，在 9.1 的卸载边界补充“删除自有 InstallDir 值并仅移除空组件注册表项”，不改需求和其他长期事实。

## 24. 固定宽度与文件/Sheet批量处理（2026-10-10）

### 24.1 范围与职责

用户已授权实施 REQUIREMENT 第14节，不替换当前页面视觉，不新增插件命令、后台服务、数据库或外部依赖。复用 Schema 多文件控件、WorkspaceManager 文件数组暂存/哈希、Runtime/Host 任务及已有插件业务算法。

- `testbox/tabular.py::read_dataset` 新增 `format=fixed`，`widths` 为连续字段宽度正整数数组，`width_unit=characters|bytes`（默认 characters）。复用编码、记录分隔符、表头/起始记录、columns及标准化；长度必须精确匹配。字节模式支持 UTF-8（可带BOM）、GBK/GB18030/GB2312、Big5及列明的单字节编码，拒绝 UTF-16 等不支持的字节布局。位置包含记录/物理行、字段起始位置与宽度；不解析填充为空值。
- `testbox/dataset_batch.py` 经 SDK `run_dataset_batch` 组织独立单项：解析文件/Sheet选择→有界Excel元数据读取→配对计划→逐项执行插件原有 `execute_one`→独立报告及批次JSON/CSV。单项不是额外 Runtime 子任务，而是同一任务内编号单元，使用派生报告文件名，输入/日志/历史归属同一父任务。
- data-preview/data-compare/data-check/schema-diff 在原命令中增加批量入口，不导入另一插件；SQL仍由原算法保留 equal/different/inconclusive，批次不会把 unknown 升级为 equal。

### 24.2 参数、状态和数据流

- 单侧命令：`input` 或 `inputs`（文件路径数组）；两侧命令：`left/right` 或 `left_inputs/right_inputs`。单/多输入互斥，SQL批次不得混用直接文本。数组复用 `file-path` 暂存，最多100项、拒绝符号链接和重复来源。
- 读取配置使用 `options.sheets="all"` 或名称数组；两侧各用 `left_options/right_options`。`sheet` 与非空 `sheets` 互斥。对混合格式批次不应用 sheets；需分别执行。未指定 sheets 保持单 Sheet行为。
- `batch.pairing=name|position` 默认name；`batch.sheet_mapping={左Sheet:右Sheet}`必须一对一并使用name。文件名/Sheet名大小写精确匹配；去扩展名重名记AMBIGUOUS_PAIR，缺失记UNMATCHED。单文件对省略文件名匹配，便于比较不同文件名的两个工作簿。
- 可恢复单项异常记failed并继续，未配对记unmatched。全部单项执行成功才返回success；有执行异常/未配对返回failed并登记所有已生成文件。data.compare/data.check分别提供整批equal/passed，失败时为false；sql.diff失败时inconclusive，完整执行时依据所有项归纳。各子报告保留具体业务结果。
- 批次不同时保留全部完整数据集。逐项读取/处理，Host响应只返回轻量摘要和产物列表；预览显示数据存为独立display.json，完整样本继续存原预览产物。最终配对和展开都超过100项即拒绝；仍受现有进程超时和任务配额约束。
- GUI复用现有布局：多文件控件可添加目录当前层支持的文件；预览面板增加定宽配置/Sheet选择，批次结果下拉按文件/Sheet切换。输入或配置变化使旧预览失效，执行期间变更会丢弃过期响应。坏项目显示失败详情，不显示旧表格。
- Runtime新增只读 `get_task_artifact_path`，允许读取已结束成功/失败任务声明的产物，以查看部分成功预览；原 `get_task_output_path` 继续只允许成功任务用于后续输入，不降低此契约。

### 24.3 兼容、验证与交付边界

新增SDK入口要求新Core，四个更新插件将兼容下限设为1.0.23，不能把新ZIP装到1.0.22或更老Core并宣称可用。本地候选版本用既有release_version脚本统一为1.0.23，四插件源码版本1.1.0；正式版本仍以自动发布流程实际分配为准，不自行发布标签/Release。

验证入口：新增 `test_dataset_batch.py`（真实Runtime/Host和读取器）、`test_gui_preview.py`（offscreen真实Host）、完整unittest、数据生成独立回归、compileall及diff检查。模拟文件夹选择只证明选择/过滤逻辑，不代表原生文件对话框验收；GUI offscreen不代表Windows实机操作。CLI文件夹处理通过显式文件数组，不提供Core目录路径暂存或递归扫描，以保护输入快照边界。

**NEEDS_CONFIRMATION / 未验证**：新提交的远端发行构建、Windows原生GUI及大批真实文件耗时；本机测试通过不等于新发行已发布。批次没有实时逐项进度/取消协议，不扩展既有一次Host响应。AI_CONTEXT更新建议：验收后将公共读取格式与批次能力、SDK兼容下限更新至实际事实；本次不改写长期上下文。

### 24.4 本地验证记录（不是远端验收）

- 汇合后的完整测试：603项通过（Python 3.14.8、本机macOS），含15项定宽/批次契约和10项offscreen预览交互回归。数据生成插件独立3项通过；compileall和diff检查通过。
- wheel与sdist构建成功，四个更新插件ZIP打包成功。将1.0.23候选wheel安装至独立临时虚拟环境，从空工作目录用隔离导入执行既有installed smoke（evidence及所有新插件命令）通过。
- 安装后另行真实Runtime/Host验证字符定宽预览、全部Sheet预览、Sheet批量质量检查和定宽批量比对，均通过；导入路径确认为独立环境site-packages，不是源码checkout。
- 未提交/推送本批变更；未构建或执行该候选版本Windows EXE/安装器。既有1.0.22远端绿色结果不作为本次新增能力的发行验收。

## 25. 码值校验 SQL 生成插件（2026-10-13，设计与首期实现）

### 25.1 目标、状态与边界

本节对应本轮用户提出的新增需求：以测试清单描述源表/源码值字段、目标表/目标码值字段和适用码值域，以统一码值映射表提供码值域、源码值、目标码值、中文含义，生成可配置字段映射和数据库方言的校验 SQL。

需求汇总入口为 REQUIREMENT.md 第 15 节；本节 25.3/25.12/25.13 定义输入、条件与布局，25.4 定义判定，25.9 定义能力适配，25.14 统一定义输出方式、语句组合、结果表和新增验收。25.11 的单用例 SQL 仍只作为无附加条件的只读逻辑探针，不能代表新增输出能力。

状态更新：用户在设计交付后授权实现。25.1～25.15 的“尚未实现/本阶段不修改代码/拟定”等表述保留为设计时记录，不代表当前交付状态；当前修改与实际证据以 25.16 为准。常见 RDB/SQL-capable NoSQL 全覆盖仍是目标而不是完成事实。

- 用户已明确模板中的角色：`ODS` 为源表，`DWD` 为目标表；`a` 为源表别名，`b` 为码值映射表别名，`T` 为源记录关联码值映射后的派生关系。比较的是 DWD 的实际码值与 ODS 经映射得到的预期目标码值，不是默认直接比较两侧原始码值。ODS/DWD 是本模板的角色称呼，不要求用户实际库名、schema 或表名必须含这些名称。
- 用户进一步确认：性别、证件类型等码值域共用映射表；映射必须同时限定适用码值域与源码值，中文含义用于解释该域内的转换。码值域之外的多条限制条件必须可配置，具体契约见 25.12。数据库码值表和 Excel 码值映射文件两种输入方式都纳入需求；Excel 输入须覆盖同一 Sheet 同时包含两类信息、同工作簿两个 Sheet、两个独立工作簿，见 25.13。覆盖目标包括常见 RDB 和具备 SQL 查询入口的 NoSQL。本阶段只支持 SQL 脚本，不生成原生聚合管道、Cypher、查询 DSL 或其他形式；具体 SQL 能力矩阵见 25.9。
- `CURRENT`：本机检查对象为 `cfd5498`，任务开始时工作区无未提交改动；现有 `sql.select` 仅按字段清单生成 SELECT，并未实现本节的关联与码值校验。现有 SDK 提供 `read_dataset`，Runtime/Host 提供参数校验、文件暂存、任务历史和输出登记；可复用这些入口，不导入另一插件内部代码。
- `RECOMMENDATION`：独立新增 `code-value-check` 插件，命令暂定 `sql.codecheck`，不改变 `sql.select` 的旧参数和行为。插件名/命令名为拟定名称，不是已有命令。
- 本阶段仅交付需求整理、技术设计和信息缺口；本轮用户要求梳理需求，已将已确认范围整理到 REQUIREMENT.md 第 15 节。不修改业务代码、GUI、发布配置；建议默认仍标记 PROPOSED，不写成已实现事实。
- 仅生成 SQL 脚本和配置诊断；用户新增确认允许 SELECT 查询、INSERT 结果落表及前置 CREATE TABLE，见 25.14。插件不连接数据库、不执行查询、不采集连接口令、不增加数据库服务或驱动。任务成功只表示脚本生成成功，不表示业务一致或数据库实机验收通过；结果写入只发生在用户外部执行脚本时。
- 先按用例生成完整判定关系，再按配置独立输出或 UNION ALL 组合、生成查询或落表脚本。一条清单记录对应一个检查的规则及合并明细表分组见 25.13；不将多个字段查询合并为改变业务语义的关联优化。
- 范围外：自动发现真实库表/主键/数据库类型、跨服务器联邦查询、自动修正数据、任意 ETL 表达式推导、任意 SQL 片段拼接、实时进度协议。本次不重做其他界面。

### 25.2 用户模板的缺口与待确认项

以下区分已确认范围、运行时必填信息和建议默认规则。用户已授权按理解优化设计，通用流程不再因缺少真实表名或每项默认值逐一等待确认；真实库表/关联键不能猜测，作为运行配置校验。特殊规则与具体数据库兼容性仍不得伪称已确认。

| 编号 | 缺口 | 为什么需要 / 建议 | 状态 |
| --- | --- | --- | --- |
| CV-Q1 | 测试清单的载体、Sheet、表头与一行粒度 | 用户确认同 Sheet 含两类信息、同工作簿两 Sheet、两个 Excel 均需支持。独立清单一行一个检查；合并明细表显式拆分用例与映射两种粒度，配置见 25.13。`PROPOSED`：XLSX 为主要模板，同时复用 CSV/TSV/JSON | 三种 Excel 组织形式已确认；合并布局规则按设计实现 |
| CV-Q2 | 码值表输入方式 | 用户确认数据库码值表与 Excel 映射文件都支持；文件模式生成只读内联映射关系，不直接引用本地文件。各查询引擎的承载方式/资源上限仍需设计和验收 | 输入范围已确认；引擎实现未完成 |
| CV-Q3 | 适用码值域的业务含义 | 用户确认样表为编造数据，可将源字段名复制到独立“码值域”列作示例；正常数据两者不同。必须独立映射域列或明确配置域值，不从源字段名推导，见 25.13；源表逐记录动态域不是本例已确认的必需能力 | 分域规则及模拟样表取值已确认；实际域值是运行配置 |
| CV-Q4 | 源/目标关联键、键顺序、其他关联条件 | `PROPOSED`：支持不同名键和组合键，等值字段对构成完整业务关联键；实际键由清单/配置提供。缺少配置不猜 ID、不按码值关联 | 运行时必填，不阻塞通用设计；非等值关联另行确认 |
| CV-Q5 | SQL 引擎覆盖、版本与可见性 | 用户明确只要 SQL 脚本，覆盖常见 RDB 和具备 SQL 入口的 NoSQL；需按实际 SQL 引擎/版本核对关联、CASE、计数、内联映射与字段路径能力，不把“能执行 SQL”直接等同支持本模板全部操作。跨引擎数据访问未确认 | SQL 输出形式已确认；具体引擎/版本/能力矩阵为 `NEEDS_CONFIRMATION`，见 25.9 |
| CV-Q6 | “其他限制码值匹配区域条件” | 用户确认域之外可以有多条条件且须可配置；支持带类型常量、清单用例元信息与源记录等值字段对，全部参与匹配/重复检测，见 25.12。按记录日期匹配有效期需单独设计区间关联和重叠检测 | 多条件可配置已确认；OR/时间区间等特殊规则 `NEEDS_CONFIRMATION` |
| CV-Q7 | 空值、未映射、默认码与数据类型规则 | `PROPOSED`：无映射报异常、不回退；保留前导零/空格/大小写，不自动 cast；两空默认 `NULL_VALUE`，可显式启用两空相同，详见 25.10 | 已有安全默认，不阻塞通用设计；类型和可选策略运行时显式配置 |
| CV-Q8 | 源记录、目标记录及码值映射的唯一性 | `PROPOSED`：源目标关联一对一；同一完整映射键须唯一，即使重复行目标值相同也报异常；多个不同源码值可映射同一目标值 | 默认规则已形成；一对多/优先级取值需另行确认 |
| CV-Q9 | 核对方向、时间/批次范围与输出 | `PROPOSED`：默认目标驱动、全部明细；提供仅异常和反向缺失检查开关。汇总按完整明细统计；两侧时间/分区过滤分别配置，默认不猜业务时间 | 默认与可选项已形成；具体过滤值是运行配置 |
| CV-Q10 | 模板中的展示字段含义 | `PROPOSED`：表名/字段名是输出元信息常量；源字段名与源码值分别展示，不凭空增加第二个源业务列；另有转换表达式时单独说明 | 以明确建议收敛，不要求重复澄清模板占位词 |
| CV-Q11 | 输出与语句组合 | 用户确认可配置 SELECT/INSERT，INSERT 前置建结果表，支持 UNION ALL 等组合选择；拟以一个输出方式下拉和一个语句组织下拉表达，见 25.14 | 能力范围已确认；默认、结果结构及重跑规则按设计实现 |

真正尚未定案的是具体 SQL 引擎/版本覆盖矩阵及非等值关联、动态域、按记录日期选映射等特殊能力，不再把用户已经说明的目标或普通运行参数列为架构阻塞。用户实际样例用于下一阶段配置和方言验收，不是当前设计完成的先决条件；可索取脱敏清单 2～3 行、映射 5～10 行和相关 DDL，不能要求真实生产数据或数据库口令。

### 25.3 输入契约与两层字段映射

需严格区分“Excel 表头映射”和“数据库物理字段映射”。前者决定从文件哪列读取配置，后者决定 SQL 引用哪张表的哪一列，二者不能混用。

**测试清单的逻辑字段（建议模板）**：

| 逻辑字段 | 必填规则 | 含义 |
| --- | --- | --- |
| `case_id` | 可选；独立清单逐用例非空唯一；合并明细允许组内重复 | PROPOSED：合并明细的同一编号表示同一组用例，组内配置须一致，见 25.13.2。未提供时按独立用例行/分组首行生成稳定 CV-000001 等标识；编号校验在全部用例形成后执行，防止用户编号与自动编号碰撞 |
| `source_table` / `source_code_field` | 必填 | 源表及原始码值列 |
| `target_table` / `target_code_field` | 必填 | 目标表及实际码值列 |
| `domain_value` | 独立域列或明确配置的域值必填 | 用例适用的码值域标识，例如性别；与 source_code_field 独立配置，样表相等只用于模拟，见 25.13；展示名称与数据库域 ID 不同的情形须显式配置 |
| `domain_mode` / `domain_source_field` | 仅在另行确认动态域需求后开放 | 本例按固定域 `literal` 设计，不强制用户配置额外模式；动态域作为待确认扩展，不冒充必需字段 |
| `source_keys` / `target_keys` | 必填或引用明确的关联配置 | 非空、等长的字段数组，按下标一一对应；不按逗号盲目拆分标识符 |
| `join_profile` / `scope_profile` | 可选高级配置 | 仅大量用例确实需要共享配置时使用；不强制首期引入配置模板库，引用不存在即失败 |
| `enabled` / `description` | 可选 | `PROPOSED`：未映射 enabled 列默认启用；已映射时只接受 true/false 或明确列出的文本写法，空值/未知值失败，绝不按真假隐式转换；停用行计入跳过数并保留来源 |

库/catalog/schema 与表名分别保存；渲染前规范化为标识符段，逐段引用。若模板提供 `schema.table` 字符串，仅支持已声明的不含点号段名；名称本身含点号或引用字符时要求显式段数组，不能用简单 split 误拆。字段只能是列标识符，不接受 `CASE ...`、函数或带别名的任意表达式。

**表头映射例（拟定配置，不代表当前可调用参数）**：

```json
{
  "test_plan_columns": {
    "source_table": "源表名",
    "source_code_field": "源码值字段",
    "target_table": "目标表名",
    "target_code_field": "目标码值字段",
    "domain_value": "适用码值域",
    "source_keys": "源关联键",
    "target_keys": "目标关联键"
  },
  "defaults": {"domain_mode": "literal"},
  "code_table": {
    "table_parts": ["ref", "code_mapping"],
    "columns": {
      "domain": "DOMAIN_ID",
      "source_code": "SRC_CODE",
      "target_code": "DST_CODE",
      "code_label": "CODE_NAME"
    }
  }
}
```

此例仅说明固定域、数据库码值表模式。`PROPOSED`：单键单元格可直接填列名；组合键使用 JSON 数组，例如 `["customer_id", "batch_id"]`，也可用高级 `join_profile` 引用字段对数组。不按逗号猜测拆列、不将配置样例当作用户实际表名或字段名。

- 每个逻辑必填字段须映射到真实、唯一表头；重复表头、映射冲突、未知配置、空必填项和失效配置引用必须给出文件/Sheet/行号及原因。
- 明确优先级：清单行的显式值覆盖任务默认；显式空必填值是错误，不悄悄回退。未知域模式、不完整字段对、非法类型在渲染前统一拒绝。
- 测试清单和任何可选映射/配置文件均以顶层 `format: file-path` 参数进入 Runtime。当前工作区文件收集识别顶层文件属性和文件数组，不把嵌套对象里的路径当成已受暂存保护的输入。
- 同一 Sheet 含两类信息、同工作簿两个 Sheet、两个 Excel 都是合法用法。`PROPOSED`：文件模式保留 `mapping_source=same_workbook|separate_file`，独立配置 `file_layout=separate_relations|combined_rows`。same_workbook 复用清单已暂存的单一 XLSX/XLSM 文件，两个逻辑关系可选择相同 Sheet 的不同区域、不同 Sheet，或同一合并明细区域，不要求重复传 mapping_input；separate_file 才要求额外顶层映射文件。具体区域/粒度契约见 25.13，避免同文件重复暂存后不一致；不增加 Host 协议，文件路径可声明拒绝符号链接。
- 读取使用 SDK `read_dataset` 和独立读取配置，保留来源定位。支持集合建议限制为 XLSX/XLSM、CSV/TSV、JSON，不因为公共读取器支持 SQL/定宽文本就自动扩大本插件需求。
- 多 Sheet 工作簿须显式选定 Sheet；不自动遍历活动表并声称已读取全部。XLSX/XLSM 读取沿用现有可选 openpyxl，缺少依赖明确失败，不新增 Core 强依赖；不支持旧 XLS、不计算公式、不执行宏。
- 配置中的表名/字段名/码值不能来自公式文本；不依赖不可证明新鲜的缓存。当前 SDK 将单元格转为普通值，没有逐格公式来源元数据，不能宣称已能精准区分公式和内容相同的字面文本。`PROPOSED`：读取 formula 模式，涉及配置/码值的 Excel 字符串以 `=` 开头时保守拒绝，说明该值需要转为非公式纯值；不对所有文件的正常文本一概套此限制。若后续要支持这种特殊字面码值，再评估 SDK 的最小增量来源元数据，不在本次隐式扩展。Excel 数值单元格不能凭显示格式还原前导零，建议码值列使用文本，数值到文本转换必须显式配置并记录警告，不宣称已恢复原码。
- 已确认的 Excel 模式增加映射文件表头映射 domain/source_code/target_code/code_label，可追加 source_label 与额外维度列，并检查复合映射键。按方言选择只读内联映射关系；不生成映射表建表/插入或临时数据加载。用户选择 INSERT 时仅生成校验结果表的 CREATE TABLE 和 INSERT SELECT，见 25.14；插件不调用数据库。无法表达所选组合时明确失败，不把未支持组合算作兼容；映射行数上限待各引擎验证。

### 25.4 关联、码值匹配与结果判定

**用户确认的转换示例**：A 为源表，B 为目标表；以下码值映射来自用户说明，证件类型仅列已说明的一条，不补造其他码值。

| 码值域 | 源码值 | 目标码值 | 中文含义 |
| --- | --- | --- | --- |
| 性别 | 1 | a | 男 |
| 性别 | 2 | b | 女 |
| 证件类型 | 1 | a | 身份证 |

校验 A 的性别字段时，限定性别域：源值 1 的预期值为 a，源值 2 的预期值为 b；关联到同一业务记录后，检查 B 的性别字段是否等于该预期值。不能查到证件类型域的 1→a 就当成性别映射命中，也不应跨域把男与身份证比较。中文含义用于说明映射，不在尚未提出需求时增加中文名称字段 JOIN 或独立语义词典。

重要可观测边界：示例中两个域的 1→a 完全相同，目标值相等本身不能证明实际 ETL 使用了哪个域；本工具可验证“结果符合指定性别域的映射”，不能仅凭 A/B 码值证明转换过程选域正确。验收须补充跨域映射输出不同的合成反例，验证生成查询限定了域并能发现串域导致的结果差异；验证 ETL 内部路径需另有转换日志/血缘，不在当前插件中假定已存在。

**建议的一对一逻辑**：对每个用例，从过滤后的目标记录出发，按全部关联键和附加条件匹配源记录；再按 `码值域 + 源码值 + 必要的系统/区域维度` 匹配有效的码值映射，比较实际目标码值和预期目标码值。

- 本例固定域：映射表的域列与清单适用域值比较。若后续确认动态域，再与源记录的域列比较；不同域相同源码值不能串配。
- 三类筛选分别属于源表、目标表和码值表。源/映射限制应在各自派生关系或 JOIN ON 中应用，不能把被左连接表的条件直接移到最终 WHERE，导致缺失记录消失。
- 表内常量筛选建议支持 `eq/ne/in/is_null/is_not_null`，左项为所属关系的列，右项为带类型常量，条件默认 AND、列表长度有界。源目标关联与映射维度关联只支持等值字段对并归入完整匹配键；不把这些筛选运算符自动开放为任意跨表关联。非等值/OR/按记录日期的区间关联须另行设计每条目标记录的候选计数，不复用按等值键聚合的计数算法。“其他限制条件”不是任意 SQL 片段拼接授权。
- 关联键默认须非空；不能使用 COALESCE 哨兵把 NULL 键强行关联。不同名组合键按字段对渲染并用 AND 连接。额外键、租户、分区等参与匹配的维度必须同时参与唯一性检查。
- 未映射不得直接回退源码值或默认目标码；需要回退时须另行确认，并在输出中注明预期值来源。
- 映射存在通过专用命中计数/标记判断，不以 `target_code IS NULL` 判断；合法映射到 NULL 与没有映射是两回事。源记录也通过匹配计数/存在标记判断，不用可空源码值列代替存在性。
- 源缺失、重复或源码值 NULL 时，映射匹配尚未有效评估，输出 mapping_match_count 为 NULL，而非假称匹配数为 0；唯一有效源的映射数为 0 才表示未映射。异常汇总按最终状态统计，不把下游未评估的结果累计为第二个缺失故障。
- 建议在生成 SQL 中计算源匹配数、目标关联键重复数和映射匹配数，并先判异常再比较；不能只生成一条重复检查查询，然后让主明细仍显示“相同”。按完整匹配键聚合计数，只有映射数为 1 才提取目标码值；不能用 MAX/MIN 静默选中多映射之一。
- 若确需一对多、按生效日期或优先级选唯一映射，必须先确定取舍和冲突判定；不默认取第一条、不用 DISTINCT 隐藏扩行。
- 映射键唯一不代表目标码值唯一：同域的多个不同源码值允许转成同一目标码值；反向检查是源目标业务记录的存在性检查，不是把码值映射倒置。默认不按目标码值反查源码值。
- 完整映射键在本次用例筛选范围内唯一；常量筛选可先应用，来自源记录的系统/区域维度必须参与映射 GROUP BY 和 JOIN。不能遗漏维度后再用 MIN/MAX 选目标值；数据库排序规则及显式标准化后产生的键碰撞也须视为重复。

**计数与关联须同义**：对业务键、域、源码值和附加维度，参与 JOIN 的两端与 GROUP BY 必须使用同一声明的类型、标准化与字符比较规则。不能在映射列按区分大小写聚合后，却在源列按忽略大小写关联，造成多组同时命中、每组计数均为 1 并扩行。比较配置由引擎支持的有限类型/排序规则选项表达，应用于聚合与关联；任意 COLLATE 字符串不是允许的输入。未提供列类型/排序规则且无法证明两端兼容时，将该组合标为待配置/未验证，不能承诺一条输入一条结果。最终实际值与预期值的比较另行配置，不与键匹配混用。

计数只按完整匹配键分组；唯一候选取值可以使用已支持类型的聚合，也可以在计数为 1 时回连原始关系读取其值。读取动态源维度、长文本、标签时不能假定 MIN/MAX 对所有类型可用，也不能把动态维度加入源业务键 GROUP BY 后将一个重复源键拆成多个“唯一源”。先按业务关联键计数、后取得唯一源整行，再用其维度查映射。

**建议结果优先级（不是已确认验收规则）**：

| 条件 | `check_status` 建议值 | 是否可判为相同 |
| --- | --- | --- |
| 目标关联键为空 | `INVALID_TARGET_KEY` | 否 |
| 同一目标关联键重复 | `DUPLICATE_TARGET_KEY` | 否 |
| 源匹配数 = 0 | `SOURCE_MISSING` | 否 |
| 源匹配数 > 1 | `DUPLICATE_SOURCE_MATCH` | 否 |
| 动态域为空/不合法 | `INVALID_DOMAIN` | 否 |
| 源码值为 NULL 且未确认 NULL 映射规则 | `SOURCE_CODE_NULL` | 否；建议作为待处理异常 |
| 映射匹配数 = 0 | `MAPPING_MISSING` | 否 |
| 映射匹配数 > 1 | `DUPLICATE_MAPPING` | 否 |
| 唯一源/唯一映射下，实际与预期均 NULL | `PROPOSED` 默认 `NULL_VALUE`；显式开启 `nulls_equal` 后为 `SAME` | 默认否，可配置 |
| 唯一源/唯一映射下，仅一侧 NULL | `DIFFERENT` | 否 |
| 唯一源/唯一映射下，两侧非 NULL | 按明确比较规则为 `SAME` / `DIFFERENT` | 可以 |

源码值为 NULL 是否可对应特殊映射是独立规则，不能因为允许“目标与预期均 NULL 相同”，就自动允许 NULL 源码值正常匹配。空字符串、空格和字符串 `"NULL"` 不默认当作 SQL NULL；Oracle 对零长度字符值的处理需单独提示其无法保持与 NULL 的区分。

“严格比较”须明确层次：插件保留输入码值，不擅自标准化；数据库中的列类型、隐式转换、排序规则和尾部空格比较仍影响 SQL 结果。未提供 DDL/比较策略时不能承诺逐字节一致。允许的 trim/cast/大小写处理只能来自确认的有限配置，关联键和码值匹配两端应用一致，并在标准化后检查碰撞。

如果要覆盖源有目标无，建议另生成同一过滤范围下的源驱动 NOT EXISTS 检查，并返回 `TARGET_MISSING`。源空键和重复键的独立诊断也应覆盖无法匹配到目标的源记录。目标驱动明细不代表双方全量覆盖；“仅异常”作为完整判定后的外层筛选，不直接写 `actual_code <> expected_code` 漏掉 NULL 与缺失情况。

### 25.5 SQL 形状与数据库适配

按用户确认的角色，模板的数据流为：`ODS（源表） → 码值映射表 → T（含预期目标码值） → 与 DWD（目标表）的实际码值比较`；外层从 DWD 出发，按源目标关联键左连接 T。下述示意的 `d` 对应 DWD，`s` 对应 ODS 源候选，`m` 对应码值映射关系；角色不随 SQL 别名改变。

用户原模板的语法/语义需修正：`LEFT JOIN (...) T ON ...` 才是完整连接结构；`CASE` 比较须引用已经明确输出的列别名；字符串使用 SQL 单引号，而不是中文弯引号；表名/字段名若是展示元信息，输出为字符串常量，不写成假定存在的业务列。

建议编译器先构建有来源定位的单用例计划，再生成三个关系：过滤目标记录 `d`、按关联条件计数后的源候选 `s`、按完整映射键计数且只在唯一时提取预期码值的映射 `m`。以下是结果层的**逻辑示意，不是当前可直接执行的完整 SQL**；关系构造与引用方式由已确认的方言渲染：

```sql
SELECT
    <用例编号常量> AS case_id,
    <目标表名常量> AS target_table_name,
    <目标字段名常量> AS target_field_name,
    <目标关联键列>,
    d.actual_code,
    <源表名常量> AS source_table_name,
    <源字段名常量> AS source_field_name,
    <源关联键列>,
    s.source_code,
    <已解析码值域> AS code_domain,
    m.expected_code,
    m.code_label,
    s.source_match_count,
    m.mapping_match_count,
    <按 25.4 顺序生成的 CASE> AS check_status
FROM <过滤并附带键诊断的目标关系> d
LEFT JOIN <源关系> s ON <完整源目标关联条件>
LEFT JOIN <映射关系> m ON <码值域、源码值与区域维度条件>;
```

- “自适应”建议定义为：文件表头可映射，数据库物理字段可配置，选择数据库类型/版本后使用受支持方言渲染。离线文件不能证明真实数据库类型、对象存在或权限；不提供无依据的自动识别。
- 用户覆盖目标为常见 RDB 与具备 SQL 查询入口的 NoSQL，不再预先限制为 MySQL、PostgreSQL、SQL Server、Oracle、SQLite。上述 SQL 形状表达业务关系，按各 SQL 引擎可用语法渲染；具体产品/版本/功能与验收矩阵按 25.9 收敛。未实现或未验证的引擎不得仅因名称相近就归入已支持名单。
- SQL 标识符/字段路径引用、字符串/日期常量、NULL/缺失判定、文本转换、常量映射关系和可用查询结构集中在插件内的小型方言函数；复用业务计划，按 SQL 引擎做能力校验和渲染，不先引入 ORM、数据库驱动或完整 SQL AST 依赖，不实现非 SQL 渲染器。
- 现有 `sql-select` 的引用函数是另一插件内部实现，不能直接导入；可参考其测试思路。本次优先在新插件局部实现所需小范围渲染，只有多个插件真实需要同一契约时再评估 SDK 公共入口。
- 库/schema/table 分段引用，字符串常量转义单引号，字符串与标识符使用不同函数。拒绝控制字符和非法字段表达式；用户输入写入 SQL 注释时也须防止注释闭合/换行改变语义，优先作为安全字面量输出。
- 对象名大小写与数据库目录中的名称须一致；不能对 Oracle/PostgreSQL 的未引用名称盲目加引用后改变查找目标。不通过 lower/upper 猜测真实列名，配置需说明对象是精确引用还是按数据库常规未引用命名。
- 生成器必须拒绝未支持的方言/版本/类型组合；无明确方言时阻止“可直接执行”查询生成，不沿用 `sql.select` 的 `auto` 参数冒充兼容性证明。
- 表关联默认同一查询环境。跨 catalog/schema 是否可用需按实际数据库验证；跨实例访问、外部表和数据库链接不在首期配置中自动创建。

技术语义核对依据（2026-10-13，只用于设计，不代表数据库实机结果）：PostgreSQL 18 官方《Comparison Functions and Operators》说明普通比较遇到 NULL 得到未知；《Table Expressions》说明 LEFT JOIN 的 ON 与外层 WHERE 对保留未匹配行的影响；Oracle 19c 官方《Nulls》说明当前对零长度字符值的 NULL 处理。参考地址：

- https://www.postgresql.org/docs/18/functions-comparison.html
- https://www.postgresql.org/docs/18/queries-table-expressions.html
- https://docs.oracle.com/en/database/oracle/oracle-database/19/sqlrf/Nulls.html

### 25.6 模块职责、参数与产物

不修改 Runtime/Host/Context/Result 协议，不增加 SQLite 业务表。SDK 的 Excel 读取选项需针对有界区域做最小向后兼容扩展，见 25.13；不是已有能力。配置随任务输入/参数快照保存，复用任务历史追溯，不预设已存在的跨任务配置模板管理。

| 拟修改入口 | 职责 / 必要性 |
| --- | --- |
| `plugins/code-value-check/manifest.yaml` | 声明 `sql.codecheck`、无网络、输出目录写入、共享环境依赖；Core 下限按实际使用的 SDK 入口确认，不能机械照搬旧 sql-select 的版本下限 |
| `plugins/code-value-check/schemas/sql.codecheck.json` | 顶层文件参数、读取配置、表头映射、code_table、SQL 引擎/版本、关联/范围、多条 mapping_conditions、比较策略；output_mode/statement_layout 下拉枚举、结果表与列/类型/批次/建表配置见 25.14。文件模式复用快照与区域配置；插件做条件必填/能力校验，不依赖简化 Schema 未具备的条件组合能力 |
| `testbox/tabular.py` 与相邻读取测试 | 拟增加仅 Excel 使用的有界 excel_range 选项，复用既有读取/安全限制及来源定位；缺省行为保持不变，不修改其他格式与插件，见 25.13 |
| `plugins/code-value-check/src/main.py` | 生命周期、SDK 读取、输入完整校验、用例计划、统一结果投影、组合与 SELECT/CREATE/INSERT 方言渲染和 Result；同一判定逻辑复用，仅在确有规模需要时拆分插件内文件 |
| `plugins/code-value-check/README.md` 与示例 | 脱敏清单模板、字段配置、各方言支持边界、输出解释和异常示例；模板与 Schema 保持一致 |
| `testbox/gui.py`（下一阶段如需要） | 仅新增该命令的清单/表头映射/关联配置编辑与 SQL 结果预览；当前通用 object 表单是 JSON 文本，不把可视化列映射编辑器写成已有能力 |
| `.github/workflows/build.yml`、发行集合测试及 smoke 入口 | 增加独立插件 ZIP、官方插件/命令集合与安装后发现/生成验证；构建 hook 自动收录不等于静态发行/冒烟名单自动更新 |

数据流：`测试清单 + 显式配置 → Runtime 文件快照/参数校验 → Host → SDK 完整读取 → 表头映射/关联与域规则验证 → 用例计划 → 方言 SQL → output 产物与轻量摘要 → 历史/GUI/导出`。物理码值表内容在数据库执行时读取，插件离线阶段无法验证其真实唯一性；必须在生成 SQL 中保留相应检测。

所有受支持 SQL 引擎统一输出 `<task-id>.sql`；建议另输出 `<task-id>.plan.json` 作为追溯资料，不是另一种可执行查询形式。计划记录用例来源行/Sheet、解析后的表/列/键/域、SQL 引擎/版本、规则和查询序号，避免把全量内容放入 Host 响应。每份 SQL 明确对应方言，不宣称一份脚本可跨所有数据库直接执行。模板/映射数据可能敏感，产物不是自动脱敏数据，不将完整单元格内容打印到日志。

Result.data 只返回 case_count/statement_count/select_count/insert_count/create_count/engine/mapping_mode/output_mode/statement_layout/run_id/sql_generated=true/db_executed=false 等生成摘要；不返回业务 equal=true 或落库行数。语句数量按实际输出统计，INSERT 内部的 SELECT 不算独立 SELECT；生成成功不代表建表/写入/业务校验成功。

建议先校验全部清单和配置，再写正式 SQL；有非法行整体 failed，可返回有界诊断产物，不输出标为完整的部分 SQL。空清单、全部停用、超限、缺少依赖分别明确报告，不用零条语句当生成成功。`PROPOSED` 初始上限 1000 个用例、50 MiB 生成 SQL，并受现有任务/Host 配额约束；数值是实现保护建议，须用样例耗时验证，不能据此承诺吞吐量。Excel 映射只完整读取一次，建立域/维度索引后按用例选择映射；检测重复后再生成内联关系，不去重。复用 SDK 的行列/单元格限制；累计 SQL 按 UTF-8 字节数增量核对，超限拒绝而非截断。

### 25.7 GUI 功能与状态建议

建议沿用“工具目录 → 执行表单 → 任务详情”路径，仅定义本插件必要操作，不授权重做其他工具页面：

1. 选择测试清单、SQL 引擎与码值输入模式；文件模式选择同文件/独立文件、Sheet/区域和独立表/合并明细布局。展示两个逻辑关系的配置，不要求用户先手工拆 Excel；未实现的模式不在 UI 中伪装成已支持。
2. 通过现有异步 Runtime 数据预览查看清单/本地映射文件样本；物理数据库码值表只预览配置，不显示虚构码值行，不直连数据库。
3. 映射逻辑表头，配置源目标键、固定适用域、多条码值匹配条件和空值策略。条件行可增删编辑，展示字段、运算符、取值来源与全部生效条件；合并表预览原始行数与解析后的用例/映射数量。动态域不作为首期必填控件；正式校验以插件结果为准，不在 GUI 复制编译器。
4. 用“输出方式”下拉选择仅查询/仅落表/查询并落表，用“语句组织”下拉选择独立/UNION ALL。INSERT 类模式显示结果表、建表策略与类型配置，SELECT-only 不要求结果表；摘要列明生成的建表/写入/查询数量，明细见 25.14。
5. 展示生成 SQL、用例数、警告、计划文件和导出动作；无“执行 SQL”按钮。成功文案使用“已生成”，不能使用“校验通过”或“已落表”。

必须区分 Empty、Loading、配置/读取 Error、Generated、Warning、任务失败及已有运行机制能表达的取消状态。输入/Sheet/映射/方言变更使旧预览和生成结果不再作为当前配置结果展示；不新增 Host 实时逐条进度/取消协议。是否首期提供专用可视化映射编辑器还是先通过 JSON 配置运行为 `NEEDS_CONFIRMATION`。

### 25.8 验收、风险与下一阶段入口

以下是下一阶段建议验收，未执行、未完成，不以本设计文档存在作为实现证据：

| 验证重点 | 必须观察的结果 |
| --- | --- |
| 文件与配置映射 | 同 Sheet 合并列、同 Sheet 不同区域、同工作簿两 Sheet、两个 Excel 解析为等价逻辑计划；自定义表头、组合键/不同名键可用，全部关联来源保留。缺列、重叠区域、冲突配置、公式与超限明确拒绝；不修改来源，专项场景见 25.13 |
| 域与范围隔离 | 不同域相同源码值不串配；源表名/系统/区域/状态等多条件共同生效，完整键计数不遗漏动态维度，见 25.12。固定域正确，动态域仅在确认扩展后验收；跨版本重叠不静默选值 |
| 异常判定 | 源缺失、目标重复、源重复、零/多映射、合法映射到 NULL、两空/单空、前导零、不同类型均按已确认策略处理；重复/未映射不能标相同 |
| 覆盖方向 | 目标驱动保留无源记录；如确认反向检查，源有目标无亦可见；仅异常筛选不遗漏 NULL/缺失/重复状态 |
| 方言渲染 | 各已支持类型/版本的引号、大小写、中文/特殊名称、常量转义、NULL 与转换语义独立覆盖；未知方言拒绝，不把文本快照通过当兼容证明 |
| Runtime/发行回归 | 真实 Host、文件暂存、产物/计划登记、历史/导出与失败诊断；wheel 和插件 ZIP 收录；旧 sql.select 与其他插件无回归 |
| GUI | 映射/Sheet/方言变更旧结果失效、缺字段提示、SQL 预览/导出、生成与校验成功措辞分离；offscreen 契约与实机交互分别报告 |

实施后可使用既有 `python -m unittest discover -s tests -v`、安装后 `scripts/smoke_installed.py --python <目标虚拟环境Python>` 和 `git diff --check`；新增插件专项测试须另行补齐。支持数据库需在其实际版本上，以合成数据由用户/验收环境手动执行产物验证语法与结果，此过程不属于插件执行能力；不能用 SQLite 单库或本机源码测试证明 Oracle/SQL Server 等已验收。

主要风险：关联范围不完整导致串配/漏查、重复扩行被误判一致、NULL/隐式转换/排序规则导致错误判定、库表不可见、Excel 丢失前导零或公式缓存不可靠、内联码值规模造成 SQL 过大。均已对应到输入确认、计数/状态保护、显式比较策略和资源边界，不承诺通过配置自动消除所有真实库问题。

下一阶段以 REQUIREMENT.md 第 15 节已整理范围、25.10/25.12～25.14 的配置契约为入口，经用户授权进入插件/必要 GUI 实现。数据库映射与 Excel、仅 SQL 输出及三种输出方式已明确；具体引擎版本验收与特殊关联仍待定，不能宣称全市场适配就绪。设计交付后停止，不自行创建插件或修改发布流程。

AI_CONTEXT 更新建议：本次不加入未实现插件。原第 15 节“暂无固定宽TXT/批量多Sheet”与当前 `testbox/tabular.py`、`dataset_batch.py` 及本文件第 24 节存在长期事实滞后，建议单独修正其现状描述；不在本次设计中顺带改写。新插件完成并验收后，再加入实际命令、支持方言边界与“只生成、不执行”的长期事实。

### 25.9 仅 SQL 脚本输出与数据库能力适配（输出形式已确认）

用户明确本阶段“先支持 SQL 脚本，其他形式不用”。不再规划 MongoDB 原生管道、Cypher、Redis 命令或查询 DSL，也不再将是否允许原生 NoSQL 输出列为待确认项。常见 RDB 与具备 SQL 查询入口的 NoSQL 都属于适配目标；是否能生成本次校验脚本取决于实际 SQL 引擎能力，不取决于数据库被归为 RDB 还是 NoSQL。

SQL 入口可以是数据库原生 SQL/SQL 方言，或用户已有的 SQL 查询层。插件配置应记录实际执行脚本的 SQL 产品、版本、兼容模式及可访问对象；若通过外部 SQL 层访问 NoSQL，需要说明该层及既有对象映射，不能仅根据底层数据库名称选择方言。不安装/创建查询网关、索引、外部表或数据同步任务，不增加在线连接能力。

技术核对（2026-10-13，官方文档，不是实机验收）：Couchbase 的 SQL++ 提供 JOIN；Elasticsearch 提供 SQL 搜索与聚合入口，并另列 SQL 功能限制。因此认可“部分 NoSQL 有 SQL 入口”，但不能把 SQL 入口存在当作本次三方关联、计数和内联映射均可执行的证明；本设计不声称市面多数或全部 NoSQL 已兼容。

- Couchbase SQL++ JOIN：https://docs.couchbase.com/server/current/n1ql/n1ql-language-reference/join.html
- Elasticsearch SQL：https://www.elastic.co/docs/reference/query-languages/sql
- Elasticsearch SQL limitations：https://www.elastic.co/docs/reference/query-languages/sql/sql-limitations

**拟议适配组织（产品名单/版本仍待确认，不是已交付名单）**：

| SQL 入口类别 | 候选举例 | 适配要求 |
| --- | --- | --- |
| RDB SQL | MySQL/MariaDB、PostgreSQL、Oracle、SQL Server、SQLite、DB2、达梦、人大金仓、OceanBase、TiDB | 按产品/版本/兼容模式渲染引用、常量、关联、计数、NULL 与类型规则，不把兼容模式等同另一引擎全部支持 |
| NoSQL 自带 SQL 方言 | Couchbase SQL++、Elasticsearch SQL 等 | 核对实际 SQL 可表达的对象/字段路径、关联/子查询、CASE/等价条件表达、计数、缺失与 null、Excel 内联映射；不因有 SELECT 就承诺整个模板可用 |
| 用户已有 SQL 查询层 | 按实际查询层与连接器确认，不预设用户已安装某一产品 | 以查询层方言为执行目标，核对源/目标/映射的可见性、连接器功能与类型语义；不自动创建 SQL 层或跨库访问设施 |

复用同一用例业务计划（源/目标对象、业务键、码值域、映射、比较/异常规则），仅实现 SQL 能力校验与 SQL 渲染。必要时用引擎支持的等价 SQL 重写，但不能为了适配而省略码值域隔离、重复诊断或缺失记录检测。SQL 方言中的文档路径、数组、缺失与 null 差异须明确配置或限制，不强行当成普通标量列。

每个适配项至少记录 sql_engine/version/compatibility_mode/access_layer/data_shape/mapping_mode/join_support/case_support/count_support/inline_mapping_support/null_semantics/union_all_support/result_projection_support/create_table_support/insert_select_support/existing_table_policy/limits/evidence/status。状态区分 planned、implemented-unverified、verified、unsupported-combination；数据库/Excel 及查询/组合/落表分别验收。某 SQL 入口只能读时，SELECT 模式仍可成立，CREATE/INSERT 模式必须明确不支持，不能从 SELECT 成功推导写能力。

所选 SQL 引擎无法表达某个必要操作时，明确报告不支持的能力/组合及原因，不输出看似完整但丢失关键规则的 SQL，不回退成非 SQL 查询。需要新 SQL 层、导出数据后本地计算或跨库在线执行时，属于范围外新增工作，须另行确认。

剩余待确认：实际优先支持的 SQL 产品/版本、NoSQL 使用的 SQL 入口、字段/业务键配置、源/目标/映射在该入口下的可见性。输出形式已定案；上述适配信息只影响具体方言分支，不阻塞已明确的分域转换验证和双码值输入设计。

### 25.10 优化后的默认规则、最小模板与生成步骤

状态：`PROPOSED`。以下是按用户授权给出的设计默认，不是已实施功能或用户逐项确认过的业务规则；没有必要把每项都设为设计阻塞，但插件必须让用户看见和选择这些策略。

| 配置 | 建议默认 / 规则 |
| --- | --- |
| `mapping_mode` | 必填 database / file；两者互斥，不按路径猜模式。database 配置数据库映射对象及字段；file 配置表头映射、mapping_source、file_layout 与两个逻辑关系的读取区域，见 25.13。same_workbook 复用清单文件，separate_file 提供 mapping_input；禁止另一模式配置 |
| `sql_engine` / `engine_version` | 显式填写或选择已验证版本档；不自动识别。一个生成任务只针对一个 SQL 引擎，每个引擎分别生成脚本，不把多个方言混在一个文件 |
| `output_mode` / `statement_layout` | PROPOSED 默认 select_only / separate；枚举及六种组合见 25.14。插入模式前置建结果表；跨用例类型统一只作用于展示/保存投影，不改变码值比较 |
| 适用域 | 每条用例固定填写 `domain_value`；源表字段名不等于域 ID。域的文本/数字类型显式声明，不猜名称到 ID 的转换 |
| 业务关联 | 至少一组等值字段对；按顺序配对，禁止空键、重复字段和不等长配置。默认同一范围内一对一，可追加系统/机构/批次等键 |
| `nulls_equal` | 默认 false：唯一映射下两空为 `NULL_VALUE`；开启后两空才可为 `SAME`。单空为不同，源码值 NULL 默认 `SOURCE_CODE_NULL` |
| 类型与标准化 | 默认不 trim/casefold/cast、不补前导零、不自动回退码值。映射码值允许显式 text/numeric 类型，禁止布尔/对象/数组；文本模式遇到 Excel 数字单元格必须明确选择转换策略，不能静默改写 |
| 允许映射到 NULL | 默认 false；若明确开启，文件输入必须使用可区分的空值表示。JSON null 可用，CSV/XLSX 须配置专用 null token；普通空白或缺失目标码值默认输入错误，不把字符串 NULL 自动当空值 |
| `result_scope` / `store_scope` | 查询范围/保存范围分别配置，默认 all；errors_only 在完整状态判定后筛选 check_status <> 'SAME'。具体模式约束见 25.14，不用同一个参数同时表达查询与保存 |
| `include_reverse_check` | 默认 false，保持用户模板的目标驱动；开启则另输出源有目标无和源无效键诊断，不反转码值映射、不把反向查询混进目标驱动明细计数 |
| `include_summary` | 默认 false；开启后从同一用例的完整明细关系统计各状态和总行数，不从 errors_only 子集计算通过率。零业务记录报告无数据，不把 0/0 变成通过 |
| 启用状态 / 用例标识 | 缺列按默认启用和稳定序号处理；显式非法值失败。输入行数、启用行数、停用数、成功生成用例数分别返回，不只显示成功子集 |

**最小测试清单（示例，不是真实业务对象）**：

| 源表名 | 源码值字段 | 目标表名 | 目标码值字段 | 适用码值域 | 源关联字段 | 目标关联字段 |
| --- | --- | --- | --- | --- | --- | --- |
| A | gender | B | gender_code | 性别 | person_id | customer_id |
| A | certificate_type | B | certificate_code | 证件类型 | person_id | customer_id |

表头均可映射。源/目标键也可用任务默认，清单无需重复列出；行内配置与 defaults 的优先级须可见。只有缺列/未配置才继承默认，显式空必填值不回退。同一清单允许不同用例的业务键不同，不按表名预设统一主键。用户给出的原始五项配置之外，业务关联键是必要补充，不要求引入新的后台或模板管理模块。

文件映射与数据库映射均规范化为 domain/source_code/target_code/code_label + 关联维度。域/源码值/目标码值字段映射必须存在，码值中文列按用户模板映射；列存在但值为空可警告。source_label 为额外可选列，缺列时明确投影 NULL，不参与 JOIN。中文含义只能说明当前映射，不能凭同一份码值表证明该表本身语义正确；检验码值表质量需另一独立权威基线，不新增不存在的词典服务。

**完整映射键校验**：文件模式完整读取并做基本格式校验；仅在已声明本地比较与 SQL 匹配同义时，按域/静态条件预筛选及全部动态维度检查重复。不能证明同义时保留候选全集，将域/静态条件及计数放进 SQL，不能按 Python 字符串比较不可逆地删掉可能命中的行。静态可证明的重复在生成前拒绝，其余在 SQL 中诊断；基本格式检查不得只做抽样。数据库模式同样在 SQL 选中范围内计数，JOIN/GROUP BY 的规则须按 25.4 一致；本地判重不是数据库排序规则验收。

只有在静态比较规则同义且可证明筛选后为空时，才生成有类型空关系及用例警告；否则保留候选并由 SQL 判断是否 MAPPING_MISSING，不把本地查不到域当执行端必然无映射。不生成非法空 VALUES，不回退其他域。所选引擎无法表达所需关系时失败。整个映射文件为空/缺列/码值非法属于输入错误，与合法文件中某用例未命中不同。

**生成步骤**：

1. Runtime 暂存输入并记录 hash；插件经 SDK 完整读取清单/可选映射文件，保留 Sheet 和来源位置，验证模式互斥与所选 SQL 能力。
2. 解析用例和默认值，核对对象/列/关联字段、适用域、码值类型、表内范围；同工作簿读取复用一份快照。生成有界诊断，不产出部分可执行 SQL 冒充完整成功。
3. 应用源、目标、映射各自过滤条件；构造完整业务键和映射键，先计数、后取唯一候选。仅计数为 1 时输出源码值/预期码值，重复候选不被 MIN/MAX 随意选中。
4. 目标明细保持每条目标输入一条结果；组合键计数按各列 GROUP BY，不拼接字符串造键，避免分隔符/NULL/类型碰撞。有效映射是函数关系，不要求可逆。
5. 生成状态、统一输出投影，按独立/组合及查询/落表模式渲染；添加可选异常筛选、汇总和反向结果，保存 SQL/追溯计划与生成统计，见 25.14。SQL 执行后的业务行数、相同数、异常数不是插件已知结果，不提前填入生成报告。

**统计与一致性**：业务汇总的总行数应等于本用例过滤后的目标行数；重复目标键的各行仍可见并计为重复，源/映射重复不会笛卡尔扩张明细。反向查询另计源侧范围，不能与目标侧总数混加为同一个通过率。多条 SQL 由用户执行时须在其环境选择一致快照或稳定业务批次；插件不自动生成 BEGIN/COMMIT，不承诺相隔时间执行的明细/汇总天然一致。

**复杂规则边界**：日期有效期、动态码值域、非等值匹配、一对多与任意表达式均未由本次示例要求；不扩大首期范围。普通地区/系统等值维度可配置且必须参与计数。引擎不支持 CTE 时可渲染等价派生表，但不可关闭重复保护；缺少必要 SQL 能力时返回明确诊断。

筛选条件的空值语义须显式：eq/ne 不能接受 NULL 常量，应使用 is_null/is_not_null；IN 列表不得混入 NULL，避免三值逻辑与用户预期不一致。需要“值列表或空值”的组合条件属于另行定义的 OR 能力，不能用未经说明的 SQL 拼接绕过限制。

### 25.11 单用例参考 SQL 与设计验证

以下是单业务键、固定性别域、全字段为 TEXT、无附加过滤、数据库映射表模式的参考查询。对象名是合成夹具，非用户实际表。默认两空不判相同；该查询用于验证设计逻辑，不是新插件实现，也不是所有方言通用模板。字段路径、类型、维度、组合键及可选策略仍须按各引擎渲染。

```sql
WITH source_summary AS (
    SELECT record_key, COUNT(*) AS source_match_count,
           CASE WHEN COUNT(*) = 1 THEN MIN(source_code) END AS source_code
    FROM source_records
    GROUP BY record_key
), target_summary AS (
    SELECT record_key, COUNT(*) AS target_match_count
    FROM target_records
    GROUP BY record_key
), mapping_summary AS (
    SELECT code_domain, source_code, COUNT(*) AS mapping_match_count,
           CASE WHEN COUNT(*) = 1 THEN MIN(target_code) END AS expected_code,
           CASE WHEN COUNT(*) = 1 THEN MIN(code_label) END AS code_label
    FROM code_mapping
    WHERE code_domain = '性别'
    GROUP BY code_domain, source_code
), checked AS (
    SELECT 'CV-000001' AS case_id, '性别' AS code_domain,
           target.record_key AS target_key,
           CASE WHEN source.source_match_count = 1 THEN source.record_key END AS source_key,
           source.source_code, target.actual_code,
           mapping.expected_code, mapping.code_label,
           COALESCE(source.source_match_count, 0) AS source_match_count,
           CASE WHEN source.source_match_count = 1 AND source.source_code IS NOT NULL
               THEN COALESCE(mapping.mapping_match_count, 0) END AS mapping_match_count,
           target_count.target_match_count,
           CASE
               WHEN target.record_key IS NULL OR target.record_key = ''
                   THEN 'INVALID_TARGET_KEY'
               WHEN target_count.target_match_count > 1 THEN 'DUPLICATE_TARGET_KEY'
               WHEN source.source_match_count IS NULL THEN 'SOURCE_MISSING'
               WHEN source.source_match_count > 1 THEN 'DUPLICATE_SOURCE_MATCH'
               WHEN source.source_code IS NULL THEN 'SOURCE_CODE_NULL'
               WHEN mapping.mapping_match_count IS NULL THEN 'MAPPING_MISSING'
               WHEN mapping.mapping_match_count > 1 THEN 'DUPLICATE_MAPPING'
               WHEN target.actual_code IS NULL AND mapping.expected_code IS NULL
                   THEN 'NULL_VALUE'
               WHEN target.actual_code IS NULL OR mapping.expected_code IS NULL
                   THEN 'DIFFERENT'
               WHEN target.actual_code = mapping.expected_code THEN 'SAME'
               ELSE 'DIFFERENT'
           END AS check_status
    FROM target_records AS target
    LEFT JOIN target_summary AS target_count
        ON target.record_key = target_count.record_key
    LEFT JOIN source_summary AS source
        ON target.record_key = source.record_key
    LEFT JOIN mapping_summary AS mapping
        ON mapping.code_domain = '性别' AND source.source_code = mapping.source_code
)
SELECT * FROM checked;
```

该例仅在候选计数为 1 时提取码值，其他域不会参与性别域计数。空键行的 target_match_count 可能为空，但在最高优先级被判 INVALID_TARGET_KEY，不能作为有效关联。合成验证允许输入预期 NULL 以检查判定层；这不表示文件输入默认放行空白目标码值。

下一阶段专项回归须包含：正常转换/转换错误、同域缺失/跨域存在、跨域不同输出反例、重复映射（同值与冲突值）、重复源/目标键、前导零、合法映射到 NULL、空源值、空关联键、多对一映射和零业务记录。组合键、系统/区域维度、过滤范围和同工作簿两 Sheet 快照另行专项验证，不能以单键参考查询替代。

**本次设计验证记录（2026-10-13）**：从本节 SQL 代码块提取查询，在 Python 标准库 SQLite 3.54.0 内存数据库以合成 TEXT 夹具验证 20 个场景，均通过。场景含正常转换、错误转换、源/映射缺失、仅其他域有映射、跨域不同目标值、两类重复映射、源/目标重复、源 NULL、目标 NULL、唯一映射到 NULL 的双空/单空、NULL/空串键、多对一、前导零、其他域重复不干扰及空目标范围。每例均验证结果行数等于目标输入行数；重复映射未输出任意预期值，双空映射的命中数仍为 1。查询阶段启用 query_only，前后夹具行数保持不变；未连接外部库、未创建持久数据库、未修改业务代码。

此记录只证明参考查询在该 SQLite 版本及这些合成条件下符合建议状态规则；不证明插件已实现、Excel 文件内联已适配、GUI/发行物已验收，也不证明其他 SQL 引擎可执行。SQLite SELECT/聚合语义及 SQL++ 对缺失/NULL 的区别核对依据：`https://www.sqlite.org/lang_select.html`、`https://docs.couchbase.com/server/current/n1ql/n1ql-language-reference/datatypes.html`。本次仅运行参考 SQL 设计探针和文档差异检查，不重新引用历史 603 项测试作为新功能证据。

本轮自查将 source_key 投影限定为唯一源时展示，与 25.14 的实际源值契约一致；原 20 场景记录保留为当时查询的验证记录，本次修改与输出包装由 25.15 的新探针另行记录。

### 25.12 多条码值匹配限制条件（配置能力已确认）

用户明确“其他限制码值匹配区域条件”不止一条，也不局限于码值域。以下配置契约为 `PROPOSED`，数据库映射和 Excel 映射使用同一业务规则；不要求把额外条件写成手工 SQL。

每条用例的 `mapping_conditions` 为条件数组，默认 AND，建议初始最多 50 条。必需的域匹配和源码值匹配由编译器独立添加，不能通过该数组删除或覆盖。额外字段不限于四个核心映射列，可通过物理字段/Excel 表头映射声明源表标识、源系统、区域、租户、状态等维度。

| 条件项 | 配置含义与限制 |
| --- | --- |
| `mapping_field` | 映射关系中的逻辑列，通过字段映射取得数据库列或 Excel 表头；不是任意 SQL 表达式 |
| `operator` | 常量条件支持 eq/ne/in/is_null/is_not_null；跨源记录字段仅支持 eq，不自动扩展成非等值关联 |
| `value_source=literal` | 明确的带类型常量或有界列表；eq/ne 不接受 NULL，IN 列表非空且不含 NULL，空值使用专用运算符 |
| `value_source=case_field` | 取已解析清单用例的 source_table、source_code_field、target_table 或 domain_value 等白名单元信息；按用例解析为常量，只支持 eq，不是查询源数据列 |
| `value_source=source_field` | 指定源记录的列，与映射维度等值关联；该列需保留在唯一源候选中，并进入映射的 GROUP BY 和 JOIN |

is_null/is_not_null 不接收右值来源；literal 的文本/数字/日期等类型须与所选引擎能力一致，不凭 Excel 显示格式猜数据库类型。源记录维度为 NULL 时，普通等值不产生匹配，输出为 MAPPING_MISSING、mapping_match_count=0，并在 25.14.2 的维度槽保留该值；不自动变成 NULL 安全等值或跳过此条件。非等值/OR/有效期区间仍是未确认扩展，必须明确报不支持，而不是忽略条件。

清单缺少行级条件时继承任务 defaults.mapping_conditions；行级显式数组整体替换默认数组，不隐式追加或按字段吞并。显式空数组表示没有额外限制，但域/源码值两条必需条件仍保留。已映射条件单元格为空视为配置错误，不等同显式 []；不要给可继承字段预先填充 []，否则会吞掉任务默认。scope_profile 和行级条件同时提供时拒绝，引用只在当前任务参数中定义，不预设外部模板库。GUI 展示全部有效条件及来源；冲突、未知列/运算符、缺右值、失效源字段和无效类型均在生成前失败。

**拟定例子（维度名/常量为合成配置）**：

```json
{
  "mapping_conditions": [
    {"mapping_field": "source_table_id", "operator": "eq", "value_source": "case_field", "field": "source_table"},
    {"mapping_field": "source_system", "operator": "eq", "value_source": "literal", "value_type": "text", "value": "CORE"},
    {"mapping_field": "region", "operator": "eq", "value_source": "source_field", "field": "region_code"},
    {"mapping_field": "enabled", "operator": "eq", "value_source": "literal", "value_type": "text", "value": "Y"}
  ]
}
```

其业务匹配等价于以下 ON 条件示意，不是可独立执行的完整查询：

```sql
ON source.gender = mapping.source_code
AND mapping.code_domain = '性别'
AND mapping.source_table_id = <本用例源表标识常量>
AND mapping.source_system = 'CORE'
AND mapping.region = source.region_code
AND mapping.enabled = 'Y'
```

域及 case_field/literal 限制先作用于该用例的映射范围，再按域/源码值/全部 source_field 维度计数。上例的 region 必须参与聚合与关联；相同域/源码值、不同区域不是该区域下的重复，同区域内两条有效行才是重复。未满足条件的映射不能计入候选数，不能先按域/源码值聚合后才筛区域。静态限制可放映射派生关系内，动态等值维度放 JOIN ON，不移至外层 WHERE。

Excel 模式完整读取并校验相关列后，按 25.10 的同义条件决定是否允许本地预筛选；不能证明时保留候选，使用 SQL 筛选/计数，超出内联配额须失败，不能以删行换取小脚本。内联关系保留全部动态维度；SQL JOIN/GROUP BY 的比较规则须一致，不能认为加一层 GROUP BY 就自动覆盖所有排序规则差异。数据库模式用同一声明的规则筛选与计数。

专项验收：域、源表标识、系统、区域和状态同时生效；其他域/表/区域及停用行不会造成串配或假重复；当前完整键重复仍为 DUPLICATE_MAPPING；删除唯一符合条件的行后为 MAPPING_MISSING；行级替换默认、空数组、源维度 NULL 和非法配置均符合上述规则。不得以单条件测试通过代表多条件计数已验证。

### 25.13 用户样表检查与三种 Excel 组织形式（输入范围已确认）

**样表事实（2026-10-13）**：原始测试范围与码值表都是单 Sheet（Sheet1），第 1 行表头、第 2～40 行数据，无公式、合并单元格、Excel Table 或命名区域。用户进一步说明样表为编造数据，并要求将测试范围“源字段名”复制一列作“码值域”；补充副本增加该独立列，原始四列和 39 行保留。该模拟取值不构成正常数据中域等于字段名的规则；这里只记录支撑设计的结构和统计。

| 文件角色 | 实际列 / 统计 | 设计补充 |
| --- | --- | --- |
| 测试范围 | 原始列为源表名、源字段名、目标表、目标字段名；补充列为码值域；39 行，原始四列组合只有 2 种，分别重复 19/20 行 | 五列表头分别映射 source_table/source_code_field/target_table/target_code_field/domain_value；不能按行号与映射表配对，仍需业务关联键及重复配置策略 |
| 码值表 | 源表、码值域、源码值、源码值含义、目标码值、码值中文；39 行，两个域各 19/20 行 | 增加源表标识维度与可选 source_label；按源表+域+源码值检查，样表无重复完整键 |
| 码值类型 | 一个域的两侧码值均为文本，另一个域均为整数；39 行均为源码值等于目标码值 | 类型策略应可按用例/域配置，不能强制全文件一种类型；该样表只覆盖恒等映射，非恒等转换需合成夹具补验 |

补充后的测试范围独立“码值域”列映射为 domain_value；本次依用户要求复制源字段名的纯值，仅为了让模拟样表完整。正式配置从该列读取真实域标识，或由按用例明确配置的域值提供；源码值字段与域值各自保持含义，不引入 domain_binding=source_code_field，不因两者在模拟表中相等就自动推导。域配置缺失则失败，不能从中文含义或映射表第一条猜域。源/目标业务键、schema/catalog、SQL 引擎/版本仍须配置；样表不能提供这些数据库事实。

建议将码值表“源表”映射为 `source_table_id`，追加 `case_field=source_table` 条件，使该样表的范围为源表+域+源码值。这是可见的配置建议，不擅自规定所有映射表都必须有源表列；其值是否包含 schema/catalog 也须显式约定，不随意去掉前缀或只取末段。源码值含义映射为可选 source_label，仅用于解释；码值中文映射为 code_label，不用两个中文字段相等代替转码校验。

#### 25.13.1 两个逻辑关系，不绑定文件数量

读取后统一得到 `TestCases` 和 `CodeMappings`，物理布局不改变分域、条件、计数和异常规则。文件输入仍走现有顶层 file-path 暂存；同一工作簿只存一份快照，各关系独立配置 Sheet、区域、表头和字段映射。

| 用户组织方式 | 拟定配置与解析 |
| --- | --- |
| 一个 Excel、一个 Sheet，合并列包含两类信息 | mapping_source=same_workbook、file_layout=combined_rows；读取一个区域，对每行分别投影用例配置和码值映射，按 25.13.2 分组，不生成每个码值一条重复检查 SQL |
| 一个 Excel、一个 Sheet，两个独立表块 | mapping_source=same_workbook、file_layout=separate_relations；配置同 Sheet 的两个不重叠矩形区域，可上下或左右排列，独立表头/数据起止位置 |
| 一个 Excel、两个 Sheet | mapping_source=same_workbook、file_layout=separate_relations；分别选择清单 Sheet 与映射 Sheet，不读取活动 Sheet 冒充两表均已读取 |
| 两个 Excel | mapping_source=separate_file、file_layout=separate_relations；分别选择文件、Sheet/区域、表头与字段映射。路径实际相同则提示改为 same_workbook，不重复暂存 |

同 Sheet 合并列和双表块属于第一种需求的两类布局，不要求用户必须改成两 Sheet。多个 Sheet 的其他内容不自动当清单或映射；不靠同名 Sheet、行数相等、格式颜色或上下行位置推导关系。可以展示候选表头供选择，正式生成必须使用已确认的读取配置；输入中的说明文字不是插件执行指令。

#### 25.13.2 合并明细表的粒度与重复规则

合并明细表的一行代表“用例配置 + 一条该用例适用的映射”。至少包含源表/源字段/目标表/目标字段、域/源码值/目标码值/中文及配置需要的其他维度；无需重复两列源表名，同一个物理表头可同时映射到不同逻辑关系。含义不同的两个字段则必须分别配置，不能把“源码值”列误认成“源字段名”。

显式 case_id 存在时以其分组，同一 ID 的表/字段/域/键/过滤/条件/类型策略必须一致；无 ID 时以这些全部已解析配置组成的完整用例签名分组，不仅按源表名或域分组。每组生成一个用例，按首个物理行确定稳定序号，保存该组全部来源行。组内各映射保留其原始行，同一完整映射键重复仍失败，不因用例配置重复就对映射去重。

同一映射可被不同用例引用；合并模式按用例保存关联，不将跨用例重复出现的映射行无条件合并成全局唯一表。两个目标字段分别使用相同源域时可以有两个用例，不能把它们折叠。单行不完整、只有清单信息但没有映射信息的空映射行默认失败；需要独立记录两类不同粒度时选择双表块/两 Sheet/两文件，不在 combined_rows 隐式混合记录种类。

独立清单保持“一行一用例”的契约；本次样表的 39 行重复四列配置不能直接证明是 39 个不同测试或仅 2 个测试。`PROPOSED`：完整配置重复默认报 DUPLICATE_CASE_CONFIG，允许显式 `duplicate_case_policy=merge_identical` 合并完全一致的用例并保留所有来源行/合并数，避免用户手工删行；此策略不合并码值映射。不同显式 case_id 不自动合并，只提示配置重复。差异键/范围/条件/域/策略意味着不同用例，不受该折叠策略影响。

应用样表的独立域列表头映射及相同业务键/条件后，选择 merge_identical 可形成 2 个用例和 39 条映射；不按两文件第 N 行配对，也不按 19/20 的行数分界硬编码规则。数据行数、用例数、映射数、折叠行数分别展示，不能把 39 条映射称为 39 次数据库校验结果。

#### 25.13.3 读取入口的实际缺口与最小增量

`CURRENT`：SDK read_dataset 接受 sheet/header_row/start_row；Excel 分支按整 Sheet 读取并要求每列表头非空、唯一，不能直接裁剪矩形区域，左右表块的空列或重复表头会失败，上下表块也没有终止行选项。现有多 Sheet 批处理是独立任务单位编排，不是两个业务关系的组合器，不能据此声称单 Sheet 分区能力已存在。

`PROPOSED`：仅向共享读取器增加可选 Excel `excel_range`（显式有界 A1 矩形，如 A1:D40），通过 SDK 原入口传入，不另写一套绕过安全限制的 Excel 读取器。未配置时保留旧行为；不支持整列/整行、多个非连续区域、外部工作簿引用或表达式。Sheet 单独配置，范围只表达行列界限。表头与 start_row 使用原 Sheet 的绝对行号；范围缺省表头为区域首行，数据到范围末行结束；显式超出范围或 start_row 不在表头之后时失败。

读取器只向关系解析器提供所选列/行，保留原始 Sheet、物理行号和列坐标，不把区域首行重编号为 1；空行跳过不改变来源行号。范围内表头仍需唯一非空，跨两个独立区域可各自拥有“源表”等同名表头。区域配置忽略其他区域属于用户明确选择，不视为自动扫描或全部读取；报告记录有效范围。保留既有 ZIP 展开、行列/单元格、文件/任务资源限制和公式规则，不用小范围选择绕过全文件安全检查。

插件负责校验两个独立关系区域是否相交；相交拒绝并建议 combined_rows。同 Sheet 的 combined_rows 必须使用同一读取快照/区域，投影后走统一计划编译；不同 Sheet 的矩形坐标相同不算相交。相关修改入口仅 tabular.py、相邻 SDK 读取测试、新插件 Schema/解析和必要预览，不修改 Runtime 顶层文件收集、Host 协议或其他格式默认行为。

#### 25.13.4 验收与未决配置

下一阶段以脱敏合成数据构建同 Sheet 合并列、同 Sheet 左右/上下表块、两 Sheet、两文件等价输入，验证得到相同的用例、适用映射、全部限制条件与 SQL 业务语义，追溯位置按各布局分别保留。必须增加“源字段名与码值域不同”的用例，例如源字段 gender、域 GENDER，确保 SQL 使用独立域值而非字段名。专项覆盖非第一行表头、区域边界、不相交/相交、多 Sheet 选择、重复表头、空值/公式、混合域码值类型、case_id 冲突、显式折叠、不同行级条件、码值重复及布局切换后旧预览失效。

SDK 区域增量需运行既有全 Sheet/其他格式读取回归及新区域专项；Runtime 验证同文件仅一份暂存输入、所有来源 hash 可追溯、输入不修改。四种物理布局均须经真实 Host 生成产物，不能把本次读样表成功等同插件已支持。

已定案的是多条件可配置、三种文件组织均支持、仅生成 SQL，以及码值域与源字段名独立配置；本轮新增输出范围及需求基线见 25.14 和 REQUIREMENT.md 第 15 节。实际关联键、物理对象、SQL 引擎版本及类型策略仍需运行配置。本阶段仅整理需求、设计与已请求的模拟 Excel 副本，不改业务代码。

### 25.14 输出方式、语句组合与结果表（新增范围已确认）

用户要求额外配置“是否组合语句”“是否生成 SELECT/INSERT”，INSERT 脚本前部声明结果表 CREATE TABLE。本节落实该范围；参数名、默认值、重跑规则及物理字段类型为 PROPOSED。此前 25.1 的只读产物边界已按本轮要求调整，所有模式仍由用户自行执行生成脚本。

#### 25.14.1 配置项与六种组合

| 参数 / 控件 | 契约与默认 |
| --- | --- |
| output_mode / 输出方式下拉 | select_only（仅查询，默认）/ insert_only（仅落表）/ select_and_insert（查询并落表）；不设两个独立开关形成全部关闭状态 |
| statement_layout / 语句组织下拉 | separate（每用例独立，默认）/ union_all（保留明细组合）；独立文件内多条语句不等于 UNION ALL |
| union_batch_size / 每组用例数 | 仅组合模式可配置，建议默认 50、初始允许 1～100 且受引擎更低限额约束；只按完整用例边界分组，不截断某用例的业务行。总 SQL 大小仍受 25.6 配额约束 |
| result_table / 结果表配置 | INSERT 类必填对象分段、列名映射、文本类型/长度配置；建议默认按任务 ID 生成安全表名，超出引擎标识符限制使用稳定 hash 后缀，保留原值到追溯计划 |
| create_policy / 建表策略 | create_new（默认，表已存在则执行失败）；可选 create_if_missing 只对能力已验证的引擎开放，前部仍有建表声明/方言等价结构；不生成 DROP/TRUNCATE/DELETE 或 ALTER |
| run_id / 校验批次标识 | 默认生成任务 ID，可填写非空批次值；长度与常量转义需校验。脚本内为常量，不用 CURRENT_TIMESTAMP 隐式充当唯一批次，也不冒充实际执行时间 |
| result_scope / 查询范围 | all（默认）/ errors_only；仅控制独立明细 SELECT 或已存结果的查询，insert_only 不适用；在完整状态判定后过滤 |
| store_scope / 保存范围 | INSERT 类 all（默认）/ errors_only；只控制保存内容，不随输出模式隐式改变。select_and_insert 的查询范围不能宽于保存范围；该模式开启全量汇总时必须 store_scope=all，冲突拒绝 |
| include_summary / include_reverse_check | 默认 false；可选汇总与反向关系沿用既定语义，反向输出同样注明方向，汇总不能从仅异常子集算通过率 |
| result_columns / 结果列名 | 按下述固定逻辑字段映射物理名字，可中文；不能删除必需诊断/追溯字段，不能更改其意义、注入表达式或让两个字段映射为同名 |

以上输出/组合/建表/批次/结果结构为任务级配置，不支持用例行悄悄改变，使同一结果集保持同一结构；关联、码值域、条件和比较策略仍可按用例配置。SELECT-only 的结果表配置不参与执行计划；GUI 可暂存隐藏字段用于切换，提交时清除无关参数，CLI 提供不适用参数时返回明确错误。

当前 SchemaValidator 会先递归应用默认值，之后插件无法仅凭已补默认参数区分“用户显式提供”与“Schema 默认注入”。因此 store_scope/create_policy/union_batch_size 等条件适用参数不在 Schema 层设置无条件默认；插件先按模式验证是否适用，再对缺失的适用参数应用默认。顶层 output_mode/statement_layout 等始终适用参数仍可使用 Schema 默认，避免 select_only/separate 被自动注入无关参数后错误拒绝。

| 输出方式 | separate | union_all |
| --- | --- | --- |
| select_only | 各用例独立明细 SELECT | 按组生成一个保留所有成员用例行的联合 SELECT |
| insert_only | 脚本前部一份结果表 CREATE，各用例显式 INSERT 列 + 校验 SELECT | 前部一份 CREATE，每组 INSERT 列 + 联合校验 SELECT |
| select_and_insert | 前部 CREATE、各用例按 store_scope INSERT，随后按 run_id 与 case_id/方向查询已存结果 | 前部 CREATE、按组按 store_scope INSERT，随后按 run_id 与本任务用例/方向查询已存结果；可拆分落表语句，但不影响统一查阅 |

INSERT SELECT 内的 SELECT 是插入数据来源，不是独立“查询结果”语句；仅落表未开启汇总时不附加 SELECT 结果查询。include_summary 是明确请求额外汇总 SELECT 的独立选项，即使 insert_only 也需在界面标明“额外生成汇总查询”，避免误解。

#### 25.14.2 统一明细投影与物理结果结构

所有查询/落表模式先生成相同的逻辑明细；以实际目标码值和预期目标码值的原有声明类型完成比较和候选判定，再转为跨用例的统一展示/保存类型。不能为了 UNION ALL 或落表先把数值转文本再比较，导致排序规则、精度、前导零等判定改变。

| 逻辑列 | 含义与类型约定 |
| --- | --- |
| run_id / case_id / check_direction | 批次、用例和 target_to_source / source_to_target 方向；非空文本，生成过程定位另保存在 plan.json |
| source_table_name / source_field_name / source_code | 源对象展示名、源字段名、实际源码值；码值为可 NULL 文本显示，源缺失或重复时不得任选值 |
| target_table_name / target_field_name / actual_code | 目标对象展示名、目标字段名、实际目标码值；原始目标行即使业务键重复也保留 |
| code_domain / expected_code | 用例适用域与唯一有效映射所得预期码值；无映射/多映射的预期值为 NULL，不能静默回退 |
| source_code_type / actual_code_type / domain_type / expected_code_type | 对应值的显式计划类型标识，帮助区分文本/数字/日期等；显示为 NULL 不覆盖声明类型 |
| source_label / code_label | 源码值含义可选，未配置时 typed NULL；码值中文按映射列取得，值为空或没有唯一映射时 NULL；不将其加入 JOIN |
| comparison_result / check_status | comparison_result 为 SAME/DIFFERENT/NOT_COMPARABLE，GUI/展示可译相同/不同/无法比较；check_status 保留 25.4 的细分状态 |
| source_match_count / target_match_count / mapping_match_count | 候选数量，使用引擎支持的非截断整数类型；下游未评估仍为 NULL，不把源缺失的映射数量写成 0 |
| source_key_N_name/value/type、target_key_N_name/value/type | N 从 1 到本任务对应侧最大业务键数；分别保存键名、文本值和声明类型，不拼接分隔符造键，不同长度用带类型 NULL 占位 |
| mapping_dimension_N_name/value/type | N 从 1 到本任务最多动态 source_field 条件数；保存源字段名、唯一源的实际维度值和声明类型。缺失/重复源时值 NULL，不任选候选；某用例没有该槽时 typed NULL，常量条件另由计划追溯 |

键槽/动态维度槽数量由生成任务的全部有效用例计算，所有分支列顺序相同；source/target 两侧分别计算最大键数。在无唯一源时 source_key 实际值保持 NULL，不把目标值伪装成已找到的源记录；目标键已保留，可结合计划重建源侧搜索条件。源表字段的未知/不存在只能在外部执行时由数据库验证，插件离线只能检查配置结构和已提供元数据，不能声称已查验真实列。未来自定义展示业务列须另定义来源/固定投影，不开放原始表达式。

comparison_result 仅在最终状态 SAME 时为 SAME、DIFFERENT 时为 DIFFERENT，其余异常为 NOT_COMPARABLE。NULL_VALUE 不强行表示“不同”；开启 nulls_equal 且唯一有效源/映射时允许 SAME。不会因重复映射的多个候选碰巧同值就标相同。反向关系的 TARGET_MISSING/源键异常按同结构补空缺列，方向标为 source_to_target，不纳入正向通过率。

字段名映射作用于 DDL、INSERT 显式列清单和 SELECT 别名，一处规范化后复用。默认使用便于跨产品的 ASCII 名字，用户可选择中文物理列名，但须按所选引擎逐段引用并检查长度/唯一性；表名字符串和物理对象引用分别处理。默认结果表不以业务键建立唯一约束，避免重复目标输入被吞掉或写入失败后丢失诊断；不预设有自增键、索引或已有结果管理数据库。

**物理类型契约**：按引擎生成支持中文的字符类型、计数类型和可空性。建议初始文本长度为批次/用例 128、对象展示名 1024、字段/域/标签/单个键值 512、码值 4000、状态 64；这些只是待引擎验收的配置建议，不能将其硬写成所有数据库通用 VARCHAR 长度。配置可选择已支持的较长字符类型/长度，禁止原样接受任意类型 SQL。跨用例所有投影，包括常量和 NULL，都采用一致类型；保留 NULL，不 COALESCE 为字符串 NULL 或空串。长文本是否支持组合/落表、字符与字节长度单位、Unicode 与引擎的组合/行宽限制均进能力矩阵。

插件能离线检查配置和本地文件值的长度，不能知道业务数据查询返回值的最大长度。引擎分支需明确超限的拒绝行为，不允许用默认截断 cast 生成假一致结果；没有可验证的保真文本转换/无截断策略时，拒绝该结果类型组合，要求合理的类型配置。不同用例码值类型不兼容时不能只依赖数据库隐式转换。不具备统一投影能力时报告具体列和用例，而不是降级为去重或删除该列。

固定长度文本转换不得先截断再写结果表；超长值即使不改变已算出的状态，也会丢失诊断证据。能采用无损较长类型的引擎优先使用，不能时选择可验证的超限失败策略。槽展开后还须检查总列数、标识符数/长度和引擎行宽，不只检查每列长度。插入/组合不支持的长文本类型明确拒绝，不能假定码值长度 4000 总能配合 UNION ALL。

#### 25.14.3 组合算法与 SQL 方言渲染

业务编译顺序为：读取并验证全部输入 → 每用例完整候选/状态关系 → 统一结果投影 → 可选异常选择 → 用例分组 → SELECT 或 INSERT 包装。组合只拼同结构最终关系，不直接把不同源表记录 UNION 后重新关联，不跨用例消除重复，不使用去重 UNION 替换 UNION ALL。每用例 case_id 和方向随行保留，组合无数据的用例仍在计划里存在，并可通过汇总显示 NO_DATA。

每组 CTE/别名按用例及关系生成稳定唯一标识，防止两个用例都叫 mapping_summary/checked 造成碰撞；内联 Excel 映射须仍限定各用例范围。不能把多个含 WITH、末尾分号或 ORDER BY 的独立 SQL 字符串直接用 UNION ALL 连接。引擎支持时提升命名 CTE 到组合语句顶层，否则按可验证的等价派生关系重写；所有分支使用显式列，禁止 SELECT * 随代码列顺序改变。

分支不默认分页/排序，组合不保证行顺序。业务范围由过滤条件决定，预览行数上限不进入正式 SQL。将来需要正式结果排序，须显式针对最终关系配置且独立检查引擎能力；不把 GUI 样本排序当数据库输出承诺。union_batch_size 只控制每条 SQL 的用例数，不使用 LIMIT/TOP 省略业务行；无法满足引擎大小上限就失败，不静默丢用例。

组合预算按最终渲染后的实际 SELECT 分支、CTE 数和 SQL 字节数计算；每用例的反向诊断及 Excel 常量关系也可能包含多条分支，不能只以用例数作为引擎限制。超出可拆分预算时只在完整用例间分组，并在 plan 中记录分组；单用例也超限时失败。不同组仍是多个执行语句，select_and_insert 只保证查询反映已保存结果，不能保证各组读取源数据的时点相同。

语义核对依据（2026-10-13，官方文档，未执行数据库验收）：PostgreSQL 18 Combining Queries 说明组合分支须同列数且对应类型兼容，UNION 默认去重而 UNION ALL 保留重复；CREATE TABLE 说明 IF NOT EXISTS 不保证已有对象结构符合当前建表声明。这两点支撑本节统一投影、保留重复诊断与已有结果表契约，不能证明其他引擎采用相同语法。参考：`https://www.postgresql.org/docs/18/queries-union.html`、`https://www.postgresql.org/docs/18/sql-createtable.html`。

CREATE TABLE、WITH 与 INSERT 的相对位置、Unicode 字面量、结果字符类型、常量关系和脚本终止符交由具体方言渲染；不能把一份固定模板认作跨库可执行。默认不生成事务控制或自动执行批处理命令，DDL 与 DML 是否事务化/原子由用户执行环境决定。SQL 引擎支持 SELECT 而不支持所需 DDL/INSERT/UNION ALL 时，返回该组合的不支持原因，不覆盖用户模式改成查询。

**结构示意（尖括号需由方言编译，非可直接执行模板）**：

```sql
CREATE TABLE <配置的结果表> (
    <统一结果字段及方言类型>
);

<按引擎放置的WITH及唯一用例关系定义>
INSERT INTO <结果表> (<显式统一列清单>)
SELECT <用例1统一投影> FROM <用例1完整判定关系>
UNION ALL
SELECT <用例2统一投影> FROM <用例2完整判定关系>;

SELECT <显式列清单>
FROM <结果表>
WHERE <批次列> = <本脚本批次常量>
  AND <用例列> IN (<本任务用例常量列表>);
```

最后 SELECT 只在 select_and_insert 生成；方向与仅异常筛选另按配置追加。示意中的 WITH 位置不表达所有引擎的正确语法，编译器必须分别渲染并实机验收。源码值到预期值的查找仍是只读关系，结果 INSERT 不执行 ETL 转码更新。

#### 25.14.4 汇总、重复执行与失败行为

- 所有 INSERT 类模式默认 store_scope=all；显式 errors_only 才只保存异常。select_and_insert 按保存范围写入，再按 result_scope 查询同批结果；禁止仅保存异常却查询 all，开启汇总也要求保存 all。摘要记录 stored_scope 与 display_scope，不能随着输出方式悄悄扩大保存范围。
- select_only 按展示范围生成明细，开启汇总时另从相同完整业务计划生成全量统计；与用户先前的多语句一致快照约束相同，插件不承诺两个 SELECT 自然读到同一时点数据。
- insert_only 使用 store_scope，不接收 result_scope。保存全量且开启汇总时读取同批已存结果；仅保存异常且开启汇总时另生成完整业务关系的汇总 SELECT，明确与已存异常的来源/读取时点不同。不能从零异常推断 NO_DATA 或全通过，不从异常结果表计算整体通过率。
- 汇总按 run_id/case_id/方向统计 total_count/same_count/different_count/not_comparable_count 和各状态数量，满足 same_count + different_count + not_comparable_count = total_count，pass_rate = same_count / total_count（需小数除法，零总数为 NULL）。每个有效用例有汇总行，编译期用例/方向关系 LEFT JOIN 明细统计，缺失计数补 0；完整范围确为零行才显示 NO_DATA。反向关系只包含缺失/键异常，不能将其比例叫反向通过率：反向 pass_rate 始终 NULL，只统计诊断数。
- create_new 前部普通建表，同名对象已存在时应让执行端报错；建议每次生成任务新表和批次，避免误追加。插件离线无法检测表存在或权限；不输出“已确认结果表可创建”。
- create_if_missing 在支持时仅跳过已存在的表，不验证其字段/类型/键槽与当前计划一致。已有表必须满足固定结果结构版本及当前键槽数量/列类型；计划记录结构版本/签名，变化时建议新表。插入显式列不得被位置猜测替代；不自动 ALTER 或截断列。
- 允许追加的脚本再次执行会重复写入同一 run_id；本期不承诺幂等，不用 DISTINCT、业务键唯一约束或 NOT EXISTS 吞掉重复目标行。再执行一次独立检查须重新生成新的 run_id；重试失败脚本的部分写入清理由用户执行环境处理，不自动生成删除/回滚。创建新表模式也不能保证 SQL 客户端遇到 CREATE 失败一定停止后续语句，执行说明应明确遇错停止，不继续向已有对象写入。
- 共享表追加的新检查必须使用未占用的 run_id；插件默认任务 ID 只能区分生成任务，不能自动识别同一脚本重跑。已有同批数据未清理时，查询/汇总会混入旧行，不能声称只读到本次写入；需要新的批次或由执行方先处理失败批次。批次存在检查若将来生成须单独按方言验收，不当作已有保护。查询汇总以所有前置建表/写入语句成功为前提；客户端继续执行失败脚本时，部分结果不能显示为完成或把未执行用例解释为 NO_DATA。
- 结果表对象不得与本任务任何已配置源表、目标表或数据库映射表相同；按方言命名规范处理引用/大小写后检测，无法获知数据库同义词/视图别名时不宣称完全排除别名冲突。不创建结果 schema/catalog、外部连接或索引设施。
- 本地任一输入/投影/方言校验失败时不产出部分 SQL 冒充完整成功。外部脚本包含多条 CREATE/INSERT，用户执行时可能部分成功；该执行状态不属于生成任务 SUCCEEDED/FAILED 的可知范围，report 只记录生成动作与静态配置。

#### 25.14.5 本轮检查补充与实施验收入口

此前欠缺的部分已在本节收敛：三个输出方式避免全关、组合列与类型一致、实际/预期码值分开、异常无法比较、业务键追溯、建表已有对象策略、批次重跑、全量/异常与汇总关系、查询并落表的一致性、只读 SQL 入口的写能力边界，以及单语句组合规模限制。参数不是任意可扩展 SQL 字符串，“等其他配置”由有限 Schema 明确声明，不开放未知键绕过校验。

专项验收对应 REQUIREMENT.md CV-AC04～CV-AC08：六种组合模式；多表/多域/多键/混合类型及 typed NULL 投影；两种布局间行数/状态/键/值一致；建表仅一次且在插入之前；INSERT 显式列正确；合并含重复输入仍保留重复诊断；分组边界不漏用例；汇总含零数据用例；反向方向分离；只有异常保存与全量汇总来源正确；同批落表后读取；已有结果表签名/重跑/冲突/超限与引擎只读入口拒绝。

这次仅进行需求/设计整理和文档一致性检查，不新增插件实现或重用 25.11 的 20 场景记录证明 UNION ALL、CREATE TABLE、INSERT SELECT 已验收。下一阶段以共享完整判定关系先完成 SELECT，再加入统一投影/组合和结果落表，最后接下拉及其他必要 GUI 配置；每一引擎与模式有独立语法/结果证据。

### 25.15 本轮自查修正与验证记录（2026-10-13）

本轮针对需求/设计一致性、现有 Schema 默认行为和 SQL 结果语义复核；发现以下设计错误或缺口并修正，未进入插件实现。

| 问题 | 修正入口与规则 |
| --- | --- |
| case_id 全行唯一与合并明细组内重复矛盾 | 25.3：独立用例唯一，合并明细允许同组重复；全部组形成后检测显式/自动编号碰撞 |
| result_scope 在仅落表和查询并落表中隐式改变保存范围 | 25.10/25.14：拆为查询 result_scope 与保存 store_scope，全部组合明确约束；不允许只存异常后声称全量查询/汇总 |
| 动态源维度要求输出，但结果表缺少相应列 | 25.12/25.14：增加动态维度槽；保留 NULL，缺失/重复源不任选值；总列数也进入能力限制 |
| 文件预筛选与数据库排序规则可能不同，无法追回已删映射 | 25.10/25.12：只有可证明同义才预筛；否则完整保留候选并交 SQL 筛选/计数，超限明确失败 |
| JOIN 与 GROUP BY 使用不同匹配规则会使每组唯一却扩行 | 25.4：键/域/源码值/动态维度统一匹配类型/排序规则；业务键先计数，动态维度不能拆散重复源候选 |
| 参考 SQL 在重复源时仍展示一个看似有效的实际 source_key | 25.11：仅唯一源投影实际 source_key，其余 NULL；读取唯一整行不假定所有类型可 MIN/MAX |
| 反向异常子集被当成可计算通过率的完整范围 | 25.14：反向仅统计诊断数量，pass_rate=NULL；正向用小数除法，零数据显式补汇总行而非通过 |
| 批次重跑/部分写入会让已存汇总混入旧行或误报 NO_DATA | 25.14：共享表使用新批次，保存后查询以所有写入成功为前提；不承诺幂等、自动失败回滚或跨分组同一快照 |
| 条件适用参数的 Schema 默认可能被误认成用户非法配置 | 25.14：条件参数默认由插件在模式校验后应用；可继承条件不预填空数组，profile/显式条件互斥 |

**本次验证**：在 bundled Python 的 SQLite 3.53.1 内存库，从修正后的 25.11 提取参考查询，以合成 TEXT 夹具生成两个用例。正常、错误、无源、重复源、重复目标共 6 条目标记录/用例；六种输出方式/组织包装均保持对应明细与状态，组合为 12 条，不去重；重复源的实际 source_key 为 NULL。另验证大小写敏感分组后忽略大小写 JOIN 会命中两组的反例，以及一致规则分组只得到一组且计数为 2；验证 Python 严格字符串预筛会删掉 SQL 忽略大小写可命中的域值；空用例的 LEFT JOIN 汇总保留零计数行。全部探针通过，无外部连接或持久数据库。

上述探针是参考 SQL 包装和语义反例，尚未覆盖正式统一投影的全部列、完整条件编译、Excel 内联渲染、真实类型/排序规则、其他数据库产品、专用 GUI 或发行物。旧 20 场景记录对应当时 SQLite 3.54.0 与旧查询，不能合并为本轮已运行测试数。文档差异/需求与设计契约检查另行通过；下一阶段按 CV-AC01～CV-AC09 实现并验收，不把本轮自查等同功能交付。

### 25.16 用户授权后的首期实现（2026-10-13）

**CURRENT（代码已存在，不等同所有环境验收）**：独立插件 `plugins/code-value-check`，命令 `sql.codecheck`；复用 Runtime→Host→SDK、输入暂存、产物登记、历史和导出。不增加数据库连接、服务、驱动、实时协议或插件间依赖。

职责及数据流：

1. `schemas/sql.codecheck.json` 定义文件、来源、引擎版本、模式、默认用例、条件与结果配置。Schema仅给无条件参数默认值；条件模式参数在插件校验后应用默认。
2. `testbox/tabular.py` 原读取器增加 `excel_range`；不配置则旧路径保持。有界A1矩形、绝对表头/来源坐标、ZIP/行列/单元格配额；文件不得在两入口重复暂存（Schema启用现有 unique_sources 策略）。
3. `src/main.py` 解析清单/映射，规范化用例、组合键、独立类型、完整限制与来源位置。两文件、两Sheet、同Sheet上下/左右分区、同Sheet合并明细复用同一逻辑计划。数字单元格不静默转文本，文本域不会猜字段名；原文件不改写。
4. 方言编译采用**相关候选计数 + 唯一时标量读取**，而非参考SQL的MIN/GROUP BY抽取。相同键/域/条件比较表达式同时用于计数和取值；每条目标记录一条明细，源和映射重复时不任选值，不以DISTINCT吞重复目标。动态条件实际取值进入统一槽位。
5. SQL脚本按六种模式组织；INSERT第一条CREATE、保存后查询批次结果。汇总从全量状态关系生成，零数据保留用例，反向诊断独立且无通过率。追溯计划/生成报告不伪造执行统计。
6. `testbox/gui.py` 复用原动态表单，增加中文标签、输出/组织下拉及模式关联隐藏/停止提交；修复本功能依赖的枚举默认值按data选中。隐藏文件不进行本地文件校验，已有视觉结构不重做。发行工作流/安装 smoke/命令清单收录新插件。

输出为 `codecheck.sql`、`codecheck-plan.json`、`codecheck-report.md`。计划保留源位置、签名、用例策略、结果列/对象、生成统计；文件原始哈希在Runtime任务manifest中。默认结果表用任务批次哈希命名，可覆盖表名和物理列；拒绝与输入对象同名，不产生DROP/TRUNCATE/自动事务/自动去重。不提供自动已有表结构核对。

**匹配规则与局部优化**：默认严格文本匹配且无trim/casefold。SQL Server的BIN2附加DATALENGTH避免尾空格等值；PostgreSQL文本常量使用E转义；文件文本内联先统一为变长文本，避免固定CHAR的尾空格规则。完整读取基本格式后，文本域才能用等义的Python严格比较预筛；数字域保留候选。文件重复有效键按域、源码值、全部动态字段、静态限制预检拒绝；数据库模式留SQL诊断。未被条件使用的额外列不能掩盖重复。文本NULL/数字内联有显式类型，numeric常量限DECIMAL(38,18)范围。实际业务列类型仍须用户核对，不声称离线生成可验证类型或排序规则。

**实际引擎边界**：SQLite3.35+；PostgreSQL12+、MySQL8+、MariaDB10.2+、SQL Server引擎14+、Oracle12.2+已实现渲染，后五者为 implemented-unverified。Oracle需要数据绑定BINARY排序规则可用，文本投影VARCHAR2(4000 CHAR)，超长实际数据未保障。Trino483+为实验性只读SQL层适配（SELECT/UNION ALL），写入明确拒绝，连接器/相关子查询/NoSQL标量关系须实机核对。直接NoSQL原生SQL及其他RDB/国产兼容模式均未宣称已支持；未知引擎/不支持组合失败，不静默换成另一方言或原生DSL。

**验证入口与证据**：新增 `tests/test_code_value_check.py` 经真实Host生成SQL，在本机SQLite3.54.0内存库执行；覆盖六种输出包装、跨域非恒等转换、多条静态/动态限制、重复源/目标/映射、NULL、组合键、前导零/大小写/尾空格、异构投影、异常范围、全量/零数据汇总、反向诊断、Excel各布局、原文件不变、失败不产可执行脚本、方言渲染/资源边界。Qt offscreen验证输出下拉、模式隐藏/不提交和旧功能回归；wheel及安装后CLI/Host smoke分别记录结果，不把其中任一证据扩大为外部数据库或Windows安装器验收。

**NEEDS_CONFIRMATION / 尚未验收**：真实源目标业务键、实际域与条件值、数据库列类型/索引、实际执行入口/版本、NoSQL连接器与对象可见性；外部引擎实机SQL语法与结果、真人桌面和Windows安装器。只生成模式无写入权限要求，生成INSERT不等于具备执行写权限。多条业务读取需要用户稳定批次或一致快照；重复执行同批次、分批写失败不自动回滚。完整RDB/NoSQL适配目标尚未全部达成，下一阶段优先根据用户实际入口补独立方言实机夹具，而不是取消诊断来凑覆盖。

验证记录（2026-10-14）：本地全量624项通过，码值插件20项专项通过；SQLite3.54.0执行合成SQL，Qt offscreen表单联动通过。wheel构建成功并确认包含新插件Schema/入口/README/三个examples；安装到隔离临时环境，从空cwd运行安装后CLI→Host及Qt offscreen smoke通过，官方资源哈希不变。该隔离环境复用本机既有依赖，Qt通过临时链接提供，不是全新下载依赖或Windows发行验收。验证日志保存在忽略目录workspace，不作为业务源码提交；用户原有需求/设计修改保留。

#### 25.16.1 审查后修复（2026-10-14）

用户授权“修复”后，局部修正四类问题，不调整已确认需求或UI：

- Oracle：明细异常筛选与汇总对投影列使用一致的引用规则，汇总别名也统一引用；进一步检查并修正业务键内部别名keyN的定义/引用大小写不一致。其他未引用的内部列保持原定义方式，不能盲目给所有列加小写引用。
- 文件域预筛：按用例声明domain类型和numeric_text_policy转换，与清单和内联关系一致。文本convert保留1.0/0.0等字符串形式，reject遇数字单元格失败；numeric域继续由SQL按数字匹配。其他文本域的混合码值类型仍可隔离，不把跨域不相关码值强行转为当前用例类型。
- 内部关系命名：生成任务级ASCII前缀，在全部用例源/目标表、数据库映射表及结果表末段名称中做保守casefold前缀避让。各用例CTE、总明细、汇总和已存结果关系统一使用该前缀；不替换SQL字符串常量、不修改用户对象名，生成名受方言长度校验。
- 标识符：统一在方言入口拒绝超限，覆盖对象各段、业务键/字段/条件、数据库已配置物理列、内联逻辑维度和结果列。PostgreSQL按默认63 UTF-8字节，MySQL/MariaDB保守64字符，Oracle按128 UTF-8字节（AL32UTF8及12.2兼容前提），SQL Server按128 UTF-16码元；SQLite/Trino保留应用级128字符。用户名字不截断，避免PostgreSQL共同前缀被截断后重名。文件表头不等同SQL标识符，不新增数据库长度限制。

新增回归先复现原问题，再覆盖文本/数字域转换与拒绝、跨域反例、三种模式及独立/UNION ALL的名称冲突、反向/完整汇总/异常筛选、多字节长度和失败不输出SQL。Oracle及其他外部引擎仅有渲染/边界证据；仍须实际数据库验收。验证日志见workspace/codecheck-fix-*.log。

本次修复验证：码值专项28项、全量632项通过（SQLite3.54.0）；git diff --check通过。重新构建wheel与插件ZIP，核对wheel内插件入口与当前源码一致；安装至项目目录外的临时虚拟环境后，空cwd CLI→Host、资源/导出/SQL及Qt offscreen smoke通过。临时环境复用本机依赖，不是全新依赖下载或Windows/外部数据库验收。未提交代码，保留任务之前的全部未提交改动。

## 26. GUI 主题、工具目录与文件选择修复（2026-10-15）

状态：本节基于本轮开始时已存在的未提交 GUI 修复代码核实。当前阶段仅补充设计、使用说明与本地验证，不新增业务代码、不提交或发布；`CURRENT` 不表示已通过真人桌面或目标平台验收。

### 26.1 目标与当前实现

- `CURRENT`：GUI 提供白色面板、黑色面板和蓝灰面板三种整体主题；主题在“首选项与设置”中下拉切换，立即作用于当前窗口，并通过 `QSettings` 持久化。
- `CURRENT`：主题颜色集中于 `testbox.gui_theme`，通过应用级 `QPalette` 和统一 QSS 同步普通控件、表单、表格、弹窗、错误状态和文件选择器，避免插件配置页出现白底浅字或局部硬编码颜色。
- `CURRENT`：工具目录加载结束后显式进入 `ready`、`empty_plugins`、`empty_search` 或 `error` 状态；加载成功不再保留“正在加载工具目录”状态，失败可重试。
- `CURRENT`：解析预览面板属于当前命令表单生命周期。切换命令或重新加载 Schema 前先停止展示旧面板并解除布局引用；正在执行的旧预览完成后丢弃其 UI 更新，避免多个“导入与解析预览”累积。
- `CURRENT`：统一 `FileDialog` 使用非原生 Qt 文件选择器，将 `Look in` 目录栏设为可编辑。用户可粘贴目录、使用 `Ctrl+L` 聚焦目录栏并按 Enter 跳转；目录输入只用于定位，不会被误提交为文件输入。打开文件、打开多个文件、选择目录和导出文件共用该入口。

已核实的修改入口与原因：

| 模块 | 原因与职责 |
| --- | --- |
| `gui_theme.py` / `MainWindow` / `SettingsDialog` | 只覆盖文字颜色而未同步控件调色板、容器背景，以及局部固定颜色，可能导致白底浅字；以同一主题色表生成 QSS 和调色板，桌面偏好使用独立 `appearance/theme` 键，不修改 Runtime 配置 |
| `ToolCatalogView.reload_tools` | `_filter_cards` 在 loading 状态直接返回；原成功分支未先退出 loading，导致卡片已创建而加载提示仍显示。读取和创建完成后进入 ready，再计算筛选状态 |
| `CommandDetailFormView.load_command` / `ParsingPreviewPanel.dispose` | 原重新加载仅移除表单，遗漏旧预览面板；现在先移除并隐藏旧面板，无工作线程时延迟删除，有运行中预览时保留接收完成信号后删除 |
| `gui_files.py` / `SingleFilePicker` / `MultiFilesPicker` | 默认目录下拉不可编辑；统一在 Qt 对话框中启用目录编辑，Enter 由目录栏事件过滤器消费，不触发提交。插件 Schema 仍决定单文件、多文件及支持格式 |

### 26.2 设计边界

- 主题只改变 GUI 展示和本地桌面偏好，不改变 Runtime、Schema、Host 协议、任务输出或插件业务逻辑。
- 文件选择器的路径跳转不改变输入暂存安全边界；真正提交的仍是用户选中的文件，目录选择仅用于批量文件夹入口。
- 预览仍是实际 Runtime/Host 任务，不新增实时事件协议；切换页面时只保护当前页面展示，已启动任务仍按 Runtime 生命周期完成并留存历史。
- 统一选择器刻意关闭原生系统对话框以保证可编辑目录栏和主题一致性。系统级原生文件选择器外观、辅助功能和平台特性不作为本次兼容承诺。
- 默认保持原黑色主题；未知或无效偏好回退黑色。主题切换不重建参数表单，不能清空用户配置；偏好保存失败时当前主题继续生效，但必须提示下次启动可能无法保留。
- 无效目录保持当前目录且不关闭对话框，显示错误提示；支持中文、空格、带引号路径、相对目录和 `~`。取消选择保持原参数；单文件输入框直接粘贴目录只作为后续浏览起点，不作为有效文件。
- 一个命令页最多展示一个预览容器，双侧输入在容器内切换，不重复追加容器。Schema 读取失败也必须清理前一命令的表单和预览；没有 `x-preview` 的命令不新增通用预览入口。

### 26.3 验收与未验收边界

- 已覆盖：主题持久化与未知值回退、三主题表单文字/输入框对比度、工具目录成功/空搜索/异常重试、命令切换不累积预览、旧预览异步完成不污染新页面、目录粘贴导航和取消行为。
- 本地验证入口为 `python -m unittest discover -s tests -v`；Qt 测试使用 offscreen 平台，不能替代真人桌面视觉、键盘辅助功能或 macOS/Windows 原生窗口验收。
- wheel 构建只确认 Python 发行包可构建及源码模块可装配；不等于真实安装器、原生文件选择器或目标平台 GUI 验收。

本轮实测（2026-10-15）：项目既有虚拟环境 Python 3.14.8 / PySide6 6.11.1，offscreen 全量 650 项通过、无跳过，其中文件选择/主题/预览/GUI Runtime 测试 38 项。通过 `python -m build --no-isolation --wheel` 构建 1.0.23 wheel，逐字节核对上述四个 GUI 模块与当前源码相同，`git diff --check` 通过。日志及 wheel 留在本机临时目录；本轮未执行安装后 smoke、原生 Windows 构建/安装、远端 CI 或发布。

下一阶段入口：UI/验收人员在实际 macOS 与 Windows 桌面逐插件检查三种主题，验证真实剪贴板、Look in 的键盘导航、打开/多选/目录/保存取消，以及运行中切换命令后的预览清理。第三种主题采用现有蓝灰方案，不增加自动随系统主题或自定义颜色功能；若用户需要这两项，另行确认范围。长期上下文更新建议：验收并合并后，将 AI_CONTEXT 中“只读设置”的相关描述局部调整为“桌面主题可编辑、Runtime 路径及诊断仍只读”，本轮不顺带改写长期事实。

### 26.4 Actions #52 测试兼容修复（2026-10-15）

通过 GitHub 实际日志核实：运行 `38669829527`（#52）由 `43fab62` 触发，两个构建任务实际检出自动版本提交 `5f19a87`（1.0.25）；相对本地提交仅版本文件变化，测试文件相同。Linux 与 Windows 均在 Run tests 阶段失败，后续打包、安装器验收与发布未执行，不能解释为安装器或 GUI 功能已经失败。

- Linux 仅安装 evidence extra，没有可选 PySide6。`test_gui_files` 的辅助方法默认参数在类定义时访问 `QtWidgets`，发生 NameError；类上的 skip 装饰器尚未来得及生效。将 Qt 默认值延后到方法执行时计算，仍只在缺少 PySide6 时跳过运行测试，不扩大异常捕获范围。
- Windows 的两个失败子用例均因 Qt 返回正斜杠路径，而测试将其与 Path 生成的反斜杠字符串直接比较。改为比较本机 Path 对象，仍检查完整路径、文件数量和顺序，不改为仅比较文件名，也不改变 GUI 返回路径。
- `test_ci_portability` 的无桌面依赖回归扩展到 files、theme、preview、runtime 四个 GUI 运行测试模块，使用既有 `python -S` 隔离机制，避免本机已经安装 Qt 时遗漏导入失败。Linux 构建继续不强制安装 Qt，Windows GUI 测试继续正常运行。

修复只涉及上述测试与本节设计记录；不调整主题、文件选择器或插件业务，不跳过失败测试，不修改自动版本策略。先通过扩展后的回归复现相同 NameError，再修复并运行专项与全量验证。远端修复验收仍需提交推送后对新提交运行 Actions；本地 macOS 结果不能替代 Windows 实机测试或把旧运行改称通过。

本地验证结果：兼容性专项 3 项、文件选择专项 6 项通过；完整桌面依赖环境全量 650 项通过、无跳过。另在保留本机其他依赖的进程内显式阻断 PySide6 导入，全量 650 项完成、仅 38 项 GUI 运行测试按既有策略跳过，无导入错误；这不是实际 Ubuntu runner。`git diff --check` 通过。尚未提交或推送修复；#52 的失败状态未改变，原生 Windows 修复后结果仍待新运行核实。

## 26.5 审计缺陷修复（2026-10-15）

状态：`CURRENT`。本轮用户明确授权修复全库审计确认的 12 项缺陷；修改保持既有 UI 布局和 Runtime/Host 边界，不把本地验证扩大为目标平台验收。

- SQL Parser 只在字符串和引用标识符之外识别 `CREATE TABLE`；SQL Select 对字段整体引用、对 `schema.table` 仅在 FROM 对象路径拆分。Data Generator 的 Excel 导入按 OOXML 单元格坐标补齐空列，联合主键输出单条表级约束，地址筛选先构造完整匹配树。
- Data Mock GUI 的字段行保留未展示元数据并允许用户清空可编辑值；历史 `rules` 回填到字段编辑器；预览 worker 保持默认 `Runtime(root=None)` 的安装态发现语义。通用数组回填使用 JSON，左右批量预览统一传递 `inputs`。
- Evidence Tool 在继承用例/验证点前跳过物理空行，交互面板按行控制可见性，避免可选预期字段隐藏整个信息卡片。
- 针对性回归位于 `tests/test_audit_regressions.py`、GUI Runtime/Preview 测试和 data-generator workflow 测试；覆盖 SQLite 实际执行、稀疏 Excel、安装态 worker、批量多 Sheet、历史回填、空行截图配对和卡片可见性。

本轮验证：新增/专项回归通过；完整 `.venv/bin/python -m unittest discover -s tests -v` 为 665 项通过，插件 workflow 为 5 项通过；`compileall`、`git diff --check` 通过；重新构建 wheel，并在仓库外临时 venv 以空 cwd 执行安装态 CLI、Qt offscreen GUI、Evidence smoke 通过。上述结果仍不替代原生 Windows 安装/更新/卸载、真人桌面和外部数据库验收，也不代表远端 CI 或 Release 已通过。

## 27. 本地插件目录扩展（2026-10-15）

状态：`CURRENT`，已实现本地目录能力；验证边界见 27.7。本轮用户确认增强现有工具目录的方向：按用途组织功能、使用中文名称、提供收藏与最近使用，并让每个插件自行声明目录分组，新增插件或分组无需修改核心。对应 REQUIREMENT 第 5.6 节的工具浏览、第 6 节的命令选择和第 7 节的 Runtime 共用约束；本节补充本次方案，不修改既有需求基线。

### 27.1 实施前核实基础与本次边界

- 实施前：官方源码目录包含 9 个插件、10 个命令；`schema-diff` 提供 `sql.diff` 与 `sql.preview`。`ToolCatalogView` 已提供命令卡片、搜索、分类筛选、刷新按钮和加载/空/错误状态，按命令排序平铺。
- 实施前：`PluginDiagnosticsView` 已按插件展示版本、命令、能力、兼容范围和路径，支持经过预览与确认的本地安装/覆盖安装/卸载。不是本次新建页面。
- 实施前：`Manifest` / `Command` 未保存显示名称，官方插件中文名称来自 GUI 的 `PLUGIN_LABELS`；`category` 是非空字符串，现有类别为 `data`、`parser`、`sql`、`evidence`。新类别可以被发现，但不保证有内置中文翻译。
- 实施前：Runtime 已有发现、命令索引、诊断和插件管理接口；桌面使用可注入的 `QSettings("TestBox", "Desktop")` 保存主题，没有收藏或最近使用的实现。
- 本次：复用这些入口和现有视觉，增加目录分组、显示名称、收藏/最近使用及必要的状态联动；管理页同步名称与安装来源。
- 范围外：在线市场、托管仓库、远程下载/自动更新、启用/禁用、依赖自动安装、独立虚拟环境、插件信任认证、新的后台服务或数据库。现有本地 ZIP 覆盖安装继续承担用户主动更新，不展示“发现新版本”或“一键在线更新”。

### 27.2 页面与操作规则

保留“工具目录”和“插件与诊断”两个侧栏入口、现有卡片样式、三主题以及命令配置/执行/结果页面。在目录原筛选区域增加互斥入口“全部 / 收藏 / 最近使用”，搜索框和分类下拉始终可用。

| 视图/操作 | 本次行为 |
| --- | --- |
| 全部 | 按用途类别分组，同类插件连续排列；一个插件的多个命令归为一组，每个命令仍保留直接打开配置的卡片，不增加必须先进入插件详情的步骤 |
| 分类组织 | 每个插件自行声明 `category` 分组键和 `category_display_name` 分组显示名；GUI 根据实际生效命令自动生成分组及筛选项，无内置分类白名单或名称表。旧清单缺少显示名时回退分组键 |
| 卡片内容 | 主标题为命令显示名称，同时保留命令 ID；展示插件显示名称/ID、当前生效版本、说明、类别和安装来源；保留既有能力说明 |
| 插件分组 | 多命令插件增加轻量组标题；单命令插件使用现有插件标记，避免每张卡片重复增加标题层级。分组只包含当前生效注册表中的命令 |
| 收藏 | 卡片提供可切换的收藏按钮；点击收藏只更新偏好，不冒泡为打开配置或执行任务；收藏视图沿用用途分组 |
| 最近使用 | 最多显示最近打开的 12 个不同工具，按最近打开顺序排列；含义为本机桌面最近成功打开配置的命令，提示文字明确这一点。CLI 运行、后台预览和单纯刷新不写入该列表 |
| 搜索与筛选 | 当前视图、分类和搜索取交集；搜索覆盖插件/命令显示名、稳定 ID、说明、分类中文名及分类键，按去首尾空白、忽略大小写的子串匹配；不增加模糊拼音搜索依赖 |
| 打开命令 | 仍调用 `MainWindow.navigate_to_command`；只打开配置，不自动运行、不默认为历史参数重跑；有明确参数的既有历史/结果入口保持原行为 |
| 顺序 | 全部/收藏按分组键、插件 ID 排列，同插件不同生效副本按版本与路径区分，副本内沿用清单声明顺序。最近使用以最近顺序优先，不再按类别重排 |
| 刷新与导航 | 用户点击刷新时由 Runtime 重新发现后读取目录；普通返回目录只读取当前注册表。保持当前视图、搜索词和仍有效的分类选择；原类别消失时回到全部分类 |
| 安装/卸载联动 | 复用 Runtime 已有刷新机制，同时重建目录和管理页展示；覆盖安装显示新版本，卸载用户副本后可能恢复内置副本，均以实际生效注册表为准 |

官方清单补充的名称如下；这些是展示文案，不是新的插件或命令：

| 插件 ID | 插件显示名 | 命令 ID → 命令显示名 |
| --- | --- | --- |
| data-generator | 测试数据生成器 | `data.mock` → 生成测试数据 |
| data-preview | 数据解析预览 | `data.preview` → 预览数据 |
| data-compare | 数据比对 | `data.compare` → 比对数据 |
| data-check | 数据质量检查 | `data.check` → 检查数据质量 |
| sql-parser | SQL 字段解析器 | `sql.parse` → 解析 DDL 字段 |
| sql-select | SELECT 生成器 | `sql.select` → 生成 SELECT |
| schema-diff | SQL 结构工具 | `sql.diff` → 比对 SQL 结构；`sql.preview` → 预览 SQL 结构 |
| code-value-check | 码值转换校验 | `sql.codecheck` → 生成码值校验 SQL |
| evidence-tool | 测试证据工具 | `evidence.build` → 生成测试证据 |

### 27.3 清单、目录接口与兼容

在现有 `schema_version: 1` 中增加可选 `display_name`：插件根节点和每个 commands 项各自支持；根节点新增可选 `category_display_name`。这些字段最长 100 字符，缺省或仅空白时分别回退到对应 `name`、`category`；其他类型或超长值通过既有清单校验报告错误。名称仅作纯文本显示，不能作为 HTML、文件路径、导入入口或命令 ID 使用。现有 `description`、`category`、`version` 继续承担说明、分类和版本职责，不另建目录配置文件。

`Manifest` / `Command` 新字段放在兼容默认参数位置，旧清单和既有构造调用不必修改；官方 9 份清单补齐显示名称及分组显示名。GUI 移除逐插件和逐分类的名称映射表；以后新增插件应自行声明分组键和显示名，新增分组无需修改核心。相同分组键自动合并，显示名冲突时按插件 ID 顺序选择首个显式名称并显示非阻塞提示；无显式名称时使用分组键。搜索保留每个插件自己的分组显示名。同步覆盖 PyYAML 读取和当前受限 YAML 兜底分支；不为名称增加列表、嵌套本地化字典、远程图标或新依赖。

`Runtime.list_catalog_entries() -> list[dict]` 作为现有 `list_commands()` 的只读展示投影，不另建持久注册表、不导入插件入口、不启动 Host。每项包含：

| 字段 | 来源/规则 |
| --- | --- |
| `command_name` / `command_display_name` / `description` | 当前命令及其显示元数据；命令说明为空时回退插件说明 |
| `plugin_name` / `plugin_display_name` / `plugin_version` | 该命令当前实际生效的 Manifest |
| `category` / `category_display_name` | Manifest 声明的分组键与分组显示名；显示名缺失时使用原分类键，GUI 不维护分类翻译表 |
| `source` | Runtime 按实际资源路径标记 `bundled`、`user`、`local`，分别显示“内置”“用户安装”“本地目录”；显式 root/源码目录为本地目录 |
| `plugin_path` / `capabilities` | 已有清单中的路径和能力，用于区分实际生效副本及展示；路径细节放插件诊断页 |

`source` 描述安装位置，不代表作者、签名或安全认证，不根据插件自述声称“官方可信”。`inspect_plugin` 和安装预览可增补显示名称，既有机器 ID、CLI JSON 字段、执行参数及插件打包协议保持兼容。

必须以 `list_commands()` 的逐命令实际生效结果建立目录，不能直接遍历某个插件清单的全部 commands。当前发现逻辑按命令处理同名用户/内置副本，可能出现同插件不同版本分别提供有效命令；插件组按名称、版本及实际路径区分，不能合并成一组后标错版本或暴露被覆盖命令。本次不顺带重写插件覆盖策略。

### 27.4 收藏与最近使用持久化

复用 MainWindow 当前可注入的桌面 QSettings，传给目录组件使用；不加入 Runtime 配置，不修改任务历史表，不额外引入数据库。收藏和最近使用使用独立的 `catalog/favorites`、`catalog/recent` 键，以 JSON 字符串保存有序标识列表，每个标识为 `{plugin_name, command_name}`；最近列表头部为最新项，去重后截至 12 项。

- 标识同时包含插件与命令，不包含版本或路径：同插件升级/从用户副本回退内置副本时保留收藏；同名命令由另一个插件提供时不继承原插件收藏或最近记录。
- 收藏按钮增删对应标识；取消收藏立即更新当前视图。列表只保存稳定标识，不保存任务参数、文件路径、输入内容、任务结果或连接信息。
- 最近使用仅在命令配置成功加载后更新。`CommandDetailFormView.load_command` 返回成功标记，MainWindow 统一在成功导航后记录；加载失败、取消选择、修改收藏、筛选和刷新均不改变最近顺序。
- 插件卸载或命令不可用后保留收藏标识；收藏视图用禁用条目展示对应 ID 与“当前不可用”，可取消收藏或打开插件诊断。最近视图只展示当前有效命令，不因扫描暂时失败删除持久记录；再次可用时能恢复。
- 设置缺失时从空列表开始；内容损坏或格式不合法时忽略无效项并显示非阻塞提示，不使目录无法使用。写入后同步并检查状态；保存失败时本次内存操作生效，提示“下次启动可能无法保留”，不声称已保存成功。
- 多窗口允许各自刷新后读取当前偏好；本期不承诺跨进程即时同步或并发编辑合并，也不覆盖主题等其他 QSettings 键。

### 27.5 职责、数据流与修改入口

```text
manifest.yaml → Manifest/Command 校验 → PluginManager 生效命令索引
                                            ↓
                              Runtime.list_catalog_entries()
                                            ↓
                 ToolCatalogView：分组、过滤、收藏/最近视图
                          ↕                   ↓
                 桌面 QSettings       navigate_to_command
                                            ↓
                                   既有 Schema 配置页
```

| 修改入口 | 实现职责 |
| --- | --- |
| `testbox/core/manifest.py` | 加入可选显示名称及兼容校验，保留旧清单和 YAML 兜底可用性 |
| `testbox/core/runtime.py` | 提供目录只读投影与真实安装来源；复用现有命令发现和插件管理，不承载桌面收藏逻辑 |
| `plugins/*/manifest.yaml` | 为官方插件/命令补充显示名称和分组显示名，保持稳定 ID、分类键、版本和业务声明原义 |
| `testbox/gui.py` 中 ToolCatalogView/ToolCardWidget | 复用既有卡片、筛选条和状态控件，增加用途分组、视图切换、中文标题、收藏与失效收藏展示 |
| 同文件 MainWindow/CommandDetailFormView | 注入桌面偏好，在成功导航后记录最近使用，确保异步任务与主题切换不清空目录偏好 |
| 同文件 PluginDiagnosticsView | 复用显示名称/来源并联动刷新，沿用原安装预览、确认、错误反馈与卸载边界 |
| `testbox/gui_catalog.py` | 保存和读取目录偏好、格式校验、去重/截断及保存失败后的内存保留；不依赖 Qt、不进入 Runtime/SDK |

不新增插件业务层或独立 Catalog 服务。若界面代码增长需要拆文件，只提取上述目录相关展示/偏好辅助逻辑，不顺带迁移执行页、重写主题或修改插件 SDK。

### 27.6 异常与验收重点

沿用 loading、empty_plugins、empty_search、error/retry，增加“尚无收藏”和“尚无最近使用”的视图空状态。全部工具为空与筛选无结果分别提示；只剩失效收藏时仍能取消收藏或进入诊断，不显示可执行按钮。部分插件不可用时有效工具继续展示，保留诊断提示；加载错误不以过期卡片冒充已刷新数据。命令打开前仍由 Runtime 校验，刷新/卸载后失效的入口显示原因而非崩溃。

验收以行为测试为主：

1. 用一个不在 GUI 名单中的临时插件及未知分类，验证发现后自动生成中文卡片、搜索命中、分类分组；旧清单无 display_name 可正常安装、发现、执行，错误元数据只隔离对应插件。PyYAML 与兜底读取结果一致。
2. 使用 9 插件/10 命令基线及多命令夹具，检查每个实际生效命令恰好展示一次；同名覆盖、不同版本部分覆盖、卸载恢复内置、命令冲突时名称/版本/来源保持真实。
3. 用独立临时 QSettings 验证收藏增删、重启恢复、失效收藏清理、同插件升级保留、不同插件不继承；三个视图与搜索/分类取交集，刷新保留筛选。
4. 验证成功打开配置才更新最近顺序，重复打开去重、超过 12 项截断；打开失败、后台预览、CLI 调用、收藏按钮和普通刷新不会误记录，也不会自动创建任务。设置损坏/写入失败有提示且仍能正常打开工具。
5. Qt offscreen 验证点击收藏不触发卡片导航、中文/长名称无富文本解释、分类和键盘焦点可操作，主题切换不重建或清空配置。源码/CLI-only 环境在缺少 PySide6 时仍能运行非 GUI 测试。
6. 收敛后运行项目完整 unittest、插件 workflow、compileall、diff 检查，再对新 wheel 执行安装态 smoke，确认目录名称与用户偏好不写入只读安装资源。真实桌面检查三主题、小窗口、长名称、焦点和返回导航；本地测试不能替代原生 Windows 或远端 CI 验收。

### 27.7 交付状态与验证边界

本节目录能力已实现，官方 9 个插件/10 个命令已补齐显示元数据与分组声明；新增插件的分组由其 `manifest.yaml` 自行提供，核心不维护插件/分类名称表。README 提供新增插件的声明片段和兼容规则。

本地验证覆盖清单正常/兜底读取、旧清单安装执行、任意新分组、有效命令投影与部分版本覆盖、偏好损坏/保存失败，以及 Qt offscreen 下的搜索筛选、收藏、最近使用、配置失败、键盘打开、安装升级/卸载联动和三主题保留。安装态 smoke 从新 wheel、空工作目录和隔离用户数据验证自定义插件分组、用户/内置来源、偏好恢复、GUI/Host 与既有业务链路；构建依赖复用本地已安装环境，未联网解析新的依赖版本。

2026-10-15 本地结果：完整 unittest 681 项通过，data-generator workflow 5 项通过，compileall 和 diff 检查通过；新 wheel 安装态 CLI、GUI、Evidence、插件覆盖及业务链路 smoke 全部通过。已逐字节核对 wheel 中本次核心代码与 9 份清单和当前工作区一致。

真实桌面、小窗口/长名称的交互体验和 Windows 原生安装器仍需目标环境验收；本地测试和 offscreen 截图不等于这些验收，也不代表远端 CI 或已发布版本通过。在线市场、启用/禁用和独立依赖环境继续在范围外。长期上下文更新建议：在 AI_CONTEXT 的 Runtime/UI 章节局部补充 `list_catalog_entries()`、插件自行声明分组及桌面偏好边界；本次不大规模改写长期事实。
