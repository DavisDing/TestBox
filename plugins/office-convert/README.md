# office-convert

本机旧 Office 转 OOXML 插件，仅提供 `office.convert`、`office.inspect`。使用**已安装**的 LibreOffice/soffice，不安装软件或 Python 依赖；不调用 shell，不接受用户提供的外部命令、引擎路径或附加参数。

## `office.convert`：输入与参数

`input`（单文件 `string file-path`）或 `inputs`（1–100 项 `array file-path`）**严格二选一**。支持：

|源扩展名|目标扩展名|固定 export filter|
|---|---|---|
|`.xls`|`.xlsx`|`Calc MS Excel 2007 XML`|
|`.doc`|`.docx`|`Office Open XML Text`|
|`.ppt`|`.pptx`|`Impress MS PowerPoint 2007 XML`|

源文件必须具有 OLE CFB 魔数 `D0 CF 11 E0 A1 B1 1A E1`，而不是仅按扩展名信任内容；这只是必要的容器预检查，不是完整 CFB 结构解析。损坏、加密或不支持的 OLE 内容仍可能在转换阶段失败。

- 不支持目录、符号链接、重复来源、空文件、HTML/OOXML 伪装文件、未知/新格式，以及源/目标格式相同；不自动扫描目录。
- 最多 100 文件，单文件 **50 MiB**，整批输入 **100 MiB**；超过文件数/大小属于输入级错误。
- `timeout_seconds`：整数 5–120，默认 60 秒。
- `batch_timeout_seconds`：整数 10–240，默认 240 秒；deadline 从 `execute` 入口开始（包含检查、profile、转换、输出验证时间），每次实际引擎 timeout 不超过剩余时间。deadline 后不再启动转换，未处理项 skipped；报告与进程回收作为收尾操作可能占用少量额外时间，不改变 Core 的 300 秒上限。
- `continue_on_error`：布尔值，默认 true；false 时首个逐项失败后未处理项为 skipped。

输入 Schema 的 `x-input-policy` 声明 `reject_symlinks=true`、`unique_sources=true`、`max_files=100`，配合主线程实现的 Workspace opt-in 在暂存之前拒绝原始选中文件的符号链接与 canonical duplicate。Host 收到的是暂存副本，不能恢复原始链接身份；插件**不按 hash 去重**，不同来源的同名同内容文件可以分别转换。系统目录中的 `/tmp` 等平台链接不被当成选中文件链接拒绝。

示例（由现有 Runtime/CLI 提供参数解析，插件不改变 CLI）：

```json
{"input":"/path/to/legacy.xls"}
```

```json
{"inputs":["/path/to/one.doc","/path/to/two.ppt"],"timeout_seconds":60,"batch_timeout_seconds":240,"continue_on_error":true}
```

不接受 `overwrite`、`mode`、`keep_structure`、`options` 或 CLI executable；不会覆盖或改写源文件。

## 产物与结果

产物固定在任务 `output/converted/{序号4位}-{原stem}.{xlsx|docx|pptx}`，例如 `converted/0001-季度报表.xlsx`。同名输出存在则逐项失败；使用原子 no-clobber 发布，不覆盖现有文件。路径经过链接、逃逸或不安全组件则拒绝。

三份报告命名为 `output/{task.id}.json`、`.csv`、`.md`，通过 `Result.files` 登记。JSON 含 `task_id`、`summary`、`items`；每项包含：

- `index`、`source_name`（仅原文件名，不含绝对路径）；
- `input_size`、`input_sha256`（可能没有 hash，例如格式预检查失败或未处理）；
- `target_format`、`status`（**succeeded / failed / skipped**）；
- `output`（仅 output 内相对路径）、`error_code`、`error_message`。

报告不保存原文、引擎 stdout/stderr 或源父目录；文件名本身属于用户数据，必要时由用户在导出前审查。CSV 使用 UTF-8 BOM 并中和公式前缀（含前导空白后的 `= + - @`、Tab/CR/LF），Markdown 转义 HTML/表格/链接字符。JSON/CSV/MD 均保留大小、hash、状态、错误信息。

`Result.data.summary` 与 JSON 报告 `summary` 完全一致，字段为：`status`、`complete`、`total_count`、`succeeded_count`、`failed_count`、`skipped_count`；这些字段也保留在 data 顶层供简洁摘要使用，不返回大明细。

- 全部成功：Result `success`，summary `status=succeeded`、`complete=true`。
- 部分成功：Result `success`，summary `status=partial`、`complete=false`，保留成功文件、所有报告及 warnings，不能据任务 success 推断所有文件成功。
- 全部失败/没有成功项：Result `failed`，summary `status=failed`、`complete=false`，仍登记三种报告。
- 参数、重复输入、路径安全、大小等配置/输入级错误：`PluginError`；缺引擎为 `DEPENDENCY_MISSING`，不会安装或伪造成功。

## `office.inspect`：只读引擎检查

仅接受 `{}`。引擎来源是**可信管理员配置** `context.config["soffice_path"]`（配置存在但无效时不会静默回退），否则按 `shutil.which("soffice"/"libreoffice")`、常见平台安装路径查找。这个配置是可信程序执行边界，不是供不可信 CLI 参数调用任意程序的能力；POSIX 允许发行版/本机的 soffice wrapper，Windows 只允许 native `.exe/.com`，拒绝 batch 脚本。

返回 `available`、`engine_path`、`engine_version`、`support`（`xls:xlsx, doc:docx, ppt:pptx`）和 `warnings`。这里 `engine_path` 是用户主动检查时的明确诊断字段，转换报告不包含此路径。缺引擎时 inspect **Result success + available=false + engine_version=null + warnings=[DEPENDENCY_MISSING]**，不是抛出转换错误。探查失败/超时也返回 available=false 和原因码。

`--version` 使用本任务 fresh `UserInstallation`，引擎执行上限 5 秒、双管道各 64 KiB；不读取或更改用户现有 profile。

## 安全措施与真实边界

- 临时目录只创建在任务 output 下 `.office-work/run-*`，每个文件独立 profile/out；目录权限 POSIX 0700。仅删除本次私有目录，不删除共享 `.office-work` 或其他任务文件；转换器未确认回收则停止后续转换并保留私有 profile，避免删除仍在使用的目录。
- profile 预置 `MacroSecurityLevel=3`、`DisableMacrosExecution=true`、`DisableActiveContent=true`、`DisableOLEAutomation=true`；`SecureURL` 空，fresh profile 不继承 TrustedAuthors 或扩展。
- Calc `Content/Update/Link=1`（never）；Writer `Link=0`（NEVER），禁用 Field/Chart 自动更新。不同组件数字枚举不同，不能统一填 1。
- 只传固定 argv：独立 `UserInstallation`、`--headless --norestore --nodefault --convert-to <固定过滤器> --outdir <私有目录> <暂存源文件>`；`shell=False`，不传 macro URL、脚本、listener 或用户额外参数。
- 双 pipe 由并发 drain 有界读取，每个最多保留 64 KiB；超额终止本次进程树，丢弃后续字节，不在内存/磁盘积累 stdout/stderr。转换产物大小也在进程期间轮询；这是应用层资源限额，不是内核磁盘 quota，快速写入可能在监测间隔内短暂超过限额。
- POSIX 使用 `Popen(start_new_session=True)`；超时只 terminate/kill **自有进程组**并 wait/reap，不用 `pgrep`、不杀用户其他 Office 会话。Windows 实现 Job Object 进程树回收，隔离失败 fail-closed；原生 Windows 实机尚未验收。
- 输出非空、普通文件、ZIP CRC 和每个部件实际解压长度校验；限制 ZIP 文件/压缩总量 100 MiB、总解压量 200 MiB、最多 10000 条目，整批已发布产物 400 MiB。拒绝重复条目、路径逃逸、symlink/特殊条目、VBA/script/ActiveX 部件或宏 Content-Type、DTD/entity。
- 主部件须为 `xl/workbook.xml`、`word/document.xml` 或 `ppt/presentation.xml`，同时验证正确的根节点与 OOXML namespace，以及 `[Content_Types].xml` 中**精确**的 non-macro content type。引擎退出 0 而无文件/空文件/坏 ZIP 不算成功。不解压 ZIP 到文件系统。

**不是 OS 沙箱。** `network: false` 是插件能力声明，不代表操作系统封网；以上配置不能宣称对任意恶意文档、引擎漏洞或可信配置被恶意替换提供安全隔离。只处理用户主动选择的**可信本地文档**，不把恶意未知附件交给本机引擎。宏被禁用/移除，OLE/DDE/外链/脚本不执行；字体、复杂排版、公式、图表、嵌入对象、动画等可能丢失或改变，需业务验收，不作逐像素或全功能无损承诺。

## 本机验证边界

2026-10-07 当前 macOS 上 PATH soffice 是 Codex wrapper，实际版本为 `LibreOfficeDev 26.8.0.0.alpha0 2c87e51eeaa2b413ff4ae097b2705eea1995d8e5`。真实专项通过 Runtime → Host → 引擎验证五项：XLS 多 Sheet、前导零、公式及缓存值；DOC 正文和表格；PPT 两页页数和中英文文本；同名不同来源混合批次；引擎检查。源文件 hash/mtime 与导出也在验收范围内。准确全量结论以交付记录为准。

DOC 真旧格式夹具使用 FODT（含真实表格）→ `MS Word 97`；该本机 alpha 引擎在 DOCX → DOC 的夹具生产阶段存在表格丢失，独立 profile 的原生基线可以复现。没有删表格断言或将表格降级为文本；FODT 产生的真实 DOC 转回 DOCX 保留表格。该发现不在本插件旧转新范围内，但说明不能把基础验收扩张为任意文档无损承诺。

PPT 真旧格式夹具使用 FODP → `MS PowerPoint 97` → 插件 PPTX，基础页数与文本通过，不等于动画、媒体、复杂模板或真实客户文稿已验收。Windows native 不在本次实际验收范围。另有二十一项假转换器测试，只验证错误/超时/容器协议，不能冒充真实 Office 转换。

官方依据：

- [启动参数与 UserInstallation](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html)
- [固定转换过滤器](https://help.libreoffice.org/latest/en-US/text/shared/guide/convertfilters.html)
- [Common 配置定义](https://raw.githubusercontent.com/LibreOffice/core/master/officecfg/registry/schema/org/openoffice/Office/Common.xcs)
- [Calc 全局 LinkUpdateMode 枚举](https://api.libreoffice.org/docs/idl/ref/interfacecom_1_1sun_1_1star_1_1sheet_1_1XGlobalSheetSettings.html)
- [Writer UpdateLinks 枚举](https://raw.githubusercontent.com/LibreOffice/core/master/sw/inc/linkenum.hxx)

### 已确认缺陷（保留失败，不降级断言）

当前 alpha 版本真实 DOC 表格验收失败：带正文和 3×2 表格的合成 DOCX 先导出 MS Word 97 DOC，再转回 DOCX 时表格丢失。绕过插件，用默认 fresh profile + `Office Open XML Text` / `MS Word 2007 XML` / FODT 对照，同一个 DOC 同样没有表格；原始 DOCX 直接导入 FODT 能保留表格。问题已定位到本机 alpha 引擎的旧 DOC 导出/导入链路，而非本插件安全 profile，不能据此宣称实际旧 DOC 表格保真已通过。插件遇到含 DOC 的任务返回显式保真 warnings；容器结构通过仍可能丢内容，报告不会将 warning 隐藏。

补充：tests agent 后续改用原生 flat ODF 含表格夹具产生真正旧 DOC，保留原有表格/正文断言后已报告真实用例通过；这与前述 python-docx → alpha DOC 导出链路的失败是不同夹具，不能互相替代。最终全量结果由主线程整合。
