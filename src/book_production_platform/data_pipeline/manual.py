"""Manual-reproduction guide: rebuild the same pipeline by hand.

Primary program is **Excel Power Query**: every PipelineIR node becomes one
Applied Step (renamed to its ``node_id``) in the same order the compiler
emits CTEs, with the exact column, operator, value, sort keys (including the
deterministic tie-breakers the compiler adds before a ``limit``), group-by and
metrics, and the equivalent M formula. Branches become referenced queries and
``union_all`` becomes Append Queries. A second section gives the compiled
read-only ``SELECT`` for a SQL tool user (DB Browser for SQLite / DBeaver).

The guide carries filter values (a person must type them), so it has the same
sensitivity as ``parameters.json`` and stays in the private output root.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from book_production_platform.data_pipeline_ir.models import (
    CellValue,
    ColumnSpec,
    ColumnType,
    PipelineIR,
    PipelineNode,
)
from book_production_platform.production_trace import ManualGuide

from .pipeline import (
    COMPARE_SQL,
    CompiledPipeline,
    _quote_identifier,
    _sort_terms,
    _sqlite_type,
)

PROGRAM = "Microsoft Excel Power Query"
PQ_TYPES: dict[str, str] = {"text": "Text", "integer": "Whole Number", "decimal": "Decimal Number", "date": "Date", "boolean": "True/False"}
M_TYPES: dict[str, str] = {"text": "type text", "integer": "Int64.Type", "decimal": "type number", "date": "type date", "boolean": "type logical"}
_FILTER_MENU: dict[str, str] = {"text": "Text Filters", "integer": "Number Filters", "decimal": "Number Filters", "date": "Date Filters", "boolean": ""}
_COMPARE_MENU: dict[str, dict[str, str]] = {
    "number": {"gt": "Greater Than…", "gte": "Greater Than Or Equal To…", "lt": "Less Than…", "lte": "Less Than Or Equal To…", "ne": "Does Not Equal…"},
    "date": {"gt": "After…", "gte": "Custom Filter… ▸ is after or equal to", "lt": "Before…", "lte": "Custom Filter… ▸ is before or equal to", "ne": "Custom Filter… ▸ does not equal"},
    "text": {"ne": "Does Not Equal…"},
}
_M_COMPARE: dict[str, str] = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "ne": "<>"}
_SECTION_SQL = "ทางเลือก: SQL (DB Browser for SQLite / DBeaver)"
_TYPE_NAMES: tuple[ColumnType, ...] = ("text", "integer", "decimal", "date", "boolean")
SOURCE_READY_STEP = "source_ready"


def _number_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def display_literal(value: CellValue) -> str:
    """The value as a person types it into a Power Query dialog."""

    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, Decimal):
        return _number_text(value)
    if isinstance(value, date):
        return value.isoformat()
    return "" if value is None else str(value)


def m_literal(value: CellValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return _number_text(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, date):
        return f"#date({value.year}, {value.month}, {value.day})"
    if value is None:
        return "null"
    return '"' + str(value).replace('"', '""') + '"'


def sql_literal(value: CellValue) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, Decimal):
        return _number_text(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, date):
        return f"'{value.isoformat()}'"
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def inline_sql(compiled: CompiledPipeline) -> str:
    """Compiled SQL with each ``?`` replaced by its literal (for tools without parameter binding).

    Identifiers are word characters only, so every ``?`` is a placeholder. The
    engine itself never interpolates values; this text is for the manual only.
    """

    parts = compiled.sql.split("?")
    if len(parts) - 1 != len(compiled.parameters):
        raise ValueError("pipeline_parameter_count_mismatch")
    output = parts[0]
    for value, part in zip(compiled.parameters, parts[1:], strict=True):
        output += sql_literal(value) + part
    return output


def _ref(name: str) -> str:
    return f'#"{name}"'


def _m_list(items: list[str]) -> str:
    return "{" + ", ".join('"' + item.replace('"', '""') + '"' for item in items) + "}"


def _column(columns: list[ColumnSpec], key: str | None) -> ColumnSpec:
    return next(column for column in columns if column.key == key)


def _parent_columns(pipeline: PipelineIR, parent_id: str) -> list[ColumnSpec]:
    return pipeline.source.columns if parent_id == "source" else pipeline.schemas_by_node[parent_id]


def _metric_m(metric: Any) -> str:
    if metric.op == "count":
        expression = "Table.RowCount(_)" if metric.column is None else f"List.NonNullCount([{metric.column}])"
        return f'{{"{metric.alias}", each {expression}, Int64.Type}}'
    function = "List.Sum" if metric.op == "sum" else "List.Average"
    return f'{{"{metric.alias}", each {function}([{metric.column}]), type nullable number}}'


def _metric_ui(metric: Any) -> str:
    if metric.op == "count":
        return f"New column name = {metric.alias} · Operation = Count Rows" + (
            "" if metric.column is None else f" (แล้วแก้สูตรเป็น List.NonNullCount([{metric.column}]) = นับเฉพาะค่าที่ไม่ว่าง เหมือน COUNT(col))"
        )
    operation = "Sum" if metric.op == "sum" else "Average"
    return f"New column name = {metric.alias} · Operation = {operation} · Column = {metric.column}"


def _node_step(guide: ManualGuide, pipeline: PipelineIR, node: PipelineNode, query: str, previous: str, queries: Mapping[str, str], section: str) -> None:
    parent_columns = _parent_columns(pipeline, node.parent_ids[0])
    note_prefix = f"Applied Step ชื่อ `{node.node_id}` (คลิกขวาที่ขั้นตอน ▸ Rename) · สูตร M: "
    target = f"query `{query}`"
    if node.op == "select":
        columns = list(node.columns or [])
        guide.step("เลือกเฉพาะคอลัมน์ แล้วเรียงตามลำดับนี้ (ลากหัวคอลัมน์ หรือแก้สูตรในแถบสูตร)", tool="Home ▸ Manage Columns ▸ Choose Columns",
                   target=target, value=", ".join(columns), note=note_prefix + f"= Table.SelectColumns({_ref(previous)}, {_m_list(columns)})", section=section)
    elif node.op == "filter_equals":
        column = _column(parent_columns, node.column)
        menu = _FILTER_MENU[column.data_type]
        tool = f"▾ ที่หัวคอลัมน์ {column.key} ▸ " + (f"{menu} ▸ Equals…" if menu else "ติ๊กเฉพาะค่า")
        guide.step("กรองแถวที่ค่าเท่ากับ (ตรงตัวพิมพ์เล็ก-ใหญ่)", tool=tool, target=f"{target} · คอลัมน์ `{column.key}`", value=display_literal(node.value),
                   note=note_prefix + f"= Table.SelectRows({_ref(previous)}, each [{column.key}] = {m_literal(node.value)})", section=section)
    elif node.op == "filter_compare":
        column = _column(parent_columns, node.column)
        operator = str(node.operator)
        family = "number" if column.data_type in {"integer", "decimal"} else ("date" if column.data_type == "date" else "text")
        menu = _COMPARE_MENU[family].get(operator, "Does Not Equal…")
        tool = f"▾ ที่หัวคอลัมน์ {column.key} ▸ " + (f"{_FILTER_MENU[column.data_type]} ▸ {menu}" if _FILTER_MENU[column.data_type] else "ติ๊กออกค่าที่ไม่เอา")
        guide.step(
            f"กรองแถวที่ `{column.key}` {COMPARE_SQL[operator]} ค่า (แถวที่ว่าง/null ถูกตัดออกเหมือน SQL — แก้สูตรให้มี `<> null and`)",
            tool=tool, target=f"{target} · คอลัมน์ `{column.key}`", value=display_literal(node.value),
            note=note_prefix + f"= Table.SelectRows({_ref(previous)}, each [{column.key}] <> null and [{column.key}] {_M_COMPARE[operator]} {m_literal(node.value)})",
            section=section,
        )
    elif node.op == "filter_null":
        guide.step("กรองให้เหลือเฉพาะแถวที่ค่าว่าง (null)", tool=f"▾ ที่หัวคอลัมน์ {node.column} ▸ ติ๊กออกทั้งหมด แล้วติ๊กเฉพาะ (null)",
                   target=f"{target} · คอลัมน์ `{node.column}`", value="(null)",
                   note=note_prefix + f"= Table.SelectRows({_ref(previous)}, each [{node.column}] = null)", section=section)
    elif node.op == "distinct":
        guide.step("เลือกทุกคอลัมน์ (คลิกหัวคอลัมน์แรก แล้ว Shift+คลิกหัวคอลัมน์สุดท้าย) แล้วลบแถวซ้ำ", tool="Home ▸ Reduce Rows ▸ Remove Rows ▸ Remove Duplicates",
                   target=target, value=", ".join(column.key for column in parent_columns),
                   note=note_prefix + f"= Table.Distinct({_ref(previous)})", section=section)
    elif node.op == "sort_by":
        children = [item for item in pipeline.ordered_nodes if node.node_id in item.parent_ids]
        feeds_limit = any(child.op == "limit" for child in children)
        terms = _sort_terms(node, pipeline.schemas_by_node[node.node_id]) if feeds_limit else [f'"{node.column}" {str(node.direction).upper()}']
        keys = [(term.rsplit(" ", 1)[0].strip('"'), term.rsplit(" ", 1)[1]) for term in terms]
        order = "; ".join(f"{index}. {key} {'Sort Descending' if direction == 'DESC' else 'Sort Ascending'}" for index, (key, direction) in enumerate(keys, start=1))
        m_terms = ", ".join(f'{{"{key}", Order.{"Descending" if direction == "DESC" else "Ascending"}}}' for key, direction in keys)
        guide.step(
            "เรียงแถว: คลิก ▾ ที่หัวคอลัมน์ตามลำดับนี้ (คอลัมน์ถัดไปจะเป็นตัวตัดสินเมื่อค่าเท่ากัน)" if feeds_limit else "เรียงแถวตามคอลัมน์นี้",
            tool="▾ ที่หัวคอลัมน์ ▸ Sort Ascending / Sort Descending", target=target, value=order,
            note=note_prefix + f"= Table.Sort({_ref(previous)}, {{{m_terms}}})", section=section,
        )
    elif node.op == "limit":
        guide.step("เก็บเฉพาะแถวบนสุดตามจำนวนนี้", tool="Home ▸ Reduce Rows ▸ Keep Rows ▸ Keep Top Rows ▸ Number of rows", target=target, value=str(node.limit),
                   note=note_prefix + f"= Table.FirstN({_ref(previous)}, {node.limit})", section=section)
    elif node.op == "aggregate":
        groups = list(node.group_by or [])
        metrics = list(node.metrics or [])
        if groups:
            guide.step("จัดกลุ่มและสรุป: เลือก Advanced แล้วเพิ่มคอลัมน์กลุ่มและ aggregation ตามนี้ (Add grouping / Add aggregation)",
                       tool="Transform ▸ Table ▸ Group By ▸ Advanced", target=target,
                       value="Group by = " + ", ".join(groups) + " · " + " · ".join(_metric_ui(metric) for metric in metrics),
                       note=note_prefix + f"= Table.Group({_ref(previous)}, {_m_list(groups)}, {{{', '.join(_metric_m(metric) for metric in metrics)}}})",
                       section=section)
        else:
            cells = []
            for metric in metrics:
                if metric.op == "count":
                    cells.append(f"Table.RowCount({_ref(previous)})" if metric.column is None else f"List.NonNullCount({_ref(previous)}[{metric.column}])")
                else:
                    cells.append(f"{'List.Sum' if metric.op == 'sum' else 'List.Average'}({_ref(previous)}[{metric.column}])")
            guide.step("สรุปทั้งตาราง (ไม่มีคอลัมน์กลุ่ม — Group By ใน UI ต้องมีอย่างน้อย 1 คอลัมน์ จึงคลิก fx แล้ววางสูตร M)",
                       tool="Formula Bar ▸ fx (Add Step)", target=target, value=" · ".join(_metric_ui(metric) for metric in metrics),
                       note=note_prefix + f"= #table({_m_list([metric.alias for metric in metrics])}, {{{{{', '.join(cells)}}}}})", section=section)
    else:  # union_all
        right = queries[node.parent_ids[1]]
        guide.step("ต่อแถวของอีกตารางท้ายตาราง (คอลัมน์ต้องตรงชื่อและลำดับ)", tool="Home ▸ Combine ▸ Append Queries ▸ Two tables ▸ Table to append",
                   target=target, value=right, note=note_prefix + f"= Table.Combine({{{_ref(previous)}, {_ref(right)}}})", section=section)


def _source_steps(guide: ManualGuide, pipeline: PipelineIR, section: str) -> None:
    source = pipeline.source
    guide.step(
        f"นำเข้าตารางต้นทาง `{source.table_name}` (อ้างอิง `{source.source_ref}`) แล้วกด Transform Data เพื่อเปิด Power Query Editor",
        tool="Data ▸ Get & Transform Data ▸ Get Data ▸ From File ▸ From Text/CSV (หรือ From Database ตามแหล่งจริง)",
        target="Excel ▸ แท็บ Data", value=source.table_name, section=section,
    )
    guide.step("ตั้งชื่อ query", tool="Query Settings ▸ Properties ▸ Name", target="Power Query Editor", value=source.table_name, section=section)
    for column in source.columns:
        if column.source_name and column.source_name != column.key:
            guide.step("เปลี่ยนชื่อคอลัมน์ (ดับเบิลคลิกหัวคอลัมน์)", tool="Transform ▸ Any Column ▸ Rename", target=f"คอลัมน์ `{column.source_name}`",
                       value=column.key, section=section)
    keys = [column.key for column in source.columns]
    guide.step("เก็บเฉพาะคอลัมน์ที่ไปป์ไลน์ประกาศไว้ เรียงตามนี้", tool="Home ▸ Manage Columns ▸ Choose Columns", target=f"query `{source.table_name}`",
               value=", ".join(keys), note=f"สูตร M: = Table.SelectColumns(<ขั้นตอนก่อนหน้า>, {_m_list(keys)})", section=section)
    for column in source.columns:
        guide.step("ตั้งชนิดข้อมูลคอลัมน์ (คลิกไอคอนชนิดที่หัวคอลัมน์)", tool="Transform ▸ Any Column ▸ Data Type", target=f"คอลัมน์ `{column.key}`",
                   value=PQ_TYPES[column.data_type], section=section)
    guide.step("เปลี่ยนชื่อขั้นตอนสุดท้ายของตารางต้นทาง (ใช้อ้างอิงในสูตรถัดไป)", tool="Applied Steps ▸ คลิกขวา ▸ Rename", target=f"query `{source.table_name}`",
               value=SOURCE_READY_STEP, section=section)


def _sql_steps(guide: ManualGuide, pipeline: PipelineIR, compiled: CompiledPipeline) -> None:
    source = pipeline.source
    ddl = (
        f"CREATE TABLE {_quote_identifier(source.table_name)} ("
        + ", ".join(f"{_quote_identifier(column.key)} {_sqlite_type(column.data_type)}" for column in source.columns)
        + ")"
    )
    guide.step("สร้างฐานข้อมูลใหม่ (ไฟล์ทดลอง)", tool="DB Browser for SQLite ▸ File ▸ New Database…", value=f"{pipeline.pipeline_id}.db", section=_SECTION_SQL)
    guide.step("สร้างตารางต้นทางด้วยคำสั่งนี้ แล้วกด Execute all (Ctrl+Return)", tool="Execute SQL", value=ddl, section=_SECTION_SQL)
    guide.step("นำเข้าข้อมูลต้นทางเข้าตารางนี้", tool="File ▸ Import ▸ Table from CSV file…", target=source.table_name, value=source.table_name, section=_SECTION_SQL)
    guide.step(
        "วางคำสั่ง SELECT แบบอ่านอย่างเดียว (ค่าตัวกรองใส่ไว้แล้ว) แล้วกด Execute all",
        tool="Execute SQL", value=inline_sql(compiled),
        note="SQL เดียวกับ pipeline.sql เพียงแทน ? ด้วยค่า; ไม่มีคำสั่งเขียน/แก้ไขข้อมูล", section=_SECTION_SQL,
    )
    if compiled.parameters:
        bound = "; ".join(
            f"{index}. {data_type} = {display_literal(value)}"
            for index, (value, data_type) in enumerate(zip(compiled.parameters, compiled.parameter_types, strict=True), start=1)
        )
        guide.step("หรือใน DBeaver: วาง SQL ต้นฉบับ (มี ?) ▸ Execute (Ctrl+Enter) ▸ ใส่ค่าในหน้าต่าง Bind parameters ตามลำดับ",
                   tool="DBeaver ▸ SQL Editor ▸ Execute SQL Statement", value=bound, note=f"SQL ต้นฉบับ: {compiled.sql}", section=_SECTION_SQL)
    guide.step("ส่งออกผลลัพธ์เป็น CSV", tool="ผลลัพธ์ ▸ Save the results view ▸ Export to CSV", value="results.csv", section=_SECTION_SQL)


def build_power_query_guide(
    pipeline: PipelineIR,
    compiled: CompiledPipeline,
    *,
    execution: Mapping[str, Any] | None = None,
) -> ManualGuide:
    """Translate a validated PipelineIR into Power Query Applied Steps plus the equivalent SQL.

    ``execution`` (from :mod:`.execute`) adds checks against the engine's
    result: row count, numeric column totals, and the first row.
    """

    guide = ManualGuide(
        program=PROGRAM,
        program_version="365",
        alternatives=["DB Browser for SQLite", "DBeaver"],
        title=pipeline.pipeline_id,
        summary=(
            "ทำตามขั้นตอนนี้ใน Power Query (Excel) จะได้ผลลัพธ์เดียวกับ SELECT ที่เครื่องมือคอมไพล์ "
            "(`pipeline.sql` + `parameters.json`): แต่ละ node คือ Applied Step หนึ่งขั้นตามลำดับเดียวกัน; "
            "ส่วนท้ายมี SQL เดียวกันสำหรับผู้ใช้เครื่องมือ SQL"
        ),
        setup=[
            "เปิด Microsoft Excel ▸ `File ▸ New ▸ Blank workbook`",
            "เปิดแถบสูตรใน Power Query Editor: `View ▸ Layout ▸ Formula Bar` เพื่อตรวจ/แก้สูตร M ให้ตรงตามคู่มือ",
            "Power Query และ SQLite เปรียบเทียบข้อความแบบตรงตัวพิมพ์เล็ก-ใหญ่ (case-sensitive)",
            "ไปป์ไลน์นี้อ่านอย่างเดียว: ไม่มีขั้นตอนใดเขียนกลับไปที่แหล่งข้อมูล",
        ],
    )
    children = Counter(parent for node in pipeline.ordered_nodes for parent in node.parent_ids)
    queries: dict[str, str] = {"source": pipeline.source.table_name}
    tails: dict[str, str] = {pipeline.source.table_name: SOURCE_READY_STEP}
    query_order = [pipeline.source.table_name]

    source_section = f"Query: {pipeline.source.table_name} (ตารางต้นทาง)"
    guide.layer(pipeline.source.table_name, "Source query", content=f"{len(pipeline.source.columns)} คอลัมน์: " + ", ".join(
        f"{column.key} ({PQ_TYPES[column.data_type]})" for column in pipeline.source.columns), position=pipeline.source.source_ref)
    _source_steps(guide, pipeline, source_section)

    for index, node in enumerate(pipeline.ordered_nodes, start=1):
        first_parent = node.parent_ids[0]
        if children[first_parent] == 1:
            query = queries[first_parent]
        else:
            query = node.node_id
            query_order.append(query)
            guide.step("สร้าง query ใหม่ที่อ้างอิงผลของ query ก่อนหน้า แล้วตั้งชื่อ", tool="Queries pane ▸ คลิกขวา ▸ Reference ▸ Properties ▸ Name",
                       target=f"query `{queries[first_parent]}`", value=query, section=f"Query: {query}")
            tails[query] = "Source"
        previous = tails[query]
        _node_step(guide, pipeline, node, query, previous, queries, f"Query: {query}")
        queries[node.node_id] = query
        tails[query] = node.node_id
        schema = pipeline.schemas_by_node[node.node_id]
        guide.layer(node.node_id, f"Applied Step · {node.op}", content=", ".join(column.key for column in schema), position=f"ขั้นที่ {index} ใน query `{query}`")

    output_query = queries[pipeline.output_node]
    guide.step("ปิด Power Query โดยให้ทุก query เป็น connection ก่อน", tool="Home ▸ Close & Load ▸ Close & Load To… ▸ Only Create Connection", section="โหลดผลลัพธ์")
    guide.step("โหลดเฉพาะ query ผลลัพธ์ลงชีตใหม่", tool="Data ▸ Queries & Connections ▸ คลิกขวา ▸ Load To… ▸ Table ▸ New worksheet",
               target=f"query `{output_query}`", value=output_query, section="โหลดผลลัพธ์")
    guide.step("บันทึกไฟล์", tool="File ▸ Save As ▸ Excel Workbook (*.xlsx)", value=f"{pipeline.pipeline_id}.xlsx", section="โหลดผลลัพธ์")
    _sql_steps(guide, pipeline, compiled)

    sql_sha = hashlib.sha256(compiled.sql.encode("utf-8")).hexdigest()
    guide.checks = [
        "ตารางผลลัพธ์มีคอลัมน์ตามลำดับ: " + ", ".join(f"{column.key} ({PQ_TYPES[column.data_type]})" for column in pipeline.output_columns),
        f"Applied Steps ตามลำดับ (query `{output_query}` และ query ที่อ้างอิง): " + " → ".join(node.node_id for node in pipeline.ordered_nodes),
        f"query ทั้งหมด {len(query_order)} ตัว: " + ", ".join(query_order) + f"; โหลดลงชีตเฉพาะ `{output_query}`",
        f"SQL ต้นฉบับตรงกับ `pipeline.sql` ของเครื่องมือ (sha256 `{sql_sha}`), พารามิเตอร์ {len(compiled.parameters)} ตัวตามลำดับใน `parameters.json`",
    ]
    if execution:
        guide.checks.append(f"ผลลัพธ์มี {execution['row_count']} แถว (เท่ากับ `results.csv` ของเครื่องมือ)")
        for key, total in dict(execution.get("numeric_totals", {})).items():
            guide.checks.append(f"ผลรวมคอลัมน์ `{key}` = {total} (เลือกคอลัมน์แล้วดูแถบสถานะ Sum ใน Excel)")
        first = execution.get("first_row")
        if first:
            guide.checks.append("แถวแรก: " + "; ".join(f"{key}={value}" for key, value in dict(first).items()))
        guide.checks.append(f"`results.csv` ของเครื่องมือ sha256 `{execution['csv_sha256']}`; ค่าทั้งหมด (ไม่ขึ้นกับรูปแบบ CSV) sha256 `{execution['values_sha256']}`")
    guide.glossary = {
        "Applied Step": "ขั้นตอนหนึ่งในแผง Query Settings ▸ Applied Steps ของ Power Query; ชื่อขั้นตอนในคู่มือ = node_id ของไปป์ไลน์",
        "Reference": "query ใหม่ที่เริ่มจากผลลัพธ์ของ query อื่น (ใช้เมื่อ node หนึ่งมีหลายทางแยก)",
        "M": "ภาษาสูตรของ Power Query; ตรวจ/แก้ได้ที่แถบสูตรหรือ Home ▸ Advanced Editor",
        "SQL types": ", ".join(f"{name} → {_sqlite_type(name)}" for name in _TYPE_NAMES),
    }
    guide.estimated_minutes = round(5 + 1.5 * len(pipeline.ordered_nodes) + 0.3 * len(pipeline.source.columns), 1)
    return guide


__all__ = ["M_TYPES", "PQ_TYPES", "PROGRAM", "build_power_query_guide", "display_literal", "inline_sql", "m_literal", "sql_literal"]
