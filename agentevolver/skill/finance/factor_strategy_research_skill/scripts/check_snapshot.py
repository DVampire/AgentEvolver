"""Verify a saved connector dataset before research (never downloads or changes data)."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path


def check_snapshot(path, *, sha256, symbol, start, end, calendar="XNAS",
                   bars_pointer="/result/bars", adjusted_close=None):
    import exchange_calendars as xc

    path = Path(path).resolve()
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError("Saved dataset SHA-256 differs from the connector receipt")
    value = json.loads(raw)
    if bars_pointer:
        if not bars_pointer.startswith("/"):
            raise ValueError("bars_pointer must be a JSON pointer")
        for key in bars_pointer[1:].split("/"):
            key = key.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
    if not isinstance(value, list) or not value:
        raise ValueError("No locally saved OHLCV observations")
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise ValueError("start must not follow end")
    # Calendar endpoints may be holidays; session libraries validate against their
    # first/last *session*, so constructing them at exactly Jan 1 can fail.
    cal = xc.get_calendar(calendar, start=str(first - timedelta(days=14)),
                          end=str(last + timedelta(days=14)))
    expected = [t.date().isoformat() for t in cal.sessions_in_range(start, end)]
    if not expected:
        raise ValueError("Requested interval contains no exchange sessions")
    dates = []
    for bar in value:
        day = date.fromisoformat(bar["date"]).isoformat()
        if bar["symbol"] != symbol:
            raise ValueError(f"Symbol mismatch on {day}")
        fields = [bar[k] for k in ("open", "high", "low", "close", "volume")]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in fields):
            raise ValueError(f"Non-finite/nonnumeric OHLCV on {day}")
        o, h, l, c, v = fields
        if min(o, h, l, c) <= 0 or v < 0 or not l <= min(o, c) <= max(o, c) <= h:
            raise ValueError(f"Invalid OHLCV on {day}")
        if adjusted_close:
            a = bar[adjusted_close]
            if isinstance(a, bool) or not isinstance(a, (int, float)) or not math.isfinite(a) or a <= 0:
                raise ValueError(f"Missing/invalid adjustment basis on {day}")
            ratio = a / c
            if not math.isfinite(ratio) or ratio <= 0 or any(not math.isfinite(p * ratio) for p in (o, h, l, c)):
                raise ValueError(f"Invalid adjusted-price ratio on {day}")
        dates.append(day)
    if dates != sorted(set(dates)):
        raise ValueError("Dataset dates must be unique and chronological")
    missing = sorted(set(expected) - set(dates))
    extra = sorted(set(dates) - set(expected))
    if missing or extra:
        raise ValueError(f"Session coverage mismatch: missing={missing[:10]}, unexpected={extra[:10]}")
    return {"ok": True, "artifact_path": str(path), "sha256": sha256, "bytes": len(raw),
            "symbol": symbol, "interval": "1d", "requested_start": start, "requested_end": end,
            "first_session": dates[0], "last_session": dates[-1], "rows": len(dates),
            "calendar": calendar, "calendar_library": f"exchange_calendars {xc.__version__}",
            "adjustment_field_checked": adjusted_close,
            "checks": ["disk_read", "sha256", "symbol", "finite_ohlcv", "ohlc_bounds",
                       "unique_sorted_dates", "exchange_session_coverage"],
            "qualification": "Structural acceptance only; source price/volume/action semantics require separate evidence."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    for name in ("sha256", "symbol", "start", "end"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--calendar", default="XNAS")
    parser.add_argument("--bars-pointer", default="/result/bars")
    parser.add_argument("--adjusted-close")
    args = vars(parser.parse_args())
    try:
        print(json.dumps(check_snapshot(args.pop("artifact"), **args)))
    except (ValueError, KeyError, IndexError, TypeError, OSError, ImportError) as error:
        parser.exit(1, f"Local dataset not ready: {error}\n")


if __name__ == "__main__":
    main()
