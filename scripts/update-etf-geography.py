from __future__ import annotations

import html
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "etf-geography.json"


SOURCE_CONFIG = {
    "VEA": {
        "source": "Vanguard",
        "url": "https://investor.vanguard.com/investment-products/etfs/profile/vea",
        "weights": [["Japão", 21.0], ["Reino Unido", 12.0], ["Canadá", 9.0], ["França", 8.0], ["Suíça", 7.5], ["Alemanha", 7.0]],
    },
    "IEFA": {
        "source": "iShares",
        "url": "https://www.ishares.com/us/products/244049/ishares-core-msci-eafe-etf",
        "weights": [["Japão", 25.7], ["Reino Unido", 13.9], ["França", 9.0], ["Suíça", 8.7], ["Alemanha", 8.0], ["Austrália", 7.1]],
    },
    "IWDA": {
        "source": "iShares UCITS",
        "url": "https://www.ishares.com/uk/individual/en/products/251882/ishares-msci-world-ucits-etf-acc-fund",
        "weights": [["EUA", 72.0], ["Japão", 5.5], ["Reino Unido", 3.5], ["Canadá", 3.0], ["França", 2.8], ["Suíça", 2.6]],
    },
    "SWDA": {
        "source": "iShares UCITS",
        "url": "https://www.ishares.com/uk/individual/en/products/251882/ishares-msci-world-ucits-etf-acc-fund",
        "weights": [["EUA", 72.0], ["Japão", 5.5], ["Reino Unido", 3.5], ["Canadá", 3.0], ["França", 2.8], ["Suíça", 2.6]],
    },
    "VGK": {
        "source": "Vanguard",
        "url": "https://investor.vanguard.com/investment-products/etfs/profile/vgk",
        "weights": [["Reino Unido", 23.0], ["França", 17.0], ["Suíça", 15.0], ["Alemanha", 13.0], ["Holanda", 7.0], ["Suécia", 6.0]],
    },
    "IEUR": {
        "source": "iShares",
        "url": "https://www.ishares.com/us/products/264617/ishares-core-msci-europe-etf",
        "weights": [["Reino Unido", 23.07], ["França", 14.41], ["Suíça", 14.26], ["Alemanha", 12.87], ["Holanda", 8.53], ["Suécia", 5.8]],
    },
    "FEZ": {
        "source": "SPDR",
        "url": "https://www.ssga.com/us/en/intermediary/etfs/spdr-euro-stoxx-50-etf-fez",
        "weights": [["França", 34.0], ["Alemanha", 31.0], ["Holanda", 14.0], ["Espanha", 8.0], ["Itália", 7.0], ["Bélgica", 3.0]],
    },
    "IMEU": {
        "source": "iShares UCITS",
        "url": "https://www.ishares.com/uk/individual/en/products/251862/ishares-msci-europe-ucits-etf-inc-fund",
        "weights": [["Reino Unido", 22.0], ["França", 18.0], ["Suíça", 16.2], ["Alemanha", 13.8], ["Holanda", 7.2], ["Dinamarca", 5.1]],
    },
    "VWO": {
        "source": "Vanguard",
        "url": "https://investor.vanguard.com/investment-products/etfs/profile/vwo",
        "weights": [["Taiwan", 29.0], ["China", 29.0], ["Índia", 19.0], ["Brasil", 4.0], ["Arábia Saudita", 3.0], ["África do Sul", 3.0]],
    },
    "IEMG": {
        "source": "iShares",
        "url": "https://www.ishares.com/us/products/244050/ishares-core-msci-emerging-markets-etf",
        "weights": [["Taiwan", 26.36], ["Coreia do Sul", 19.7], ["China", 19.42], ["Índia", 12.96], ["Brasil", 4.03], ["África do Sul", 3.11]],
    },
    "EIMI": {
        "source": "iShares UCITS",
        "url": "https://www.ishares.com/uk/individual/en/products/264659/ishares-core-msci-em-imi-ucits-etf",
        "weights": [["Taiwan", 28.0], ["Coreia do Sul", 21.5], ["China", 18.0], ["Índia", 12.0], ["Brasil", 3.7], ["África do Sul", 3.0]],
    },
    "EMXC": {
        "source": "iShares",
        "url": "https://www.ishares.com/us/products/288504/ishares-msci-emerging-markets-ex-china-etf",
        "weights": [["Taiwan", 33.85], ["Coreia do Sul", 25.19], ["Índia", 15.05], ["Brasil", 5.28], ["África do Sul", 3.85], ["Arábia Saudita", 3.26]],
    },
    "EXCH": {
        "source": "iShares UCITS",
        "url": "https://www.ishares.com/ch/individual/en/products/315592/",
        "weights": [["Taiwan", 34.5], ["Coreia do Sul", 28.0], ["Índia", 13.5], ["Brasil", 4.5], ["África do Sul", 3.5], ["Arábia Saudita", 3.0]],
    },
}


COUNTRY_TRANSLATION = {
    "United States": "EUA",
    "United Kingdom": "Reino Unido",
    "Japan": "Japão",
    "France": "França",
    "Switzerland": "Suíça",
    "Germany": "Alemanha",
    "Netherlands": "Holanda",
    "Sweden": "Suécia",
    "Spain": "Espanha",
    "Italy": "Itália",
    "Belgium": "Bélgica",
    "Denmark": "Dinamarca",
    "Australia": "Austrália",
    "Canada": "Canadá",
    "Korea (South)": "Coreia do Sul",
    "South Korea": "Coreia do Sul",
    "India": "Índia",
    "China": "China",
    "Brazil": "Brasil",
    "South Africa": "África do Sul",
    "Saudi Arabia": "Arábia Saudita",
    "Taiwan": "Taiwan",
}


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 general-channels-dashboard/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="ignore")


def clean_text(raw: str) -> str:
    raw = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.I)
    raw = re.sub(r"<style[\s\S]*?</style>", " ", raw, flags=re.I)
    text = re.sub(r"<[^>]+>", "\n", raw)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text


def parse_geography_weights(raw: str) -> list[list] | None:
    text = clean_text(raw)
    geography_start = text.lower().find("geography")
    if geography_start < 0:
        return None
    section = text[geography_start : geography_start + 5000]
    rows = []
    for country, value in re.findall(r"([A-Za-z][A-Za-z \(\)\/&.-]{2,40})\s*\|\s*([0-9]+(?:\.[0-9]+)?)%", section):
        country = country.strip()
        if country.lower() in {"type", "fund", "other", "sector", "geography"}:
            continue
        rows.append([COUNTRY_TRANSLATION.get(country, country), round(float(value), 2)])
    return rows[:6] if len(rows) >= 3 else None


def main() -> None:
    instruments = {}
    for ticker, config in SOURCE_CONFIG.items():
        status = "seed_fallback"
        weights = config["weights"]
        error = None
        try:
            parsed = parse_geography_weights(fetch_text(config["url"]))
            if parsed:
                weights = parsed
                status = "source_parsed"
        except Exception as exc:
            error = str(exc)

        instruments[ticker] = {
            "weights": weights,
            "source": config["source"],
            "sourceUrl": config["url"],
            "status": status,
            "error": error,
        }

    payload = {
        "version": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "methodology": "Country weights are refreshed monthly from public provider pages when parsing succeeds. If provider layout blocks parsing, the script preserves dashboard seed weights as an explicit fallback.",
        "instruments": instruments,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
