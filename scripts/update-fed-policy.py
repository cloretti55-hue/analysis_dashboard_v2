from __future__ import annotations

import html
import io
import json
import re
import time
import urllib.request
import zipfile
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "fed-policy.json"
FED_MONETARY_FEED_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"
MPT_XLSX_URL = "https://www.atlantafed.org/-/media/Project/Atlanta/FRBA/Documents/cenfis/market-probability-tracker/mpt_histdata.xlsx"
TARGET_REFERENCE_START = date(2026, 9, 16)
REQUEST_ATTEMPTS = 3
REQUEST_TIMEOUT_SECONDS = 90

NAMESPACES = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def fetch_bytes(url: str, label: str) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, REQUEST_ATTEMPTS + 1):
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 general-channels-dashboard/0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return response.read()
        except Exception as exc:
            last_error = exc
            if attempt < REQUEST_ATTEMPTS:
                print(
                    f"WARNING: tentativa {attempt}/{REQUEST_ATTEMPTS} para {label} falhou: {exc}. "
                    "Nova tentativa em instantes."
                )
                time.sleep(5 * attempt)
    raise RuntimeError(
        f"{label} indisponível após {REQUEST_ATTEMPTS} tentativas: {last_error}"
    ) from last_error


def parse_rate(value: str) -> float:
    token = value.strip()
    if "-" in token:
        whole, fraction = token.split("-", 1)
        numerator, denominator = fraction.split("/", 1)
        return float(whole) + float(numerator) / float(denominator)
    if "/" in token:
        numerator, denominator = token.split("/", 1)
        return float(numerator) / float(denominator)
    return float(token)


def latest_fomc_target_range() -> tuple[date, float, float, str]:
    feed = ET.fromstring(fetch_bytes(FED_MONETARY_FEED_URL, "feed oficial do Federal Reserve"))
    statement = next(
        (
            item
            for item in feed.findall("./channel/item")
            if (item.findtext("title") or "").strip() == "Federal Reserve issues FOMC statement"
        ),
        None,
    )
    if statement is None:
        raise ValueError("comunicado mais recente do FOMC não encontrado no feed oficial")

    statement_url = (statement.findtext("link") or "").strip()
    published_at = parsedate_to_datetime((statement.findtext("pubDate") or "").strip()).date()
    source = fetch_bytes(statement_url, "comunicado mais recente do FOMC").decode("utf-8", errors="ignore")
    source = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", source, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", source)
    text = re.sub(r"\s+", " ", html.unescape(text))
    match = re.search(
        r"target range for the federal funds rate at\s+"
        r"(\d+(?:-\d+/\d+)?|\d+/\d+)\s+to\s+"
        r"(\d+(?:-\d+/\d+)?|\d+/\d+)\s+percent",
        text,
        flags=re.I,
    )
    if not match:
        raise ValueError("faixa-alvo não encontrada no comunicado mais recente do FOMC")
    return published_at, parse_rate(match.group(1)), parse_rate(match.group(2)), statement_url


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
    content = fetch_bytes(MPT_XLSX_URL, "Atlanta Fed Market Probability Tracker")

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
    target_date, target_lower, target_upper, statement_url = latest_fomc_target_range()
    mpt = latest_mpt_probability_rows(TARGET_REFERENCE_START)
    hike = round(float(mpt.get("Prob: hike", 0.0)), 1)
    cut = round(float(mpt.get("Prob: cut", 0.0)), 1)
    steady = round(max(0.0, 100.0 - hike - cut), 1)

    payload = {
        "asOf": mpt["asOf"].isoformat(),
        "updatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "Federal Reserve FOMC statement / Atlanta Fed Market Probability Tracker",
        "targetRange": {
            "lower": target_lower,
            "upper": target_upper,
            "label": format_range(target_lower, target_upper),
            "asOf": target_date.isoformat(),
            "sourceUrl": statement_url,
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
            print(f"ERROR: não foi possível atualizar os dados do Fed: {exc}")
            print(f"O último arquivo válido foi preservado: {OUTPUT_PATH}")
            raise SystemExit(1) from exc
        else:
            raise
