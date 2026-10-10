# Data Check（P3 首版）

`data.check` 是本地、只读的声明式质量检查插件。复用公共 SDK `read_dataset` / `normalize_dataset`，通过真实 Plugin Host 执行；不依赖其他业务插件，不改写原文件，不清洗回写，不连接网络或数据库，不执行 SQL、Python、表达式或 Excel 公式/宏。未提供 regex：首版无法可靠限制 ReDoS，因此显式拒绝。

## 参数与预览

- `input`：顶层必填 `file-path`；Runtime 暂存到任务 input。
- `options`：读取选项，默认 `{}`，遵循 SDK。支持 CSV / TSV / TXT / JSON / JSONL（及 ndjson）/ XLSX / XLSM / SQL。`.xls` 不支持。
- `normalize`：显式标准化，默认 `{}`，遵循 SDK。支持 `column_mapping`、`trim`、`casefold`、`null_values`、`types`（string / integer / decimal / boolean / date / datetime）。只改变独立内存副本，源文件和原始 dataset 不变。
- `rules`：必填、1..1000 条声明式规则，`type` 标识规则种类。
- `max_issues`：默认 1000，0..10000，控制**报告明细**保留数量；0 只输出汇总。仍检查所有已读取行、所有规则，不抽样、不提前停止。

Schema 的 `x-preview` 声明 `data.preview`，来源 input/options/normalize，标签「输入数据」。预览入口由公共层提供，本插件不实现另一个预览或读文件引擎。

已知读取配置：`format`、`encoding`、`delimiter`、`record_separator`、`quotechar`、`escapechar`、`has_header`、`header_row`、`start_row`、`sheet`、`json_path`、`columns`、`skip_empty_rows`、`formula_mode`、`max_rows`、`max_columns`、`max_bytes`、`max_cells`、`max_cell_length`。基本类型由 Schema 检查，实际含义、支持范围和资源边界由公共 SDK 检查；已知但与所选格式无关的正确类型选项可能不生效。未知读取/标准化选项报错，不悄悄丢弃。

Excel 需要**已有**可选依赖 `openpyxl`；本插件没有新增或自动安装依赖。无此依赖时读取失败。默认 `formula_mode=formula` 读取公式文本；显式 `cached` 读取缓存、不重算，有读取警告，不能区分无缓存与真正的空值；XLSM 宏不执行。默认活动工作表，多工作表或缓存模式提示保留在报告中。

SQL 文件是仅含 `sql` 字段的一条文本记录，**不解析为表数据，不执行语句**。JSON/JSONL 每条是对象；重复 JSON 键、NaN、Infinity 等由 SDK 拒绝。CSV/TSV/TXT 字段默认是字符串，不隐式推断数值；用 normalize.types 明确转换。

SDK 的 max_rows 等是硬读取上限，超过上限会使任务失败，**不是采样大小**。只有 `complete=true` 且行数与 locations 一致的结果才能进入检查。完整检查指 SDK 依据显式 sheet/json_path/header_row/start_row 等选定的数据区域，不是工作簿全部工作表。

## 规则

所有规则可设置 `id`（唯一非空字符串，最多 128 字符，默认 `rule_1` 等）和 `severity`（error / warning，默认 error）。每条规则严格检查适用字段，未知规则、未知参数、缺少配置、非法类型、重复字段/规则 ID、反向或非有限边界，均使任务失败；所有质量规则在读取前验证。不接受 `expression`、`sql`、`pattern` 或任意代码。

| type | 必填/条件参数 | 首版语义 |
| --- | --- | --- |
| required | fields：非空、不重复的字段名列表 | 按字段检查缺失、null、空字符串；0/false 非空；空白字符串只有明确 trim 后才变空 |
| unique | fields：同上，组合键 | 任一键字段缺失/null/空字符串，该行直接 EMPTY_KEY 违规，即使只出现一次；完整键后续重复行违规，第一次不违规 |
| type | field, value_type | string/integer/number/boolean/date/datetime；严格 native 类型，不将 bool 当 integer/number；数值字符串不自动视为数字，normalize decimal 的精确字符串通过 types 元数据视为 number |
| enum | field, values：非空 JSON 值列表 | 类型感知的精确相等，true != 1；数值 1 == 1.0；嵌套 JSON 对象忽略键顺序；null 仅在 values 明确含 null 时通过，缺字段总不通过 |
| range | field；min/max 至少一个 | 含端点，Decimal 精确有限数值比较；接受数值或十进制字符串，不接受 bool/null/NaN/Infinity |
| length | field；min/max 至少一个非负整数 | 仅字符串的 Unicode 码点数，非字节数/字形簇数；不把数组、数字或 null 强转为文本 |
| compare | left, right, operator | eq/ne/lt/le/gt/ge，固定操作符；eq/ne 使用类型感知 JSON 相等；有序比较仅两个数值或两个字符串；缺失/null/不兼容类型不通过，bool 不参与有序比较 |
| row_count | min/max 至少一个非负整数 | 选定完整数据区域的行数，含端点；数据集级别明细的 location=null |

规则引用的是**标准化后**字段名。数据集完全缺少被引用列时产生一个 MISSING_COLUMN 结构违规（空数据集也不静默通过）；有数据行时仍逐行检查，逐行违规与结构违规分别计数。因此 errors/warnings 是**违规项数**，不是去重后的违规行数，required 多字段一行可产生多项。

unique 与 enum 的 JSON 相等区分字符串与数字、bool 与数字；非 bool 的有限数值按 Decimal 精确相等。compare 的两个未转换字符串为字典序（如 `"10" < "2"`）；需要数值比较时必须 normalize.types。date 严格为有效 `YYYY-MM-DD`；datetime 为带日期和时间部分的 ISO 字符串（可有时区），仅验证类型，不承诺自动时区转换或时间先后比较。

normalize.types.decimal 由 SDK 输出不损失精度的字符串，并在报告 types 中保留 `"decimal"`。number/type、enum、unique、compare 通过此元数据恢复精确数值语义，**不转换为 float**。type.string 验证物理字符串类型，所以 decimal 字符串也可以满足 string。

## 结果、输出与安全

- 配置或读取/标准化/资源错误：`Result.status=failed`，不输出伪通过的报告。
- 检查已完成、有质量违规：`Result.status=success`，Runtime 历史为 `SUCCEEDED`；只要 errors>0，`passed=false`。必须检查 passed，不能把任务执行成功当成数据合格。
- warning-only：passed=true，不阻断通过；warnings 总数与 Result.warnings 均可见。
- 读取/标准化提示单独保存于 `input_warnings`，不计入规则 warnings。
- 报告包含 errors/warnings 总数、规则级 `checked/errors/warnings/issues/passed`、rows_checked、complete、issue_count、retained_issues、omitted_issues、truncated。rule_index 从 0 开始；checked 对行规则是行数，对 row_count 是 1。
- 逐条明细只含规则、字段、severity、原因、SDK 原始 location（包含来源、行号、可选 JSON path / 工作表等），**不包含单元格原值、重复键或表达式**。只有数据集级结构/行数违规没有行 location。
- 明细按规则顺序、源行顺序保留前 max_issues 项。即使第一项是 warning、后续 error 全部被截断，仍正确返回 passed=false 和完整错误总数。
- 输出固定为 `<task.id>.json`（完整计数与有限明细）、`<task.id>.csv`（汇总、规则计数与有限明细，UTF-8 BOM）、`<task.id>.md`（计数与有限明细）。三种报告都明确展示 truncated 和省略数量。CSV 的 record_type 区分 summary / rule / issue；即使 max_issues=0 仍有汇总与规则计数，勿用明细行数反推错误总数。
- CSV 对每个单元格防公式注入（以 =/+/-/@ 或控制前缀开头时添加单引号，包括空白后的公式前缀）；Markdown 表格转义管道、反引号、HTML 角括号和换行。
- Host Result.data 只含计数和轻量摘要，不携带原值/明细；日志只打印检查行数/errors/warnings。报告仍含字段名、来源路径和规则 ID，需按本地测试资料保管。

唯一检查保留已见键的内存集合，SDK 全量读取占用内存；非流式数据库检查，不承诺百万行×千条规则的性能。遵循 Runtime 的超时、SDK 资源限制与输出配额；它们不是恶意插件安全沙箱。本轮无 UI 代码修改，无数据迁移。

## 示例

CLI JSON 参数文件内容（修改 input 为实际文件路径）：

```json
{
  "input": "/absolute/path/orders.csv",
  "options": {"encoding": "utf-8-sig", "delimiter": ","},
  "normalize": {"trim": true, "types": {"amount": "decimal", "limit": "decimal"}},
  "max_issues": 1000,
  "rules": [
    {"type": "required", "fields": ["order_id", "amount"]},
    {"type": "unique", "fields": ["order_id"]},
    {"type": "type", "field": "amount", "value_type": "number"},
    {"type": "range", "field": "amount", "min": "0", "max": "999999999999999999.99"},
    {"type": "length", "field": "order_id", "min": 1, "max": 64},
    {"type": "enum", "field": "state", "values": ["ready", "done"]},
    {"type": "compare", "left": "amount", "right": "limit", "operator": "le", "severity": "warning"},
    {"type": "row_count", "min": 1}
  ]
}
```

示例同时为 amount 和 limit 声明 decimal；若省略右侧 limit 的转换，数值/字符串不兼容，按违规处理。

## 验证

在项目根目录使用已有虚拟环境：

```text
.venv/bin/python -m unittest discover -s tests -p test_data_check.py -v
```

测试建立临时真实 Runtime 并通过 Host 子进程运行插件，不安装依赖、不使用读取 Mock；覆盖跨格式、配置边界、精度/类型、全量截断与源文件不变。缺少已有 openpyxl 时 Excel 两项明确 skip，不把跳过称为通过。


## 固定宽度与批量处理（Core >= 1.0.23）

- 定宽读取选项：`{"format":"fixed","widths":[3,2],"width_unit":"characters","has_header":false,"columns":["id","name"]}`。例如 `001张三` 为两个字段；按字符计数而非视觉宽度。字节格式使用 `width_unit:"bytes"` 并指定 encoding（UTF-8/GBK/GB18030等）；字段边界切断编码字符会失败。默认按物理 CR/LF 分记录，可指定 record_separator；每条记录必须精确等于字段宽度总和，不截断或补空格。空格/前导零默认保留，trim须显式启用。
- 原单文件参数继续可用。预览/质量检查/SQL预览用 `inputs:["/absolute/a.csv","/absolute/b.csv"]`；数据/结构比对用 `left_inputs` / `right_inputs` 数组。与对应 `input/left/right` 单文件互斥；SQL直接文本不与批量文件混用。所有文件经 Runtime 暂存并哈希，不直接批量读取原目录。
- Excel配置 `options:{"sheets":"all"}` 或 `{"sheets":["客户","订单"]}`；两侧分别用 left_options/right_options。与 sheet 互斥。非Excel不使用sheets；混合文件批次应分别执行。各Sheet独立处理，不合并、不执行公式或宏。
- 两侧默认精确匹配去扩展名后的文件名及Sheet名；单文件对允许文件名不同。`batch:{"pairing":"position"}` 显式按顺序配对；`batch:{"sheet_mapping":{"客户":"Customers"}}` 映射Sheet名（必须一对一、name配对）。重名冲突不随机配对，缺失项报告UNMATCHED。
- 每侧最多100个文件，展开后的文件×Sheet以及最终配对最多100项；仍受每文件读取上限、整个任务累计输入/输出配额及超时限制。单项异常继续其他项；有执行失败/未配对时父任务failed，保留已生成报告和history。业务差异/质量违规不等于执行失败；整批equal/passed不得只看成功子集。SQL未知语法仍inconclusive。
- 输出各项原有报告，加 `<task>-batch.json` 和防公式注入的 `<task>-batch.csv` 汇总。预览额外生成各项display.json，GUI下拉选择文件/Sheet显示；修改输入/配置后旧预览失效。完整数据与样本以登记产物为准，不将全部批次样本塞进Host响应。
- GUI批量控件支持多选文件和“添加文件夹内数据文件”，仅当前层、按名称排序、不递归、不跟随符号链接；CLI通过明确文件路径数组批量运行，没有目录路径参数。定宽宽度、字段名、Sheet数组在预览配置中填写JSON数组。
