"""Unit tests for the connection-state helpers (no Azure SDK needed)."""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lifecycle as lc  # noqa: E402

UTC = datetime.timezone.utc
T0 = datetime.datetime(2026, 11, 21, 18, 0, tzinfo=UTC)


def event_data(device="ems-sim-01", seq="000000000000000001D4132452F67CE200000002000000000000000000000001", module=None):
    data = {"deviceId": device, "hubName": "iot-ems1000-stg",
            "deviceConnectionStateEventInfo": {"sequenceNumber": seq}}
    if module:
        data["moduleId"] = module
    return data


def test_connected_and_disconnected_parse():
    on = lc.parse_event(lc.CONNECTED, "devices/ems-sim-01", event_data(), T0)
    off = lc.parse_event(lc.DISCONNECTED, "devices/ems-sim-01", event_data(), "2026-11-21T18:05:00.1234567Z")
    assert on["online"] is True and on["device_id"] == "ems-sim-01" and on["time"] == T0
    assert off["online"] is False
    assert off["time"] == datetime.datetime(2026, 11, 21, 18, 5, 0, 123456, tzinfo=UTC)


def test_other_events_and_modules_ignored():
    assert lc.parse_event("Microsoft.Devices.DeviceCreated", "devices/x", event_data(), T0) is None
    assert lc.parse_event(lc.CONNECTED, "devices/x/modules/m", event_data(module="m"), T0) is None


def test_device_id_falls_back_to_subject():
    info = lc.parse_event(lc.CONNECTED, "devices/ems-sim-02", {}, T0)
    assert info["device_id"] == "ems-sim-02" and info["seq"] == ""


def test_sequence_ordering():
    older = "000000000000000001D4132452F67CE200000002000000000000000000000001"
    newer = "000000000000000001D4132452F67CE200000002000000000000000000000002"
    assert lc.is_newer(newer, older)
    assert not lc.is_newer(older, newer)
    assert not lc.is_newer(older, older)
    assert lc.is_newer(older, None) and lc.is_newer("", newer)


def test_status_patch_touches_only_connection_fields():
    info = lc.parse_event(lc.DISCONNECTED, "devices/d1", event_data("d1", "05"), T0)
    patch = lc.status_patch("d1", info)
    assert set(patch) == {"PartitionKey", "RowKey", "connection_status", "last_status_change", "connection_seq"}
    assert patch["connection_status"] == "Offline" and patch["RowKey"] == "latest"


def test_grace_period():
    since = T0.isoformat()
    assert not lc.is_due(since, T0 + datetime.timedelta(seconds=119), 2)
    assert lc.is_due(since, T0 + datetime.timedelta(minutes=2), 2)
    assert not lc.is_due(None, T0, 2)


def test_telemetry_after_disconnect_means_back():
    since = T0.isoformat()
    assert lc.telemetry_after(T0 + datetime.timedelta(seconds=1), since)
    assert not lc.telemetry_after(T0 - datetime.timedelta(seconds=90), since)   # last message before the drop
    assert not lc.telemetry_after(None, since)


def test_alert_row_matches_webhook_shape():
    row = lc.offline_alert_row("d1", T0.isoformat(), 2)
    assert row["PartitionKey"] == "d1" and row["RowKey"] == f"offline-{int(T0.timestamp() * 1000)}"
    assert row["condition"] == "Fired" and row["rule"] == "device-offline"
    assert "2 minutes" in row["description"] and row["fired_at"] == T0.isoformat()
    res = lc.resolved_patch("d1", row["RowKey"], T0 + datetime.timedelta(minutes=5))
    assert res["condition"] == "Resolved" and res["resolved_at"].startswith("2026-11-21T18:05")


def test_purge_only_old_resolved():
    cutoff = T0
    assert lc.purge_due({"condition": "Resolved", "resolved_at": "2026-10-01T00:00:00Z"}, cutoff)
    assert not lc.purge_due({"condition": "Resolved", "resolved_at": "2026-11-21T19:00:00Z"}, cutoff)
    assert not lc.purge_due({"condition": "Fired", "fired_at": "2026-01-01T00:00:00Z"}, cutoff)
    assert lc.purge_due({"condition": "Resolved", "resolved_at": "2026-11-01T10:00:00.1234567Z"}, cutoff)
