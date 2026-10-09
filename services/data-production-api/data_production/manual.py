"""Manual-reproduction guide for Data + Data Viz: the same job by hand in Microsoft Excel.

Every step is derived from the IR the engine executed: the CSV import options
of the request, the column schema and types of the ``DatasetIR``, each
allowlisted transform in order with the rows it changed or dropped (from
:class:`data_production.recipe.DatasetBuildStats`), one ``SUMIFS`` /
``COUNTIFS`` / ``AVERAGEIFS`` formula per chart category, and the chart type,
axis titles, bounds, number format, colours, and size that
:mod:`data_production.render` drew. Category order and the computed values are
copied from the engine, never recalculated by eye; the formulas must reproduce
the same numbers.
"""

from __future__ import annotations

from decimal import Decimal

from data_ir import ChartIR, DatasetIR, DatasetRequest
from production_trace import ManualGuide

from .recipe import DatasetBuildStats, TransformStat
from .render import (
    PIE_COLORS,
    excel_number_format,
    pie_percentages,
    value_axis,
    y_axis_text,
)

_POWER_QUERY_TYPES = {
    "text": "Text",
    "integer": "Whole Number",
    "decimal": "Decimal Number",
    "date": "Date",
    "boolean": "True/False",
}
_DELIMITERS = {",": "Comma", ";": "Semicolon", "\t": "Tab"}
_CHART_MENUS = {
    "bar": "Insert ▸ Charts ▸ Insert Column or Bar Chart ▸ 2-D Column ▸ Clustered Column",
    "horizontal_bar": "Insert ▸ Charts ▸ Insert Column or Bar Chart ▸ 2-D Bar ▸ Clustered Bar",
    "line": "Insert ▸ Charts ▸ Insert Line or Area Chart ▸ 2-D Line ▸ Line with Markers",
    "pie": "Insert ▸ Charts ▸ Insert Pie or Doughnut Chart ▸ 2-D Pie ▸ Pie",
}
_MAX_LISTED_ROWS = 30


def _text(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _column_letter(index: int) -> str:
    letters = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _inches(pixels: int) -> str:
    return f"{pixels / 96:.2f} in ({pixels * 2.54 / 96:.2f} cm)"


def _rows_text(rows: tuple[int, ...]) -> str:
    listed = ", ".join(str(row) for row in rows[:_MAX_LISTED_ROWS])
    return listed + (f" … (+{len(rows) - _MAX_LISTED_ROWS} แถว)" if len(rows) > _MAX_LISTED_ROWS else "")


def _import_steps(guide: ManualGuide, request: DatasetRequest, dataset: DatasetIR, stats: DatasetBuildStats) -> None:
    section = "นำเข้า CSV (Power Query)"
    guide.step("เปิดตัวนำเข้าไฟล์ CSV", tool="Data ▸ Get Data ▸ From File ▸ From Text/CSV", value=request.source_ref, section=section)
    guide.step("ตั้งค่าการเข้ารหัสไฟล์", tool="File Origin", value="65001: Unicode (UTF-8)", section=section)
    guide.step("ตั้งตัวคั่น", tool="Delimiter", value=_DELIMITERS[request.delimiter], section=section)
    guide.step(
        "ปิดการเดาชนิดข้อมูลอัตโนมัติ แล้วกด Transform Data",
        tool="Data Type Detection",
        value="Do not detect data types",
        note="เครื่องมือกำหนดชนิดเองในขั้นถัดไป; การเดาของ Excel อาจตัดเลข 0 นำหน้า",
        section=section,
    )
    guide.step("ใช้แถวแรกเป็นหัวคอลัมน์", tool="Home ▸ Transform ▸ Use First Row as Headers", target="Power Query Editor", section=section)
    if stats.schema_mode == "explicit":
        sources = [column.source_name or column.key for column in dataset.columns]
        guide.step("เลือกเฉพาะคอลัมน์ตาม schema (ตามลำดับนี้)", tool="Home ▸ Manage Columns ▸ Choose Columns", value=" | ".join(sources), section=section)
    for index, column in enumerate(dataset.columns):
        source = column.source_name or column.key
        if source != column.key:
            guide.step("เปลี่ยนชื่อหัวคอลัมน์ให้ตรงกับ cleaned.csv", tool="ดับเบิลคลิกหัวคอลัมน์", target=f"คอลัมน์ {source}", value=column.key, section=section)
        guide.step(
            f"ตั้งชนิดข้อมูลคอลัมน์ที่ {index + 1}",
            tool="Transform ▸ Any Column ▸ Data Type",
            target=f"คอลัมน์ {column.key}",
            value=_POWER_QUERY_TYPES[column.data_type],
            note=_type_note(column.data_type, stats.schema_mode),
            section=section,
        )
    required = [column.key for column in dataset.columns if column.required]
    if required:
        guide.step("ตรวจว่าคอลัมน์บังคับไม่มีค่าว่าง (เครื่องมือปฏิเสธงานถ้ามี)", tool="ปุ่ม ▼ ที่หัวคอลัมน์ ▸ ดูว่าไม่มี (null)", value=", ".join(required), section=section)


def _type_note(data_type: str, schema_mode: str) -> str:
    notes = {
        "date": "รูปแบบในไฟล์ต้องเป็น YYYY-MM-DD; ถ้า Excel อ่านผิดให้ใช้ Data Type ▸ Using Locale… ▸ English (United States)",
        "boolean": "เครื่องมือรับ true/1/yes/y และ false/0/no/n; ถ้าไฟล์ใช้ yes/no ให้ Replace Values เป็น true/false ก่อน",
        "decimal": "ค่าทศนิยมเครื่องมือเก็บแบบ Decimal ตรงตัว; ให้คัดลอกค่าจาก cleaned.csv ถ้าตำแหน่งที่ 15+ ต่างกัน",
        "text": "เลขที่ขึ้นต้นด้วย 0 (เช่น 001) ต้องคงเป็น Text" if schema_mode == "inferred" else "",
    }
    return notes.get(data_type, "")


def _transform_steps(guide: ManualGuide, request: DatasetRequest, dataset: DatasetIR, stats: DatasetBuildStats) -> None:
    section = "ทำความสะอาดข้อมูล (ตามลำดับเดียวกับเครื่องมือ)"
    types = {column.key: column.data_type for column in dataset.columns}
    for stat, transform in zip(stats.transforms, request.transforms):
        target = f"คอลัมน์ {stat.column}"
        effect = f"แถว {stat.rows_before} → {stat.rows_after}; เซลล์ที่เปลี่ยน {stat.cells_changed}"
        if stat.op == "trim_text":
            guide.step("ตัดช่องว่างหน้า-หลังข้อความ", tool="Transform ▸ Text Column ▸ Format ▸ Trim", target=target, note=effect, section=section)
        elif stat.op == "fill_null":
            guide.step(
                "แทนค่าว่างด้วยค่าที่กำหนด",
                tool="Transform ▸ Any Column ▸ Replace Values",
                target=target,
                value=f"Value To Find = {'(เว้นว่าง)' if types.get(stat.column) == 'text' else 'null'}; Replace With = {_text(transform.value)}",
                note=effect + ("; คอลัมน์ Text ที่นำเข้าแบบไม่เดาชนิด เซลล์ว่างเป็นข้อความว่าง ไม่ใช่ null" if types.get(stat.column) == "text" else ""),
                section=section,
            )
        elif stat.op == "filter_equals":
            guide.step(
                "กรองให้เหลือเฉพาะแถวที่มีค่าเท่ากับค่านี้",
                tool="ปุ่ม ▼ ที่หัวคอลัมน์ ▸ Text/Number Filters ▸ Equals…",
                target=target,
                value=_text(transform.value),
                note=_filter_note(stat, transform.value),
                section=section,
            )
        elif stat.op == "sort_by":
            menu = "Home ▸ Sort ▸ Sort Ascending" if transform.direction == "asc" else "Home ▸ Sort ▸ Sort Descending"
            guide.step(
                "เรียงแถว",
                tool=menu,
                target=target,
                value=transform.direction,
                note="เครื่องมือวางแถวที่ค่าว่าง (null) ไว้ท้ายสุดเสมอ และคงลำดับเดิมของแถวที่ค่าเท่ากัน; ถ้า Excel วาง null ไว้บน ให้ย้ายแถวเหล่านั้นลงท้าย",
                section=section,
            )


def _filter_note(stat: TransformStat, value: object) -> str:
    if not stat.dropped_source_rows:
        return f"ไม่มีแถวถูกตัด (แถว {stat.rows_before} → {stat.rows_after})"
    return (
        f"ตัด {len(stat.dropped_source_rows)} แถว (แถว {stat.rows_before} → {stat.rows_after}) เพราะค่าใน {stat.column} ไม่เท่ากับ "
        f"'{_text(value)}' — เลขบรรทัดใน CSV ต้นฉบับ (บรรทัด 2 = ข้อมูลแถวแรก): {_rows_text(stat.dropped_source_rows)}"
    )


def _load_steps(guide: ManualGuide, dataset: DatasetIR) -> None:
    section = "โหลดเข้าชีตและส่งออก cleaned.csv"
    guide.step("โหลดผลลัพธ์เป็นตารางในชีตใหม่", tool="Home ▸ Close & Load ▸ Close & Load To… ▸ Table ▸ New worksheet", section=section)
    guide.step("ตั้งชื่อตารางและชื่อชีต", tool="Table Design ▸ Properties ▸ Table Name", value="cleaned", note="ดับเบิลคลิกแท็บชีตแล้วพิมพ์ cleaned ด้วย", section=section)
    dates = [column.key for column in dataset.columns if column.data_type == "date"]
    if dates:
        guide.step("ตั้งรูปแบบวันที่ให้ตรงกับไฟล์ของเครื่องมือ", tool="Home ▸ Number ▸ More Number Formats… ▸ Custom", target=", ".join(dates), value="yyyy-mm-dd", section=section)
    guide.step(
        "บันทึกสำเนาเป็น CSV UTF-8",
        tool="File ▸ Save a Copy ▸ CSV UTF-8 (Comma delimited) (*.csv)",
        value="cleaned.csv",
        note="Excel อาจเขียน TRUE/FALSE, ตัดเลข 0 ท้ายทศนิยม และใส่ BOM/CRLF; ให้เทียบ 'ค่า' กับ cleaned.csv ของเครื่องมือ ไม่ใช่เทียบไบต์",
        section=section,
    )


def _criteria(category: str) -> str:
    return '"="' if category == "(null)" else "A{row}"


def _chart_data_steps(guide: ManualGuide, chart: ChartIR) -> None:
    spec = chart.spec
    section = "ชีต chart_data (ค่าที่กราฟใช้)"
    x_ref, y_ref = f"cleaned[{spec.x_column}]", f"cleaned[{spec.y_column}]" if spec.y_column else ""
    guide.step("เพิ่มชีตใหม่ชื่อ chart_data", tool="Home ▸ Cells ▸ Insert ▸ Insert Sheet", value="chart_data", section=section)
    guide.step("พิมพ์หัวตาราง", target="A1 | B1 | C1", value=f"{spec.x_axis_label or spec.x_column} | {spec.y_axis_label or 'Value'} | ค่าจากเครื่องมือ", section=section)
    sort_note = {
        "none": "ลำดับ = ลำดับที่หมวดปรากฏครั้งแรกในตาราง cleaned",
        "value_desc": "ลำดับ = ค่ามากไปน้อย (Data ▸ Sort ▸ Largest to Smallest ที่คอลัมน์ B; ค่าที่เท่ากันคงลำดับเดิม)",
        "value_asc": "ลำดับ = ค่าน้อยไปมาก (Data ▸ Sort ▸ Smallest to Largest ที่คอลัมน์ B; ค่าที่เท่ากันคงลำดับเดิม)",
        "category_asc": "ลำดับ = ชื่อหมวด A→Z / ค่าน้อยไปมาก, (null) ท้ายสุด",
        "category_desc": "ลำดับ = ชื่อหมวด Z→A / ค่ามากไปน้อย, (null) ท้ายสุด",
    }[spec.sort]
    if spec.top_n is not None:
        sort_note += f"; เก็บแค่ {spec.top_n} หมวดแรก" + (f" แล้วรวมที่เหลือเป็น '{spec.group_rest_as}'" if spec.group_rest_as else " (หมวดที่เหลือไม่แสดง)")
    guide.step("พิมพ์ชื่อหมวดลงคอลัมน์ A ตามลำดับนี้ (คัดลอกจากเครื่องมือ อย่าเรียงเอง)", target=f"A2:A{len(chart.categories) + 1}", value=" | ".join(chart.categories), note=sort_note, section=section)
    if chart.folded_categories:
        guide.step(
            f"พิมพ์รายชื่อหมวดที่ถูกรวมเป็น '{spec.group_rest_as}' ลงคอลัมน์ E (ช่วยสูตร)",
            target=f"E2:E{len(chart.folded_categories) + 1}",
            value=" | ".join(chart.folded_categories),
            section=section,
        )
    folded_range = f"$E$2:$E${len(chart.folded_categories) + 1}"
    for offset, (category, value) in enumerate(zip(chart.categories, chart.values)):
        row = offset + 2
        if chart.folded_categories and spec.group_rest_as == category and offset == len(chart.categories) - 1:
            formula = {
                "count": f"=SUMPRODUCT(COUNTIFS({x_ref},{folded_range}))",
                "sum": f"=SUMPRODUCT(SUMIFS({y_ref},{x_ref},{folded_range}))",
                "avg": f'=IFERROR(SUMPRODUCT(SUMIFS({y_ref},{x_ref},{folded_range}))/SUMPRODUCT(COUNTIFS({x_ref},{folded_range},{y_ref},"<>")),0)',
            }[spec.aggregation]
        else:
            criteria = _criteria(category).format(row=row)
            formula = {
                "count": f"=COUNTIFS({x_ref},{criteria})",
                "sum": f"=SUMIFS({y_ref},{x_ref},{criteria})",
                "avg": f"=IFERROR(AVERAGEIFS({y_ref},{x_ref},{criteria}),0)",
            }[spec.aggregation]
        guide.step(
            f"ใส่สูตร {spec.aggregation} ของหมวด '{category}'",
            target=f"B{row}",
            value=formula,
            note=f"ต้องได้ {_text(value)} — พิมพ์ค่านี้ลง C{row} เพื่อเทียบ (copy, don't recompute)",
            section=section,
        )
    number_format = excel_number_format(spec, chart.values)
    if number_format != "General":
        guide.step("ตั้งรูปแบบตัวเลขของคอลัมน์ B", tool="Home ▸ Number ▸ More Number Formats… ▸ Custom", target=f"B2:B{len(chart.values) + 1}", value=number_format, section=section)
    total_row = len(chart.categories) + 3
    guide.step("ใส่ผลรวมเพื่อตรวจ", target=f"B{total_row}", value=f"=SUM(B2:B{total_row - 2})", note=f"ต้องได้ {_text(chart.aggregate_total)} (aggregate_total ใน report.json)", section=section)


def _chart_steps(guide: ManualGuide, chart: ChartIR) -> None:
    spec = chart.spec
    section = "สร้างกราฟ"
    last = len(chart.categories) + 1
    guide.step("เลือกช่วงข้อมูลกราฟ", target="ชีต chart_data", value=f"A1:B{last}", section=section)
    guide.step("แทรกกราฟ", tool=_CHART_MENUS[spec.chart_type], section=section)
    guide.step("ตั้งขนาดกราฟ (96 px = 1 in)", tool="Chart Format ▸ Size ▸ Shape Width / Shape Height", value=f"Width {_inches(spec.width)}; Height {_inches(spec.height)}", section=section)
    guide.step("คลิกชื่อกราฟแล้วพิมพ์", tool="Chart Title", value=spec.title, section=section)
    if spec.chart_type == "pie":
        guide.step("ให้ชิ้นแรกเริ่มที่ 12 นาฬิกา เรียงตามเข็มนาฬิกา", tool="Format Data Series ▸ Series Options ▸ Angle of first slice", value="0°", section=section)
        for index, (category, percent) in enumerate(zip(chart.categories, pie_percentages(chart.values))):
            guide.step(
                f"ตั้งสีชิ้น '{category}'",
                tool="คลิกชิ้นนั้น 2 ครั้ง ▸ Format Data Point ▸ Fill ▸ Solid fill ▸ Color ▸ More Colors ▸ Hex",
                value=PIE_COLORS[index % len(PIE_COLORS)].upper(),
                note=f"สัดส่วน {percent}%",
                section=section,
            )
        guide.step("แสดงคำอธิบายด้านขวา", tool="Chart Design ▸ Add Chart Element ▸ Legend ▸ Right", section=section)
        guide.step(
            "แสดงเปอร์เซ็นต์ (ทศนิยม 1 ตำแหน่ง)",
            tool="Chart Design ▸ Add Chart Element ▸ Data Labels ▸ More Data Label Options ▸ Percentage; Number ▸ Percentage ▸ Decimal places",
            value="1",
            note="เครื่องมือเขียนเปอร์เซ็นต์ไว้ในคำอธิบาย (legend) เช่น 'หมวด (40.0%)' ส่วน Excel แสดงบนชิ้น; ตัวเลขต้องตรงกัน",
            section=section,
        )
        if spec.data_labels:
            guide.step("แสดงค่าบนชิ้นด้วย", tool="Format Data Labels ▸ Label Options ▸ Value", section=section)
        return
    category_axis, value_axis_name = ("Primary Vertical", "Primary Horizontal") if spec.chart_type == "horizontal_bar" else ("Primary Horizontal", "Primary Vertical")
    guide.step("ใส่ชื่อแกนหมวด", tool=f"Chart Design ▸ Add Chart Element ▸ Axis Titles ▸ {category_axis}", value=spec.x_axis_label or spec.x_column, section=section)
    guide.step("ใส่ชื่อแกนค่า (รวมหน่วย)", tool=f"Chart Design ▸ Add Chart Element ▸ Axis Titles ▸ {value_axis_name}", value=y_axis_text(chart), section=section)
    guide.step("ปิดคำอธิบาย (เครื่องมือไม่มี legend)", tool="Chart Design ▸ Add Chart Element ▸ Legend ▸ None", section=section)
    gridlines = "Primary Major Vertical" if spec.chart_type == "horizontal_bar" else "Primary Major Horizontal"
    guide.step("เปิดเส้นตารางหลักของแกนค่า", tool=f"Chart Design ▸ Add Chart Element ▸ Gridlines ▸ {gridlines}", section=section)
    low, high = value_axis(chart)
    step = (high - low) / 4
    guide.step(
        "ล็อกช่วงแกนค่าให้มี 5 ขีดเท่ากับเครื่องมือ",
        tool="ดับเบิลคลิกแกนค่า ▸ Format Axis ▸ Axis Options ▸ Bounds / Units",
        value=f"Minimum = {_text(low)}; Maximum = {_text(high)}; Major = {_text(step)}",
        section=section,
    )
    number_format = excel_number_format(spec, [low + step * index for index in range(5)])
    if number_format != "General":
        guide.step("ตั้งรูปแบบตัวเลขของแกนค่า", tool="Format Axis ▸ Number ▸ Category: Custom ▸ Format Code", value=number_format, section=section)
    if spec.chart_type == "horizontal_bar":
        guide.step(
            "ให้หมวดแรกอยู่บนสุดเหมือนเครื่องมือ",
            tool="ดับเบิลคลิกแกนหมวด ▸ Format Axis ▸ Axis Options",
            value="Categories in reverse order ✓; Horizontal axis crosses: At maximum category",
            section=section,
        )
    if spec.chart_type == "line":
        guide.step("ตั้งสีเส้น", tool="Format Data Series ▸ Fill & Line ▸ Line ▸ Solid line ▸ Color ▸ More Colors ▸ Hex", value="2563EB", section=section)
        guide.step("ตั้งความหนาเส้น", tool="Format Data Series ▸ Fill & Line ▸ Line ▸ Width", value="2.25 pt (3 px)", section=section)
        guide.step("ตั้งจุดบนเส้น", tool="Format Data Series ▸ Fill & Line ▸ Marker ▸ Marker Options ▸ Built-in", value="Type: ● circle; Size: 6; Fill 2563EB", section=section)
    else:
        guide.step("ตั้งสีแท่ง", tool="Format Data Series ▸ Fill & Line ▸ Fill ▸ Solid fill ▸ Color ▸ More Colors ▸ Hex", value="2563EB", section=section)
        guide.step("ตั้งความกว้างช่องว่างระหว่างแท่ง (แท่งกว้าง 70% ของช่อง)", tool="Format Data Series ▸ Series Options ▸ Gap Width", value="43%", section=section)
    if spec.data_labels:
        position = {"bar": "Outside End", "horizontal_bar": "Outside End", "line": "Above"}[spec.chart_type]
        guide.step("แสดงค่าบนแต่ละจุด", tool=f"Chart Design ▸ Add Chart Element ▸ Data Labels ▸ {position}", note="รูปแบบตัวเลขใช้ตามคอลัมน์ B (Linked to source)", section=section)


def _export_steps(guide: ManualGuide, has_chart: bool) -> None:
    section = "บันทึกและส่งออก"
    if has_chart:
        guide.step("ส่งออกกราฟเป็น SVG", tool="คลิกขวาที่ขอบกราฟ ▸ Save as Picture… ▸ Save as type: Scalable Vector Graphics (*.svg)", value="chart.svg", section=section)
    guide.step("บันทึกสมุดงานไว้ทวนสอบ", tool="File ▸ Save As ▸ Excel Workbook (*.xlsx)", value="data-viz-manual.xlsx", section=section)
    guide.step(
        "report.json ไม่มีปุ่มเทียบเท่าใน Excel — ใช้ไฟล์ของเครื่องมือ",
        note="report.json เก็บ lineage ทุกเซลล์ (แถว/คอลัมน์ต้นทาง), จำนวน null และผลรวม; ใช้ตรวจว่างานมือได้ตัวเลขเดียวกัน",
        section=section,
    )


def build_excel_guide(request: DatasetRequest, dataset: DatasetIR, chart: ChartIR | None, stats: DatasetBuildStats) -> ManualGuide:
    """Translate the executed request + DatasetIR + ChartIR into numbered Excel steps and a sheet map."""

    guide = ManualGuide(
        program="Microsoft Excel",
        program_version="365 (Power Query)",
        alternatives=["Google Sheets", "LibreOffice Calc"],
        title=dataset.title,
        summary=(
            "ทำตามขั้นตอนนี้ใน Excel จะได้ตาราง `cleaned.csv`"
            + (" และกราฟ `chart.svg`" if chart is not None else "")
            + " แบบเดียวกับที่เครื่องมือสร้าง; ลำดับหมวดและตัวเลขที่เครื่องมือคำนวณให้คัดลอกมาเทียบ ไม่ต้องคิดใหม่"
        ),
        setup=[
            f"เตรียมไฟล์ CSV ต้นฉบับ `{request.source_ref}` (UTF-8, {stats.source_rows} แถวข้อมูล + 1 แถวหัว)",
            "เปิด Microsoft Excel ▸ `File ▸ New ▸ Blank workbook`",
            "ถ้าใช้ Google Sheets: `File ▸ Import ▸ Upload` เลือก Separator type ให้ตรงกับไฟล์ และปิด 'Convert text to numbers, dates, and formulas'",
        ],
        glossary={
            "Power Query": "ตัวแก้ข้อมูลของ Excel (Data ▸ Get Data); ใน Google Sheets ทำด้วยเมนู Data ▸ Data cleanup / Filter views แทน",
            "SUMIFS / COUNTIFS / AVERAGEIFS": "สูตรรวมตามเงื่อนไข มีชื่อเดียวกันใน Google Sheets",
            "Chart editor": "Google Sheets: Insert ▸ Chart ▸ Setup (Chart type) / Customize (Chart & axis titles, Vertical axis Min/Max)",
        },
    )
    guide.layer("raw (Power Query)", "Query", content=f"{stats.source_rows} แถวจาก {request.source_ref}, ตัวคั่น {_DELIMITERS[request.delimiter]}", position="Queries & Connections")
    guide.layer(
        "cleaned",
        "Worksheet + Table",
        content="; ".join(f"{_column_letter(index)}={column.key} ({_POWER_QUERY_TYPES[column.data_type]})" for index, column in enumerate(dataset.columns)),
        position=f"A1:{_column_letter(len(dataset.columns) - 1)}{len(dataset.rows) + 1}",
    )
    for stat in stats.transforms:
        guide.layer(f"ขั้น {stat.index + 1}: {stat.op}", "Power Query Applied Step", content=f"{stat.column}: แถว {stat.rows_before}→{stat.rows_after}, เปลี่ยน {stat.cells_changed} เซลล์", position="Applied Steps")
    if chart is not None:
        guide.layer("chart_data", "Worksheet", content=f"{len(chart.categories)} หมวด, สูตร {chart.spec.aggregation}", position=f"A1:C{len(chart.categories) + 1}")
        series = "ชิ้นพาย" if chart.spec.chart_type == "pie" else "series"
        guide.layer(
            "Chart",
            _CHART_MENUS[chart.spec.chart_type].rsplit("▸", 1)[-1].strip(),
            content=f"1 {series}: {chart.spec.y_axis_label} ← chart_data!B2:B{len(chart.categories) + 1}; หมวด ← A2:A{len(chart.categories) + 1}",
            position=f"{chart.spec.width}×{chart.spec.height} px",
        )

    _import_steps(guide, request, dataset, stats)
    _transform_steps(guide, request, dataset, stats)
    _load_steps(guide, dataset)
    if chart is not None:
        _chart_data_steps(guide, chart)
        _chart_steps(guide, chart)
    _export_steps(guide, chart is not None)

    guide.checks = [
        f"ตาราง cleaned มี {len(dataset.rows)} แถว และคอลัมน์ {', '.join(column.key for column in dataset.columns)} ตามลำดับ",
        "ค่าทุกเซลล์ตรงกับ cleaned.csv ของเครื่องมือ (เปิดคู่กันแล้วเทียบ)",
    ]
    if chart is not None:
        guide.checks += [
            f"chart_data มี {len(chart.categories)} หมวดเรียงตามนี้: {' | '.join(chart.categories)}",
            "ทุกแถว B = C (ค่าจากสูตร = ค่าจากเครื่องมือ): " + ", ".join(f"{category}={_text(value)}" for category, value in zip(chart.categories, chart.values)),
            f"ผลรวม = {_text(chart.aggregate_total)} (report.json ▸ chart.aggregate_total)",
        ]
        if chart.spec.chart_type == "pie":
            guide.checks.append("เปอร์เซ็นต์ตรงกับ report.json ▸ chart.percentages: " + ", ".join(f"{p}%" for p in pie_percentages(chart.values)))
        else:
            low, high = value_axis(chart)
            guide.checks.append(f"แกนค่า {_text(low)} ถึง {_text(high)} มี 5 ขีด; ชื่อแกน '{chart.spec.x_axis_label}' และ '{y_axis_text(chart)}'")
        if chart.folded_categories:
            guide.checks.append(f"หมวด '{chart.spec.group_rest_as}' รวม {len(chart.folded_categories)} หมวด: {', '.join(chart.folded_categories)}")
    guide.estimated_minutes = round(8 + 1.5 * len(stats.transforms) + (10 + 0.5 * len(chart.categories) if chart is not None else 0) + 0.2 * len(guide.steps), 1)
    return guide


__all__ = ["build_excel_guide"]
