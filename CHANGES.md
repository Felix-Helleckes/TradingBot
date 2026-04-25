# CHANGES

- 2026-04-25: Notifications disabled — `core/notifier.py` now a no-op and direct Telegram calls in scripts suppressed. This prevents accidental outbound messages from CI or deployed instances. To re-enable, restore notifier logic and set `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` in your environment.
