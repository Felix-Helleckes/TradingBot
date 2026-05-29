Dokumentation: Pfad ‚kraken_daten‘ ist jetzt ein Symlink auf /mnt/fritz_nas/Volume/kraken (erzeugt von Friday). Backtests greifen lokal auf /home/felix/kraken_daten/2026/ OHLC Daten zu.

Automatische Schritte, die ich ausgeführt habe:
- Verified NAS mount at /mnt/fritz_nas/Volume/kraken
- Created symlink: /home/felix/kraken_daten -> /mnt/fritz_nas/Volume/kraken
- Launched focused and full grid backtests in background; results saved to reports/

Hinweis:
- Kraken API rate limits appeared during grid backtests; scripts fall back to cached OHLC from the NAS when API fails.
- If du willst, kann ich die backtests priorisieren, Ergebnisse filtern und die besten Parameter vorschlagen.
