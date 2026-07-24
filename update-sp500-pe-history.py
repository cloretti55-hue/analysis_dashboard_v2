from __future__ import annotations

import csv
import html
import json
import re
import urllib.request
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "sp500-pe-history.json"
MULTPL_URL = "https://www.multpl.com/s-p-500-pe-ratio/table/by-month"
DATAHUB_CSV_URL = "https://datahub.io/core/s-and-p-500/_r/-/data/data.csv"
USER_AGENT = "Mozilla/5.0 general-channels-dashboard/0.1"


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_multpl(html_text: str) -> list[dict]:
    rows: list[dict] = []
    for date_text, value_html in re.findall(r"<tr[^>]*>\s*<td>([^<]+)</td>\s*<td>(.*?)</td>\s*</tr>", html_text, flags=re.S | re.I):
        clean_value = re.sub(r"<[^>]+>", " ", value_html)
        clean_value = html.unescape(clean_value)
        value_match = re.search(r"([0-9]+(?:\.[0-9]+)?)", clean_value)
        if not value_match:
            continue
        try:
            dt = datetime.strptime(html.unescape(date_text).strip(), "%b %d, %Y").date()
            pe = float(value_match.group(1))
        except ValueError:
            continue
        rows.append({"date": dt.isoformat(), "pe": round(pe, 2)})

    rows.sort(key=lambda item: item["date"])
    return [row for row in rows if 0 < row["pe"] <= 120]


def parse_datahub(csv_text: str) -> list[dict]:
    rows: list[dict] = []
    for row in csv.DictReader(StringIO(csv_text)):
        try:
            sp500 = float(row["SP500"])
            earnings = float(row["Earnings"])
        except (KeyError, TypeError, ValueError):
            continue
        if sp500 <= 0 or earnings <= 0:
            continue
        pe = sp500 / earnings
        if 0 < pe <= 120:
            rows.append({"date": row["Date"], "pe": round(pe, 2)})
    return rows


def downsample_year_end(rows: list[dict]) -> list[dict]:
    by_year: dict[str, dict] = {}
    for row in rows:
        by_year[row["date"][:4]] = row
    return [by_year[year] for year in sorted(by_year)]


def mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 2)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 2)


def build_payload(rows: list[dict], source: str, source_url: str, status: str = "ok") -> dict:
    year_end = downsample_year_end(rows)
    latest = rows[-1] if rows else None
    values = [row["pe"] for row in rows]
    return {
        "asOf": latest["date"] if latest else None,
        "updatedAt": datetime.now(timezone.utc).date().isoformat(),
        "status": status,
        "source": source,
        "sourceUrl": source_url,
        "methodology": (
            "Historical trailing P/E for the S&P 500. This is trailing/reported P/E, not forward P/E. "
            "Multpl is used as the primary current public table; DataHub/Shiller is used only as fallback."
        ),
        "stats": {
            "firstDate": rows[0]["date"] if rows else None,
            "lastDate": latest["date"] if latest else None,
            "latestPE": latest["pe"] if latest else None,
            "longTermAverage": mean(values),
            "averageSince1950": mean([row["pe"] for row in rows if row["date"] >= "1950-01-01"]),
            "medianSince1950": median([row["pe"] for row in rows if row["date"] >= "1950-01-01"]),
            "averageSince1990": mean([row["pe"] for row in rows if row["date"] >= "1990-01-01"]),
        },
        "monthly": rows,
        "yearEnd": year_end,
    }


def main() -> None:
    try:
        rows = parse_multpl(fetch_text(MULTPL_URL))
        if len(rows) < 100:
            raise ValueError("Multpl table returned too few rows.")
        payload = build_payload(rows, "Multpl", MULTPL_URL)
    except Exception as multpl_error:
        rows = parse_datahub(fetch_text(DATAHUB_CSV_URL))
        payload = build_payload(rows, "DataHub / Robert Shiller dataset", DATAHUB_CSV_URL, status="fallback")
        payload["refreshError"] = str(multpl_error)

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH.relative_to(ROOT)} rows={len(payload['monthly'])} asOf={payload['asOf']} source={payload['source']}")


if __name__ == "__main__":
    main()
