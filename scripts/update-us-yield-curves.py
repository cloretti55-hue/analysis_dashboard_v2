from __future__ import annotations

import csv
import json
import math
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "curve-us.json"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start_date}"
TREASURY_CSV_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/all/{month}?_format=csv&field_tdr_date_value_month={month}&type={curve_type}"
)
FED_TIPS_MODEL_CSV_URL = "https://www.federalreserve.gov/data/yield-curve-tables/feds200805.csv"

MATURITIES = ["2Y", "5Y", "7Y", "10Y", "30Y"]
NOMINAL_SERIES = {
    "2Y": "DGS2",
    "5Y": "DGS5",
    "7Y": "DGS7",
    "10Y": "DGS10",
    "30Y": "DGS30",
}
REAL_SERIES = {
    "5Y": "DFII5",
    "7Y": "DFII7",
    "10Y": "DFII10",
    "30Y": "DFII30",
}
REAL_MODEL_SERIES = {
    "2Y": "TIPSPY02",
}
REQUEST_TIMEOUT_SECONDS = 90
REQUEST_ATTEMPTS = 3
FRED_LOOKBACK_DAYS = 560
TREASURY_NOMINAL_COLUMNS = {"2Y": "2 Yr", "5Y": "5 Yr", "7Y": "7 Yr", "10Y": "10 Yr", "30Y": "30 Yr"}
TREASURY_REAL_COLUMNS = {"5Y": "5 YR", "7Y": "7 YR", "10Y": "10 YR", "30Y": "30 YR"}


@dataclass(frozen=True)
class Observation:
    series_id: str
    date: date
    value: float


def fetch_text(request: urllib.request.Request, label: str, encoding: str = "utf-8") -> str:
    last_error: Exception | None = None
    for attempt in range(1, REQUEST_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return response.read().decode(encoding)
        except Exception as exc:
            last_error = exc
            if attempt == REQUEST_ATTEMPTS:
                break
            wait_seconds = attempt * 10
            print(f"WARNING: {label} failed on attempt {attempt}: {exc}. Retrying in {wait_seconds}s.")
            time.sleep(wait_seconds)
    raise RuntimeError(f"{label} failed after {REQUEST_ATTEMPTS} attempts: {last_error}")


def fetch_observations(series_id: str) -> list[Observation]:
    start_date = (date.today() - timedelta(days=FRED_LOOKBACK_DAYS)).isoformat()
    request = urllib.request.Request(
        FRED_CSV_URL.format(series_id=series_id, start_date=start_date),
        headers={"User-Agent": "Mozilla/5.0 general-channels-dashboard/0.1"},
    )
    text = fetch_text(request, f"FRED {series_id}")

    observations: list[Observation] = []
    for row in csv.DictReader(text.splitlines()):
        raw_value = (row.get(series_id) or "").strip()
        if not raw_value or raw_value == ".":
            continue
        observations.append(
            Observation(
                series_id=series_id,
                date=datetime.strptime(row["observation_date"], "%Y-%m-%d").date(),
                value=float(raw_value),
            )
        )

    if not observations:
        raise ValueError(f"No valid observation found for {series_id}")
    return observations


def month_key(day: date) -> str:
    return f"{day.year}{day.month:02d}"


def treasury_months_to_fetch(today: date) -> list[str]:
    months = {month_key(subtract_months(today, months_ago)) for months_ago in (0, 1, 2, 3, 4, 12, 13)}
    return sorted(months)


def parse_treasury_date(raw_date: str) -> date:
    return datetime.strptime(raw_date, "%m/%d/%Y").date()


def parse_treasury_value(raw_value: str) -> float | None:
    value = (raw_value or "").strip()
    if not value or value.upper() in {"N/A", "NA", "."}:
        return None
    return float(value)


def fetch_treasury_curve_observations(curve_type: str, columns: dict[str, str]) -> dict[str, list[Observation]]:
    observations = {maturity: [] for maturity in columns}
    for month in treasury_months_to_fetch(date.today()):
        request = urllib.request.Request(
            TREASURY_CSV_URL.format(month=month, curve_type=curve_type),
            headers={"User-Agent": "Mozilla/5.0 general-channels-dashboard/0.1"},
        )
        text = fetch_text(request, f"Treasury {curve_type} {month}")
        for row in csv.DictReader(text.splitlines()):
            raw_date = (row.get("Date") or "").strip()
            if not raw_date:
                continue
            obs_date = parse_treasury_date(raw_date)
            for maturity, column in columns.items():
                value = parse_treasury_value(row.get(column, ""))
                if value is not None:
                    observations[maturity].append(Observation(series_id=column, date=obs_date, value=value))

    for maturity in observations:
        observations[maturity].sort(key=lambda observation: observation.date)
    missing = [maturity for maturity, values in observations.items() if not values]
    if missing:
        raise ValueError(f"No Treasury observations found for {', '.join(missing)}")
    return observations


def fetch_fed_tips_model_observations(columns: list[str]) -> dict[str, list[Observation]]:
    request = urllib.request.Request(
        FED_TIPS_MODEL_CSV_URL,
        headers={"User-Agent": "Mozilla/5.0 general-channels-dashboard/0.1"},
    )
    text = fetch_text(request, "Fed TIPS model", encoding="utf-8-sig")

    data_start = text.find("Date,")
    if data_start < 0:
        raise ValueError("Could not find data header in Fed TIPS model CSV")

    observations = {column: [] for column in columns}
    for row in csv.DictReader(text[data_start:].splitlines()):
        obs_date = datetime.strptime(row["Date"], "%Y-%m-%d").date()
        for column in columns:
            raw_value = (row.get(column) or "").strip()
            if not raw_value or raw_value == "NA":
                continue
            observations[column].append(Observation(series_id=column, date=obs_date, value=float(raw_value)))

    missing = [column for column, values in observations.items() if not values]
    if missing:
        raise ValueError(f"No valid observations found for {', '.join(missing)}")
    return observations


def latest_on_or_before(observations: list[Observation], target: date | None = None) -> Observation:
    for observation in reversed(observations):
        if target is None or observation.date <= target:
            return observation
    raise ValueError(f"No observation available on or before {target}")


def subtract_months(day: date, months: int) -> date:
    month_index = day.month - 1 - months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    month_lengths = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(year, month, min(day.day, month_lengths[month - 1]))


def fisher_inflation(nominal_pct: float, real_pct: float) -> float:
    nominal = nominal_pct / 100
    real = real_pct / 100
    return ((1 + nominal) / (1 + real) - 1) * 100


def round_curve(values: dict[str, float]) -> dict[str, float]:
    return {maturity: round(values[maturity], 2) for maturity in MATURITIES}


def build_curve_snapshot(
    nominal_series: dict[str, list[Observation]],
    real_series: dict[str, list[Observation]],
    real_model_series: dict[str, list[Observation]],
    target: date | None = None,
) -> dict[str, object]:
    nominal_obs = {maturity: latest_on_or_before(values, target) for maturity, values in nominal_series.items()}
    real_obs = {maturity: latest_on_or_before(values, target) for maturity, values in real_series.items()}
    real_model_obs = {maturity: latest_on_or_before(values, target) for maturity, values in real_model_series.items()}

    nominal = {maturity: observation.value for maturity, observation in nominal_obs.items()}
    real = {maturity: observation.value for maturity, observation in real_obs.items()}
    real["2Y"] = real_model_obs["2Y"].value
    inflation = {maturity: fisher_inflation(nominal[maturity], real[maturity]) for maturity in MATURITIES}

    real_dates = {maturity: observation.date for maturity, observation in real_obs.items()}
    real_dates["2Y"] = real_model_obs["2Y"].date
    component_as_of = {
        "nominal": {maturity: observation.date.isoformat() for maturity, observation in nominal_obs.items()},
        "real": {maturity: real_dates[maturity].isoformat() for maturity in MATURITIES},
        "inflation": {
            maturity: min(nominal_obs[maturity].date, real_dates[maturity]).isoformat()
            for maturity in MATURITIES
        },
    }
    curves = {
        "nominal": round_curve(nominal),
        "real": round_curve(real),
        "inflation": round_curve(inflation),
    }

    if not all(math.isfinite(value) for curve in curves.values() for value in curve.values()):
        raise ValueError("Non-finite curve value generated")

    nominal_as_of = min(observation.date for observation in nominal_obs.values()).isoformat()
    return {"asOf": nominal_as_of, "componentAsOf": component_as_of, "curves": curves}


def main() -> None:
    nominal_series = fetch_treasury_curve_observations("daily_treasury_yield_curve", TREASURY_NOMINAL_COLUMNS)
    real_series = fetch_treasury_curve_observations("daily_treasury_real_yield_curve", TREASURY_REAL_COLUMNS)
    model_by_column = fetch_fed_tips_model_observations(list(REAL_MODEL_SERIES.values()))
    real_model_series = {"2Y": model_by_column["TIPSPY02"]}

    current = build_curve_snapshot(nominal_series, real_series, real_model_series)
    as_of = datetime.strptime(str(current["asOf"]), "%Y-%m-%d").date()
    one_month = build_curve_snapshot(nominal_series, real_series, real_model_series, subtract_months(as_of, 1))
    three_months = build_curve_snapshot(nominal_series, real_series, real_model_series, subtract_months(as_of, 3))
    one_year = build_curve_snapshot(nominal_series, real_series, real_model_series, subtract_months(as_of, 12))

    payload = {
        "country": "US",
        "asOf": current["asOf"],
        "componentAsOf": current["componentAsOf"],
        "updatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "U.S. Treasury / Federal Reserve GSW TIPS model",
        "methodology": "Nominal yields use the U.S. Treasury Daily Treasury Par Yield Curve Rates. Real yields use the U.S. Treasury Daily Treasury Real Yield Curve Rates for 5Y, 7Y, 10Y and 30Y; 2Y real uses TIPSPY02 from the Federal Reserve Gürkaynak-Sack-Wright TIPS yield curve model. Implied inflation is calculated with Fisher: (1+nominal)/(1+real)-1.",
        "series": {
            "nominal": "daily_treasury_yield_curve",
            "real": {
                **REAL_MODEL_SERIES,
                "5Y": "daily_treasury_real_yield_curve",
                "7Y": "daily_treasury_real_yield_curve",
                "10Y": "daily_treasury_real_yield_curve",
                "30Y": "daily_treasury_real_yield_curve",
            },
            "inflation": "calculated_fisher",
        },
        "curves": current["curves"],
        "comparisons": {
            "current": {"label": "Atual", **current},
            "1m": {"label": "1 mes atras", **one_month},
            "3m": {"label": "3 meses atras", **three_months},
            "1y": {"label": "1 ano atras", **one_year},
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
            print(f"WARNING: could not refresh US yield curves: {exc}")
            print(f"Keeping existing {OUTPUT_PATH}")
        raise

