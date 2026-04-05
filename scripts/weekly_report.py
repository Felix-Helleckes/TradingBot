#!/usr/bin/env python3
"""Weekly performance report — reads NAS trade history, outputs summary to log + reports/."""
from __future__ import annotations
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import nas_paths

NAS = nas_paths()
TRADE_FILE = NAS["nas_root"] / "2026" / "trade_history" / "trades_2026.json"
REPORT_DIR = Path(__file__).resolve().parent.parent / "reports"
REPORT_DIR.mkdir(exist_ok=True)


def load_trades(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    # Kraken format: dict of trade_id → trade_dict
    if isinstance(data, dict):
        return list(data.values())
    return data


def analyse(trades: list[dict], since: datetime) -> dict:
    since_ts = since.timestamp()
    week = [t for t in trades if float(t.get("time", t.get("timestamp", 0))) >= since_ts]

    total = len(week)
    if total == 0:
        return {"trades": 0}

    buys = [t for t in week if t.get("type") == "buy"]
    sells = [t for t in week if t.get("type") == "sell"]
    fees = sum(float(t.get("fee", 0)) for t in week)
    volume_eur = sum(float(t.get("cost", 0)) for t in week)

    by_pair: dict[str, int] = defaultdict(int)
    for t in week:
        by_pair[t.get("pair", "?")] += 1

    most_traded = max(by_pair, key=by_pair.get) if by_pair else "—"

    # P&L from bot activity log (last line with AdjPnL)
    log_file = Path(__file__).resolve().parent.parent / "logs" / "bot_activity.log"
    adj_pnl = None
    if log_file.exists():
        for line in reversed(log_file.read_text(errors="ignore").splitlines()):
            if "AdjPnL:" in line:
                try:
                    adj_pnl = float(line.split("AdjPnL:")[1].split("EUR")[0].strip())
                except Exception:
                    pass
                break

    return {
        "trades": total,
        "buys": len(buys),
        "sells": len(sells),
        "volume_eur": round(volume_eur, 2),
        "fees_eur": round(fees, 4),
        "most_traded": f"{most_traded} ({by_pair[most_traded]}x)" if by_pair else "—",
        "adj_pnl": adj_pnl,
        "pairs": dict(by_pair),
    }


def format_report(stats: dict, week_start: datetime, week_end: datetime) -> str:
    lines = [
        "=" * 52,
        "  WEEKLY PERFORMANCE REPORT",
        f"  {week_start.strftime('%Y-%m-%d')} → {week_end.strftime('%Y-%m-%d')}",
        "=" * 52,
    ]
    if stats["trades"] == 0:
        lines.append("  No trades this week.")
    else:
        lines += [
            f"  Trades    : {stats['trades']}  (Buys: {stats['buys']} | Sells/Shorts: {stats['sells']})",
            f"  Volume    : {stats['volume_eur']:.2f} EUR",
            f"  Fees      : {stats['fees_eur']:.4f} EUR",
            f"  Top Pair  : {stats['most_traded']}",
        ]
        if stats["adj_pnl"] is not None:
            lines.append(f"  Adj. PnL  : {stats['adj_pnl']:+.4f} EUR (cumulative)")
        if stats["pairs"]:
            lines.append("  By Pair   :")
            for pair, count in sorted(stats["pairs"].items(), key=lambda x: -x[1]):
                lines.append(f"    {pair}: {count}x")
    lines.append("=" * 52)
    return "\n".join(lines)


def main():
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)

    trades = load_trades(TRADE_FILE)
    stats = analyse(trades, week_start)
    report = format_report(stats, week_start, now)

    print(report)

    # Save to reports/
    fname = REPORT_DIR / f"weekly_{now.strftime('%Y_%m_%d')}.txt"
    fname.write_text(report + "\n", encoding="utf-8")
    print(f"\nSaved → {fname}")


if __name__ == "__main__":
    main()
