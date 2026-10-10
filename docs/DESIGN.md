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
