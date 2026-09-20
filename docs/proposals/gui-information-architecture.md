# TestBox GUI 信息架构设计提案

- **状态**：`RECOMMENDATION`；第一阶段设计产出，未修改 GUI、Runtime 或插件逻辑
- **日期**：2026-09-20
- **范围**：PySide6 桌面 GUI 的页面结构、导航、核心流程、Runtime 状态呈现、Schema 表单、任务历史、结果/报告/导出、错误状态、平台优先级和分阶段实现建议

## 1. 设计目标与边界

TestBox GUI 应是 Runtime 的可视化入口，而不是第二套业务层。主路径为：

```text
工具目录 → 命令工作台 → 提交任务 → 运行中 → 任务详情
                                      ↘ 任务历史 ↗
```

本提案遵守已确认边界：

- CLI 和 GUI 共用 Runtime；插件发现、Schema 权威校验、任务状态、工作区、结果和导出由 Runtime 负责。
- 插件通过 Plugin Host 子进程执行；当前 Host 是一次 JSON 请求/一次 JSON 响应，不承诺实时进度或日志流。
- 任务状态使用 `PENDING`、`RUNNING`、`SUCCEEDED`、`FAILED`、`CANCELLED`、`ABANDONED`。
- 任务工作区围绕任务 ID 保存输入、输出、日志、`manifest.json`、`result.json` 和 `report.md`。
- GUI 不直接写任务工作区、不直连 SQLite、不执行 SQL、不复制插件业务逻辑。
- 当前 GUI 的布局、颜色和控件不是兼容约束；可以整体重做，但必须保持 Runtime/SDK/Result/插件契约。

本阶段明确不做：重写 `testbox/gui.py`、修改 Runtime/Host 协议、增加 GUI 专用数据库、确定最终视觉主题或扩大 Evidence Tool 范围。

## 2. 已阅读资料与现状观察

已阅读：

- `docs/AI_CONTEXT.md`
- `docs/REQUIREMENT.md`
- `docs/DESIGN.md`
- `testbox/gui.py`
- `docs/ui_proposals/proposal_1_linear_obsidian.png`
- `docs/ui_proposals/proposal_2_supabase_emerald.png`
- `docs/ui_proposals/proposal_3_macos_studio.png`
- `docs/ui_proposals/proposal_4_polar_light.png`
- `docs/ui_proposals/testbox_workbench_ui.png`
- `docs/ui_proposals/testbox_history_plugins_ui.png`
- `docs/design_proposals/current_style_catalog.png`
- `docs/design_proposals/current_style_form.png`
- `docs/design_proposals/current_style_history.png`
- `docs/design_proposals/current_style_plugins.png`

当前 `testbox/gui.py` 已具备目录、动态 Schema 表单、运行中页、任务结果页、历史页、插件诊断页、设置对话框和 Evidence 标注对话框。主窗口内部已有六个页面索引，但侧边栏只暴露工具目录、任务历史、插件与诊断；表单、运行中和结果属于上下文页面。这与长期建议基本一致，但页面回链、状态模型和错误呈现需要统一。

现有 UI 提案的共同结构是“左侧导航 + 中间配置 + 右侧结果/产物”。`testbox_workbench_ui.png` 对一次任务的配置、结果、文件和日志闭环表达最清楚；`testbox_history_plugins_ui.png` 覆盖任务审计和插件健康；旧样式资源暴露出卡片文本拥挤、结果消息框承载过多信息等问题。上述资源可作为结构参考，不应把任何颜色、字体或品牌风格视为已确认需求。

## 3. 推荐页面与导航

### 3.1 一级导航

```text
TestBox
├── 工具目录（Tools）
├── 任务历史（Tasks）
├── 插件与诊断（Plugins & Diagnostics）
└── 设置（Settings）
```

“命令工作台”“运行中”“任务详情”是上下文路由，不固定在一级导航中：

```text
工具目录 → 命令工作台 → 运行中 → 任务详情
任务历史 ───────────────────────→ 任务详情
插件与诊断 → 插件详情/安装确认
```

### 3.2 工具目录

目的：按“要完成的测试工作”发现工具，而不只是浏览插件包。

- 搜索命令名、插件名、描述和分类。
- 分类由 manifest 提供，GUI 不硬编码业务能力。
- 卡片显示命令名、描述、插件/版本、并发能力、文件能力和可用状态。
- 无效插件或命令不能阻塞有效插件；必须显示原因，并提供诊断入口。
- 无插件、无搜索结果、加载失败分别使用不同空/错误状态。
- 点击卡片进入命令工作台，目录不直接执行任务。

### 3.3 命令工作台

推荐布局：

```text
页面头部：返回目录 / 命令名 / 插件版本 / 帮助
┌─ Schema 参数配置区 ─────────┬─ 命令与插件信息 ─────────┐
│ 主要参数                    │ 命令说明                  │
│ 文件/多文件输入             │ 插件能力与限制             │
│ 高级参数（折叠）             │ 输出类型与安全提示          │
│ 字段级校验错误               │ Schema/版本标识（可用时）    │
└────────────────────────────┴──────────────────────────┘
底部：重置 / 提交 Runtime.run
```

- 参数顺序和默认值来源于 Schema/Runtime；GUI 不能复制业务默认值。
- 主要参数、文件输入和高级参数分层；高级参数默认折叠。
- 提交前说明会创建本地任务工作区并保存脱敏参数。
- 文件选择器只负责收集选择结果；暂存、哈希、路径校验和安全处理走 Runtime。
- `data.mock` 的字段规则表格可以是专用 renderer，但外层标题、错误、提交和结果回链统一。

### 3.4 运行中

显示：`PENDING`/`RUNNING`、命令、插件/版本、启动时间、任务 ID（可用时）和脱敏参数摘要。当前协议下使用不定进度指示器和“正在等待插件结果”说明。

不应承诺实时百分比、逐行日志或用户取消；取消按钮只有在 Runtime/Host 明确支持后才能出现。是否允许用户离开运行页、后台任务是否可恢复，标记 `NEEDS_CONFIRMATION`。

### 3.5 任务详情

按信息优先级拆成摘要、参数、结果、警告/错误、产物、报告、日志、工作区和继续工作区块：

1. **摘要**：状态、任务 ID、命令、插件/版本、开始/结束时间、耗时。
2. **参数**：脱敏参数；敏感值显示“已提供”或遮蔽值。
3. **结果**：`result.json` 中适合用户理解的摘要和业务统计。
4. **警告/错误**：结构化信息、原因和下一步建议。
5. **产物**：文件名、相对路径、大小、类型、预览/打开/单文件导出。
6. **报告/日志**：`report.md` 默认可读；原始日志按需展开。
7. **工作区**：路径和打开动作仅在现有 Runtime/平台能力支持时显示。
8. **继续工作**：重新执行、进入下游命令、回到目录。

成功任务突出“得到什么”；失败任务突出“哪里失败、如何修复”；取消/异常中断任务突出“保留了什么、是否可以安全重试”。

### 3.6 任务历史

任务历史是审计入口，不是简单的最近运行列表：

- 搜索任务 ID；后续可扩展命令、插件和时间范围。
- 按状态、命令、时间筛选，数据来自 Runtime。
- 列表显示任务 ID、命令、插件/版本、状态、时间、耗时和产物数（有数据时）。
- 行操作提供查看详情、重新打开配置和导出；重新执行不能默默提交。
- 继续使用 Runtime 的 `list_tasks` 等入口，不直连 SQLite。
- 清理工作区是高影响动作，明确日期范围、影响范围和确认步骤。
- 没有记录、筛选无结果、读取失败分别处理。

### 3.7 插件与诊断

分为插件清单、问题插件、Runtime 诊断和高影响操作：

- 清单显示名称、版本、命令、分类、能力和状态。
- manifest、Schema、依赖或兼容性问题必须显示具体原因。
- 安装、覆盖安装和卸载单独确认，展示插件名、版本和影响范围。
- 只读诊断可展示 Runtime 版本、插件目录、任务工作区和协议/配置状态。
- 不称 Plugin Host 为安全沙箱；它是异常隔离，不是恶意代码隔离。

### 3.8 设置

第一阶段只放 Runtime/工作区/插件目录、版本信息和已有的只读配置/诊断入口。插件启用/禁用、权限确认、独立依赖环境、配置引用和钥匙串均为 `NEEDS_CONFIRMATION`。

## 4. 核心用户流程

### 4.1 普通命令

```text
工具目录 → 选择命令 → 读取 Schema → 填写参数
→ 本地提示明显错误 → Runtime.run(command, params)
→ PENDING/RUNNING → task_id → 任务详情
→ 查看结果/警告/产物/报告 → 单文件或全部导出
```

验收重点：状态、任务 ID、结果和文件列表必须来自 Runtime/工作区；GUI 不自行写文件。

### 4.2 历史重新执行

```text
任务历史 → 任务详情 → 重新执行
→ 回填可安全使用的参数 → 重新验证文件路径
→ 用户确认 → 返回工作台并提交
```

原输入文件丢失时，显示重新选择；不得把历史记录当作自动提交授权。

### 4.3 SQL parse → SQL select

```text
sql.parse → 任务详情字段清单产物
→ 选择 JSON/CSV/XLSX → 进入 sql.select 并预填 input
→ 选择 dialect/注释 → 创建新 Runtime 任务
```

插件仍只通过任务工作区产物衔接。第一版已提供“用字段清单生成 SELECT”快捷动作：仅将成功 `sql.parse` 任务中已声明的 JSON、CSV 或 XLSX 产物预填到 `sql.select` 表单，不会创建或自动执行下游任务。产物路径须由 Runtime 校验和解析；更多下游编排能力仍为 `NEEDS_CONFIRMATION`。

### 4.4 Data Generator

主要参数（格式、数量、seed）→ 选择规则来源（规则编辑器/SQL/Excel/规则集）→ 编辑字段规则 → 显示“测试数据”合规提示 → 提交 → 任务详情显示产物和摘要。该流程是 UI renderer 扩展，不改变 `data.mock` 命令契约。

### 4.5 Evidence

普通批处理复用命令工作台；`interactive=true` 进入特殊桌面流程。当前代码会隐藏主窗口、由插件 Host 执行交互截图、任务结束后恢复窗口。是否升级为独立向导、如何提示屏幕录制权限、是否支持取消，均为 `NEEDS_CONFIRMATION`，本阶段不扩展。

## 5. Runtime 状态映射

| Runtime 状态 | UI 标签 | 默认页面 | 允许动作 |
| --- | --- | --- | --- |
| `PENDING` | 等待执行 | 运行中 | 等待；返回策略待确认 |
| `RUNNING` | 正在运行 | 运行中 | 查看摘要；取消仅在协议支持后提供 |
| `SUCCEEDED` | 执行成功 | 任务详情 | 查看、导出、重新执行、继续下游 |
| `FAILED` | 执行失败 | 任务详情 | 查看错误/日志、修改参数后重试 |
| `CANCELLED` | 已取消 | 任务详情 | 查看保留内容、重新执行 |
| `ABANDONED` | 异常中断 | 任务详情 | 查看诊断、重试、保留或清理工作区 |

Loading、Empty、Warning、Permission denied、Unavailable 是展示层状态或结果 facets，不应擅自新增 Runtime 枚举。当前结果页注释把 `WARNING`、`EMPTY` 描述为任务状态，但核心契约没有这两个值；实现前应统一为“Runtime status + warnings/empty/error facets”，是否正式扩展 Result 契约标记 `NEEDS_CONFIRMATION`。

每个状态至少同时提供文字、图标/结构差异、解释、下一步动作；不能只靠颜色。

## 6. Schema 表单与插件信息

| Schema 类型/当前控件 | 推荐呈现 | 约束 |
| --- | --- | --- |
| `string` | 单行文本 | 描述、默认值、长度/模式错误 |
| `integer`/`number` | 数字输入 | 显示 min/max 和资源风险提示 |
| `boolean` | 开关/复选框 | 同时显示文字语义 |
| `enum` | 下拉/单选 | 人类标签 + 原始值映射 |
| `file-path` | 单文件选择器 | 存在性/可读性提示，暂存由 Runtime 完成 |
| `array`/多文件 | 可编辑列表 | 数量、顺序和不存在文件可见 |
| `object` | 结构化或受控 JSON 编辑器 | 不把任意文本视为已通过 Runtime 校验 |
| `oneOf`/条件字段 | 分支表单 | 需先核实通用 renderer 支持度 |
| `data.mock` 规则 | 专用字段表格 | 规则业务仍由插件负责 |

插件信息至少展示名称、版本、命令描述、Core 兼容范围、并发/串行能力、文件能力、输入/输出提示和不可用原因。Schema 是否正式增加 `label`、`help`、`group`、`secret`、`widget`、`order` 等 GUI 元数据，以及如何兼容旧插件，标记 `NEEDS_CONFIRMATION`。

## 7. 结果、报告、导出

首屏顺序建议：

```text
状态/任务 ID → 结果摘要/警告 → 关键产物 → 报告
→ 脱敏参数 → 日志/诊断 → 工作区/高级动作
```

导出动作分三层：

1. 单文件导出：调用 Runtime 的 `commit_output`。
2. 全部产物归档：若 Runtime 已提供归档能力，保持工作区相对目录结构。
3. 打开报告/工作区：仅在已有 Runtime 和平台能力支持时显示。

GUI 不用 `shutil.copy` 等方式直接复制输出；导出失败显示目标、原因和重试动作，不能覆盖原始任务结果。

## 8. 错误、空、加载和权限状态

| 场景 | UI 状态 | 建议动作 |
| --- | --- | --- |
| 插件目录/Schema/历史加载中 | Loading | 等待/重试 |
| 无可用插件 | Empty + Error | 检查插件目录、打开诊断 |
| 搜索无匹配 | Empty | 清除筛选 |
| 必填字段缺失 | Validation error | 聚焦首个错误字段 |
| 文件不存在/不可读 | Input error | 重新选择 |
| `PENDING`/`RUNNING` | Running | 等待；取消仅在支持时出现 |
| 成功有产物 | Success | 查看、导出、继续 |
| 成功有警告 | Success + Warning | 查看警告/报告 |
| 成功无产物 | Success + Empty | 查看结果/日志 |
| 插件业务失败 | Failed | 查看错误/日志/修正参数 |
| Host 崩溃/超时 | Failed 或 Abandoned | 打开诊断/重试 |
| 用户取消 | Cancelled | 查看保留内容/重新执行 |
| 导出/屏幕录制无权限 | Permission denied | 更换位置或查看系统权限说明 |
| 历史读取失败 | Error | 重试/诊断，不删除数据库 |

错误首层显示可理解摘要和下一步；完整堆栈只在受控诊断中展示。当前 `MainWindow._on_task_failed` 对部分异常只弹消息框并返回表单；未来应统一纳入任务详情/诊断，但“Runtime 创建 task ID 前失败是否必须留下可追溯记录”需 Core 任务确认，标记 `NEEDS_CONFIRMATION`。

## 9. 平台优先级

推荐信息架构对 macOS 和 Windows 同等适用，P0 都覆盖：目录、Schema 表单、文件选择、任务执行、结果查看、历史、导出和只读诊断。

平台专项验收：

- macOS：文件/目录权限、屏幕录制权限、应用包路径、窗口恢复和原生菜单行为。
- Windows：PyInstaller `onedir`、安装目录与用户数据目录、路径分隔符、权限/长路径和 Inno Setup 产物。
- 窄窗口：侧边栏可折叠；左右分栏转为上下布局，不裁掉提交按钮或错误提示。

以下为 `NEEDS_CONFIRMATION`：正式版本是否 macOS/Windows 同等完整；是否支持 Linux GUI；是否需要原生菜单、托盘、文件关联；Evidence 屏幕录制是否为两平台 P0。

## 10. 分阶段实现建议

### Phase 0：契约与路由准备

显式化目录、工作台、运行中、详情、历史、诊断、设置路由；建立只读的任务展示适配层；区分 Runtime status 与 warning/empty/error facets；盘点 Runtime 已有查询、导出和报告入口。不得在 GUI 补 Runtime 缺口。

### Phase 1：应用壳与工具目录

统一主窗口、导航、页面头部和反馈区；保留搜索、分类、插件版本/能力展示；加入不可用原因和空/加载/错误状态。

### Phase 2：命令工作台与 Schema renderer

抽取通用 renderer；复用现有 `DynamicSchemaForm`、文件选择器和 `data.mock` 字段编辑器；统一主要/高级参数、字段错误、插件信息和提交操作；未确认 Schema UI 元数据前保留兼容映射。

### Phase 3：运行中、详情和导出

统一 `PENDING/RUNNING`；按区块展示结果、警告、错误、产物、报告、日志、参数和工作区；所有导出调用 Runtime；补齐六种 Runtime 状态及展示层状态回归。不提前实现实时日志/取消。

### Phase 4：历史、诊断和设置

优化筛选、分页、详情回链、重新执行；分离插件只读信息和安装/卸载确认；设置先承载路径、版本和只读运行信息；清理工作区仍经 Runtime。

### Phase 5：专用工作流和平台 QA

在需求确认后实现 SQL 下游预填、Data Generator 规则编辑、Evidence 交互截图/权限/标注回链；执行 macOS/Windows 路径、窗口、权限、安装后运行、导出、键盘、缩放和窄窗口回归。

## 11. 待确认事项汇总

1. 是否需要实时日志、实时进度和取消；如需要，Host 协议如何版本化。
2. macOS 与 Windows 是否同等优先；是否正式支持 Linux GUI。
3. Evidence Tool 是否提升为下一阶段 P0/P1。
4. 是否提供 SQL parse → SQL select 的快捷预填动作；建议不自动执行。
5. 是否为 JSON Schema 增加 GUI 元数据及旧插件兼容策略。
6. Result 是否正式区分 warnings、empty result 和 error facets。
7. 是否支持插件启用/禁用、权限确认、独立依赖环境、配置引用和钥匙串。
8. Runtime 在创建任务前失败时是否需要生成可追溯错误记录。

## 12. 关键结论

推荐把 GUI 从“按命令切换的表单集合”调整为“以任务为中心的本地工作台”：工具目录负责发现，命令工作台负责配置，运行中页负责可信等待，任务详情负责审计与产物，历史负责回溯，插件诊断负责解释可用性，设置负责本地环境信息。

该方案最大化复用当前 `testbox/gui.py` 的页面和控件事实，也吸收了现有 UI 提案对“配置 + 结果 + 产物”的优点；同时明确了 Runtime 单一事实源、导出边界、状态模型和平台风险，不需要本阶段修改业务代码。
