import json
import csv
import io
import re
from itertools import count
from typing import Any, List
from ..models import JiraExportInput, JiraExportResponse, TestCase


_EXCEL_CELL_UNITS = 32_767
_EXCEL_CELL_LINE_FEEDS = 253
# Calc inserts a line break into uninterrupted runs near 16K UTF-16 units on re-save.
_EXCEL_CONTINUATION_UNITS = 16_000
_EXCEL_ESCAPE_MARKER = re.compile(r"_(?=x[0-9a-f]{4}_)", re.IGNORECASE)
_XML_UNSAFE_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def export_to_jira(payload: JiraExportInput) -> JiraExportResponse:
    """Export test cases to JIRA (stub - requires JIRA credentials)."""
    return JiraExportResponse(
        status="stubbed",
        message=(
            f"JIRA export adapter not configured. Would export {len(payload.test_cases)} "
            f"test cases to project '{payload.project_key}' as '{payload.issue_type}' issues. "
            "Provide JIRA credentials and API configuration to enable this feature."
        ),
    )


def _spreadsheet_text(value: Any) -> Any:
    # CSV quoting does not prevent spreadsheet formula evaluation.
    if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + value
    if isinstance(value, str) and value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def _audit_cells(metadata: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "Audit " + key: json.dumps(value, ensure_ascii=False)
        if isinstance(value, (dict, list))
        else "null"
        if value is None
        else str(value).lower()
        if isinstance(value, bool)
        else value
        for key, value in (metadata or {}).items()
    }


def _excel_text_parts(value: str):
    """Split within Excel's limits while keeping each Unicode character intact."""
    start = units = line_feeds = 0
    for index, char in enumerate(value):
        width = 2 if ord(char) > 0xFFFF else 1
        newline = char == "\n"
        if units + width > _EXCEL_CONTINUATION_UNITS or line_feeds + newline > _EXCEL_CELL_LINE_FEEDS:
            yield value[start:index]
            start = index
            units = line_feeds = 0
        units += width
        line_feeds += newline
    yield value[start:]


def export_to_csv(test_cases: List[TestCase], *, audit_metadata: dict[str, Any] | None = None) -> str:
    """Export test cases to CSV format."""
    output = io.StringIO()

    # Define headers based on industry standard fields
    headers = [
        "ID",
        "Title",
        "Description",
        "Priority",
        "Type",
        "Status",
        "Preconditions",
        "Steps",
        "Expected Result",
        "Test Data",
        "Estimated Time",
        "Automation Status",
        "Component",
        "Linked Requirements",
        "Scenario Refs",
        "Source Refs",
        "Tags",
    ]

    audit_cells = _audit_cells(audit_metadata)
    headers.extend(audit_cells)
    writer = csv.writer(output, quoting=csv.QUOTE_ALL)
    writer.writerow(headers)

    for tc in test_cases:
        # Format steps as numbered list
        steps_text = "\n".join([f"{s.step}. {s.action} -> Expected: {s.expected}" + (f" [Data: {s.test_data}]" if s.test_data else "") for s in tc.steps])

        row = [
            tc.id,
            tc.title,
            tc.description or "",
            tc.priority,
            tc.type,
            tc.status,
            tc.preconditions or "",
            steps_text,
            tc.expected_result or "",
            tc.test_data or "",
            tc.estimated_time or "",
            tc.automation_status,
            tc.component or "",
            ", ".join(tc.linked_requirement_ids or []),
            ", ".join(tc.scenario_refs or []),
            ", ".join(tc.source_refs or []),
            ", ".join(tc.tags) if tc.tags else "",
        ]
        writer.writerow([_spreadsheet_text(value) for value in [*row, *audit_cells.values()]])

    return output.getvalue()


def export_to_excel(test_cases: List[TestCase], *, audit_metadata: dict[str, Any] | None = None) -> bytes:
    """Export test cases to Excel format (XLSX)."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
    from openpyxl.worksheet.hyperlink import Hyperlink

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Test Cases"
    detail_sheets = []
    if audit_metadata is not None:
        audit, sources, coverage = (wb.create_sheet(name) for name in ("Audit", "Sources", "Coverage"))
        detail_sheets.extend((audit, sources, coverage))

    long_values = None
    detail_row = 2

    def write_cell(cell, value):
        if not isinstance(value, str):
            cell.value = value
            return

        escaped = bool(_XML_UNSAFE_TEXT.search(value) or _EXCEL_ESCAPE_MARKER.search(value))
        safe_text = _spreadsheet_text(value)
        if not escaped and len(safe_text.encode("utf-16-le")) <= _EXCEL_CELL_UNITS * 2 and safe_text.count("\n") <= _EXCEL_CELL_LINE_FEEDS:
            cell.value = safe_text
            cell.data_type = "s"
            return

        nonlocal long_values, detail_row
        if long_values is None:
            long_values = wb.create_sheet("Long Values")
            long_values.append(["Source sheet", "Cell", "Part", "Encoding", "Text"])
            detail_sheets.append(long_values)

        encoding = "JSON string" if escaped else "Text"
        text = _EXCEL_ESCAPE_MARKER.sub(lambda _: r"\u005f", json.dumps(value, ensure_ascii=True)) if escaped else value
        parts = list(_excel_text_parts(text))
        label = f"See Long Values ({len(parts)} {'part' if len(parts) == 1 else 'parts'})"
        cell.value = label
        cell.style = "Hyperlink"
        cell.hyperlink = Hyperlink(ref=cell.coordinate, location=f"'Long Values'!A{detail_row}", display=label)
        for part_number, part in enumerate(parts, start=1):
            long_values.append([cell.parent.title, cell.coordinate, part_number, encoding, part])
            long_values.cell(row=detail_row, column=5).data_type = "s"
            long_values.row_dimensions[detail_row].height = 48
            detail_row += 1

    # Define headers
    headers = [
        "ID",
        "Title",
        "Description",
        "Priority",
        "Type",
        "Status",
        "Preconditions",
        "Steps",
        "Expected Result",
        "Test Data",
        "Estimated Time",
        "Automation Status",
        "Component",
        "Linked Requirements",
        "Scenario Refs",
        "Source Refs",
        "Tags",
    ]

    # Styling
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    thin_border = Border(left=Side(style="thin"), right=Side(style="thin"), top=Side(style="thin"), bottom=Side(style="thin"))

    # Priority colors
    priority_colors = {"Critical": "FF6B6B", "High": "FFA500", "Medium": "FFD93D", "Low": "6BCB77"}

    # Write headers
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Write data
    for row_idx, tc in enumerate(test_cases, 2):
        # Format steps
        steps_text = "\n".join([f"{s.step}. {s.action}\n   → {s.expected}" + (f"\n   [Data: {s.test_data}]" if s.test_data else "") for s in tc.steps])

        data = [
            tc.id,
            tc.title,
            tc.description or "",
            tc.priority,
            tc.type,
            tc.status,
            tc.preconditions or "",
            steps_text,
            tc.expected_result or "",
            tc.test_data or "",
            tc.estimated_time or "",
            tc.automation_status,
            tc.component or "",
            ", ".join(tc.linked_requirement_ids or []),
            ", ".join(tc.scenario_refs or []),
            ", ".join(tc.source_refs or []),
            ", ".join(tc.tags) if tc.tags else "",
        ]

        for col, value in enumerate(data, 1):
            cell = ws.cell(row=row_idx, column=col)
            write_cell(cell, value)
            cell.border = thin_border
            cell.alignment = Alignment(wrap_text=True, vertical="top")

            # Color-code priorities
            if col == 4 and value in priority_colors:
                cell.fill = PatternFill(start_color=priority_colors[value], end_color=priority_colors[value], fill_type="solid")

    # Adjust column widths
    column_widths = [12, 40, 50, 10, 15, 12, 40, 60, 40, 30, 15, 18, 20, 24, 24, 24, 30]
    for col, width in enumerate(column_widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = width

    # Freeze header row
    ws.freeze_panes = "A2"

    if audit_metadata is not None:
        audit.append(["Field", "Value"])
        for row, (key, value) in enumerate(_audit_cells(audit_metadata).items(), start=2):
            write_cell(audit.cell(row, 1), key.removeprefix("Audit "))
            write_cell(audit.cell(row, 2), value)
        sources.append(["Normalized requirement", "Original IDs", "Source ID", "Source version", "Verified excerpt", "Excerpt"])
        source_rows = count(start=2)
        for mapping in audit_metadata.get("source_mappings", []):
            for source in mapping.get("sources") or [{}]:
                row = next(source_rows)
                values = [
                    mapping["normalized_requirement_id"],
                    json.dumps(mapping["original_requirement_ids"], ensure_ascii=False),
                    source.get("source_id"),
                    source.get("source_version"),
                    source.get("excerpt_verified"),
                    source.get("excerpt"),
                ]
                for col, value in enumerate(values, start=1):
                    write_cell(sources.cell(row, col), value)
        coverage.append(["Kind", "Total", "Covered IDs", "Missing IDs", "Referenced IDs"])
        for row, kind in enumerate(("requirements", "scenarios"), start=2):
            item = audit_metadata.get("coverage", {}).get(kind, {})
            values = [kind, item.get("total"), *[json.dumps(item.get(key), ensure_ascii=False) for key in ("covered_ids", "missing_ids", "referenced_ids")]]
            for col, value in enumerate(values, start=1):
                write_cell(coverage.cell(row, col), value)
        coverage.append(["Behavioral assessment", "Unknown unless separately evaluated; references are not behavioral coverage."])

    for sheet in detail_sheets:
        sheet.freeze_panes = "A2"
        for cell in sheet[1]:
            cell.font = header_font
            cell.fill = header_fill
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        widths = [20, 10, 8, 17, 100] if sheet is long_values else [40, *([70] * (sheet.max_column - 1))]
        for column, width in enumerate(widths, start=1):
            sheet.column_dimensions[openpyxl.utils.get_column_letter(column)].width = width

    # Save to bytes
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


def export_to_json(test_cases: List[TestCase], *, audit_metadata: dict[str, Any] | None = None) -> str:
    """Export test cases to JSON format."""
    # Convert to dict with proper serialization
    data = {
        "export_format": "test_cases_v1",
        "total_count": len(test_cases),
        "test_cases": [
            {
                "id": tc.id,
                "title": tc.title,
                "description": tc.description,
                "priority": tc.priority,
                "type": tc.type,
                "status": tc.status,
                "preconditions": tc.preconditions,
                "steps": [{"step": s.step, "action": s.action, "expected": s.expected, "test_data": s.test_data} for s in tc.steps],
                "expected_result": tc.expected_result,
                "test_data": tc.test_data,
                "estimated_time": tc.estimated_time,
                "automation_status": tc.automation_status,
                "component": tc.component,
                "linked_requirement_ids": tc.linked_requirement_ids,
                "scenario_refs": tc.scenario_refs,
                "source_refs": tc.source_refs,
                "tags": tc.tags,
            }
            for tc in test_cases
        ],
    }
    if audit_metadata is not None:
        data["audit_metadata"] = audit_metadata
    return json.dumps(data, indent=2)
