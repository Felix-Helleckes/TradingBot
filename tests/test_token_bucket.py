import os
import time
import tempfile
from core import token_bucket


def test_token_bucket_basic():
    td = tempfile.mkdtemp()
    dbp = os.path.join(td, 'tb.db')
    token_bucket.init_db(dbp)
    token_bucket.create_bucket('tb_test', capacity=2.0, refill_rate_per_sec=1.0)
    # consume two tokens immediately
    assert token_bucket.try_consume('tb_test', amount=1.0, block=False)
    assert token_bucket.try_consume('tb_test', amount=1.0, block=False)
    # third should fail without waiting
    assert not token_bucket.try_consume('tb_test', amount=1.0, block=False)
    # after ~1s one token refilled
    time.sleep(1.1)
    assert token_bucket.try_consume('tb_test', amount=1.0, block=False)
