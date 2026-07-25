from __future__ import annotations

import html
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "sp500-valuation.json"
TOPIC_URL = "https://insight.factset.com/topic/earnings"
BASE_URL = "https://insight.factset.com"
USER_AGENT = "Mozilla/5.0 general-channels-dashboard/0.1"


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read().decode("utf-8", errors="replace")


def strip_html(raw_html: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", raw_html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def load_previous() -> dict:
    if not OUTPUT_PATH.exists():
        return {}
    try:
        return json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def find_candidate_urls(topic_html: str) -> list[str]:
    candidates: list[str] = []
    for href, title in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>\s*([^<]+?)\s*</a>', topic_html, flags=re.I):
        clean_title = html.unescape(title).strip()
        if "S&P 500" not in clean_title:
            continue
        if not any(term in clean_title for term in ("Earnings Season Update", "Earnings Season Preview", "Likely to Report")):
            continue
        url = href if href.startswith("http") else f"{BASE_URL}{href}"
        if url not in candidates:
            candidates.append(url)
    return candidates


def parse_factset_post(url: str, raw_html: str) -> dict | None:
    text = strip_html(raw_html)
    pe_match = re.search(r"forward 12-month P/E ratio is ([0-9]+(?:\.[0-9]+)?)", text, flags=re.I)
    if not pe_match:
        return None

    date_match = re.search(
        r"By\s+John Butters\s+\|\s+([A-Z][a-z]+ \d{1,2}, \d{4})",
        text,
    )
    five_year = re.search(r"5-year average \(([0-9]+(?:\.[0-9]+)?)\)", text, flags=re.I)
    ten_year = re.search(r"10-year average \(([0-9]+(?:\.[0-9]+)?)\)", text, flags=re.I)

    def number(match: re.Match[str] | None) -> float | None:
        return float(match.group(1)) if match else None

    as_of = None
    if date_match:
        as_of = datetime.strptime(date_match.group(1), "%B %d, %Y").date().isoformat()

    return {
        "asOf": as_of,
        "sourceUrl": url,
        "forwardPE": number(pe_match),
        "averages": {
            "5y": number(five_year),
            "10y": number(ten_year),
        },
    }


def merged_payload(parsed: dict, previous: dict) -> dict:
    previous_sp500 = previous.get("sp500", {})
    previous_averages = previous_sp500.get("averages", {})
    parsed_averages = parsed.get("averages", {})
    return {
        "asOf": parsed.get("asOf") or previous.get("asOf"),
        "updatedAt": datetime.now(timezone.utc).date().isoformat(),
        "status": "ok",
        "source": "FactSet Earnings Insight",
        "sourceUrl": parsed.get("sourceUrl"),
        "methodology": (
            "Automated extraction from public FactSet Insight earnings posts. "
            "Fields not available in the post are preserved from the previous JSON when present."
        ),
        "sp500": {
            "forwardPE": parsed.get("forwardPE") or previous_sp500.get("forwardPE"),
            "forwardEPS": previous_sp500.get("forwardEPS"),
            "trailingPE": previous_sp500.get("trailingPE"),
            "averages": {
                "5y": parsed_averages.get("5y") or previous_averages.get("5y"),
                "10y": parsed_averages.get("10y") or previous_averages.get("10y"),
                "15y": previous_averages.get("15y"),
                "20y": previous_averages.get("20y"),
                "25y": previous_averages.get("25y"),
            },
        },
    }


def failure_payload(previous: dict, error: Exception) -> dict:
    payload = previous if previous else {
        "asOf": None,
        "source": "FactSet Earnings Insight",
        "sp500": {
            "forwardPE": None,
            "forwardEPS": None,
            "trailingPE": None,
            "averages": {"5y": None, "10y": None, "15y": None, "20y": None, "25y": None},
        },
    }
    payload["status"] = "fallback"
    payload["updatedAt"] = datetime.now(timezone.utc).date().isoformat()
    payload["refreshError"] = str(error)
    return payload


def main() -> None:
    previous = load_previous()
    try:
        topic_html = fetch_text(TOPIC_URL)
        candidates = find_candidate_urls(topic_html)
        if not candidates:
            raise ValueError("No candidate FactSet S&P 500 earnings posts found.")

        parsed = None
        for url in candidates[:12]:
            post_html = fetch_text(url)
            parsed = parse_factset_post(url, post_html)
            if parsed:
                break

        if not parsed:
            raise ValueError("No recent FactSet post contained a forward 12-month P/E sentence.")

        payload = merged_payload(parsed, previous)
    except Exception as exc:
        payload = failure_payload(previous, exc)

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH.relative_to(ROOT)} with status={payload.get('status')} asOf={payload.get('asOf')}")


if __name__ == "__main__":
    main()
