"""Market assets retain missing observations and independent chronological windows."""

import pandas as pd
import pytest

from agentevolver.data.factor_mining import FactorMarketDataset


def test_panel_parquet_roundtrip_keeps_missing_bars(tmp_path):
    panel = FactorMarketDataset.synthetic(bars=400, assets=3)
    records = panel.records()
    timestamp = records.timestamp.iloc[30]
    records = records[records.timestamp != timestamp]  # missing across the entire universe
    restored = FactorMarketDataset.from_records(records)
    assert restored.fields["close"].loc[timestamp].isna().all()
    FactorMarketDataset.save(restored, tmp_path / "market")
    loaded = FactorMarketDataset.load(tmp_path / "market")
    pd.testing.assert_frame_equal(loaded.fields["close"], restored.fields["close"])
    with pytest.raises(FileExistsError):
        FactorMarketDataset.save(restored, tmp_path / "market")


def test_bad_rows_and_absent_assets_fail_before_mining():
    records = FactorMarketDataset.synthetic(bars=100, assets=2).records()
    with pytest.raises(ValueError, match="duplicate"):
        FactorMarketDataset.from_records(pd.concat([records, records.iloc[:1]]))
    with pytest.raises(ValueError, match="absent"):
        FactorMarketDataset.from_records(records, symbols=["SYNTH0", "unknown"])
    records.loc[0, "low"] = 10000
    with pytest.raises(ValueError, match="OHLC"):
        FactorMarketDataset.from_records(records)


def test_bundle_is_disjoint_and_fingerprinted(tmp_path):
    panel = FactorMarketDataset.synthetic(bars=800, assets=3)
    path = tmp_path / "bundle"
    manifest = FactorMarketDataset.bundle(panel, path, gap=7)
    parts = {s: FactorMarketDataset.load(path / s) for s in ("train", "valid", "test")}
    assert parts["valid"].index[0] - parts["train"].index[-1] == pd.Timedelta(hours=8)
    assert parts["test"].index[0] > parts["valid"].index[-1]
    assert not set(parts["train"].index) & set(parts["test"].index)
    assert manifest["sha256"] == FactorMarketDataset.fingerprint(path)
    assert FactorMarketDataset.fingerprint(path / "train") != manifest["sha256"]


def test_observed_trading_calendar_does_not_invent_weekend_bars():
    panel = FactorMarketDataset.synthetic(bars=3, assets=2)
    dates = pd.bdate_range("2020-01-03", periods=3, tz="UTC")
    for frame in panel.fields.values():
        frame.index = dates
    records = panel.records()
    records = records[~((records.symbol == "SYNTH1") & (records.timestamp == dates[1]))]
    loaded = FactorMarketDataset.from_records(records, frequency="observed")
    assert len(loaded.index) == 3
    assert pd.isna(loaded.fields["close"].loc[dates[1], "SYNTH1"])
    assert loaded.index[1].dayofweek == 0  # Monday follows Friday, not a fake Saturday


@pytest.mark.asyncio
async def test_market_adapter_uses_data_manager_local_assets(tmp_path):
    from agentevolver.data import DataManager

    records = FactorMarketDataset.synthetic(bars=80, assets=2).records()
    records["timestamp"] = records.timestamp.astype(str)
    manager = DataManager(base_dir=str(tmp_path))
    saved = await manager("dataset_save", {"repo": "ohlcv", "target": "local",
                                           "records": records.to_dict("records")})
    assert saved.success
    panel = await FactorMarketDataset.from_dataset("ohlcv", source="local", base_dir=str(tmp_path))
    assert panel.symbols == ["SYNTH0", "SYNTH1"]
    assert len(panel.index) == 80
