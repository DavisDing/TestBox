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

## 16. 旧版Office转换插件方案

- 独立 `office-convert` 官方插件，命令 `office.convert` 与无文件的 `office.inspect`。Schema使用顶层file-path `input` 或array file-path `inputs`，Runtime沿用既有文件暂存、额度、Host、结果和导出契约；无需新增后端或数据库。
- GUI复用Schema表单和现有SingleFilePicker/MultiFilesPicker，添加旧Office过滤器与二选一说明；不在GUI执行转换。转换器不可用通过真实inspect/失败结果展示，不造可用状态。
- 可信配置 `soffice_path` 或PATH/常见安装路径定位引擎，不允许CLI提交任意命令参数。每个文件用固定headless参数和指定OOXML过滤器；临时UserInstallation仅在本任务的私有工作目录，清理不触碰用户配置。
- 所有输出命名 `converted/{四位序号}-{源stem}.{固定目标扩展}`，多来源同名不覆盖，以同文件系统no-clobber发布。单文件输入50MiB、累计100MiB；单输出100MiB、整批400MiB、ZIP解压总量200MiB/10000条目。输出OOXML校验ZIP路径、CRC、重复条目、宏/实体、必要XML根与namespace及精确内容类型，不接受退出0无文件；报告仅登记确认成功的产物。
- `continue_on_error` 默认true；逐文件时限60秒、批次总时限240秒，在Core默认300秒内给清理/报告留时间。双pipe各64KiB有界读取，不持久化引擎正文诊断；过程资源监测不是内核quota。超时清理本插件创建进程，不杀全局Office；报告指出失败/跳过。批次partial与执行失败分开，全部失败返回failed并保留报告。
- 本次是用户明确请求的外部程序转换例外：仅固定Office引擎、不执行输入中的SQL、宏或脚本、不开放任意shell，不把原先禁止静默外部命令的规则改为普遍许可。系统依赖不加入pip、不自动安装，保真依赖转换器版本及文档特点。

- 原始输入策略由Schema字段显式 `x-input-policy` opt-in，在Workspace暂存前拒绝选中符号链接、重复原始canonical来源及文件数超限；Host继续校验快照内容。避免将内容相同的两个合法不同来源按hash判重，不改变既有插件链接输入契约。

- 转换结果 `data.summary` 与JSON报告summary一致，同时保留顶层小摘要；逐项状态为succeeded/failed/skipped，summary状态为succeeded/partial/failed，均不替换Core任务状态。失败报告仍可从任务详情读取；全部失败的产物导出遵循既有Runtime仅成功任务可导出的规则。
