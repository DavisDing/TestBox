# 多格式解析预览

命令 `data.preview`，GUI 的“解析预览”面板可调整参数并重新预览。预览调用真实 Runtime/Host、记录任务，完整解析后只展示前 20 行；样本截断不代表输入被截断。预览与 data.compare/data.check 使用 SDK 同一套解析器。

支持 `.csv/.tsv/.txt/.json/.jsonl/.ndjson/.xlsx/.xlsm`；SQL 文本由 sql.preview 提取结构。

```sh
python -m testbox.cli run data.preview --set input=sample.txt --set 'options={"format":"txt","delimiter":"||","record_separator":"\\n","encoding":"utf-8-sig"}'
```

两侧配置独立，支持实际或转义 `\t/\r/\n`。CSV/TSV 单字符列分隔符；TXT 支持多字符列分隔符、自定义记录分隔符（必须与列分隔符不重叠）。引号字段中的换行/分隔符保留；双引号加倍转义。空行默认跳过，缺列/多列、重复表头、未闭合引号失败。表头不自动猜测，默认第一条记录。`has_header=false` 时可给 `columns`，不提供时生成 column_1 等列。

参数 options：format(auto/csv/tsv/txt/json/jsonl/ndjson/xlsx/xlsm/sql)、encoding、delimiter、record_separator(auto)、quotechar、escapechar、has_header、header_row(1起，文本为记录序号，Excel为物理行)、start_row(0自动跟随表头)、columns、sheet(默认活动Sheet)、json_path(点分路径，可数字下标，无通配符)、skip_empty_rows、formula_mode(formula/cached)。Excel 使用可选 openpyxl（已有 evidence extra），不重新计算公式，不执行宏，旧xls不支持。XML 尚不支持；固定宽度 TXT 与批量文件/Sheet 用法见下节。

资源默认上限：20MiB文件、100000行、256列、2000000单元格、100000字符单元格；可在支持范围内调整 max_bytes/max_rows/max_columns/max_cells/max_cell_length，超限失败，不比较部分数据。JSON拒绝重复键、NaN/Infinity和过深嵌套；不把字段缺失填成null。

normalize：column_mapping(输入名→标准名)、trim、casefold、null_values(字符串列表)、types(标准字段名→string/integer/decimal/boolean/date/datetime)。默认不做宽松转换；日期为ISO，布尔仅true/false或布尔值，decimal以精确文本输出并记录types。标准化列冲突、类型转换失败立即报错，原文件不变。预览产物包含数据样本，使用真实敏感数据前请注意本地输出的访问权限；日志不主动打印样本。

预览界面/Host中的长单元格按120字符有界显示，并明确标注；完整采样值在预览JSON中。文本定位同时包含物理行row与解析记录序号record，自定义记录分隔符即使没有物理换行也能定位。新插件依赖新增SDK能力，Core兼容下限1.0.23。


## 固定宽度与批量处理（Core >= 1.0.23）

- 定宽读取选项：`{"format":"fixed","widths":[3,2],"width_unit":"characters","has_header":false,"columns":["id","name"]}`。例如 `001张三` 为两个字段；按字符计数而非视觉宽度。字节格式使用 `width_unit:"bytes"` 并指定 encoding（UTF-8/GBK/GB18030等）；字段边界切断编码字符会失败。默认按物理 CR/LF 分记录，可指定 record_separator；每条记录必须精确等于字段宽度总和，不截断或补空格。空格/前导零默认保留，trim须显式启用。
- 原单文件参数继续可用。预览/质量检查/SQL预览用 `inputs:["/absolute/a.csv","/absolute/b.csv"]`；数据/结构比对用 `left_inputs` / `right_inputs` 数组。与对应 `input/left/right` 单文件互斥；SQL直接文本不与批量文件混用。所有文件经 Runtime 暂存并哈希，不直接批量读取原目录。
- Excel配置 `options:{"sheets":"all"}` 或 `{"sheets":["客户","订单"]}`；两侧分别用 left_options/right_options。与 sheet 互斥。非Excel不使用sheets；混合文件批次应分别执行。各Sheet独立处理，不合并、不执行公式或宏。
- 两侧默认精确匹配去扩展名后的文件名及Sheet名；单文件对允许文件名不同。`batch:{"pairing":"position"}` 显式按顺序配对；`batch:{"sheet_mapping":{"客户":"Customers"}}` 映射Sheet名（必须一对一、name配对）。重名冲突不随机配对，缺失项报告UNMATCHED。
- 每侧最多100个文件，展开后的文件×Sheet以及最终配对最多100项；仍受每文件读取上限、整个任务累计输入/输出配额及超时限制。单项异常继续其他项；有执行失败/未配对时父任务failed，保留已生成报告和history。业务差异/质量违规不等于执行失败；整批equal/passed不得只看成功子集。SQL未知语法仍inconclusive。
- 输出各项原有报告，加 `<task>-batch.json` 和防公式注入的 `<task>-batch.csv` 汇总。预览额外生成各项display.json，GUI下拉选择文件/Sheet显示；修改输入/配置后旧预览失效。完整数据与样本以登记产物为准，不将全部批次样本塞进Host响应。
- GUI批量控件支持多选文件和“添加文件夹内数据文件”，仅当前层、按名称排序、不递归、不跟随符号链接；CLI通过明确文件路径数组批量运行，没有目录路径参数。定宽宽度、字段名、Sheet数组在预览配置中填写JSON数组。
