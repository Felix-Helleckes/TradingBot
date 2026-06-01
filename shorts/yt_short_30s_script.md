YT Short (30-45s) — Script & Shotlist

0–3s — Hook (text on screen + voice)
- On-screen: "Live-Bugfix in 30s: Shorts geschlossen + Persistenz repariert"
- Voice: "Wir haben den Trading-Bot gefixt — in unter einer Minute."
- SFX: schneller Tastatur-ASMR

3–12s — Problem zeigen (code/log)
- On-screen: Terminal clip (fast): grep logs -> "SHORT OPEN SUMMARY: XRPEUR 10.706572 (~12.27 EUR)"
- Voice: "Fehler: Close-Orders scheiterten, weil reduce_only fehlte und Volumina gerundet werden mussten."
- On-screen: Screenshot diff (before): close_everything_now.py ohne reduce_only

12–24s — Fix kurz erklären (code diff)
- On-screen: Code diff (green): set reduce_only='true', Volumen rounding, min-volume check
- Voice: "Fix: Orders jetzt mit reduce_only, Volumen auf 8 Dezimalstellen gerundet, Mindestvolumen geprüft — schließt zuverlässig."
- On-screen text: "Commit: 0ea57da — fix(close): make short-close reduce_only..."

24–33s — Persistenz-Problem & Fix
- On-screen: purchase_prices.json vorher: {} → nachher: 4 entries
- Voice: "Persistenz: purchase_prices.json war leer — rekonstruiert aus Logs und atomisch gespeichert. Neustart lädt jetzt die Entry-Preise."
- On-screen text: "Commit: 1ce8839 — rebuild: purchase_prices.json from logs"

33–40s — UX improvement
- On-screen: shortened log line: "purchase_prices[SOLEUR]: last_buy=71.18012 EUR | live_qty=0.2107"
- Voice: "Logs gekürzt, Phantom-Checks rate-limited auf 60s — reduziert API-Limits."
- On-screen text: "Commit: 0ea57da / 5570591 (changelog)"

40–45s — Proof & Call to action
- On-screen: Terminal: "OpenPositions => {}" + bot startup snippet: "TRADING BOT STARTED — Watching: XBTEUR, ..."
- Voice: "Alles überprüft, Shorts sind geschlossen, Persistenz intakt. Mehr Deep-Dives im Repo — Link in Beschreibung."
- End-screen: "Like, Subscribe, Follow for more dev ops & trading bot fixes"

Assets to capture (for editor)
- Terminal recordings: git log --oneline; tail -n 50 logs/close_everything.log; tail -n 40 logs/bot_stdout.log
- File diffs: git show 0ea57da -- close_everything_now.py trading_bot.py
- Screenshot: data/purchase_prices.json (before/after if available)
- Voiceover copy: use the Voice track lines above (German, punchy, 110–130 wpm)

Notes for editing
- Keep cuts fast, motion graphics minimal.
- Use keyboard-ASMR loop under voice (low volume).
- Overlay commit SHAs small bottom-left for authenticity.
