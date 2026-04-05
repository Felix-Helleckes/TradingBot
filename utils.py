# Utility Functions for Kraken Trading Bot

import toml
import logging
from pathlib import Path

_DEFAULT_CFG_PATH = Path(__file__).parent / "config.toml"


def load_config(config_path):
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
        "nas_root":  root,
        "ohlc_2026": Path(cfg.get("nas_ohlc_2026", str(root / "2026" / "ohlc"))),
        "ohlc_2025": Path(cfg.get("nas_ohlc_2025", str(root / "2025" / "ohlcvt"))),
        "bot_cache": Path(cfg.get("nas_bot_cache",  str(root / "bot_cache"))),
    }

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
