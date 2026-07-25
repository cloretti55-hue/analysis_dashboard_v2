from __future__ import annotations

import csv
import io
import json
import urllib.request
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "fed-policy.json"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
MPT_XLSX_URL = "https://www.atlantafed.org/-/media/Project/Atlanta/FRBA/Documents/cenfis/market-probability-tracker/mpt_histdata.xlsx"
TARGET_REFERENCE_START = date(2026, 9, 16)

NAMESPACES = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def fred_latest(series_id: str) -> tuple[date, float]:
    request = urllib.request.Request(
        FRED_CSV_URL.format(series_id=series_id),
        headers={"User-Agent": "Mozilla/5.0 general-channels-dashboard/0.1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        text = response.read().decode("utf-8")

    for row in reversed(list(csv.DictReader(text.splitlines()))):
        raw_value = (row.get(series_id) or "").strip()
        if not raw_value or raw_value == ".":
            continue
        return datetime.strptime(row["observation_date"], "%Y-%m-%d").date(), float(raw_value)
    raise ValueError(f"No valid observation found for {series_id}")


def cell_ref_to_column(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha())
    column = 0
    for letter in letters:
        column = column * 26 + (ord(letter.upper()) - 64)
    return column


def read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    strings = []
    for item in root.findall("main:si", NAMESPACES):
        parts = [text.text or "" for text in item.findall(".//main:t", NAMESPACES)]
        strings.append("".join(parts))
    return strings


def workbook_sheet_paths(archive: zipfile.ZipFile) -> dict[str, str]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_paths = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in relationships.findall("pkg:Relationship", NAMESPACES)
    }

    paths = {}
    for sheet in workbook.findall("main:sheets/main:sheet", NAMESPACES):
        name = sheet.attrib["name"]
        rel_id = sheet.attrib[f"{{{NAMESPACES['rel']}}}id"]
        target = rel_paths[rel_id]
        paths[name] = f"xl/{target}" if not target.startswith("xl/") else target
    return paths


def cell_value(cell: ET.Element, shared_strings: list[str]) -> object:
    cell_type = cell.attrib.get("t")
    value = cell.find("main:v", NAMESPACES)
    if value is None or value.text is None:
        return None
    if cell_type == "s":
        return shared_strings[int(value.text)]
    return value.text


def iter_sheet_rows(archive: zipfile.ZipFile, sheet_name: str) -> list[list[object]]:
    shared_strings = read_shared_strings(archive)
    sheet_paths = workbook_sheet_paths(archive)
    root = ET.fromstring(archive.read(sheet_paths[sheet_name]))
    rows = []
    for row in root.findall(".//main:sheetData/main:row", NAMESPACES):
        values_by_column = {}
        max_column = 0
        for cell in row.findall("main:c", NAMESPACES):
            column = cell_ref_to_column(cell.attrib["r"])
            max_column = max(max_column, column)
            values_by_column[column] = cell_value(cell, shared_strings)
        rows.append([values_by_column.get(column) for column in range(1, max_column + 1)])
    return rows


def excel_serial_to_date(value: object) -> date:
    if isinstance(value, str) and "-" in value:
        return datetime.strptime(value, "%Y-%m-%d").date()
    serial = int(float(str(value).strip()))
    return date.fromordinal(date(1899, 12, 30).toordinal() + serial)


def fetch_mpt_rows() -> list[dict[str, object]]:
    request = urllib.request.Request(
        MPT_XLSX_URL,
        headers={"User-Agent": "Mozilla/5.0 general-channels-dashboard/0.1"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read()

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        rows = iter_sheet_rows(archive, "DATA")

    headers = [str(header) for header in rows[0]]
    return [dict(zip(headers, row)) for row in rows[1:] if row and row[0] is not None]


def latest_mpt_probability_rows(reference_start: date) -> dict[str, object]:
    rows = fetch_mpt_rows()
    candidates = []
    for row in rows:
        try:
            row_reference_start = excel_serial_to_date(row["reference_start"])
            row_date = excel_serial_to_date(row["date"])
        except Exception:
            continue
        if row_reference_start == reference_start and row.get("field") in {"Prob: hike", "Prob: cut"}:
            candidates.append({**row, "date": row_date, "reference_start": row_reference_start})

    if not candidates:
        raise ValueError(f"No MPT rows found for {reference_start.isoformat()}")

    latest_date = max(row["date"] for row in candidates)
    latest_rows = [row for row in candidates if row["date"] == latest_date]
    result = {"asOf": latest_date, "referenceStart": reference_start, "targetRange": latest_rows[0].get("target_range")}
    for row in latest_rows:
        result[str(row["field"])] = float(str(row["value"]).strip())
    return result


def format_range(lower: float, upper: float) -> str:
    return f"{lower:.2f}%–{upper:.2f}%".replace(".", ",")


def main() -> None:
    lower_date, target_lower = fred_latest("DFEDTARL")
    upper_date, target_upper = fred_latest("DFEDTARU")
    mpt = latest_mpt_probability_rows(TARGET_REFERENCE_START)
    hike = round(float(mpt.get("Prob: hike", 0.0)), 1)
    cut = round(float(mpt.get("Prob: cut", 0.0)), 1)
    steady = round(max(0.0, 100.0 - hike - cut), 1)

    payload = {
        "asOf": min(lower_date, upper_date, mpt["asOf"]).isoformat(),
        "updatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "FRED / Atlanta Fed Market Probability Tracker",
        "targetRange": {
            "lower": target_lower,
            "upper": target_upper,
            "label": format_range(target_lower, target_upper),
            "asOf": min(lower_date, upper_date).isoformat(),
            "series": {"lower": "DFEDTARL", "upper": "DFEDTARU"},
        },
        "marketProbability": {
            "referenceStart": TARGET_REFERENCE_START.isoformat(),
            "label": "até 16/09/26",
            "asOf": mpt["asOf"].isoformat(),
            "hike": hike,
            "steady": steady,
            "cut": cut,
            "targetRangeAtObservation": mpt.get("targetRange"),
            "sourceDataset": "Atlanta Fed mpt_histdata.xlsx",
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} as of {payload['asOf']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if OUTPUT_PATH.exists():
            print(f"WARNING: could not refresh Fed policy data: {exc}")
            print(f"Keeping existing {OUTPUT_PATH}")
        else:
            raise
