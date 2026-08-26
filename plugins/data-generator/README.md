# Data Generator

`data.mock` 支持两种模式：从 SQL DDL / Excel 字段清单自动匹配字段类型生成数据，或直接声明任意多个字段及其生成规则。生成结果可复现，仅用于测试。

```text
testbox run data.mock --count 100 --format csv --seed 10001
```

支持 `json`、`csv`、`xlsx`、`txt`、`sql`、`zip` 输出，单次最多 100,000 条。导出文件名使用任务 ID 作为主体，例如 `<task-id>.json`；ZIP 内的数据文件也使用任务 ID 命名。`template` 支持 `retail_customer`、`account`、`product`、`transaction`。字段唯一性使用 `unique: true` 显式开启，仅在当前任务内保证不重复。

## 自定义字段

`fields` 是推荐的新写法。每个字段可单独定义 `name`、数据库 `type`、`generator`、`options`、`unique`、`nullable_rate`，字段数量和顺序完全由用户控制：

```json
{
  "count": 3,
  "format": "json",
  "table": "test_customer",
  "seed": 7,
  "fields": [
    {"name": "id", "type": "BIGINT", "generator": "sequence", "unique": true, "options": {"prefix": "TEST-", "width": 6}},
    {"name": "status", "type": "VARCHAR(20)", "generator": "weighted_enum", "options": {"values": ["NEW", "DONE"]}},
    {"name": "amount", "type": "DECIMAL(12,2)", "generator": "decimal_random", "options": {"min": 10, "max": 99, "scale": 2}},
    {"name": "remark", "type": "VARCHAR(32)", "generator": "constant", "options": {"value": "TEST DATA ONLY"}}
  ]
}
```

可用生成器包括：`sequence`、`string_random`、`integer_random`、`decimal_random`、`boolean_random`、`date_random`、`datetime_random`、`name_cn`、`mobile_cn`、`email`、`china_address`、`weighted_enum`、`constant`、`template`、`uuid`、`transaction_id`。`rules` 仍保留兼容；`fields` 与 `rules` 不能同时使用。

SQL DDL 会读取字段类型、长度/精度、主键、唯一、自增和注释，并据此自动选择生成器；Excel 字段清单支持 `field/name`、`type`、`comment`、`generator`、`options`、`unique`、`nullable_rate` 列。

`txt` 支持 `txt_delimiter`（默认 `|`）和 `txt_header`；`sql` 支持 MySQL、PostgreSQL、SQL Server、Oracle、SQLite 的批量 `INSERT`，并通过 `sql_table`、`sql_dialect`、`sql_batch_size`、`sql_transaction` 配置。`zip` 使用 `zip_formats` 将 JSON、CSV、XLSX、TXT、SQL 组合为数据包，内含生成摘要。

地址可按全国、省、市筛选；内置资源覆盖 34 个省级行政区。可在项目根目录 `config.yaml` 设置 `phone_prefixes: "138,139"` 覆盖默认常用手机号段。

## 第三方数据

大陆省市区县街道及港澳台行政区划数据来自 [modood/Administrative-divisions-of-China](https://github.com/modood/Administrative-divisions-of-China/tree/c49d495b40ac73eb1a66f6eeae5f8fd10696f035) 的固定提交 `c49d495`，上游声明为 WTFPL-2.0。许可证、文件校验值和完整来源说明位于 [`data/`](./data)。
