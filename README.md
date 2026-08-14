# TestBox

TestBox 是面向测试工程师的本地化、插件化测试效能工具框架。它通过统一 CLI、任务工作区和插件 SDK，把测试数据生成、SQL 字段解析、环境检查等高频工具纳入同一套可追溯运行机制。

> 当前仓库已完成 V1.0 的首个可运行闭环：本地 CLI、插件发现与校验、Host 子进程执行、任务工作区、结构化结果/报告，以及 Data Generator、SQL Parser 两个 P0 插件。

## 文档导航

| 文档 | 用途 |
| --- | --- |
| [产品需求文档](./TestBox%20本地测试效能工具箱%20产品需求文档（PRD）.md) | 确定目标用户、首期范围、优先级和验收标准 |
| [Core 技术设计](./TestBox%20Core%20技术设计文档.md) | 定义 Runtime、CLI、任务、配置、安全及持久化契约 |
| [插件开发规范与 SDK](./TestBox%20插件开发规范与%20SDK%20设计文档.md) | 定义插件清单、生命周期、Context、结果与发布要求 |
| [UI 设计规范](./TestBox%20UI%20设计规范.md) | 定义 GUI 信息架构、组件、状态、视觉与可访问性约束 |

## V1.0 边界

- 交付本地 CLI、插件发现与运行、独立工作区、日志、结构化结果和 Markdown 报告。
- 首批 P0 插件：Data Generator 与 SQL Parser。
- GUI 客户端、插件市场、AI 插件和远程执行不属于 V1.0；GUI 设计约束已在本仓库固化，确保后续实现不偏离 Core 契约。

## 统一使用方式（设计目标）

```text
testbox plugin list
testbox run data.mock --count 100 --format csv --seed 10001
testbox run sql.parse --input ./schema.sql --format xlsx
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

工作区默认保存在 `workspace/<task-id>/`。测试可用 `python3 -m unittest discover -s tests` 执行。

## 插件包管理

插件包为 ZIP 文件，根目录必须直接包含 `manifest.yaml`。安装会先在临时目录完成校验，成功后才启用；卸载只接受清单名称与目录一致的已安装插件。

```text
python3 -m testbox.cli plugin package ./plugins/data-generator --output ./dist/data-generator-1.0.0.zip
python3 -m testbox.cli plugin install ./dist/data-generator-1.0.0.zip
python3 -m testbox.cli plugin uninstall data-generator
```

## Windows 使用

GitHub Release 会提供独立的 `TestBox.exe`，无需安装 Python。将它放在任意目录后，可在 PowerShell 或命令提示符执行：

```text
.\TestBox.exe plugin list
.\TestBox.exe run data.mock --count 100 --format csv --seed 10001
```

程序内置 P0 插件；通过 ZIP 安装的插件和任务工作区保存在 `%LOCALAPPDATA%\TestBox\`，因此不会尝试写入受保护的安装目录。插件安装、卸载命令与其他平台一致。

## 任务历史与清理

任务状态和摘要保存于 `workspace/task_history.sqlite3`。查看任务或删除指定日期前的工作区：

```text
testbox task show <task-id>
testbox workspace clean --before 2026-01-01 --confirm
```
