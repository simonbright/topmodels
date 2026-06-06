"""Google Trends relative search interest via pytrends (unofficial, brittle)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from topmodels.config import PipelineConfig
from topmodels.connectors.base import BaseConnector
from topmodels.models import SignalRecord, VehicleKey


class TrendsConnector(BaseConnector):
    name = "trends"

    def __init__(self, config: PipelineConfig, *, refresh: bool = False) -> None:
        self.config = config
        self.refresh = refresh
        self.cache_dir = config.cache_path / "trends"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, keyword: str) -> Path:
        safe = keyword.replace(" ", "_").replace("/", "-")[:80]
        return self.cache_dir / f"{safe}.json"

    def _interest_for_keyword(self, keyword: str) -> float | None:
        cache_file = self._cache_path(keyword)
        if cache_file.exists() and not self.refresh:
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
            return payload.get("mean_interest")

        try:
            from pytrends.request import TrendReq
        except ImportError:
            return None

        try:
            pytrends = TrendReq(hl="en-US", tz=360)
            pytrends.build_payload(
                [keyword],
                cat=0,
                timeframe=self.config.trends.timeframe,
                geo=self.config.trends.geo,
            )
            frame = pytrends.interest_over_time()
            if frame is None or frame.empty or keyword not in frame.columns:
                mean = 0.0
            else:
                mean = float(frame[keyword].mean())
            cache_file.write_text(
                json.dumps(
                    {
                        "keyword": keyword,
                        "mean_interest": mean,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "source": "Google Trends via pytrends (unofficial)",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            time.sleep(self.config.trends.request_pause_sec)
            return mean
        except Exception:  # noqa: BLE001 — pytrends is brittle; missing signal = 0
            cache_file.write_text(
                json.dumps(
                    {
                        "keyword": keyword,
                        "mean_interest": None,
                        "error": "pytrends request failed",
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            return None

    def fetch(self, models: list[VehicleKey] | None = None) -> list[SignalRecord]:
        if not models:
            return []
        now = datetime.now(timezone.utc)
        records: list[SignalRecord] = []
        for vehicle in models:
            keyword = f"used {vehicle.year} {vehicle.make.title()} {vehicle.model.title()}"
            interest = self._interest_for_keyword(keyword)
            if interest is None:
                continue
            records.append(
                SignalRecord.from_vehicle(
                    vehicle,
                    signal="search_interest",
                    value=float(interest),
                    source="Google Trends (pytrends, unofficial)",
                    as_of=now,
                    metadata={"keyword": keyword, "geo": self.config.trends.geo},
                )
            )
        return records
