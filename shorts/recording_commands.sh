#!/bin/bash
# Terminal clips for YT short
sleep 0.2; git --no-pager log --oneline -n 5
sleep 0.2; echo '--- close_everything.log (tail -n 40)' ; tail -n 40 logs/close_everything.log
sleep 0.2; echo '--- bot stdout (tail -n 40)' ; tail -n 40 logs/bot_stdout.log
sleep 0.2; echo '--- purchase_prices.json' ; cat data/purchase_prices.json
