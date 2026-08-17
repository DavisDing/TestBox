from __future__ import annotations

import csv
import hashlib
import io
import json
import random
import re
import uuid
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from testbox.sdk import PluginError, Result

SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨"
GIVEN = "伟芳娜敏静丽强磊军洋勇艳杰娟涛明超秀霞平刚桂英"
MOBILE_PREFIXES = tuple(str(item) for section in (range(130, 140), range(145, 150), range(150, 160), (162, 165, 166, 167), range(170, 179), range(180, 190), range(191, 200)) for item in section)
CREATE_START_RE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?([\w.]+)[`\"\]]?\s*\(", re.I)
FIELD_RE = re.compile(r"^\s*[`\"\[]?([\w]+)[`\"\]]?\s+([A-Z]+(?:\s*\([^)]*\))?)(.*)$", re.I | re.S)


def column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def xlsx(rows: list[dict[str, object]]) -> bytes:
    headers = list(rows[0])
    values = [headers] + [[str(row.get(header, "")) for header in headers] for row in rows]
    sheet_rows = []
    for row_index, row in enumerate(values, 1):
        cells = "".join(f'<c r="{column_name(column_index + 1)}{row_index}" t="inlineStr"><is><t>{escape(value)}</t></is></c>' for column_index, value in enumerate(row))
        sheet_rows.append(f'<row r="{row_index}">{cells}</row>')
    sheet = '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(sheet_rows) + "</sheetData></worksheet>"
    files = {
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="测试数据" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": sheet,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def split_fields(body: str) -> list[str]:
    result, current, depth, quote = [], [], 0, None
    for character in body:
        if character in "'\"" and (not current or current[-1] != "\\"):
            quote = None if quote == character else character if quote is None else quote
        elif not quote:
            depth += character == "("
            depth -= character == ")"
        if character == "," and depth == 0 and not quote:
            result.append("".join(current)); current = []
        else:
            current.append(character)
    if current:
        result.append("".join(current))
    return result


def first_create_table_body(content: str) -> str | None:
    """Extract a CREATE TABLE body while preserving nested type parentheses."""
    match = CREATE_START_RE.search(content)
    if not match:
        return None
    depth, quote = 1, None
    for index, character in enumerate(content[match.end():], match.end()):
        if character in "'\"" and (index == 0 or content[index - 1] != "\\"):
            quote = None if quote == character else character if quote is None else quote
        elif not quote:
            if character == "(": depth += 1
            elif character == ")": depth -= 1
            if depth == 0: return content[match.end():index]
    return None


def rule(name: str, generator: str, *, unique: bool = False, options: dict | None = None) -> dict:
    return {"name": name, "generator": generator, "enabled": True, "unique": unique, "options": options or {}}


def template_rules(template: str) -> list[dict]:
    templates = {
        "retail_customer": [rule("customer_id", "sequence", unique=True, options={"prefix": "TEST-CUST-", "width": 8}), rule("full_name", "name_cn"), rule("gender", "weighted_enum", options={"values": ["男", "女"]}), rule("mobile", "mobile_cn"), rule("registered_address", "china_address"), rule("customer_type", "weighted_enum", options={"values": [{"value": "个人客户", "weight": 80}, {"value": "小微企业", "weight": 15}, {"value": "对公客户", "weight": 5}]}), rule("risk_level", "weighted_enum", options={"values": [{"value": "低", "weight": 60}, {"value": "中", "weight": 30}, {"value": "高", "weight": 10}]}), rule("registered_at", "datetime_random", options={"start": "2020-01-01T00:00:00", "end": "2026-12-31T23:59:59"})],
        "account": [rule("account_id", "sequence", unique=True, options={"prefix": "TEST-ACCT-", "width": 12}), rule("customer_id", "sequence", options={"prefix": "TEST-CUST-", "width": 8}), rule("account_type", "weighted_enum", options={"values": ["活期", "定期", "信用"]}), rule("currency", "weighted_enum", options={"values": [{"value": "CNY", "weight": 95}, {"value": "USD", "weight": 3}, {"value": "HKD", "weight": 2}]}), rule("balance", "decimal_random", options={"min": 0, "max": 500000, "scale": 2}), rule("opened_at", "date_random", options={"start": "2020-01-01", "end": "2026-12-31"})],
        "product": [rule("product_id", "sequence", unique=True, options={"prefix": "TEST-PROD-", "width": 6}), rule("product_name", "template", options={"value": "测试产品-{index}"}), rule("product_type", "weighted_enum", options={"values": ["存款", "贷款", "理财"]}), rule("term_months", "weighted_enum", options={"values": [3, 6, 12, 24]}), rule("rate", "decimal_random", options={"min": 0.01, "max": 0.08, "scale": 4})],
        "transaction": [rule("transaction_id", "transaction_id", unique=True), rule("customer_id", "sequence", options={"prefix": "TEST-CUST-", "width": 8}), rule("account_id", "sequence", options={"prefix": "TEST-ACCT-", "width": 12}), rule("amount", "decimal_random", options={"min": 0.01, "max": 500000, "scale": 2}), rule("currency", "weighted_enum", options={"values": ["CNY", "USD", "HKD"]}), rule("transaction_time", "datetime_random", options={"start": "2026-01-01T00:00:00", "end": "2026-12-31T23:59:59"}), rule("channel", "weighted_enum", options={"values": ["柜面", "手机银行", "网银", "ATM"]}), rule("status", "weighted_enum", options={"values": [{"value": "SUCCESS", "weight": 92}, {"value": "FAILED", "weight": 5}, {"value": "PROCESSING", "weight": 3}]})],
    }
    return templates[template]


def default_rules() -> list[dict]:
    """Maintain the original simple command contract when no template is requested."""
    return [
        rule("name", "name_cn"),
        rule("gender", "weighted_enum", options={"values": ["男", "女"]}),
        rule("birth_date", "date_random", options={"start": "1970-01-01", "end": "2007-12-31"}),
        rule("phone", "mobile_cn"),
    ]


def infer_rule(name: str, data_type: str, comment: str = "") -> dict:
    probe = f"{name} {comment}".lower()
    if any(item in probe for item in ("mobile", "phone", "手机号", "电话")): return rule(name, "mobile_cn")
    if any(item in probe for item in ("address", "地址")): return rule(name, "china_address")
    if any(item in probe for item in ("name", "姓名")): return rule(name, "name_cn")
    if any(item in probe for item in ("customer", "客户")) and "id" in probe: return rule(name, "sequence", unique=True, options={"prefix": "TEST-CUST-", "width": 8})
    if any(item in probe for item in ("account", "账户", "账号")): return rule(name, "sequence", unique=True, options={"prefix": "TEST-ACCT-", "width": 12})
    if any(item in probe for item in ("transaction", "trade", "流水", "交易")): return rule(name, "transaction_id", unique=True)
    if any(item in probe for item in ("risk", "风险")): return rule(name, "weighted_enum", options={"values": ["低", "中", "高"]})
    if any(item in probe for item in ("type", "类型", "status", "状态")): return rule(name, "weighted_enum", options={"values": ["默认值"]})
    if any(item in probe for item in ("amount", "balance", "金额", "余额")) or data_type.upper().startswith(("DECIMAL", "NUMERIC")): return rule(name, "decimal_random", options={"min": 0, "max": 100000, "scale": 2})
    if data_type.upper().startswith(("DATE", "DATETIME", "TIMESTAMP")): return rule(name, "datetime_random", options={"start": "2020-01-01T00:00:00", "end": "2026-12-31T23:59:59"})
    if name.lower() == "id" or name.lower().endswith("_id"): return rule(name, "sequence", unique=True, options={"prefix": "TEST-", "width": 8})
    return rule(name, "template", options={"value": f"TEST-{name}-{{index}}"})


def sql_rules(path: Path) -> list[dict]:
    body = first_create_table_body(path.read_text(encoding="utf-8"))
    if body is None:
        raise PluginError("INPUT_INVALID", "未找到支持的 CREATE TABLE 语句")
    rules = []
    for definition in split_fields(body):
        match = FIELD_RE.match(definition)
        if not match or match.group(1).upper() in {"PRIMARY", "KEY", "UNIQUE", "CONSTRAINT", "INDEX"}:
            continue
        comment = re.search(r"COMMENT\s+['\"]([^'\"]+)", match.group(3), re.I)
        rules.append(infer_rule(match.group(1), match.group(2), comment.group(1) if comment else ""))
    if not rules:
        raise PluginError("INPUT_INVALID", "DDL 中未解析到字段")
    return rules


def excel_rules(path: Path) -> list[dict]:
    try:
        with zipfile.ZipFile(path) as archive:
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared = ["".join(item.itertext()) for item in ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))]
            sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        raise PluginError("INPUT_INVALID", "Excel 字段清单格式不支持") from error
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rows = []
    for row in sheet.findall(f".//{namespace}row"):
        values = []
        for cell in row.findall(f"{namespace}c"):
            value = cell.find(f"{namespace}v")
            inline = cell.find(f".//{namespace}t")
            values.append(inline.text if inline is not None else shared[int(value.text)] if value is not None and cell.get("t") == "s" else value.text if value is not None else "")
        rows.append(values)
    if len(rows) < 2:
        raise PluginError("INPUT_INVALID", "Excel 字段清单至少需要表头和一行字段")
    headers = [item.strip().lower() for item in rows[0]]
    aliases = {"field": {"field", "name", "字段", "字段名"}, "type": {"type", "类型", "字段类型"}, "comment": {"comment", "注释", "说明"}}
    positions = {key: next((index for index, header in enumerate(headers) if header in names), None) for key, names in aliases.items()}
    if positions["field"] is None:
        raise PluginError("INPUT_INVALID", "Excel 字段清单缺少字段名列")
    result = []
    for row in rows[1:]:
        name = row[positions["field"]] if positions["field"] < len(row) else ""
        if name:
            data_type = row[positions["type"]] if positions["type"] is not None and positions["type"] < len(row) else "VARCHAR"
            comment = row[positions["comment"]] if positions["comment"] is not None and positions["comment"] < len(row) else ""
            result.append(infer_rule(name, data_type, comment))
    return result


def delimited_text(rows: list[dict[str, object]], delimiter: str, include_header: bool) -> str:
    if len(delimiter) != 1:
        raise PluginError("INVALID_PARAMS", "txt_delimiter 必须为单个字符")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), delimiter=delimiter, lineterminator="\n")
    if include_header:
        writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def sql_identifier(value: str, dialect: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*", value):
        raise PluginError("INVALID_PARAMS", "sql_table 只能包含字母、数字、下划线和点")
    left, right = {"mysql": ("`", "`"), "postgresql": ('"', '"'), "sqlite": ('"', '"'), "sqlserver": ("[", "]"), "oracle": ('"', '"')}[dialect]
    return ".".join(f"{left}{part}{right}" for part in value.split("."))


def sql_literal(value: object, dialect: str) -> str:
    if value is None: return "NULL"
    if isinstance(value, bool): return ("1" if value else "0") if dialect in {"mysql", "sqlserver", "oracle"} else ("TRUE" if value else "FALSE")
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))): raise PluginError("INVALID_PARAMS", "SQL 不支持 NaN 或无穷数值")
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def sql_script(rows: list[dict[str, object]], params: dict) -> str:
    dialect = params.get("sql_dialect", "mysql")
    table = sql_identifier(params.get("sql_table", "test_data"), dialect)
    columns = [sql_identifier(str(column), dialect) for column in rows[0]]
    batch_size = params.get("sql_batch_size", 500)
    values = ["(" + ", ".join(sql_literal(row.get(column), dialect) for column in rows[0]) + ")" for row in rows]
    statements = []
    if dialect == "oracle":
        for offset in range(0, len(values), batch_size):
            batch = values[offset:offset + batch_size]
            statements.append("INSERT ALL\n" + "\n".join(f"  INTO {table} (" + ", ".join(columns) + f") VALUES {value}" for value in batch) + "\nSELECT 1 FROM DUAL;")
    else:
        for offset in range(0, len(values), batch_size):
            batch = values[offset:offset + batch_size]
            statements.append(f"INSERT INTO {table} (" + ", ".join(columns) + ") VALUES\n  " + ",\n  ".join(batch) + ";")
    if params.get("sql_transaction", True):
        if dialect == "sqlserver": return "BEGIN TRANSACTION;\n\n" + "\n\n".join(statements) + "\n\nCOMMIT TRANSACTION;\n"
        return "BEGIN;\n\n" + "\n\n".join(statements) + "\n\nCOMMIT;\n"
    return "\n\n".join(statements) + "\n"


def render_output(rows: list[dict[str, object]], output_format: str, params: dict) -> tuple[str, bytes]:
    if output_format == "json": return "mock-data.json", json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")
    if output_format == "csv": return "mock-data.csv", delimited_text(rows, ",", True).encode("utf-8")
    if output_format == "xlsx": return "mock-data.xlsx", xlsx(rows)
    if output_format == "txt": return "mock-data.txt", delimited_text(rows, params.get("txt_delimiter", "|"), params.get("txt_header", True)).encode("utf-8")
    if output_format == "sql": return "mock-data.sql", sql_script(rows, params).encode("utf-8")
    raise PluginError("INVALID_PARAMS", f"不支持的输出格式: {output_format}")


def zip_output(rows: list[dict[str, object]], params: dict) -> bytes:
    formats = params.get("zip_formats", ["json", "csv", "xlsx", "txt", "sql"])
    if not formats or any(item not in {"json", "csv", "xlsx", "txt", "sql"} for item in formats):
        raise PluginError("INVALID_PARAMS", "zip_formats 必须是 json、csv、xlsx、txt、sql 的非空列表")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for output_format in formats:
            name, content = render_output(rows, output_format, params)
            archive.writestr(name, content)
        archive.writestr("generation-summary.json", json.dumps({"count": len(rows), "formats": formats, "sql_dialect": params.get("sql_dialect", "mysql")}, ensure_ascii=False, indent=2))
    return output.getvalue()


class Plugin:
    def init(self, context):
        self.context = context
        data_dir = Path(__file__).resolve().parents[1] / "data"
        self.data_metadata = json.loads((data_dir / "metadata.json").read_text(encoding="utf-8"))
        metadata = self.data_metadata["administrative_divisions"]
        self.mainland_regions = self._load_resource(data_dir, metadata["mainland_file"], metadata["mainland_sha256"])
        self.special_regions = self._load_resource(data_dir, metadata["hk_mo_tw_file"], metadata["hk_mo_tw_sha256"])

    @staticmethod
    def _load_resource(data_dir: Path, file_name: str, expected_sha256: str) -> dict:
        content = (data_dir / file_name).read_bytes()
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise PluginError("EXECUTION_FAILED", f"内置行政区划资源校验失败: {file_name}")
        return json.loads(content)

    def _rules(self, params: dict) -> list[dict]:
        if params.get("rules"):
            return params["rules"]
        if params.get("rule_set"):
            loaded = json.loads(Path(params["rule_set"]).read_text(encoding="utf-8"))
            return loaded["fields"] if isinstance(loaded, dict) else loaded
        if params.get("source_file"):
            if params.get("source_format") == "sql": return sql_rules(Path(params["source_file"]))
            if params.get("source_format") == "excel": return excel_rules(Path(params["source_file"]))
            raise PluginError("INVALID_PARAMS", "source_file 需要 source_format")
        return template_rules(params["template"]) if params.get("template") else default_rules()

    def _address(self, options: dict, rng: random.Random) -> str:
        provinces = set(options.get("province", []))
        cities = set(options.get("city", []))
        districts = set(options.get("district", []))
        combined = {**self.mainland_regions, **self.special_regions}
        province_options = [(name, value) for name, value in combined.items() if not provinces or name in provinces]
        if not province_options: raise PluginError("INVALID_PARAMS", "地址省份筛选无匹配项")
        province, city_map = rng.choice(province_options)
        city_options = [(name, value) for name, value in city_map.items() if not cities or name in cities]
        if not city_options: raise PluginError("INVALID_PARAMS", "地址城市筛选与省份不匹配")
        city, district_map = rng.choice(city_options)
        if isinstance(district_map, list):
            district_options = [(name, []) for name in district_map if not districts or name in districts]
        else:
            district_options = [(name, value) for name, value in district_map.items() if not districts or name in districts]
        if not district_options: raise PluginError("INVALID_PARAMS", "地址区县筛选与省市不匹配")
        district, streets = rng.choice(district_options)
        granularity = options.get("granularity", "full")
        if granularity == "province": return province
        if granularity == "city": return province + city
        address = province + city + district
        if granularity == "district": return address
        street_mode = options.get("street_mode", "virtual" if options.get("include_virtual_street", True) else "none")
        if street_mode == "real" and streets: return address + rng.choice(streets)
        if street_mode == "none": return address
        return address + f"测试路{rng.randint(1, 999)}号"

    def _unique_capacity(self, item: dict) -> int | None:
        """Return a finite generator capacity when it can be calculated safely."""
        generator, options = item.get("generator"), item.get("options", {})
        if generator == "mobile_cn":
            configured = options.get("prefixes") or self.context.config.get("phone_prefixes") or MOBILE_PREFIXES
            prefixes = configured.split(",") if isinstance(configured, str) else configured
            return len(set(prefixes)) * 100_000_000
        if generator == "name_cn":
            lengths = options.get("given_name_length", [1, 2])
            return len(SURNAMES) * sum(len(GIVEN) ** int(length) for length in lengths)
        if generator == "weighted_enum":
            values = options.get("values", [])
            return len({entry.get("value") if isinstance(entry, dict) else entry for entry in values})
        if generator == "template" and "{index}" not in str(options.get("value", "")):
            return 1
        return None

    def _value(self, item: dict, index: int, rng: random.Random) -> object:
        generator, options = item.get("generator"), item.get("options", {})
        if generator == "sequence": return f"{options.get('prefix', '')}{int(options.get('start', 1)) + index:0{int(options.get('width', 0))}d}"
        if generator == "name_cn": return rng.choice(SURNAMES) + "".join(rng.choice(GIVEN) for _ in range(rng.choice(options.get("given_name_length", [1, 2]))))
        if generator == "mobile_cn":
            configured = options.get("prefixes") or self.context.config.get("phone_prefixes") or MOBILE_PREFIXES
            prefixes = tuple(item.strip() for item in configured.split(",")) if isinstance(configured, str) else tuple(map(str, configured))
            return rng.choice(prefixes) + f"{rng.randrange(100000000):08d}"
        if generator == "china_address": return self._address(options, rng)
        if generator in {"date_random", "datetime_random"}:
            start = datetime.fromisoformat(options.get("start", "2020-01-01T00:00:00")); end = datetime.fromisoformat(options.get("end", "2026-12-31T23:59:59"))
            value = start + timedelta(seconds=rng.randrange(int((end - start).total_seconds()) + 1))
            return value.date().isoformat() if generator == "date_random" else value.isoformat(sep=" ")
        if generator == "decimal_random": return round(rng.uniform(float(options.get("min", 0)), float(options.get("max", 1))), int(options.get("scale", 2)))
        if generator == "weighted_enum":
            values = options.get("values", [])
            if not values: raise PluginError("INVALID_PARAMS", f"{item.get('name')} 的枚举值不能为空")
            values, weights = zip(*[(entry.get("value"), entry.get("weight", 1)) if isinstance(entry, dict) else (entry, 1) for entry in values])
            return rng.choices(values, weights=weights, k=1)[0]
        if generator == "transaction_id": return f"TEST-TXN-{date.today():%Y%m%d}-{index + 1:08d}"
        if generator == "uuid": return f"TEST-{uuid.UUID(int=rng.getrandbits(128))}"
        if generator == "template": return str(options.get("value", "TEST-{index}")).format(index=index + 1)
        raise PluginError("INVALID_PARAMS", f"不支持的生成器: {generator}")

    def execute(self, command, params):
        if command != "data.mock": raise PluginError("INVALID_PARAMS", "不支持的命令")
        raw_rules = self._rules(params)
        if any(not isinstance(item, dict) or not item.get("name") for item in raw_rules): raise PluginError("INVALID_PARAMS", "字段规则必须包含 name")
        rules = [item for item in raw_rules if item.get("enabled", True)]
        if not rules: raise PluginError("INVALID_PARAMS", "至少需要一个启用字段")
        for item in rules:
            if item.get("unique"):
                capacity = self._unique_capacity(item)
                if capacity is not None and params["count"] > capacity:
                    raise PluginError("INVALID_PARAMS", f"字段 {item['name']} 的唯一值容量为 {capacity}，不足以生成 {params['count']} 条")
        rng, unique_values, rows = random.Random(params.get("seed")), {}, []
        for index in range(params["count"]):
            row = {"record_id": f"TEST-{index + 1:06d}"}
            for item in rules:
                name, attempts = item["name"], 0
                nullable_rate = float(item.get("nullable_rate", 0))
                if not 0 <= nullable_rate <= 1:
                    raise PluginError("INVALID_PARAMS", f"字段 {name} 的 nullable_rate 必须在 0 到 1 之间")
                if rng.random() < nullable_rate:
                    row[name] = None
                    continue
                while True:
                    value = self._value(item, index, rng)
                    if not item.get("unique") or value not in unique_values.setdefault(name, set()): break
                    attempts += 1
                    if attempts >= 1000: raise PluginError("INVALID_PARAMS", f"字段 {name} 的唯一值容量不足")
                if item.get("unique"): unique_values[name].add(value)
                row[name] = value
            rows.append(row)
        output_format = params["format"]
        if output_format == "zip":
            name = "mock-data-bundle.zip"
            self.context.files.write_bytes(name, zip_output(rows, params))
            output_details = {"zip_formats": params.get("zip_formats", ["json", "csv", "xlsx", "txt", "sql"])}
        else:
            name, content = render_output(rows, output_format, params)
            self.context.files.write_bytes(name, content)
            output_details = {"format": output_format}
        self.context.logger.info(f"生成 {len(rows)} 条测试数据，启用 {len(rules)} 个字段规则")
        return Result("success", f"已生成 {len(rows)} 条模拟测试数据", {"count": len(rows), "seed": params.get("seed"), "fields": [item["name"] for item in rules], "unique_fields": sorted(unique_values), "administrative_divisions_version": self.data_metadata["administrative_divisions"]["version"], **output_details}, [name])

    def destroy(self):
        pass
