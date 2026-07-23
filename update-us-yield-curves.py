from __future__ import annotations

import csv
import json
import math
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "curve-us.json"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
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


@dataclass(frozen=True)
class Observation:
    series_id: str
    date: date
    value: float


def fetch_observations(series_id: str) -> list[Observation]:
    request = urllib.request.Request(
        FRED_CSV_URL.format(series_id=series_id),
        headers={"User-Agent": "Mozilla/5.0 general-channels-dashboard/0.1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        text = response.read().decode("utf-8")

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


def fetch_fed_tips_model_observations(columns: list[str]) -> dict[str, list[Observation]]:
    request = urllib.request.Request(
        FED_TIPS_MODEL_CSV_URL,
        headers={"User-Agent": "Mozilla/5.0 general-channels-dashboard/0.1"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        text = response.read().decode("utf-8-sig")

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

    observation_dates = (
        [observation.date for observation in nominal_obs.values()]
        + [observation.date for observation in real_obs.values()]
        + [observation.date for observation in real_model_obs.values()]
    )
    curves = {
        "nominal": round_curve(nominal),
        "real": round_curve(real),
        "inflation": round_curve(inflation),
    }

    if not all(math.isfinite(value) for curve in curves.values() for value in curve.values()):
        raise ValueError("Non-finite curve value generated")

    return {"asOf": min(observation_dates).isoformat(), "curves": curves}


def main() -> None:
    nominal_series = {maturity: fetch_observations(series) for maturity, series in NOMINAL_SERIES.items()}
    real_series = {maturity: fetch_observations(series) for maturity, series in REAL_SERIES.items()}
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
        "updatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "FRED / Federal Reserve H.15 / Fed GSW TIPS model",
        "methodology": "Nominal yields use DGS2, DGS5, DGS7, DGS10 and DGS30 from FRED/H.15. Real yields use DFII5, DFII7, DFII10 and DFII30 from FRED/H.15; 2Y real uses TIPSPY02 from the Federal Reserve Gürkaynak-Sack-Wright TIPS yield curve model. Implied inflation is calculated with Fisher: (1+nominal)/(1+real)-1.",
        "series": {
            "nominal": NOMINAL_SERIES,
            "real": {**REAL_MODEL_SERIES, **REAL_SERIES},
            "inflation": "calculated_fisher",
        },
        "curves": current["curves"],
        "comparisons": {
            "current": {"label": "Atual", **current},
            "1m": {"label": "1 mês atrás", **one_month},
            "3m": {"label": "3 meses atrás", **three_months},
            "1y": {"label": "1 ano atrás", **one_year},
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
        else:
            raise
