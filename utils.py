# Utility Functions for Kraken Trading Bot
"""
Utility Helpers
===============
Shared utilities for the Kraken trading bot.

Functions
---------
``load_config(path)``
    Load and parse ``config.toml``; raises ``FileNotFoundError`` if missing.

``validate_config(config)``
    Check that all required sections and keys are present.  Returns a bool
    so callers can warn and fall back rather than crash.

``nas_paths(cfg_path)``
    Return a dict of resolved ``pathlib.Path`` objects for NAS directories:

    - ``nas_root``  — mount point (default ``/mnt/fritz_nas/Volume/kraken``)
    - ``ohlc_2026`` — 2026 OHLC data directory
    - ``ohlc_2025`` — 2025 OHLC data directory
    - ``bot_cache`` — shared cache for pre-processed indicator data

All paths are sourced from the ``[paths]`` section of ``config.toml`` so
moving the NAS mount only requires editing one place.
"""

import toml
import logging
from pathlib import Path
from typing import Dict, Any

_DEFAULT_CFG_PATH = Path(__file__).parent / "config.toml"


def load_config(config_path: str):
    """
    Load configuration from a TOML file.

    Args:
        config_path (str): Path to the TOML configuration file.

    Returns:
        dict: Configuration dictionary.
    """
    try:
        if not Path(config_path).exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        with open(config_path, 'r') as f:
            config = toml.load(f)

        logging.info(f"Configuration loaded successfully from {config_path}")
        return config
    except Exception as e:
        logging.error(f"Error loading configuration: {e}")
        raise


def nas_paths(cfg_path: Path = _DEFAULT_CFG_PATH) -> dict:
    """Return NAS path config as a dict of Path objects.

    Keys: nas_root, ohlc_2026, ohlc_2025, bot_cache
    Falls back to sensible defaults if config is missing.
    """
    try:
        cfg = toml.load(cfg_path).get("paths", {})
    except Exception:
        cfg = {}

    root = Path(cfg.get("nas_root", "/mnt/fritz_nas/Volume/kraken"))
    return {
        "nas_root": root,
        "ohlc_2026": Path(cfg.get("nas_ohlc_2026", str(root / "2026" / "ohlc"))),
        "ohlc_2025": Path(cfg.get("nas_ohlc_2025", str(root / "2025" / "ohlcvt"))),
        "bot_cache": Path(cfg.get("nas_bot_cache", str(root / "bot_cache"))),
    }


def pct_to_frac(v: Any) -> float:
    """Normalize a fee/slippage value to a fractional form.

    Supported input forms (examples):
      - 0.0026   -> treated as fraction (returned unchanged)
      - 0.26     -> treated as percent (0.26%) and converted to 0.0026
      - 26       -> treated as percent (26%) and converted to 0.26

    Rule used:
      - If value is None or 0 -> 0.0
      - If abs(value) < 0.01 -> assume it's already a fraction
      - Otherwise assume it's a percentage and divide by 100

    This mirrors existing backtester normalization and keeps backward
    compatibility with config values like 0.16 (meaning 0.16%).
    """
    try:
        f = float(v)
    except Exception:
        return 0.0
    if f == 0.0:
        return 0.0
    if abs(f) < 0.01:
        return f
    return f / 100.0


def apply_trade_costs(price: float, qty: float, cfg: Dict[str, Any], maker: bool = False, side: str = 'buy') -> Dict[str, float]:
    """Apply configured fees to a hypothetical trade and return cost/proceeds.

    Args:
      price: price per unit (quote currency)
      qty: quantity (base asset units)
      cfg: full config dict (reads risk_management fees)
      maker: whether to apply maker fee (True) or taker fee (False)
      side: 'buy' or 'sell' (affects sign of net result)

    Returns dict with keys:
      - fee: fee amount (quote currency)
      - gross: price * qty
      - net_cost (for buys) or net_proceeds (for sells)
    """
    try:
        rm = cfg.get('risk_management', {}) if isinstance(cfg, dict) else {}
        maker_pct = pct_to_frac(rm.get('fees_maker_percent', 0.0))
        taker_pct = pct_to_frac(rm.get('fees_taker_percent', 0.0))
        fee_pct = maker_pct if maker else taker_pct
        gross = float(price) * float(qty)
        fee_amt = gross * fee_pct
        if side.lower() == 'buy':
            net_cost = gross + fee_amt
            return {'fee': fee_amt, 'gross': gross, 'net_cost': net_cost}
        else:
            net_proceeds = gross - fee_amt
            return {'fee': fee_amt, 'gross': gross, 'net_proceeds': net_proceeds}
    except Exception:
        return {'fee': 0.0, 'gross': float(price) * float(qty), 'net_cost': float(price) * float(qty)}


def validate_config(config):
    """
    Validate that all required configuration values are present.

    Args:
        config (dict): Configuration dictionary.

    Returns:
        bool: True if valid, False otherwise.
    """
    required_sections = ['bot_settings', 'risk_management', 'logging']
    for section in required_sections:
        if section not in config:
            logging.warning(f"Missing config section: {section}")
            return False

    bot_settings = config.get('bot_settings', {})
    trade_amounts = bot_settings.get('trade_amounts', {})

    # Accept both legacy single-pair config and current multi-pair config
    has_pairs = bool(bot_settings.get('trade_pairs')) or bool(bot_settings.get('trade_pair'))
    if not has_pairs:
        logging.warning("Missing config key: bot_settings.trade_pairs (or legacy trade_pair)")
        return False

    if 'trade_amount_eur' not in trade_amounts:
        logging.warning("Missing config key: bot_settings.trade_amounts.trade_amount_eur")
        return False

    risk = config.get('risk_management', {})
    for k in ['max_drawdown_percent', 'stop_loss_percent']:
        if k not in risk:
            logging.warning(f"Missing config key: risk_management.{k}")
            return False

    logging_cfg = config.get('logging', {})
    if 'log_level' not in logging_cfg:
        logging.warning("Missing config key: logging.log_level")
        return False

    return True
