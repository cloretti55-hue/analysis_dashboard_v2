from __future__ import annotations

import json
import math
import statistics
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_PATH = ROOT / "data" / "etf-universe.json"
OUTPUT_PATH = ROOT / "data" / "etf-performance.json"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5y&interval=1d&events=history"
TRADING_DAYS = 252


def pct(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value * 100, 2)


def fetch_yahoo_chart_history(symbol: str) -> list[dict]:
    url = YAHOO_CHART_URL.format(symbol=symbol.upper())
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 general-channels-dashboard/0.1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    result = payload.get("chart", {}).get("result", [])
    if not result:
        error = payload.get("chart", {}).get("error")
        raise ValueError(f"No Yahoo chart result for {symbol}: {error}")

    series = result[0]
    timestamps = series.get("timestamp") or []
    indicators = series.get("indicators", {})
    adjclose = (indicators.get("adjclose") or [{}])[0].get("adjclose") or []
    close = (indicators.get("quote") or [{}])[0].get("close") or []
    values = adjclose if adjclose else close

    history = []
    for timestamp, price in zip(timestamps, values):
        if price is None:
            continue
        history.append(
            {
                "date": datetime.fromtimestamp(timestamp, timezone.utc).date(),
                "close": float(price),
            }
        )

    if not history:
        raise ValueError(f"No price history returned for {symbol}")
    return history


def nearest_on_or_after(history: list[dict], target: date) -> dict | None:
    for row in history:
        if row["date"] >= target:
            return row
    return None


def nearest_on_or_before(history: list[dict], target: date) -> dict | None:
    candidate = None
    for row in history:
        if row["date"] <= target:
            candidate = row
        else:
            break
    return candidate


def cumulative_return(history: list[dict], start: date, end_close: float) -> float | None:
    start_row = nearest_on_or_after(history, start)
    if not start_row or start_row["close"] <= 0:
        return None
    return end_close / start_row["close"] - 1


def annualized_return(history: list[dict], years: int, end_date: date, end_close: float) -> float | None:
    start_row = nearest_on_or_after(history, end_date - timedelta(days=365 * years))
    if not start_row or start_row["close"] <= 0:
        return None
    elapsed_days = max((end_date - start_row["date"]).days, 1)
    total = end_close / start_row["close"] - 1
    return (1 + total) ** (365 / elapsed_days) - 1


def trailing_daily_returns(history: list[dict], end_date: date, days: int = 365) -> list[float]:
    start_date = end_date - timedelta(days=days)
    rows = [row for row in history if row["date"] >= start_date]
    returns = []
    for prev, cur in zip(rows, rows[1:]):
        if prev["close"] > 0:
            returns.append(cur["close"] / prev["close"] - 1)
    return returns


def daily_returns_by_date(history: list[dict], start_date: date) -> dict[date, float]:
    rows = [row for row in sorted(history, key=lambda item: item["date"]) if row["date"] >= start_date]
    returns = {}
    for prev, cur in zip(rows, rows[1:]):
        if prev["close"] > 0:
            returns[cur["date"]] = cur["close"] / prev["close"] - 1
    return returns


def annualized_volatility(history: list[dict], end_date: date) -> float | None:
    returns = trailing_daily_returns(history, end_date)
    if len(returns) < 30:
        return None
    return statistics.stdev(returns) * math.sqrt(TRADING_DAYS)


def max_drawdown_1y(history: list[dict], end_date: date) -> float | None:
    start_date = end_date - timedelta(days=365)
    rows = [row for row in history if row["date"] >= start_date]
    if len(rows) < 2:
        return None
    peak = rows[0]["close"]
    max_dd = 0.0
    for row in rows:
        peak = max(peak, row["close"])
        if peak > 0:
            max_dd = min(max_dd, row["close"] / peak - 1)
    return max_dd


def beta_1y(history: list[dict], benchmark_history: list[dict] | None) -> float | None:
    if not benchmark_history:
        return None
    history = sorted(history, key=lambda row: row["date"])
    benchmark_history = sorted(benchmark_history, key=lambda row: row["date"])
    if len(history) < 40 or len(benchmark_history) < 40:
        return None

    end_date = min(history[-1]["date"], benchmark_history[-1]["date"])
    start_date = end_date - timedelta(days=365)
    left = daily_returns_by_date(history, start_date)
    right = daily_returns_by_date(benchmark_history, start_date)
    common_dates = sorted(set(left) & set(right))
    if len(common_dates) < 30:
        return None

    x = [right[day] for day in common_dates]
    y = [left[day] for day in common_dates]
    mean_x = statistics.mean(x)
    mean_y = statistics.mean(y)
    variance_x = sum((value - mean_x) ** 2 for value in x)
    if variance_x == 0:
        return None
    covariance = sum((a - mean_y) * (b - mean_x) for a, b in zip(y, x))
    return round(covariance / variance_x, 2)


def correlation_1y(history: list[dict], benchmark_history: list[dict] | None) -> float | None:
    if not benchmark_history:
        return None
    history = sorted(history, key=lambda row: row["date"])
    benchmark_history = sorted(benchmark_history, key=lambda row: row["date"])
    if len(history) < 40 or len(benchmark_history) < 40:
        return None

    end_date = min(history[-1]["date"], benchmark_history[-1]["date"])
    start_date = end_date - timedelta(days=365)
    left = daily_returns_by_date(history, start_date)
    right = daily_returns_by_date(benchmark_history, start_date)
    common_dates = sorted(set(left) & set(right))
    if len(common_dates) < 30:
        return None

    x = [right[day] for day in common_dates]
    y = [left[day] for day in common_dates]
    mean_x = statistics.mean(x)
    mean_y = statistics.mean(y)
    std_x = math.sqrt(sum((value - mean_x) ** 2 for value in x))
    std_y = math.sqrt(sum((value - mean_y) ** 2 for value in y))
    if std_x == 0 or std_y == 0:
        return None
    covariance = sum((a - mean_y) * (b - mean_x) for a, b in zip(y, x))
    return round(covariance / (std_x * std_y), 2)


def metrics_for_history(history: list[dict]) -> dict:
    history = sorted(history, key=lambda row: row["date"])
    last = history[-1]
    end_date = last["date"]
    end_close = last["close"]
    ytd_start = date(end_date.year, 1, 1)

    return {
      "asOf": end_date.isoformat(),
      "lastClose": round(end_close, 4),
      "returnYtdPct": pct(cumulative_return(history, ytd_start, end_close)),
      "return1yPct": pct(cumulative_return(history, end_date - timedelta(days=365), end_close)),
      "return3yAnnPct": pct(annualized_return(history, 3, end_date, end_close)),
      "return5yAnnPct": pct(annualized_return(history, 5, end_date, end_close)),
      "vol1yAnnPct": pct(annualized_volatility(history, end_date)),
      "maxDrawdown1yPct": pct(max_drawdown_1y(history, end_date)),
    }


def active_metrics(item_metrics: dict, benchmark_metrics: dict | None) -> dict | None:
    if not benchmark_metrics:
        return None
    fields = ["returnYtdPct", "return1yPct", "return3yAnnPct", "return5yAnnPct", "vol1yAnnPct"]
    active = {}
    for field in fields:
        left = item_metrics.get(field)
        right = benchmark_metrics.get(field)
        active[field.replace("Pct", "VsBenchmarkPct")] = (
            round(left - right, 2) if left is not None and right is not None else None
        )
    return active


def downsample(rows: list[dict], max_points: int = 180) -> list[dict]:
    if len(rows) <= max_points:
        return rows
    step = (len(rows) - 1) / (max_points - 1)
    sampled = [rows[round(i * step)] for i in range(max_points)]
    return sampled


def normalized_chart_series(
    history: list[dict],
    benchmark_history: list[dict] | None = None,
    years: int = 3,
) -> dict | None:
    history = sorted(history, key=lambda row: row["date"])
    if not history:
        return None

    end_date = history[-1]["date"]
    start_date = max(history[0]["date"], end_date - timedelta(days=365 * years))
    rows = [row for row in history if row["date"] >= start_date]
    if len(rows) < 2 or rows[0]["close"] <= 0:
        return None

    benchmark_by_date = {}
    benchmark_start = None
    if benchmark_history:
        benchmark_history = sorted(benchmark_history, key=lambda row: row["date"])
        benchmark_start = nearest_on_or_before(benchmark_history, rows[0]["date"])
        benchmark_by_date = {row["date"]: row for row in benchmark_history}

    chart_rows = []
    last_benchmark = benchmark_start
    base = rows[0]["close"]
    benchmark_base = benchmark_start["close"] if benchmark_start and benchmark_start["close"] > 0 else None

    for row in rows:
        if benchmark_history:
            same_day = benchmark_by_date.get(row["date"])
            if same_day:
                last_benchmark = same_day

        point = {
            "date": row["date"].isoformat(),
            "etf": round((row["close"] / base) * 100, 2),
        }
        if last_benchmark and benchmark_base:
            point["sp500"] = round((last_benchmark["close"] / benchmark_base) * 100, 2)
        chart_rows.append(point)

    return {
        "base": 100,
        "period": "3y_or_available",
        "startDate": chart_rows[0]["date"],
        "endDate": chart_rows[-1]["date"],
        "points": downsample(chart_rows),
    }


def main() -> None:
    universe = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
    benchmark_cache: dict[str, dict] = {}
    benchmark_history_cache: dict[str, list[dict]] = {}
    output_items = []
    as_of_dates = []

    for item in universe["instruments"]:
        result = {
            "ticker": item["ticker"],
            "name": item["name"],
            "assetClass": item["assetClass"],
            "category": item["category"],
            "wrapper": item["wrapper"],
            "currency": item["currency"],
            "quoteSource": item["quoteSource"],
            "quoteSymbol": item.get("quoteSymbol"),
            "benchmark": item.get("benchmark"),
            "compareToSp500": item.get("compareToSp500", False),
            "valuation": item.get("valuation"),
            "status": "pending",
        }

        if item["quoteSource"] != "yahoo_chart" or not item.get("quoteSymbol"):
            result["status"] = "manual_required"
            result["note"] = item.get("notes", "No automated quote mapping yet.")
            output_items.append(result)
            continue

        try:
            history = fetch_yahoo_chart_history(item["quoteSymbol"])
            item_metrics = metrics_for_history(history)
            result.update(item_metrics)
            result["status"] = "ok"
            as_of_dates.append(item_metrics["asOf"])

            benchmark_symbol = universe["benchmarkDefaults"]["sp500"]["quoteSymbol"]
            benchmark_history = None
            if item["quoteSymbol"].upper() != benchmark_symbol.upper():
                if benchmark_symbol not in benchmark_history_cache:
                    benchmark_history_cache[benchmark_symbol] = fetch_yahoo_chart_history(benchmark_symbol)
                benchmark_history = benchmark_history_cache[benchmark_symbol]
            result["performanceChart"] = normalized_chart_series(history, benchmark_history)
            result["beta1yVsSp500"] = beta_1y(history, benchmark_history) if benchmark_history else 1.0
            result["correlation1yVsSp500"] = correlation_1y(history, benchmark_history) if benchmark_history else 1.0

            if item.get("compareToSp500"):
                if benchmark_symbol not in benchmark_cache:
                    benchmark_cache[benchmark_symbol] = metrics_for_history(benchmark_history or history)
                result["activeVsBenchmark"] = active_metrics(item_metrics, benchmark_cache[benchmark_symbol])

        except Exception as exc:
            result["status"] = "error"
            result["error"] = str(exc)

        output_items.append(result)

    payload = {
        "version": 1,
        "asOf": max(as_of_dates) if as_of_dates else None,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Yahoo Finance chart adjusted close where available; manual_required for unmapped instruments.",
        "methodology": (
            "Return metrics use adjusted close from Yahoo Finance chart data when available. 3Y and 5Y returns are annualized. "
            "Volatility is annualized from trailing daily returns. Public/free data may differ from licensed index-provider total return data."
        ),
        "benchmarkPolicy": "Only US equity exposure is compared against S&P 500 proxy SPY.",
        "instruments": output_items,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
