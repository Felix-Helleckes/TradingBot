#!/usr/bin/env python3
"""Watch expanded grid summary and apply best config automatically.

Behavior:
 - Poll for reports/backtest_full_grid_expanded_summary.json
 - When file exists and contains a non-null 'finished_at', read 'best'
 - Create branch feature/apply-best-grid-<ts>, backup config, apply best params to config.toml,
   commit on the new branch, push to origin, and write a small summary to reports/auto_apply_best.log
 - Does NOT merge to main. Creates branch + pushes only.
"""
import datetime
import json
import os
import subprocess
import time

import toml

REPO = "/home/felix/tradingbot"
SUMMARY = os.path.join(REPO, "reports", "backtest_full_grid_expanded_summary.json")
RESULTS_JSONL = os.path.join(REPO, "reports", "backtest_full_grid_expanded_results.jsonl")
CFG = os.path.join(REPO, "config.toml")
LOG = os.path.join(REPO, "reports", "auto_apply_best.log")


def log(msg):
    ts = datetime.datetime.utcnow().isoformat()
    with open(LOG, "a") as f:
        f.write(f"{ts} {msg}\n")
    print(msg)


log("auto_apply_best watcher started")
# Poll loop
while True:
    try:
        if os.path.exists(SUMMARY) and os.path.getsize(SUMMARY) > 0:
            try:
                s = json.load(open(SUMMARY, "r"))
            except Exception:
                time.sleep(5)
                continue
            if s.get("finished_at"):
                log("Summary finished detected; reading best result")
                best = s.get("best") or {}
                # Fallback: if best is empty, scan results JSONL
                if not best:
                    if os.path.exists(RESULTS_JSONL):
                        best = None
                        with open(RESULTS_JSONL, "r") as f:
                            for ln in f:
                                try:
                                    o = json.loads(ln)
                                except Exception:
                                    continue
                                tr = o.get("total_return_pct")
                                if tr is None:
                                    continue
                                if (
                                    best is None
                                    or tr > best.get("total_return_pct")
                                    or (
                                        tr == best.get("total_return_pct")
                                        and (o.get("max_drawdown_pct") or 999) < (best.get("max_drawdown_pct") or 999)
                                    )
                                ):
                                    best = o
                if not best:
                    log("No best found; aborting auto-apply")
                    break
                # Prepare branch
                ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M")
                branch = f"feature/apply-best-grid-{ts}"
                log(f"Creating branch {branch}")
                # git operations
                try:
                    subprocess.check_call(["git", "-C", REPO, "checkout", "-b", branch])
                except subprocess.CalledProcessError as e:
                    log("git checkout -b failed: " + str(e))
                    break
                # backup config
                bak = CFG + ".autoapply.bak"
                try:
                    subprocess.check_call(["cp", CFG, bak])
                except Exception as e:
                    log("backup failed: " + str(e))
                # load config
                cfg = toml.load(CFG)
                rm = cfg.setdefault("risk_management", {})
                # apply keys if present in best
                if "min_net" in best:
                    rm["min_net_sell_profit_pct"] = float(best.get("min_net"))
                if "min_reentry" in best:
                    rm["min_reentry_profit_pct"] = float(best.get("min_reentry"))
                if "take_profit_percent" in best:
                    rm["take_profit_percent"] = float(best.get("take_profit_percent"))
                if "sell_fee_buffer_percent" in best:
                    rm["sell_fee_buffer_percent"] = float(best.get("sell_fee_buffer_percent"))
                if "atr_multiplier" in best:
                    rm["atr_multiplier"] = float(best.get("atr_multiplier"))
                if "allocation_per_trade_percent" in best:
                    cfg.setdefault("bot_settings", {})
                    cfg["bot_settings"]["allocation_per_trade_percent"] = float(
                        best.get("allocation_per_trade_percent")
                    )
                # write config
                toml.dump(cfg, open(CFG, "w"))
                # git add/commit/push
                try:
                    subprocess.check_call(["git", "-C", REPO, "add", "config.toml"])
                    msg = f"tuning(apply-grid): apply best grid result (automatic) {datetime.datetime.utcnow().isoformat()}"
                    subprocess.check_call(["git", "-C", REPO, "commit", "-m", msg])
                    subprocess.check_call(["git", "-C", REPO, "push", "--set-upstream", "origin", branch])
                    pr_url = f"https://github.com/Felix-Helleckes/TradingBot/pull/new/{branch}"
                    log(f"Applied best config and pushed branch {branch}; PR: {pr_url}")
                except subprocess.CalledProcessError as e:
                    log("git push/commit failed: " + str(e))
                break
    except Exception as e:
        log("Watcher error: " + str(e))
    time.sleep(10)

log("auto_apply_best watcher exiting")
