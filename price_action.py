# Simple Price-Action helpers: detect 2/3-bar patterns on OHLC bars (o,h,l,c tuples)
# Minimal, non-exhaustive implementations used only as optional boosts/confirmations.

def two_bar_pattern(bars):
    # bars: [(o,h,l,c), (o,h,l,c)]
    try:
        b1, b2 = bars
    except Exception:
        return None
    o1,h1,l1,c1 = b1
    o2,h2,l2,c2 = b2
    if c2 > h1 and c2 > o2:
        return 'BREAKOUT_UP'
    if c2 < l1 and c2 < o2:
        return 'BREAKOUT_DOWN'
    return 'INSIDE'


def three_bar_pattern(bars):
    # bars: [(o,h,l,c), (o,h,l,c), (o,h,l,c)]
    try:
        b1,b2,b3 = bars
    except Exception:
        return None
    o1,h1,l1,c1 = b1
    o2,h2,l2,c2 = b2
    o3,h3,l3,c3 = b3
    # Simple breakout sequence: bar2 inside bar1, bar3 closes above bar1 high -> breakout
    if o2 > l1 and h2 < h1 and c3 > h1:
        return 'BREAKOUT_UP'
    if o2 > l1 and h2 < h1 and c3 < l1:
        return 'BREAKOUT_DOWN'
    # Fallback: sequential higher closes
    if c1 < c2 < c3:
        return 'RISING'
    if c1 > c2 > c3:
        return 'FALLING'
    return 'INDIFFERENT'
