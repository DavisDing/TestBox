# TestBox 插件开发规范与 SDK 设计文档

版本：V1.0

# 1. 文档目的

本文档用于规范 TestBox 插件开发。

所有插件必须遵循：

- 目录规范
- 接口规范
- 配置规范
- 日志规范
- 异常规范
- 输出规范

保证插件：

- 独立
- 可维护
- 可扩展
- 可复用

# 2. 插件设计原则

## 2.1 独立原则

插件之间禁止直接依赖。

错误：

```
pluginA

调用

pluginB代码
```

正确：

```
PluginA

↓

Core

↓

PluginB
```

## 2.2 Core能力复用原则

插件禁止自行实现：

- 日志
- 配置
- 文件管理
- 报告

统一使用Core提供能力。

# 3. 插件目录规范

统一结构：

```
plugin-name


├── manifest.yaml

├── src

│   └── main.py


├── config

│   └── config.yaml


├── resources


├── templates


├── tests


├── README.md


└── requirements.txt
```

# 4. 插件描述文件

`manifest.yaml` 示例（字段的强制性和校验规则以第 20.1 节为准）：

```
schema_version: 1
name: data-generator
version: 1.0.0
description: 测试数据生成插件
category: data
core_compatibility: ">=1.0,<2.0"
entry: src.main:Plugin
commands:
  - name: data.mock
    description: 生成模拟数据
  - name: data.export
    description: 导出模拟数据
```

# 5. 插件生命周期

插件必须实现：

```
class Plugin:


    def init(self, context):
        pass


    def execute(self, command, params):
        pass


    def destroy(self):
        pass
```

# 6. Context规范

Core提供：

## 日志

```
context.logger.info()
```

禁止：

```
print()
```

## 配置

```
context.config.get()
```

禁止：

代码写死。

## 工作目录

只允许使用：

```
context.workspace.input_dir
context.workspace.output_dir
context.files.write_*()
```

`context.files` 负责安全路径校验、原子写入和附件登记。禁止：

直接访问用户目录。

# 7. 输入参数规范

插件参数统一：

JSON结构。

示例：

```
{
 "count":1000,

 "format":"excel",

 "output":"./result"

}
```

# 8. 输出规范

插件必须返回统一格式：

```
{
 "status":"success",

 "message":"执行完成",

 "data":{},

 "files":[

 ]

}
```

# 9. 异常规范

禁止：

```
exit()
```

必须：

```
raise PluginError("EXECUTION_FAILED", "执行失败")
```

Core统一捕获。

# 10. 配置规范

配置文件：

```
config/config.yaml
```

禁止：

密码：

```
password:123456
```

要求：

- 加密
- 环境变量
- 本地安全存储

# 11. 日志规范

日志目录：

```
logs/

 plugin-name/

 yyyyMMdd.log
```

必须记录：

- 开始时间
- 参数
- 执行结果
- 异常信息

# 12. 测试要求

插件提交必须包含：

```
tests/


test_main.py

test_exception.py
```

至少覆盖：

- 正常流程
- 参数异常
- 文件异常

# 13. 数据生成类插件特殊规范

适用于：

Data Generator。

必须满足：

## 数据安全

- 所有数据必须为模拟数据
- 禁止真实个人数据
- 图片必须增加测试标识

## 可重复生成

支持：

```
seed
```

例如：

```
--seed 10001
```

保证测试复现。

# 14. 图片生成规范

生成图片必须：

包含：

```
TEST DATA ONLY

测试数据
```

禁止：

生成可用于真实身份认证的材料。

# 15. 插件代码质量要求

必须：

- 遵循PEP8
- 有README
- 有注释
- 有测试代码

禁止：

- 修改Core代码
- 引入无关依赖
- 上传敏感信息

# 16. 插件发布流程

流程：

```
开发

↓

单元测试

↓

打包

↓

安装测试

↓

进入plugins目录
```

# 17. 插件版本规范

采用：

```
Major.Minor.Patch
```

例如：

```
1.2.3
```

含义：

1：

重大升级

2：

功能增加

3：

问题修复

# 18. 推荐技术规范

开发语言：

Python

CLI：

Typer

GUI：

PySide6

配置：

YAML

日志：

Loguru

打包：

PyInstaller

# 19. 后续扩展方向

支持：

- AI插件
- 自动化测试插件
- 接口测试插件
- 数据校验插件

最终形成：

```
TestBox Plugin Ecosystem
```

# 20. V1.0 强制契约

本节定义可由 Core 自动校验的插件契约；与前文示例有冲突时，以本节为准。

## 20.1 `manifest.yaml`

每个插件根目录必须只有一个 `manifest.yaml`，使用 UTF-8 编码。最小有效示例如下：

```yaml
schema_version: 1
name: data-generator
version: 1.0.0
description: 生成可复现的测试数据
category: data
core_compatibility: ">=1.0,<2.0"
entry: src.main:Plugin
commands:
  - name: data.mock
    description: 生成模拟数据
    input_schema: schemas/data.mock.json
```

`name` 只能使用小写字母、数字和连字符；`version` 遵循语义化版本；`commands[].name` 使用小写点分格式且在所有已安装插件中唯一。`input_schema` 可选，但 P0 插件必须提供 JSON Schema，用于 CLI 和 GUI 的参数校验与表单生成。

## 20.2 生命周期与并发

入口类必须实现 `init(context)`、`execute(command, params)`、`destroy()`，并且 `execute` 返回 SDK 的 `Result`。插件对象按任务创建；不得把任务参数、文件句柄或可变全局状态留给下一次任务。若插件不支持并发执行，必须在清单中声明 `capabilities: {concurrency: false}`，由 Core 串行调度。

## 20.3 Context API 边界

插件仅使用以下稳定能力：

| API | 用途 |
| --- | --- |
| `context.logger` | 记录结构化运行日志 |
| `context.config.get(key, default=None)` | 读取已合并且已解析的配置 |
| `context.workspace.input_dir` / `output_dir` | 获取本任务输入与输出目录 |
| `context.files.write_*()` | 在输出目录安全写入文件 |
| `context.task.id` | 关联任务与外部日志 |
| `context.report.add_attachment(path)` | 将已生成产物加入报告 |

禁止访问 `context` 的私有属性、直接改写任务目录以外的文件、调用 `sys.exit()` 或使用 `print()` 代替日志。

## 20.4 参数、结果与错误

参数在进入 `execute` 前已按命令 Schema 校验。插件仍须对业务范围校验，并对用户可修复的问题抛出 `PluginError`：

| 错误码 | 含义 |
| --- | --- |
| `INVALID_PARAMS` | 参数缺失、类型或取值不正确 |
| `INPUT_NOT_FOUND` | 输入文件或资源不存在 |
| `INPUT_INVALID` | 输入内容无法解析或不受支持 |
| `DEPENDENCY_UNAVAILABLE` | 可选依赖、网络或外部服务不可用 |
| `EXECUTION_FAILED` | 不可预期的插件执行失败 |

成功结果使用 `status: success`；失败由 Core 统一转换为 `status: failed` 并保存错误码。`files` 只包含相对于任务 `output/` 的路径，不能包含绝对路径、`..` 或敏感配置。

## 20.5 安全与依赖

- 样例、测试夹具、日志和报告不得包含真实个人信息、令牌、密码或连接串。
- 涉及身份或证件的生成器必须默认关闭图片输出，并在所有图像上保留 `TEST DATA ONLY / 测试数据` 水印。
- `requirements.txt` 必须锁定兼容范围；不得修改 Core 的依赖版本或运行时路径。
- 插件安装前应在隔离环境中安装依赖；安装失败不得污染已启用插件。

## 20.6 发布检查清单

- [ ] `manifest.yaml` 通过 `testbox plugin validate`。
- [ ] README 说明用途、命令、参数、样例和安全限制。
- [ ] 正常流程、参数异常、文件异常测试均可运行。
- [ ] `Result`、日志和产物路径符合本规范。
- [ ] 未提交密钥、真实数据、构建缓存或机器相关绝对路径。

## 20.7 隔离运行与权限声明

插件由 Core 的 Plugin Host 子进程运行，不得假设与 CLI、GUI 或其他插件处于同一 Python 进程。插件只能使用 SDK 提供的 Context，不得读取父进程环境变量、用户目录或任务目录以外的路径。

清单必须声明能力；未声明即为不允许：

```yaml
capabilities:
  concurrency: false
  network: false
  filesystem: output-only
  resources: []
```

网络、浏览器、数据库等能力必须由用户在命令执行时显式授予，且写入任务清单。插件不得绕开能力声明自行创建持久化后台进程。

## 20.8 依赖、协议与取消

`requirements.txt` 只声明直接依赖；发布包必须同时提供带版本与哈希的锁定清单。依赖安装由 Core 在插件独立环境中完成，插件不得在运行时自行安装、升级或修改依赖。

Host 协议使用 JSON Lines；插件的日志、进度、结果和错误都通过 SDK 发送，禁止直接写入标准输出。插件必须定期检查取消信号，接到取消后停止新工作、关闭资源、调用 `destroy()`，并返回 `cancelled` 或由 Core 标记为 `CANCELLED`。

# 21. 规则驱动数据插件规范

数据生成插件的规则集必须使用 JSON 对象或 JSON 字段数组，字段至少包含 `name`、`generator`、`enabled`、`unique` 和 `options`。生成器必须在执行前校验规则类型、枚举值、地址筛选和唯一容量；`unique` 只作用于用户指定字段和当前任务。

涉及手机号、地址、金融客户、账户和交易的生成器必须提供可版本化的内置规则与数据资源，允许项目规则覆盖但不得改写内置数据。金融标识应使用不可用于真实业务的 `TEST-` 前缀；规则集须记录名称、版本、描述和字段定义，以便复现与审计。

引入 GitHub 开源数据时，插件必须随包保存来源仓库、固定提交、许可证文本、许可证标识和资源文件 SHA-256；不得仅链接浮动分支。上游公开许可证是使用依据，不得暗示作者对 TestBox 的认可或背书。

解析类插件必须将无法识别的输入片段转换为可定位的 `Result.warnings`，不得静默丢弃。需要严格模式时通过 Schema 参数控制；插件不得执行用户输入的 SQL、脚本或外部命令。
