#!/bin/bash
cd /home/felix/tradingbot || exit 1
# load environment (contains TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)
if [ -f .env ]; then
  set -a; source .env; set +a
fi
# run the python summary
/usr/bin/env python3 /home/felix/tradingbot/send_update.py
