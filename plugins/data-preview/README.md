# 多格式解析预览

命令 `data.preview`，GUI 的“解析预览”面板可调整参数并重新预览。预览调用真实 Runtime/Host、记录任务，完整解析后只展示前 20 行；样本截断不代表输入被截断。预览与 data.compare/data.check 使用 SDK 同一套解析器。

支持 `.csv/.tsv/.txt/.json/.jsonl/.ndjson/.xlsx/.xlsm`；SQL 文本由 sql.preview 提取结构。

```sh
python -m testbox.cli run data.preview --set input=sample.txt --set 'options={"format":"txt","delimiter":"||","record_separator":"\\n","encoding":"utf-8-sig"}'
```

两侧配置独立，支持实际或转义 `\t/\r/\n`。CSV/TSV 单字符列分隔符；TXT 支持多字符列分隔符、自定义记录分隔符（必须与列分隔符不重叠）。引号字段中的换行/分隔符保留；双引号加倍转义。空行默认跳过，缺列/多列、重复表头、未闭合引号失败。表头不自动猜测，默认第一条记录。`has_header=false` 时可给 `columns`，不提供时生成 column_1 等列。

参数 options：format(auto/csv/tsv/txt/json/jsonl/ndjson/xlsx/xlsm/sql)、encoding、delimiter、record_separator(auto)、quotechar、escapechar、has_header、header_row(1起，文本为记录序号，Excel为物理行)、start_row(0自动跟随表头)、columns、sheet(默认活动Sheet)、json_path(点分路径，可数字下标，无通配符)、skip_empty_rows、formula_mode(formula/cached)。Excel 使用可选 openpyxl（已有 evidence extra），不重新计算公式，不执行宏，旧xls不支持。固定宽度TXT、XML、目录批量、多Sheet批量未实现。

资源默认上限：20MiB文件、100000行、256列、2000000单元格、100000字符单元格；可在支持范围内调整 max_bytes/max_rows/max_columns/max_cells/max_cell_length，超限失败，不比较部分数据。JSON拒绝重复键、NaN/Infinity和过深嵌套；不把字段缺失填成null。

normalize：column_mapping(输入名→标准名)、trim、casefold、null_values(字符串列表)、types(标准字段名→string/integer/decimal/boolean/date/datetime)。默认不做宽松转换；日期为ISO，布尔仅true/false或布尔值，decimal以精确文本输出并记录types。标准化列冲突、类型转换失败立即报错，原文件不变。预览产物包含数据样本，使用真实敏感数据前请注意本地输出的访问权限；日志不主动打印样本。

预览界面/Host中的长单元格按120字符有界显示，并明确标注；完整采样值在预览JSON中。文本定位同时包含物理行row与解析记录序号record，自定义记录分隔符即使没有物理换行也能定位。新插件依赖新增SDK能力，Core兼容下限1.0.16。
