# SQL Select Generator

`sql.select` 根据 `sql.parse` 生成的字段清单文件自动生成 `SELECT` 语句，不连接数据库，也不执行 SQL。

支持直接使用 `sql.parse` 任务工作区 `output/` 中的 JSON、CSV 或 XLSX 文件：

```text
testbox run sql.parse --input ./schema.sql --format json
# 将上一步任务 output/<task-id>.json 作为 input
testbox run sql.select --input ./workspace/<task-id>/output/<task-id>.json --dialect mysql
```

插件会按 `table` 分组、保持字段原有顺序，并为每张表生成一条语句。`dialect` 可选择 `mysql`、`postgresql`、`sqlserver`、`oracle`、`sqlite` 或 `auto`；`auto` 保留未加引号的标识符。输出文件名为当前下游任务 ID 加 `.sql` 后缀。
