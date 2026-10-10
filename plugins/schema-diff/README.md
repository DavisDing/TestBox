# schema-diff：本地 SQL 结构比对与预览

命令：`sql.diff`、`sql.preview`。插件只解析本地文本和声明结构清单，**不执行 SQL、不连接数据库、不联网、不导入其它插件**。使用 SDK `read_dataset(path, options)`，无需新增依赖；Excel 使用现有可选 `openpyxl`，环境缺失时由 SDK 明确失败。

## 输入契约

`sql.diff`：

- 左侧 `left`（顶层 file-path）或 `left_text`（string），**恰好一种**；右侧 `right` 或 `right_text` 同样恰好一种。
- `left_options` / `right_options`：dict，默认 `{}`；透传 SDK。
- `left_mode` / `right_mode`：`sql`（默认）或 `structure`。
- `sql_column`：默认 `sql`，用于表格中的 SQL 文本列。SDK `format=sql` 固定读取 `sql` 全文列，不自行拆分文件行。

`sql.preview`：

- `input`（顶层 file-path）或 `text`（string），恰好一种。
- `options`：dict；`input_mode`：`sql` / `structure`，默认 `sql`；`sql_column` 默认 `sql`。
- `sample_rows`：0–100，默认 20，**仅限制展示，不限制结构解析**。
- 直接 text 仅用于 SQL，不支持把 JSON/CSV 字符串冒充结构清单；直接 text 的 options 仅接受 `format=auto/sql`，表格配置必须用于文件。
- exactly-one 按键是否存在检查；空字符串不是另一种输入的替代品。

支持文件载体：`.sql`、`.txt`、`.csv`、`.tsv`、`.xlsx`、`.xlsm`、`.json`、`.jsonl`。格式缺省自动判断。

**纯 SQL `.txt` 必须明确 `options.format="sql"`**；默认 TXT 是表格，不会把普通业务数据当 SQL 或声明结构。CSV/TSV/TXT/Excel/JSON 的 SQL 模式要求选定 SQL 列的每条记录为非空文本，每个单元格可以包含多条 SQL。

SDK options（以 SDK 当前契约为准）：`format`、`encoding`、`delimiter`、`record_separator`、`quotechar`、`escapechar`、`has_header`、`columns`、`sheet`、`header_row`、`start_row`、`json_path`、`skip_empty_rows`、`formula_mode`、`max_rows`、`max_columns`、`max_bytes`、`max_cells`、`max_cell_length` 等。JSON 支持对象、对象数组及明确数据路径，拒绝重复键、非有限值、过深嵌套。Excel 默认读取公式文本；`formula_mode="cached"` 必须手动配置，不计算公式或执行宏，无缓存值不是可靠声明。SDK 额度超限失败，不静默取样后宣称完整。

## SQL 与结构清单是不同模式

`structure` 消费原 `sql.parse` 字段行格式，至少要求非空字符串 `table`、`field`、`type`；普通数据不得作为声明结构。接受 `length`、`precision`、`scale`、`nullable`、`default`、`primary_key`、`unique`、`auto_increment`、`comment`、`foreign_table`、`foreign_field`。布尔可用 JSON boolean 或常见 CSV true/false、yes/no、1/0；维度可用非负整数。缺失/空布尔是 unknown，不默认 false。类型参数和独立维度冲突失败。类型参数中的维度是声明信息，可推导但不推断数据值的类型。

缺少约束列会产生 unknown；完整清单可与同结构 DDL 判 equal。清单仅含 `primary_key` 成员标志时不能恢复复合主键顺序，显式标 unknown。额外非空且未支持属性会形成 unknown。清单不能恢复 SQL 引用标识符的限定层级，不扩大既有 `sql.parse` 契约。

## SQL 覆盖边界

使用 tokenizer，而非按逗号/分号朴素 split：支持单引号字符串及 doubled quote、双引号/backtick/bracket 标识符及转义闭合符、行注释、嵌套块注释、有界括号。字符串里的逗号、换行、分号不会拆语句。未闭合引号/注释/括号、空列表项等明确语法损坏返回 failed。Dollar quote、方言相关反斜杠转义、可执行注释或 hint 安全消费后标 unsupported，不猜测方言执行语义。

- **CREATE TABLE**：常见显式字段声明、schema 限定表名、`IF NOT EXISTS`、TEMP/TEMPORARY；支持有限常见整数、字符、decimal/numeric/number、日期时间、JSON、二进制等类型，长度/precision/scale，UNSIGNED；支持 NULL/NOT NULL、DEFAULT、字段 PRIMARY KEY/UNIQUE、自增标志、COMMENT、表级 PRIMARY KEY/单列 UNIQUE。
- DDL 是 `declared=true`。没有 NULL 限定的普通声明记录 nullable=true；不依据具体数据库隐式规则（如 PRIMARY KEY 的隐式 NOT NULL）推断声明。类型和表达式按 token 文本比较，不归并 INTEGER/INT 等方言别名，也不宣称 `1+2` 与 `3` 语义等价。
- 表级复合 PRIMARY KEY 保留成员与顺序；复合 UNIQUE 不能转成每列 UNIQUE，保留约束详情并标 unknown。
- **SELECT**：限定单表 FROM；保留投影顺序、别名、表达式、直接来源 `source_field` 和表达式引用列表 `source_fields`。支持简单列、限定列、literal、函数调用、括号、常见算术/比较/连接操作、简单显式或隐式别名。函数名不当字段，`COUNT(*)` 不当投影星号。
- **INSERT INTO ... VALUES**：显式 columns 保留顺序；逐行校验列数与 VALUES 数量，多行合法。未写 columns 不从值推断字段；明确标 unknown。INSERT SELECT/SET/DEFAULT、VALUES 子查询及尾部扩展不支持。
- SELECT/INSERT **不声明类型、nullable、default、主键或唯一约束**，这些值为 null/unknown，不假造数据类型。
- SELECT `*` / `table.*` 无法离线展开，字段集合 incomplete，不能把 `*` 当普通缺失字段，也不能判 equal。
- CTE、JOIN、多表、子查询、UNION/INTERSECT/EXCEPT、SELECT INTO、CASE/CAST/窗口函数、ALTER/DROP/UPDATE 等不在支持范围。WHERE/GROUP/ORDER/LIMIT 等尾部子句不验证，保留投影但标 unsupported/incomplete；DDL 表尾选项、外键、CHECK、生成列、索引、未知类型/修饰同样不宣称完整。
- 不支持语句逐条保留，不漏掉后仅比较剩余 CREATE。缺少分号的尾部内容不会静默丢弃。

这不是完整 SQL 方言解析器、SQL 校验服务或数据库结构查询工具。支持范围外但可安全 tokenize 的内容返回 success + unknown，而不是宣称可执行 SQL 合法。

## 结论与产物

采用保守 strict-declared 策略：按输入语句顺序配对；字段顺序重要；比较完整字段集合及两侧**共同已知**的声明属性；SELECT 对 SELECT 同时比较投影、别名和来源。已知差异可证明 different，不要求所有属性已知。

| verdict | 含义 |
| --- | --- |
| `equal` | 没有已知差异，而且两侧结构覆盖完整、无 unknown |
| `different` | 有可证实的已知差异；仍可能 complete=false |
| `inconclusive` | 没有已知差异，但存在未知、未声明或不支持内容 |

unknown **既不算 different，也不算 equal**。SELECT/INSERT 与 DDL 列名相同通常为 inconclusive，因为没有类型/约束声明。success 表示读取、解析和比较正常完成，**不表示 equal**。硬解析错误、无效输入/配置、额度超限 failed。

每次成功输出 `<task.id>.json`（完整结构、差异、unknown、warnings、来源位置）与 `<task.id>.md`（有界摘要和来源样例）。不写任务 output 外的路径，不修改输入。来源含暂存文件/source、数据 row/path/sheet（SDK 提供时）、SQL 单元格 column、sql_line/sql_column/offset 与单元格内 statement 序号。

preview `result.data` 含 `columns`、`rows`（每行 table/field/type/声明属性/投影/known/location）、`structure`（语句和约束摘要）、`complete`、unknown/warnings、`total_rows`、`sample_count`、`sample_truncated`。结果展示限制最多 100 条列表记录和每字符串 2000 字符，提供相关 truncated/count 标记及 display_bounded；完整原始结构见 JSON。采样不改变 complete，预览不产生 equal 结论。GUI 通过 Schema `x-preview` 的两侧 sources 和顶层 sql_column 公共面板调用命令，插件不实现或修改 GUI。

## CLI 示例

```sh
testbox run sql.preview --set 'text=CREATE TABLE t (id INTEGER,name VARCHAR(10));'
testbox run sql.diff --set left=left.sql --set right=right.sql
testbox run sql.diff --set left=fields.csv --set left_mode=structure --set right=ddl.sql
testbox run sql.preview --set input=ddl.txt --set 'options={"format":"sql"}'
testbox run sql.preview --set input=queries.json --set sql_column=query --set 'options={"json_path":"payload.queries"}'
```

测试入口（项目根目录）：

```sh
.venv/bin/python -m unittest discover -s tests -p test_schema_diff.py -v
```

测试使用临时 root 仅安装本插件，经过真实 Runtime→Host 子进程，不 Mock 插件执行或数据读取。


## 固定宽度与批量处理（Core >= 1.0.23）

- 定宽读取选项：`{"format":"fixed","widths":[3,2],"width_unit":"characters","has_header":false,"columns":["id","name"]}`。例如 `001张三` 为两个字段；按字符计数而非视觉宽度。字节格式使用 `width_unit:"bytes"` 并指定 encoding（UTF-8/GBK/GB18030等）；字段边界切断编码字符会失败。默认按物理 CR/LF 分记录，可指定 record_separator；每条记录必须精确等于字段宽度总和，不截断或补空格。空格/前导零默认保留，trim须显式启用。
- 原单文件参数继续可用。预览/质量检查/SQL预览用 `inputs:["/absolute/a.csv","/absolute/b.csv"]`；数据/结构比对用 `left_inputs` / `right_inputs` 数组。与对应 `input/left/right` 单文件互斥；SQL直接文本不与批量文件混用。所有文件经 Runtime 暂存并哈希，不直接批量读取原目录。
- Excel配置 `options:{"sheets":"all"}` 或 `{"sheets":["客户","订单"]}`；两侧分别用 left_options/right_options。与 sheet 互斥。非Excel不使用sheets；混合文件批次应分别执行。各Sheet独立处理，不合并、不执行公式或宏。
- 两侧默认精确匹配去扩展名后的文件名及Sheet名；单文件对允许文件名不同。`batch:{"pairing":"position"}` 显式按顺序配对；`batch:{"sheet_mapping":{"客户":"Customers"}}` 映射Sheet名（必须一对一、name配对）。重名冲突不随机配对，缺失项报告UNMATCHED。
- 每侧最多100个文件，展开后的文件×Sheet以及最终配对最多100项；仍受每文件读取上限、整个任务累计输入/输出配额及超时限制。单项异常继续其他项；有执行失败/未配对时父任务failed，保留已生成报告和history。业务差异/质量违规不等于执行失败；整批equal/passed不得只看成功子集。SQL未知语法仍inconclusive。
- 输出各项原有报告，加 `<task>-batch.json` 和防公式注入的 `<task>-batch.csv` 汇总。预览额外生成各项display.json，GUI下拉选择文件/Sheet显示；修改输入/配置后旧预览失效。完整数据与样本以登记产物为准，不将全部批次样本塞进Host响应。
- GUI批量控件支持多选文件和“添加文件夹内数据文件”，仅当前层、按名称排序、不递归、不跟随符号链接；CLI通过明确文件路径数组批量运行，没有目录路径参数。定宽宽度、字段名、Sheet数组在预览配置中填写JSON数组。
