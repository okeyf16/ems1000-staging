import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

KEY = "test-key-1"
KEY2 = "test-key-2"
TOKEN = "webhook-token"


class FakeEntity(dict):
    def __init__(self, data, timestamp=None):
        super().__init__(data)
        self.metadata = {"timestamp": timestamp}


class FakeTable:
    """Just enough of azure.data.tables.TableClient for the API."""

    def __init__(self):
        self.rows = {}

    def get_entity(self, partition_key, row_key):
        try:
            return self.rows[(partition_key, row_key)]
        except KeyError:
            raise LookupError("not found")

    def upsert_entity(self, entity, mode=None):
        key = (entity["PartitionKey"], entity["RowKey"])
        merged = dict(self.rows.get(key, {}))
        merged.update(entity)
        self.rows[key] = FakeEntity(merged)

    def list_entities(self):
        return list(self.rows.values())

    def query_entities(self, query_filter, parameters, results_per_page=None):
        pk = parameters["pk"]
        rows = [r for (p, _), r in self.rows.items() if p == pk]
        return sorted(rows, key=lambda r: r.get("RowKey", ""))


class FakeRegistry:
    def __init__(self):
        self.reply = SimpleNamespace(status=200, payload={})
        self.error = None
        self.calls = []

    def invoke_device_method(self, device_id, method):
        self.calls.append((device_id, method.method_name))
        if self.error:
            raise self.error
        return self.reply


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("API_KEYS", f"{KEY},{KEY2}")
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", TOKEN)
    import main
    from fastapi.testclient import TestClient

    state, history, alerts, registry = FakeTable(), FakeTable(), FakeTable(), FakeRegistry()
    monkeypatch.setattr(main, "table_client", state)
    monkeypatch.setattr(main, "history_client", history)
    monkeypatch.setattr(main, "alerts_client", alerts)
    monkeypatch.setattr(main, "iot_registry_manager", registry)
    access_table = FakeTable()
    from access import AccessRecorder
    monkeypatch.setattr(main, "recorder", AccessRecorder(access_table, flush_s=0))
    monkeypatch.delenv("ENTRA_SIGNIN", raising=False)
    client = TestClient(main.app)
    return SimpleNamespace(client=client, state=state, history=history, alerts=alerts, access=access_table,
                           registry=registry, main=main, headers={"X-API-Key": KEY})


def stored_row(device_id="ems-sim-01", **over):
    row = {"PartitionKey": device_id, "RowKey": "latest", "sv": 1, "id": device_id,
           "ts": 1795030205000, "boot": 7, "seq": 120, "v": 229.8, "i": 6.8, "f": 50.4,
           "fuel": 64.0, "oil": 0, "t": 71.5, "vb": 12.45, "flt": 0, "run": 2, "rssi": -71,
           "enqueued_time": datetime(2026, 11, 18, 22, 10, 5, tzinfo=timezone.utc)}
    row.update(over)
    return FakeEntity(row)
