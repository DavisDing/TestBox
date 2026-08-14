from __future__ import annotations

import csv, io, json, random, zipfile
from datetime import date, timedelta
from xml.sax.saxutils import escape

from testbox.sdk import PluginError, Result

SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨"
GIVEN = "伟芳娜敏静丽强磊军洋勇艳杰娟涛明超秀霞平刚桂英"
PREFIXES = ("138", "139", "150", "151", "186", "188")

def xlsx(rows: list[dict[str, object]]) -> bytes:
    headers = list(rows[0]); values = [headers] + [[str(row[h]) for h in headers] for row in rows]
    cells = []
    for r, row in enumerate(values, 1):
        cells.append("<row r=\"%s\">%s</row>" % (r, "".join(f'<c r="{chr(65+c)}{r}" t="inlineStr"><is><t>{escape(value)}</t></is></c>' for c, value in enumerate(row))))
    sheet = '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + ''.join(cells) + '</sheetData></worksheet>'
    files = {"[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>', "_rels/.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>', "xl/workbook.xml": '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="测试数据" sheetId="1" r:id="rId1"/></sheets></workbook>', "xl/_rels/workbook.xml.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>', "xl/worksheets/sheet1.xml": sheet}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items(): archive.writestr(name, content)
    return output.getvalue()

class Plugin:
    def init(self, context): self.context = context
    def execute(self, command, params):
        if command != "data.mock": raise PluginError("INVALID_PARAMS", "不支持的命令")
        rng = random.Random(params.get("seed")); rows = []
        for index in range(params["count"]):
            birthday = date(1970, 1, 1) + timedelta(days=rng.randrange(18 * 365, 55 * 365))
            rows.append({"record_id": f"TEST-{index + 1:06d}", "name": rng.choice(SURNAMES) + rng.choice(GIVEN) + rng.choice(GIVEN), "gender": rng.choice(["男", "女"]), "birth_date": birthday.isoformat(), "phone": rng.choice(PREFIXES) + "0000" + f"{rng.randrange(1000, 10000):04d}"})
        output_format = params["format"]
        if output_format == "json": name = "mock-data.json"; self.context.files.write_text(name, json.dumps(rows, ensure_ascii=False, indent=2))
        elif output_format == "csv":
            name = "mock-data.csv"; buffer = io.StringIO(); writer = csv.DictWriter(buffer, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows); self.context.files.write_text(name, buffer.getvalue())
        else: name = "mock-data.xlsx"; self.context.files.write_bytes(name, xlsx(rows))
        self.context.logger.info(f"生成 {len(rows)} 条测试数据")
        return Result("success", f"已生成 {len(rows)} 条模拟测试数据", {"count": len(rows), "seed": params.get("seed")}, [name])
    def destroy(self): pass
