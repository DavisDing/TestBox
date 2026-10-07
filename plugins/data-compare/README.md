# Data Compare（P1）

本地命令 `data.compare` 通过公共 SDK `read_dataset` / `normalize_dataset` 完整读取并比较两个数据集，不导入其他插件，也不改写源文件。插件不新增依赖、不访问网络，仅向任务 `output/` 写报告。

## 输入与预览

`left` / `right` 是必填的顶层文件路径。`left_options` / `right_options`、`left_normalize` / `right_normalize` 分别独立配置，默认均为 `{}`。不同格式不会自动当作同一类型：CSV 单元格通常是字符串，JSON/Excel 可以包含真正的数值；需要显式标准化才能比较它们。

读取选项直接交给 SDK，不增加插件私有解析参数或覆盖其默认值：

- `format`: `auto/csv/tsv/txt/json/jsonl/xlsx/xlsm/sql`；auto 根据扩展名识别，旧版 `.xls` 不支持。
- `encoding`: 默认 `utf-8-sig`；`delimiter`、`quotechar`、`escapechar`、`record_separator` 可调整。TXT 支持多字符列分隔符和自定义记录分隔符；CSV/TSV 列分隔符必须单字符。记录换行支持实际或转义 `\n` / `\r` / `\r\n`。
- `has_header`、`header_row`（从 1 起）、`start_row`（默认 0，跟随表头）、无表头时的 `columns`、Excel 的 `sheet`、JSON 的简化点分 `json_path`。
- **空记录默认使用 SDK 的 `skip_empty_rows:true`**，与 `data.preview` 完全一致。设置 `skip_empty_rows:false` 后，一列 CSV 的空记录会成为空字符串行；多列 CSV 空记录宽度不符会失败；JSONL 空记录会报输入错误；Excel 全空行保留为 null 行。允许显式跳空不等于忽略其他无效行，字段数量错误、无效 JSON 等仍失败。
- 默认读取上限为 100000 行、256 列、20 MiB；可使用 SDK 的 `max_rows/max_columns/max_bytes` 等有界选项。超过限制失败，不用截断数据判断相等。JSON 重复键、NaN/Infinity、过深嵌套由 SDK 拒绝。
- Excel 需要环境已有可选依赖 `openpyxl`（项目 evidence extra），插件不安装依赖。`formula_mode` 默认 `formula`（比较公式文本）；明确选择 `cached` 后仅比较已存储缓存，不重新计算公式，无缓存与空值无法区分且有警告。只读取选中的/活动工作表，多表未指定 sheet 时有 SDK 警告。XLSM 不执行宏。
- SQL 输入作为单列 `sql` 的完整文本比较，不执行 SQL，不将 SQL 解析为数据库查询结果，也不忽略空白或 SQL 语义差异。

Schema 的 `x-preview` 指向官方独立插件的 `data.preview`，分别映射左/右文件、选项与标准化对象。同一文件、同一选项、同一标准化规则具有相同解析语义；比对插件不重复注册预览命令。不包含 GUI 代码或界面改动。

标准化对象支持 `column_mapping`（输入列 → 统一列）、`trim`、`casefold`、`null_values` 和 `types`（统一列名 → `string/integer/decimal/boolean/date/datetime`）。列名映射冲突或转换失败直接报错；不猜测字段映射。Decimal 标准化为精确 fixed 格式字符串，去掉末尾小数零，类型保留在 `types` 元数据中；不使用任何 `normalized_decimals` 参数。

## 比较语义

| 参数 | 含义 |
|---|---|
| `mode` | 默认 `position`；还支持 `key`、`multiset` |
| `keys` | key 模式必填，使用标准化后的字段名；其他模式不接受非空 keys |
| `ignore_columns` | 标准化后的忽略列名；未知列和忽略主键报错 |
| `absolute_tolerance` / `relative_tolerance` | 默认 0，有限非负数 |
| `max_differences` | 默认 1000，范围 1–100000；只限制报告明细数量 |

- `position` 按记录顺序配对；`key` 忽略顺序，按唯一主键配对；`multiset` 忽略顺序，**保留重复次数**。
- 默认严格区分布尔、整数、浮点数、字符串、decimal 类型标记、null 和缺失字段，包括嵌套 JSON。对象键顺序和列顺序不影响相等；列表内部顺序仍有效。date/datetime 使用 SDK 标准化后的 ISO 字符串，不额外做时区等价转换。
- key 模式拒绝主键列/单元格缺失、null、空/纯空白字符串、数组/对象、重复主键及标准化后的值冲突。复合主键支持多个字段，类型参与身份；主键匹配不应用容差。
- 非零容差仅用于原生数值或显式 decimal，不将任意数字字符串自动转数值，也不将 bool 当作数值。使用精确有理数计算：`abs(L-R) <= max(absolute_tolerance, relative_tolerance * max(abs(L), abs(R)))`。
- 容差多重集使用最大匹配而非贪心，以免非传递容差导致误判；候选字段比较与匹配搜索累计上限 2000000 次，超限返回 `COMPARE_LIMIT_EXCEEDED`，绝不把无法完成当作相等。无容差使用类型化哈希分组；同质容差组直接比较计数。大规模非同质数值组可通过非数值字段分组或缩小数据集降低代价。
- 右侧独有行为 `added_row`，左侧独有行为 `missing_row`；配对行字段变化为 `field_changed`。独有列另计 `added_column/missing_column`，即使没有数据行也会报告。独有列也可产生逐行字段变化，差异数是事件数量而不是唯一行数。

示例（源码或可编辑安装环境）：

```sh
python -m testbox.cli run data.compare \
  --set left=/absolute/left.csv --set right=/absolute/right.json \
  --set mode=key --set 'keys=["id"]' \
  --set 'left_normalize={"column_mapping":{"ID":"id"},"trim":true,"types":{"id":"integer","amount":"decimal"}}' \
  --set 'right_normalize={"types":{"id":"integer","amount":"decimal"}}'
```

## 结果与报告

有效数据存在差异时仍返回 `status:"success"`、`data.equal:false`，任务为 `SUCCEEDED`。非法输入、标准化/主键错误、参数/资源限制才使任务失败。

登记产物为 `<task-id>.json`、`<task-id>.csv`、`<task-id>.md`。JSON/Markdown 包含全量计数、已报告数量和 `truncated`；明细截断产生可见警告，`equal` 与计数始终依据全量比较。`Result.data` 为轻量摘要，明细只在登记文件中。`matched_rows` 表示 position 配对数量、key 同主键数量、multiset 相等匹配数量，不表示 position/key 配对字段完全一致。

明细保留 SDK 源定位（物理行/记录、工作表或 JSON 路径）。Runtime 暂存输入后，源路径指向该任务 `input/` 文件，不假装仍为用户原路径。JSON 区分缺失标记与 null，禁止非有限数值输出；CSV 对公式前缀 `= + - @`（含空白/控制前缀）加单引号，仅保护导出的 CSV，不改变 JSON/比较值；Markdown 将数据作为文本转义。CSV 的行事件值为 JSON 对象字符串，不展开成原始源文件列。

单个报告上限 64 MiB，超限失败并提示降低 `max_differences`；不会为缩小报告静默丢弃更多差异。输入文件始终只读，输出仅通过 `Context.files` 写入任务目录。不提供数据库连接、自动容错或任意 SQL/宏执行能力。

## 验证

仅复制自身、官方 `data-preview` 与必要 Python 包到临时 root，通过真实 Runtime/Host 验证，不依赖其他正在开发的插件。

```sh
.venv/bin/python -m unittest discover -s tests -p test_data_compare.py -v
```

Excel 测试在已有 openpyxl 时执行；不可用时明确 skip，不安装依赖。覆盖跨 CSV/JSON/Excel、预览选项与空记录一致性、主键异常、多重集重复次数与容差最大匹配、报告截断、公式注入、严格 JSON、输入上限、源文件不变与无 NaN 输出。
