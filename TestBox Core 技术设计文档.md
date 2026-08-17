# TestBox Core 技术设计文档

版本：V1.0

# 1. 文档目的

本文档定义 TestBox 核心框架技术实现方案。

目标：

建设一个：

- 本地运行
- 插件化扩展
- CLI/GUI统一
- 高可维护性

的测试工具运行框架。

# 2. 技术架构

整体结构：

```
                  TestBox


        CLI              GUI


             TestBox Runtime


------------------------------------------------

Plugin Manager

Command Manager

Config Manager

Logger Manager

Task Manager

Report Manager


------------------------------------------------


Plugins


Data Generator

SQL Parser

Evidence Tool

Excel Tool

Env Check
```

# 3. 技术选型

| 模块       | 技术                   |
| ---------- | ---------------------- |
| 开发语言   | Python 3.11+           |
| CLI        | Typer                  |
| 配置       | Yaml                   |
| 日志       | Loguru                 |
| 插件机制   | `manifest.yaml` 校验 + 受控动态加载 |
| 数据模型   | Pydantic               |
| GUI        | PySide6                |
| 打包       | PyInstaller            |
| 本地数据库 | SQLite                 |

# 4. 工程目录设计

TestBox主工程：

```
testbox/


├── core/

│
│── plugin/

│   ├── loader.py

│   ├── manager.py

│   └── base.py


│
├── command/

│   └── registry.py


│
├── config/

│   ├── manager.py

│   └── schema.py


│
├── context/

│   └── context.py


│
├── logger/

│   └── logger.py


│
├── task/

│   └── runner.py


│
├── report/

│   └── generator.py


├── cli/

│   └── main.py


├── plugins/


├── workspace/


├── tests/


└── main.py
```

# 5. Core模块设计

# 5.1 Plugin Manager

## 职责

负责：

- 插件发现
- 插件加载
- 插件生命周期管理

流程：

```
启动TestBox

↓

扫描plugins目录

↓

读取manifest.yaml

↓

校验插件

↓

加载Plugin类

↓

注册Command
```

# 5.2 Plugin Loader

功能：

动态加载插件。

伪代码：

```
class PluginLoader:


    def load(path):

        read manifest


        import module


        instance = Plugin()


        return instance
```

# 5.3 Plugin Base接口

所有插件继承：

```
class BasePlugin:


    name:str

    version:str


    def init(self, context):

        pass


    def execute(self, command, params):

        pass


    def destroy(self):

        pass
```

# 6. Context上下文设计

Context是Core提供给插件的运行环境。

结构：

```
Context


├── logger

├── config

├── workspace

├── report

├── file

└── runtime
```

示例：

插件：

```
context.logger.info(
"start"
)
```

生成文件：

```
context.files.write_text("result.txt", content)
```

# 7. Command命令体系

目标：

插件自动生成CLI命令。

例如插件：

manifest.yaml

```
commands:

 - name:data.mock
```

自动注册：

```
testbox

 └── data

      └── mock
```

执行：

```
testbox data mock
```

# 8. CLI执行流程

```
用户输入命令


↓

CLI Parser


↓

Command Registry


↓

找到Plugin


↓

创建Context


↓

执行Plugin.execute()


↓

返回Result


↓

生成报告
```

# 9. Result结果模型

所有插件统一返回：

```
class Result:


    status:str


    message:str


    data:dict


    files:list
```

例如：

```
{
"status":"success",

"message":"生成完成",

"files":[
 "customer.xlsx"
]

}
```

# 10. 配置管理设计

目录：

```
~/.testbox/


config.yaml


plugins/


logs/


workspace/


reports/
```

配置优先级与敏感信息处理统一以第 23.6 节为准，避免插件配置覆盖项目策略或命令参数。

# 11. 日志管理

统一：

LogManager

日志：

```
logs/


testbox.log


plugins/


data-generator.log
```

日志级别：

| 级别    | 用途     |
| ------- | -------- |
| DEBUG   | 调试     |
| INFO    | 正常流程 |
| WARNING | 异常提示 |
| ERROR   | 失败     |

# 12. 文件管理

禁止插件直接操作路径。

提供：

```
context.file
```

能力：

- 创建目录
- 保存文件
- 压缩
- 清理

# 13. Workspace设计

每次任务独立：

```
workspace/


20260812_001/


├── input

├── output

├── logs

└── attachments
```

方便：

- 问题追踪
- 测试复现

# 14. Task执行模型

一次执行定义为Task。

例如：

```
Task:


name:
客户数据生成


plugin:
data-generator


command:
customer


params:

count=10000
```

执行记录：

SQLite保存：

表：

task_history

字段：

| 字段       | 说明     |
| ---------- | -------- |
| id         | 任务ID   |
| plugin     | 插件     |
| command    | 命令     |
| start_time | 开始时间 |
| status     | 状态     |
| result     | 结果     |

# 15. Report模块

统一生成：

支持：

- HTML
- Markdown
- Excel

结构：

```
report/


task001/


├── report.html

├── result.json

└── attachments/
```

# 16. 插件安装机制

第一阶段：

本地安装。

目录：

```
plugins/


data-generator/

sql-parser/
```

后续：

支持：

```
testbox install xxx
```

# 17. 错误处理机制

异常链：

```
Plugin


↓

PluginError


↓

Core Handler


↓

Result


↓

Report
```

统一异常：

```
class PluginError(Exception):

    code

    message
```

# 18. 安全设计

## 密码

禁止：

```
config.yaml

password=123456
```

支持：

- 环境变量
- 本地加密

## 临时文件

任务结束：

支持自动清理。

# 19. 测试策略

Core必须覆盖：

## 单元测试

包括：

- Plugin Loader
- Config Manager
- Command Registry

## 集成测试

验证：

```
安装插件

↓

执行命令

↓

生成结果
```

# 20. 第一阶段开发任务

## Sprint 1

完成：

- 工程初始化
- CLI框架
- Plugin Base
- Plugin Loader

## Sprint 2

完成：

- Context
- Config
- Logger
- Result

## Sprint 3

完成：

- Data Generator插件

## Sprint 4

完成：

- SQL Parser插件

# 21. 后续演进

V1.0：

CLI + Plugin

V1.5：

GUI

V2.0：

任务编排

V3.0：

AI测试助手

# 22. 设计原则总结

TestBox遵循：

1. Core稳定，Plugin扩展
2. 插件独立，不互相依赖
3. 所有能力服务化
4. 所有执行可追踪
5. 所有结果可复现

最终目标：

打造个人及团队测试工程生产力平台。

# 23. V1.0 可执行契约

本节补充前文的实现边界。若与示例表述不一致，以本节为准。

## 23.1 组件职责与依赖方向

```
CLI / GUI
    │
    ▼
Runtime（参数校验、任务编排、异常映射）
    ├── PluginManager（发现、校验、注册、生命周期）
    ├── CommandRegistry（命令到插件的唯一映射）
    └── ContextFactory（配置、日志、工作区、文件与报告服务）
            │
            ▼
          Plugin SDK
```

插件只可依赖 SDK 暴露的接口，不得导入其他插件，也不得反向依赖 CLI、GUI 或 Manager 的内部实现。Core 只负责运行和治理，不包含具体业务规则。

## 23.2 命令与退出码

统一命令形态：`testbox run <command> [--param value]`。命令名采用小写点分格式，例如 `data.mock`、`sql.parse`。参数协议为：由 `input_schema` 可安全映射的标量参数生成命令选项；所有命令同时支持重复的 `--set key=value` 与 `--params-file <json>`。未知参数、重复键、无法按 Schema 转换的值必须以退出码 `2` 拒绝。以下命令由 Core 提供：

| 命令 | 用途 |
| --- | --- |
| `testbox plugin list` | 查看发现结果、版本、命令和不可用原因 |
| `testbox plugin validate <path>` | 仅校验插件包，不安装或执行 |
| `testbox run <command>` | 执行已注册插件命令 |
| `testbox task show <task-id>` | 展示任务结果与产物位置 |
| `testbox workspace clean --before <YYYY-MM-DD>` | 清理过期任务工作区，需显式确认 |

进程退出码：`0` 成功，`2` 命令或参数错误，`3` 插件发现/加载错误，`4` 任务执行失败，`5` Core 内部错误，`130` 用户取消。终端仅输出摘要；结构化结果始终写入任务目录。

## 23.3 任务状态与目录

任务状态为 `PENDING`、`RUNNING`、`SUCCEEDED`、`FAILED`、`CANCELLED`、`ABANDONED`。允许的迁移是：`PENDING → RUNNING | CANCELLED`；`RUNNING → SUCCEEDED | FAILED | CANCELLED | ABANDONED`。终态不可变更。启动时，Core 将没有活动心跳且所属进程不存在的 `RUNNING` 任务标记为 `ABANDONED`。

```
workspace/<task_id>/
├── input/          # 运行时复制或引用的输入说明
├── output/         # 插件产物
├── logs/task.log
├── result.json     # Result 的序列化结果
├── report.md
└── manifest.json   # 插件版本、命令、脱敏参数、时间与运行环境
```

`task_id` 使用 `YYYYMMDDTHHMMSS-<8位随机值>`，由 Core 生成。所有插件返回的相对文件路径都相对于 `output/`；Core 在写入前校验路径不能逃逸该目录。小型输入文件复制到 `input/`；大文件可引用，但 `manifest.json` 必须记录原始路径、大小、修改时间和 SHA-256。`result.json` 与 `manifest.json` 先写入临时文件后原子替换。

## 23.4 数据模型

建议使用 Pydantic 定义并在 SDK 中导出以下模型：

```python
class Result(BaseModel):
    status: Literal["success", "failed", "cancelled"]
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

class PluginError(Exception):
    def __init__(self, code: str, message: str, *, details: dict | None = None): ...
```

`data` 只能放体积较小、可 JSON 序列化的摘要；大对象必须输出为文件。错误详情仅写入日志和 `result.json`，CLI 默认不显示堆栈。

## 23.5 插件加载与生命周期

发现阶段读取每个插件根目录的 `manifest.yaml` 并校验名称、语义化版本、入口、命令唯一性和兼容的 Core 版本。校验失败的插件标记为不可用并记录原因，不阻塞启动。

对单个任务，Runtime 按以下顺序调用：`Plugin(context)` → `init()` → `execute(command, params)` → `destroy()`。无论执行成功还是失败都必须调用 `destroy()`；`init` 失败时也应尽力释放已创建资源。插件实例不得跨任务复用，以避免状态泄漏。

## 23.6 配置与敏感信息

配置合并顺序由低到高为：Core 默认值、全局配置、插件默认配置、项目配置、环境变量、命令参数。环境变量以 `TESTBOX_` 开头；敏感值只允许通过系统钥匙串引用或环境变量注入，禁止写入日志、任务清单和报告。

配置文件中的密钥字段使用 `<env:VARIABLE_NAME>` 或 `<keyring:service/account>` 引用。未解析的引用应在实际使用时返回明确错误，而非回退到空字符串。

## 23.7 最小持久化表

`task_history` 至少保存：`id`、`plugin_name`、`plugin_version`、`command`、`started_at`、`finished_at`、`status`、`result_path`、`workspace_path`、`error_code`、`heartbeat_at`、`host_pid`。`params` 必须在持久化前按字段规则脱敏。状态更新使用事务和条件更新，防止并发写入覆盖终态。数据库写入失败不得掩盖原始任务结果，应记录为警告。

# 24. 插件隔离、权限与故障恢复

## 24.1 运行边界

`manifest.yaml` 校验只保证结构正确，不构成安全沙箱。V1.0 的插件均视为“本地可信”，但仍必须由 **Plugin Host 子进程** 执行，不能由 Core 进程直接导入。Core 负责发现、参数校验、工作区、状态与报告；Host 负责在独立解释器中加载一个插件、执行生命周期并返回结构化结果。

每个插件使用独立虚拟环境。依赖由锁定清单安装并校验哈希；安装或升级在临时环境完成，验证成功后才切换。插件崩溃、超时或被取消时，Core 终止 Host、写入诊断摘要，并将任务转换为对应终态。

## 24.2 能力声明与最小权限

插件必须在清单中声明所需能力，例如：

```yaml
capabilities:
  concurrency: false
  network: false
  filesystem: output-only
  external_services: []
```

Core 默认只向 Host 暴露任务输入、输出目录和经过筛选的配置。网络、浏览器、数据库或用户选定的外部路径均应在命令执行前显式声明、记录并由用户确认。V1.0 不承诺对恶意本地代码提供操作系统级安全隔离；来自未知来源的插件不得安装。

## 24.3 Host 协议与资源治理

Core 与 Host 使用版本化的 JSON Lines 协议：`start`（任务、命令、脱敏参数、许可能力）→ `log` / `heartbeat` / `progress` → `result` 或 `error`。协议消息必须包含 `task_id` 和 `protocol_version`。Core 负责超时、取消信号、最大日志大小、最大输出大小及资源锁。

并发控制不只依赖布尔值。插件可声明 `resources`，例如 `browser`、`network`、`database:qa`；同一独占资源在同一时刻只允许一个 Host 占用。V1.0 默认最大执行时长、输出大小和日志大小由全局配置设定，插件只能申请更低的限制。

# 25. 规则驱动数据生成契约

`data.mock` 的 Schema 可包含 `rules`、`rule_set`、`source_file`、`source_format` 和 `template`。Runtime 必须校验对象、数组、布尔值及文件路径，将规则集和表结构输入复制到任务 `input/` 后再交给 Host。

规则中的 `unique` 是字段级声明，不得隐式对姓名、手机号等字段启用。Core 负责记录实际规则、随机种子、输入文件哈希、已启用唯一字段和结果摘要；插件负责在当前任务边界内检测唯一容量与冲突。行政区划、手机号段、枚举字典等内置数据必须随插件版本发布并在任务清单中记录版本。

第三方数据资源必须固定到不可变上游提交并在插件中保留许可证文本、来源 URL、许可证标识和 SHA-256。仅当上游许可证允许复制和分发时才可内置；许可证授予的范围不应被表述为作者的额外背书。

SQL Parser 的输入仅作为文本在 Host 内解析，不得执行 SQL。插件结果应将解析警告放入 `Result.warnings` 与 `data.warnings`；CLI 默认允许带警告成功返回，命令参数可选择严格失败。
