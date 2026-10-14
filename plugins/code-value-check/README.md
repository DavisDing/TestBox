# 码值转换校验 SQL 生成

命令：`sql.codecheck`。**仅生成脚本，不连接数据库，不执行脚本，不把生成成功称作业务校验通过。**

ODS/A 是源表，DWD/B 是目标表。例如源性别 `1` 经 `GENDER + 1 → a` 映射，与目标实际性别 `a` 比较；同样的 `1` 在证件域不能参与性别匹配。域与源字段名独立配置，中文含义是解释信息，不是另一份权威校验基线。

## 快速开始

插件目录 `examples/` 包含纯合成清单、码值表与配置。将配置中的两条输入路径改为本机绝对路径，或在项目根目录运行：

```sh
python -m testbox.cli run sql.codecheck --params-file plugins/code-value-check/examples/config.json
```

也可从桌面命令列表打开“码值转换校验 SQL”，填写测试清单、引擎版本、来源及用例默认配置。输出方式和语句组织为下拉选项，切换模式会隐藏并停止提交不适用参数。

输出包含 `codecheck.sql`、`codecheck-plan.json` 与 `codecheck-report.md`；SQL 可以预览和导出，计划记录来源行、完整配置、结果列映射与生成统计。不提前填写真实数据库的相同/异常行数。

## 输入与字段映射

逻辑清单字段：`source_table/source_code_field/target_table/target_code_field/domain_value`。默认表头适配用户样表：源表名、源字段名、目标表、目标字段名、码值域；其他表头用 `test_plan_columns` 映射。

源目标业务键**必须配置**，不会猜主键。可以放入 `defaults`：

```json
{
  "source_keys": ["person_id", "tenant_id"],
  "target_keys": ["customer_id", "tenant_id"],
  "types": {"source_code": "text", "target_code": "text", "domain": "text", "keys": ["text", "text"]}
}
```

组合键按顺序配对，不拼成字符串。也可将 `source_keys/target_keys/types/mapping_conditions/source_filters/target_filters/nulls_equal/enabled/case_id` 映射到清单列。Excel 的数组、对象单元格须是合法 JSON。只有未配置的列继承默认；显式空必填值失败，行内数组替换默认数组而不隐式合并。

码值表核心字段：`domain/source_code/target_code/code_label`；默认表头为码值域、源码值、目标码值、码值中文。`source_label` 可映射源码值含义；任意额外逻辑维度可在 `mapping_columns` 或数据库 `code_table.columns` 中声明。

数据库模式：

```json
{
  "mapping_mode": "database",
  "code_table": {
    "table_parts": ["reference", "code_mapping"],
    "columns": {"domain": "code_domain", "source_code": "raw_code", "target_code": "converted_code", "code_label": "name_cn", "region": "region_id"}
  }
}
```

文件模式支持 XLSX/XLSM，以及 CSV/TSV/JSON。旧 XLS 不支持；Excel 需要现有可选 `evidence` 依赖中的 openpyxl。公式和公式缓存不作为配置数据，输入说明文字不作为可执行指令。

| 输入组织 | 配置 |
| --- | --- |
| 两文件 | `mapping_source=separate_file`，填写 `mapping_input`，两边各配读取选项 |
| 一文件两Sheet | `same_workbook`，两边分别明确 `sheet`；不按活动Sheet推断 |
| 一Sheet双表块 | `same_workbook`，两边同Sheet、不同且不相交的 `excel_range`，如 A1:G20 / I1:N40 |
| 一Sheet合并明细 | `same_workbook + file_layout=combined_rows`，同一读取区域分别投影清单和映射 |

读取选项支持 `sheet/excel_range/header_row/start_row` 等共享读取器配置。A1 范围有明确结束行列；行号是原Sheet绝对位置。原文件不修改。同一文件不能用两个独立输入参数重复提交。

合并明细中一行代表用例配置加一条映射；按显式 `case_id` 或完整配置签名分组，仅生成一个该用例；同ID配置冲突失败。独立清单完全重复配置默认失败，`duplicate_case_policy=merge_identical` 才会合并并保留来源行；不同显式ID保持不同用例。不存在按两个文件的第N行配对。

## 多条额外限制

域与源码值始终必选，其余条件按数组全部 AND 连接，不接收任意 SQL 表达式：

```json
{
  "mapping_conditions": [
    {"mapping_field": "region", "operator": "eq", "value_source": "source_field", "field": "region_id"},
    {"mapping_field": "system", "operator": "eq", "value": "CRM"},
    {"mapping_field": "source_table_id", "operator": "eq", "value_source": "case_field", "field": "source_table"},
    {"mapping_field": "enabled", "operator": "in", "value": ["Y", "YES"]}
  ],
  "types": {"dimensions": {"region": "text", "system": "text"}}
}
```

`mapping_field` 指码值表的**逻辑字段名**，对应已配置映射；动态 `field` 是源表真实列名。用例取值允许表名、码值字段名或域；表名包含声明的schema/catalog，不会只取末段。动态条件仅支持 `eq`。常量支持 `eq/ne/in/is_null/is_not_null`，类型 `text/numeric`；NULL 必须使用空值运算符，IN不含NULL。`source_filters/target_filters` 使用相同常量运算符，左侧键为 `field`，在各自范围内过滤，不会由外层WHERE吞掉缺失诊断。日期区间、OR、任意表达式、一对多和动态域暂不支持。

## 输出选项与结果

`output_mode`：`select_only` / `insert_only` / `select_and_insert`。

`statement_layout`：`separate` / `union_all`；组合不去重，`union_batch_size` 缺省50、上限100。三个输出模式与两种组织方式均可组合。

- INSERT脚本第一条是CREATE结果表，后续是INSERT SELECT；不修改源表、目标表或码值表。默认结果表名按批次生成，可配置 `result_table/result_columns`。
- `create_policy=create_new` 缺省；支持的引擎可选 `create_if_missing`，但生成器不知道已有表结构，执行前必须核对。无自动DROP/TRUNCATE/事务/重跑去重，相同批次重跑会重复追加。
- `result_scope` 只管SELECT，`store_scope` 只管INSERT，取 `all/errors_only`。仅查询不可提交保存参数，仅落表不可提交查询范围。
- 查询并落表先保存、后查询已保存批次，不重新读业务表。仅保存异常时不能查询全量，也不能声称有全量汇总。
- `include_summary` 生成完整状态关系的汇总；不从异常子集算通过率。仅异常落表的汇总另读业务范围，可能不是同一快照，会提示警告。
- `include_reverse_check` 单独生成源有目标无、源无效/重复键诊断，不反转码值映射、不与正向目标数混算通过率。零行汇总保留用例，`NO_DATA`、通过率NULL。

结果包括批次、用例、方向、源目标表和码值列、各业务键列名和值、源实际码值、目标实际码值、**预期**目标码值、域、中文说明、相同/不同/无法比较、状态、源/目标/映射候选计数。动态维度列名和实际取值另保留；跨用例缺失槽位是有类型NULL。

目标驱动保证每条目标业务记录一条结果，重复目标记录全部保留。候选数为1才提取对应源和映射值，不用MIN/MAX选一个蒙混通过。

状态优先级：无效目标键 → 重复目标键 → 无源 → 重复源 → 源码NULL → 无映射 → 重复映射 → 空值策略 → 相同/不同。异常为“无法比较”，绝不默认为相同。

文件完整检查基本格式；在明确严格比较下，重复有效映射键生成前报错（包含全部动态维度且已应用静态限制）。数据库映射在SQL中按同一套匹配条件计数，重复时预期值NULL。合法多对一不视为重复。默认不trim/忽略大小写/补零/回退无码值。

`nulls_equal` 缺省false；唯一映射中预期与实际都NULL为 `NULL_VALUE`，启用后才SAME；单空DIFFERENT。`allow_mapping_null` 缺省false，Excel/CSV须配专用 `null_token`，不能把普通空白当合法NULL映射。文本模式遇数字单元格默认拒绝，只有明确 `numeric_text_policy=convert` 才转换，不恢复已经丢失的前导零。

域预筛与清单、内联映射使用相同的声明类型和转换策略。例如文本域启用convert时，数字单元格1.0保留为文本`1.0`，不会提前归一成`1`；拒绝策略下遇数字域单元格直接报错，不静默丢弃。numeric域仍按数字比较。

## 名称校验

内部关系使用避开所有用例源/目标表、数据库映射表及结果表的独立前缀，包含汇总、反向检查与已存结果查询；不重命名用户对象。投影后的字段始终按方言引用，Oracle内部业务键别名也保持定义与引用一致。

对象名每段及SQL列名超过以下生成器边界时拒绝，不截断或自动改写，结果列仍拒绝大小写变体重复：

| 引擎 | 每个标识符的生成器上限 / 前提 |
| --- | --- |
| PostgreSQL | 63 UTF-8字节，按默认标识符长度配置；自定义编译参数需另行核对 |
| MySQL / MariaDB | 64字符；生成别名也采用这个保守上限 |
| Oracle 12.2+ | 128 UTF-8字节，要求数据库使用AL32UTF8且兼容级别满足12.2；其他字符集需单独适配 |
| SQL Server | 128 UTF-16码元，补充平面字符占两个码元 |
| SQLite / Trino | 保留应用级128字符上限，不宣称这是数据库原生限制 |

文件表头不是数据库物理列名，不套数据库字节上限；会渲染为SQL的逻辑维度名须校验。数据库模式对所有已配置物理列（包括本用例未引用列）校验。外部引擎这些边界只有渲染/拒绝测试，未代替实机验收。

## 引擎边界与验收

| 入口 | 最低版本 | 查询/组合/落表 | 当前证据 |
| --- | --- | --- | --- |
| SQLite | 3.35 | 全部六种组合 | 本地3.54.0合成数据执行测试 |
| PostgreSQL | 12 | 已渲染 | implemented-unverified，未实机验收 |
| MySQL | 8 | 已渲染 | implemented-unverified，未实机验收 |
| MariaDB | 10.2 | 已渲染 | implemented-unverified，未实机验收 |
| SQL Server | 引擎14（2017） | 已渲染；仅新建表 | implemented-unverified，未实机验收 |
| Oracle | 12.2 | 已渲染；仅新建表 | implemented-unverified，未实机验收；要求BINARY数据绑定排序规则可用 |
| Trino | 483 | 实验性SELECT/UNION ALL，禁止落表 | implemented-unverified；必须由用户已有SQL层提供对象与连接器，关联子查询组合需实机验证 |

这不是“全部RDB/NoSQL已支持”。NoSQL若由现有Trino SQL入口暴露标量关系，可试验该入口，不能仅填写底层MongoDB/Elasticsearch名称冒充兼容。未知引擎及未实现组合明确拒绝，未部署查询网关/同步服务，不输出原生管道/DSL。直接Couchbase SQL++、Elasticsearch SQL、MongoDB查询入口、DB2/国产数据库兼容模式等仍待适配和实际版本验收；`NEEDS_CONFIRMATION`：用户实际SQL入口、版本、连接器和对象可见性。

比较按声明类型进行，统一展示/保存转文本在比较之后。文本采用明确区分大小写及尾空格的策略，SQL Server附加长度检查；**数据库列实际类型必须与声明匹配**，字符排序规则、Oracle空串与最大长度仍需实际环境验收。Oracle展示字段最大4000字符，超长实际数据不保证可落表；不得以SQLite通过证明其他引擎兼容。Trino适配尤其不能从模板渲染推断特定NoSQL连接器支持全部关联。

资源边界：有效用例最多1000、业务键最多16、条件最多50、SQL默认最多50MiB；内联映射单用例最多SQLite400/其余1000行，不靠删候选绕过配额，超限失败并建议数据库码值表。严格文本域会在完整格式检查后按等义规则预筛；数字域保留候选由SQL匹配。不同用例使用各自的代码/维度类型，混域数字与文本不要求全文件单一类型。numeric常量限定在DECIMAL(38,18)范围；text对应变长字符列，固定CHAR填充、超长文本、浮点精度和特殊对象类型须另行核对，离线生成不验证真实列类型。

## 回归

```sh
python -m unittest discover -s tests -p test_code_value_check.py -v
python -m unittest discover -s tests -p test_gui_runtime.py -v
python -m unittest discover -s tests -v
```

真实Host、六种SQL组合、域隔离、条件、重复、NULL、前导零、组合键、反向、零数据、Excel布局和原文件不变均有专项测试。GUI使用可选Qt offscreen；这不等于真人桌面交互/Windows安装器/外部数据库验收。
