"""ApiAccess recorder: batching, daily rows, summary."""
import base64
import json

from access import AccessRecorder, Caller, entra_caller, key_caller
from conftest import FakeTable

T0 = 1_795_300_000.0     # 2026-11-22 (UTC)
PAUL = Caller("oid-paul", "Paul", "entra")


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


def test_writes_are_batched_per_caller():
    table, clock = FakeTable(), Clock()
    rec = AccessRecorder(table, clock=clock, flush_s=60)
    for _ in range(5):
        rec.record(PAUL, "GET /api/alerts")
    row = next(iter(table.rows.values()))
    assert row["calls"] == 1                     # first call written, the rest held for a minute
    clock.t += 61
    rec.record(PAUL, "GET /api/devices/x/state")
    row = next(iter(table.rows.values()))
    assert row["calls"] == 6 and row["last_endpoint"] == "GET /api/devices/x/state"


def test_summary_flushes_and_merges_days():
    table, clock = FakeTable(), Clock()
    rec = AccessRecorder(table, clock=clock, flush_s=3600)
    rec.record(PAUL, "GET /a")
    clock.t += 86400                              # next UTC day
    rec.record(PAUL, "GET /b")
    rec.record(key_caller(0), "GET /c")
    today = rec.summary(1)
    assert {u["who"]: u["calls"] for u in today} == {"Paul": 1, "API key (primary)": 1}
    both = {u["who"]: u for u in rec.summary(2)}
    assert both["Paul"]["calls"] == 2 and both["Paul"]["last_endpoint"] == "GET /b"


def test_table_trouble_never_breaks_a_call():
    class Broken(FakeTable):
        def upsert_entity(self, entity, mode=None):
            raise RuntimeError("storage down")
    rec = AccessRecorder(Broken(), flush_s=0)
    rec.record(PAUL, "GET /a")                    # no exception
    rec.record(PAUL, "GET /a")


def test_entra_caller_reads_app_service_headers(monkeypatch):
    monkeypatch.setenv("ENTRA_SIGNIN", "true")
    principal = base64.b64encode(json.dumps({"claims": [{"typ": "name", "val": "Okey F"}]}).encode()).decode()
    c = entra_caller({"X-MS-CLIENT-PRINCIPAL-ID": "a/b#c", "X-MS-CLIENT-PRINCIPAL-NAME": "okey@x.com",
                      "X-MS-CLIENT-PRINCIPAL": principal})
    assert c.who == "Okey F (okey@x.com)" and c.kind == "entra" and c.key == "a_b_c"
    assert entra_caller({}) is None
    monkeypatch.setenv("ENTRA_SIGNIN", "false")
    assert entra_caller({"X-MS-CLIENT-PRINCIPAL-ID": "x"}) is None
