import pytest

from kraken_interface import KrakenAPI


def test_place_order_paper_mode_midprice(monkeypatch):
    api = KrakenAPI(api_key='x', api_secret='y', paper_mode=True)

    # fake orderbook response structure
    fake_ob = {
        'XXBTZEUR': {
            'bids': [["100.0", "1.0"]],
            'asks': [["102.0", "1.5"]]
        }
    }

    def fake_get_order_book(pair, count=3):
        return fake_ob

    monkeypatch.setattr(api, 'get_order_book', fake_get_order_book)

    res = api.place_order(pair='XXBTZEUR', direction='buy', volume=0.01)
    assert isinstance(res, dict)
    assert res.get('simulated') is True
    # mid of 100 and 102 is 101.0
    assert float(res.get('fill_price')) == pytest.approx(101.0)
