# SQL Parser

`sql.parse` 使用括号深度扫描解析常见 `CREATE TABLE` 语句，提取表、字段、完整类型、长度/精度、可空性、默认值、主键、唯一、外键、自增、注释和解析警告。

```text
testbox run sql.parse --input ./schema.sql --format xlsx
```

支持 `json`、`csv` 和 `xlsx` 输出；`dialect` 可指定 `auto`、`mysql`、`postgresql`、`sqlserver`、`oracle`、`sqlite`，首期按兼容语法解析，不依赖数据库连接。`include_constraints` 控制约束列（默认由解析结果保留），`fail_on_unsupported` 可在存在无法识别定义时直接失败。可在项目根目录 `config.yaml` 设置 `input_encoding: gb18030` 处理非 UTF-8 SQL 文件。

当前兼容范围包括反引号、双引号、方括号标识符，嵌套类型如 `DECIMAL(18,2)`，PostgreSQL `COMMENT ON`，MySQL `COMMENT`，SQL Server `IDENTITY`，Oracle/SQLite 常见字段类型及表级主键、唯一、外键约束。
