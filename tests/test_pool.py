import threading
import time

from lfi.pool import IsolatedPool


def test_crash_and_hang_are_isolated():
    tasks = [(1, "helpers:echo", {"value": 1}), (2, "helpers:crash", {}), (3, "helpers:hang", {"seconds": 60}),
             (4, "helpers:echo", {"value": 4}), (5, "helpers:echo", {"value": 5})]
    t0 = time.monotonic()
    with IsolatedPool(2, timeout=3) as pool:
        out = dict(pool.imap_unordered(tasks))
        restarts = pool.restarts
    assert time.monotonic() - t0 < 40
    assert out[1]["value"] == 1 and out[4]["value"] == 4 and out[5]["value"] == 5
    assert out[2]["status"] == "error" and "crashed" in out[2]["error"]
    assert out[3]["status"] == "timeout"
    assert restarts >= 2


def test_cancel_stops_quickly():
    cancel = threading.Event()
    tasks = ((i, "helpers:hang", {"seconds": 30}) for i in range(10))
    with IsolatedPool(2, timeout=60) as pool:
        threading.Timer(1.0, cancel.set).start()
        t0 = time.monotonic()
        results = list(pool.imap_unordered(tasks, cancel))
    assert time.monotonic() - t0 < 15
    assert results == []
