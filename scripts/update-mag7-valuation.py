from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "mag7-valuation.json"
TICKERS = ["NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META", "TSLA"]
NAMES = {
    "NVDA": "Nvidia",
    "MSFT": "Microsoft",
    "AAPL": "Apple",
    "AMZN": "Amazon",
    "GOOGL": "Alphabet",
    "META": "Meta",
    "TSLA": "Tesla",
}


def load_previous() -> dict:
    if not OUTPUT_PATH.exists():
        return {}
    try:
        return json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def clean_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def implied_earnings(market_cap: float | None, pe: float | None) -> float | None:
    if not market_cap or not pe:
        return None
    return market_cap / pe


def round_optional(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def fetch_company(ticker: str) -> dict:
    stock = yf.Ticker(ticker)
    info = stock.get_info()
    market_cap = clean_number(info.get("marketCap"))
    trailing_pe = clean_number(info.get("trailingPE"))
    forward_pe = clean_number(info.get("forwardPE"))
    price = clean_number(info.get("currentPrice") or info.get("regularMarketPrice"))
    trailing_eps = clean_number(info.get("trailingEps"))
    forward_eps = clean_number(info.get("forwardEps"))
    return {
        "ticker": ticker,
        "company": NAMES.get(ticker, ticker),
        "price": round_optional(price),
        "marketCap": round_optional(market_cap, 0),
        "trailingPE": round_optional(trailing_pe),
        "forwardPE": round_optional(forward_pe),
        "trailingEPS": round_optional(trailing_eps),
        "forwardEPS": round_optional(forward_eps),
        "impliedTrailingEarnings": round_optional(implied_earnings(market_cap, trailing_pe), 0),
        "impliedForwardEarnings": round_optional(implied_earnings(market_cap, forward_pe), 0),
    }


def aggregate(companies: list[dict]) -> dict:
    market_cap = sum(company.get("marketCap") or 0 for company in companies)
    trailing_earnings = sum(company.get("impliedTrailingEarnings") or 0 for company in companies)
    forward_earnings = sum(company.get("impliedForwardEarnings") or 0 for company in companies)
    for company in companies:
        company["weight"] = round_optional((company.get("marketCap") or 0) / market_cap * 100 if market_cap else None)
    return {
        "marketCap": round_optional(market_cap, 0),
        "trailingPE": round_optional(market_cap / trailing_earnings if trailing_earnings else None),
        "forwardPE": round_optional(market_cap / forward_earnings if forward_earnings else None),
    }


def failure_payload(previous: dict, error: Exception) -> dict:
    payload = previous if previous else {
        "asOf": None,
        "source": "Yahoo Finance via yfinance",
        "group": {"trailingPE": None, "forwardPE": None, "marketCap": None},
        "companies": [],
    }
    payload["status"] = "fallback"
    payload["updatedAt"] = datetime.now(timezone.utc).date().isoformat()
    payload["refreshError"] = str(error)
    return payload


def main() -> None:
    previous = load_previous()
    try:
        companies = [fetch_company(ticker) for ticker in TICKERS]
        valid = [company for company in companies if company.get("marketCap")]
        if len(valid) < 5:
            raise ValueError("Too few Magnificent 7 companies returned usable Yahoo data.")
        payload = {
            "asOf": datetime.now(timezone.utc).date().isoformat(),
            "updatedAt": datetime.now(timezone.utc).date().isoformat(),
            "status": "ok",
            "source": "Yahoo Finance via yfinance",
            "methodology": (
                "Forward P/E and trailing P/E for individual Magnificent 7 stocks. "
                "Group multiples are calculated as total market cap divided by implied aggregate earnings."
            ),
            "group": aggregate(valid),
            "companies": valid,
        }
    except Exception as exc:
        payload = failure_payload(previous, exc)

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH.relative_to(ROOT)} status={payload.get('status')} companies={len(payload.get('companies', []))}")


if __name__ == "__main__":
    main()
