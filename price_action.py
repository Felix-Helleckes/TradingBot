# Minimal price-action helpers for bar pattern detection
"""
Price-Action Pattern Helpers
============================
Lightweight bar-pattern detection for adding candlestick context to signals.

Functions
---------
``wick_ratio(candle)``
    Ratio of total wick length to body size.  A high ratio signals
    indecision or a potential reversal (e.g. doji, hammer, shooting star).

``two_bar_pattern(prev, cur)``
    Detects classic two-bar reversals:

    - ``'BULL_ENGULF'`` — current bullish bar fully engulfs prior bearish bar
    - ``'BEAR_ENGULF'`` — current bearish bar fully engulfs prior bullish bar
    - ``'NONE'``        — no pattern detected

``three_bar_pattern(bars)``
    Detects a two-bar squeeze followed by a large breakout candle:

    - ``'BREAKOUT_UP'``   — squeeze then big bullish bar
    - ``'BREAKOUT_DOWN'`` — squeeze then big bearish bar
    - ``'NONE'``          — no pattern

Each function accepts candles as ``(open, high, low, close)`` tuples.

Note: these helpers are importable by ``analysis.py`` or custom strategies
but are **not** wired into the live signal pipeline by default.
"""

from typing import List, Tuple


def wick_ratio(candle: Tuple[float,float,float,float]) -> float:
    """Return the wick-to-body ratio for a single candle.

    A ratio > 2 suggests indecision or a potential reversal.
    Returns 0.0 for doji candles (zero-body) to avoid division by zero.
    """
    # candle = (open, high, low, close)
    o,h,l,c = candle
    body = abs(c - o)
    upper = h - max(c,o)
    lower = min(c,o) - l
    if body <= 0:
        return 0.0
    return (upper + lower) / body


def two_bar_pattern(prev: Tuple[float,float,float,float], cur: Tuple[float,float,float,float]) -> str:
    """Detect a two-bar bullish or bearish engulfing pattern.

    Returns ``'BULL_ENGULF'``, ``'BEAR_ENGULF'``, or ``'NONE'``.
    """
    # simple engulfing / continuation detection
    po,ph,pl,pc = prev
    o,h,l,c = cur
    # bullish engulf
    if c > o and pc < po and c > ph and o < pl:
        return 'BULL_ENGULF'
    if c < o and pc > po and c < pl and o > ph:
        return 'BEAR_ENGULF'
    return 'NONE'


def three_bar_pattern(bars: List[Tuple[float,float,float,float]]) -> str:
    """Detect a squeeze-then-breakout over three consecutive candles.

    A "squeeze" is two small-body bars (< 50 % of the breakout bar) followed
    by a large directional bar.  Returns ``'BREAKOUT_UP'``, ``'BREAKOUT_DOWN'``,
    or ``'NONE'``.  Requires at least 3 bars; returns ``'NONE'`` otherwise.
    """
    if len(bars) < 3:
        return 'NONE'
    p2,p1,c = bars[-3],bars[-2],bars[-1]
    # simple pattern: two small bars then big breakout
    b2 = abs(p2[3]-p2[0])
    b1 = abs(p1[3]-p1[0])
    b0 = abs(c[3]-c[0])
    if b2 < b0*0.5 and b1 < b0*0.5 and c[3] > c[0]:
        return 'BREAKOUT_UP'
    if b2 < b0*0.5 and b1 < b0*0.5 and c[3] < c[0]:
        return 'BREAKOUT_DOWN'
    return 'NONE'
