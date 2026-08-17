# Data Generator

`data.mock` 根据默认规则、金融模板、SQL DDL、Excel 字段清单或 JSON 规则集生成可复现的模拟测试数据，仅用于测试。

```text
testbox run data.mock --count 100 --format csv --seed 10001
```

支持 `json`、`csv`、`xlsx`、`txt`、`sql`、`zip` 输出，单次最多 100,000 条。`template` 支持 `retail_customer`、`account`、`product`、`transaction`。字段唯一性使用 `unique: true` 显式开启，仅在当前任务内保证不重复。

`txt` 支持 `txt_delimiter`（默认 `|`）和 `txt_header`；`sql` 支持 MySQL、PostgreSQL、SQL Server、Oracle、SQLite 的批量 `INSERT`，并通过 `sql_table`、`sql_dialect`、`sql_batch_size`、`sql_transaction` 配置。`zip` 使用 `zip_formats` 将 JSON、CSV、XLSX、TXT、SQL 组合为数据包，内含生成摘要。

地址可按全国、省、市筛选；内置资源覆盖 34 个省级行政区。可在项目根目录 `config.yaml` 设置 `phone_prefixes: "138,139"` 覆盖默认常用手机号段。

## 第三方数据

大陆省市区县街道及港澳台行政区划数据来自 [modood/Administrative-divisions-of-China](https://github.com/modood/Administrative-divisions-of-China/tree/c49d495b40ac73eb1a66f6eeae5f8fd10696f035) 的固定提交 `c49d495`，上游声明为 WTFPL-2.0。许可证、文件校验值和完整来源说明位于 [`data/`](./data)。
