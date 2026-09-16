"""Market-data assets for factor research; evaluation policy lives in benchmark.

Canonical on-disk format is Parquet partitioned by symbol. The in-memory format
is one timestamp × asset DataFrame per field. Missing bars are never filled.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

FIELDS = ("open", "high", "low", "close", "volume")


@dataclass
class MarketPanel:
    fields: dict[str, pd.DataFrame]
    frequency: str

    @property
    def index(self):
        return self.fields["close"].index

    @property
    def symbols(self):
        return list(self.fields["close"].columns)

    def slice(self, start: int, end: int) -> "MarketPanel":
        return MarketPanel({k: v.iloc[start:end].copy() for k, v in self.fields.items()},
                           self.frequency)

    def records(self) -> pd.DataFrame:
        # Explicit flattening preserves missing bars on pandas 2.x and 3.x alike.
        return pd.DataFrame({"timestamp": self.index.repeat(len(self.symbols)),
                             "symbol": np.tile(self.symbols, len(self.index)),
                             **{k: v.to_numpy().reshape(-1) for k, v in self.fields.items()}})


class FactorMarketDataset:
    """Validate, import, split and persist OHLCV through the data module."""

    @staticmethod
    def from_records(records, *, frequency: str = "1h", symbols=None) -> MarketPanel:
        frame = pd.DataFrame(records).copy()
        required = {"timestamp", "symbol", *FIELDS}
        if not required.issubset(frame.columns):
            raise ValueError(f"market data missing columns: {sorted(required - set(frame.columns))}")
        frame = frame[list(required)]
        if frame["symbol"].isna().any():
            raise ValueError("symbol cannot be null")
        frame["symbol"] = frame.symbol.astype(str)
        if not frame.symbol.map(lambda s: bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", s))).all():
            raise ValueError("symbols must be safe identifiers (letters, digits, dot, dash, underscore)")
        if pd.api.types.is_numeric_dtype(frame.timestamp):
            raise ValueError("timestamp must be ISO datetime, not an ambiguous numeric epoch")
        frame["timestamp"] = pd.to_datetime(frame.timestamp, utc=True, errors="raise")
        if frame.timestamp.isna().any() or frame.duplicated(["timestamp", "symbol"]).any():
            raise ValueError("null timestamp or duplicate timestamp/symbol")
        for field in FIELDS:
            frame[field] = pd.to_numeric(frame[field], errors="raise").astype(float)
        values = frame[list(FIELDS)]
        if np.isinf(values.to_numpy()).any():
            raise ValueError("infinite market values")
        if (values[list(FIELDS[:4])] <= 0).any().any() or (frame.volume < 0).any():
            raise ValueError("prices must be positive; volume nonnegative")
        if ((frame.high < frame[["open", "close", "low"]].max(axis=1)) |
                (frame.low > frame[["open", "close", "high"]].min(axis=1))).any():
            raise ValueError("inconsistent OHLC bounds")
        scope = sorted(set(frame.symbol)) if symbols is None else list(symbols)
        if len(scope) < 2 or len(set(scope)) != len(scope):
            raise ValueError("research requires at least two distinct assets")
        if set(scope) - set(frame.symbol):
            raise ValueError("requested assets absent from data")
        frame = frame[frame.symbol.isin(scope)]
        if frame.empty:
            raise ValueError("empty market dataset")
        if frequency == "observed":
            # Explicit source-calendar mode for session-based markets: weekends
            # and exchange holidays are not invented as missing trading bars.
            index = pd.DatetimeIndex(frame.timestamp.unique()).sort_values()
            count = len(index)
        else:
            step = pd.tseries.frequencies.to_offset(frequency)
            if step.nanos <= 0:
                raise ValueError("frequency must be a positive fixed duration or 'observed'")
            count = int((frame.timestamp.max() - frame.timestamp.min()) / pd.Timedelta(step)) + 1
        if count > 2_000_000 or count * len(scope) > 20_000_000:
            raise ValueError("panel too large; select a smaller asset/time scope")
        if frequency != "observed":
            index = pd.date_range(frame.timestamp.min(), frame.timestamp.max(), freq=step)
        if not frame.timestamp.isin(index).all():
            raise ValueError("timestamps do not align to the configured frequency")
        return MarketPanel({key: frame.pivot(index="timestamp", columns="symbol", values=key)
                            .reindex(index=index, columns=scope) for key in FIELDS}, frequency)

    @classmethod
    def load(cls, path: str | Path, *, frequency: str = "1h", symbols=None) -> MarketPanel:
        path = Path(path)
        if path.suffix.lower() == ".csv":
            frame = pd.read_csv(path)
        else:
            frame = pd.read_parquet(path, columns=["timestamp", "symbol", *FIELDS])
        return cls.from_records(frame, frequency=frequency, symbols=symbols)

    @classmethod
    async def from_dataset(cls, repo: str, *, source="hub", base_dir=None,
                           split="train", frequency="1h", symbols=None) -> MarketPanel:
        """Use the existing DataManager for HF/local Dataset assets."""
        from agentevolver.data.server import DataManager

        response = await DataManager(base_dir=base_dir)("dataset_load", {
            "repo": repo, "source": source, "split": split,
        })
        if not response.success:
            raise ValueError(response.message)
        return cls.from_records(response.data["records"], frequency=frequency, symbols=symbols)

    @staticmethod
    def save(panel: MarketPanel, path: str | Path) -> None:
        path = Path(path)
        if path.exists():
            raise FileExistsError(f"refusing to mix dataset versions: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        panel.records().to_parquet(path, partition_cols=["symbol"], index=False)

    @staticmethod
    def split(panel: MarketPanel, train=0.6, valid=0.2, gap=5) -> dict[str, MarketPanel]:
        if not 0 < train < 1 or not 0 < valid < 1 or train + valid >= 1:
            raise ValueError("train/valid fractions must leave a nonempty test split")
        if type(gap) is not int or gap < 0:
            raise ValueError("gap must be a nonnegative integer number of bars")
        n = len(panel.index)
        a, b = int(n * train), int(n * (train + valid))
        parts = {"train": panel.slice(0, a), "valid": panel.slice(a + gap, b),
                 "test": panel.slice(b + gap, n)}
        if min(len(p.index) for p in parts.values()) < 64:
            raise ValueError("each split needs at least 64 bars after the gap")
        return parts

    @classmethod
    def bundle(cls, panel: MarketPanel, path: str | Path, **split_options) -> dict:
        path = Path(path)
        if path.exists():
            raise FileExistsError(path)
        parts = cls.split(panel, **split_options)
        for name, part in parts.items():
            cls.save(part, path / name)
        manifest = {"version": 1, "frequency": panel.frequency, "symbols": panel.symbols,
                    "splits": {k: {"rows": len(v.index), "start": str(v.index[0]),
                                   "end": str(v.index[-1])} for k, v in parts.items()}}
        manifest["sha256"] = cls.fingerprint(path)
        (path / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        return manifest

    @staticmethod
    def fingerprint(path: str | Path) -> str:
        digest = hashlib.sha256()
        root = Path(path)
        for file in sorted(root.rglob("*.parquet")):
            digest.update(str(file.relative_to(root)).encode())
            with file.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def synthetic(cls, *, bars=1800, assets=6, seed=7) -> MarketPanel:
        """A predictable toy fixture for plumbing checks, never a market performance claim."""
        rng = np.random.default_rng(seed)
        returns = rng.normal(0, 0.002, (bars, assets))
        for i in range(1, bars):
            returns[i] += 0.75 * returns[i - 1]
        close = 100 * np.exp(np.cumsum(returns, axis=0))
        opening = np.vstack([np.full((1, assets), 100.0), close[:-1]])
        index = pd.date_range("2020-01-01", periods=bars, freq="1h", tz="UTC")
        columns = [f"SYNTH{i}" for i in range(assets)]
        raw = {"open": opening, "close": close, "high": np.maximum(opening, close) * 1.001,
               "low": np.minimum(opening, close) * 0.999,
               "volume": rng.lognormal(6, 0.7, (bars, assets))}
        return MarketPanel({k: pd.DataFrame(v, index=index, columns=columns)
                            for k, v in raw.items()}, "1h")
