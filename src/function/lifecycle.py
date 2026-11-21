"""Device connection state from IoT Hub Event Grid events.

Pure helpers with no Azure SDK imports, so they can be unit-tested on their own.
function_app.py does the table reads and writes.

Event Grid doesn't guarantee order and may miss events, and IoT Hub reports connection
changes at least 60 seconds apart. So:
- each event carries a sequence number; an event older than the one already stored is ignored;
- a device that sends telemetry after its last disconnect counts as back online, even if
  the DeviceConnected event never arrived.
"""
import datetime

PENDING_PARTITION = "pending"      # OfflinePending table: one row per disconnected device
OFFLINE_RULE = "device-offline"    # 'rule' of the alert rows this module creates
OFFLINE_SOURCE = "event-grid"
OFFLINE_SEVERITY = "Sev1"

CONNECTED = "Microsoft.Devices.DeviceConnected"
DISCONNECTED = "Microsoft.Devices.DeviceDisconnected"


def as_utc(value):
    """datetime or ISO string -> aware UTC datetime (None if it can't be read)."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        # Azure sometimes sends 7 fractional digits; Python accepts at most 6.
        if "." in text:
            head, rest = text.split(".", 1)
            digits = "".join(ch for ch in rest if ch.isdigit())
            tail = rest[len(digits):]
            text = f"{head}.{digits[:6]}{tail}"
        try:
            value = datetime.datetime.fromisoformat(text)
        except ValueError:
            return None
    if not isinstance(value, datetime.datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc)


def parse_event(event_type, subject, data, event_time):
    """Event Grid connection event -> {device_id, online, seq, time}, or None if it isn't
    a device-level connect/disconnect (module connections are ignored)."""
    if event_type == CONNECTED:
        online = True
    elif event_type == DISCONNECTED:
        online = False
    else:
        return None
    data = data if isinstance(data, dict) else {}
    if data.get("moduleId"):
        return None
    device_id = data.get("deviceId") or (subject or "").rstrip("/").split("/")[-1]
    if not device_id:
        return None
    info = data.get("deviceConnectionStateEventInfo") or {}
    return {
        "device_id": str(device_id),
        "online": online,
        "seq": str(info.get("sequenceNumber") or ""),
        "time": as_utc(event_time) or datetime.datetime.now(datetime.timezone.utc),
    }


def is_newer(seq, stored_seq):
    """True if this event's sequence number is after the stored one. IoT Hub's numbers are
    fixed-width hex strings, so longer-then-larger ordering is enough. Missing numbers are
    accepted (better a rare stale update than dropping every event)."""
    if not seq or not stored_seq:
        return True
    return (len(seq), seq) > (len(stored_seq), stored_seq)


def status_patch(device_id, info):
    """Merge into DeviceState/latest: connection fields only, telemetry fields untouched."""
    return {
        "PartitionKey": device_id,
        "RowKey": "latest",
        "connection_status": "Online" if info["online"] else "Offline",
        "last_status_change": info["time"].isoformat(),
        "connection_seq": info["seq"],
    }


def pending_row(device_id, since):
    return {
        "PartitionKey": PENDING_PARTITION,
        "RowKey": device_id,
        "since": since.isoformat(),
        "alerted": False,
        "alert_row": "",
    }


def is_due(since, now, grace_minutes):
    """Disconnected for at least the grace period?"""
    since = as_utc(since)
    return since is not None and now - since >= datetime.timedelta(minutes=grace_minutes)


def telemetry_after(enqueued_time, since):
    """A message enqueued after the disconnect means the device is back (the Connected
    event may have been missed)."""
    sent, since = as_utc(enqueued_time), as_utc(since)
    return sent is not None and since is not None and sent > since


def offline_alert_row(device_id, since, grace_minutes):
    """Alerts-table row in the same shape the API's webhook writes, so /api/alerts and the
    panel show it like any Azure Monitor alert."""
    since = as_utc(since)
    return {
        "PartitionKey": device_id,
        "RowKey": f"offline-{int(since.timestamp() * 1000)}",
        "rule": OFFLINE_RULE,
        "severity": OFFLINE_SEVERITY,
        "condition": "Fired",
        "fired_at": since.isoformat(),
        "description": f"Device not connected to IoT Hub for at least {grace_minutes} minutes.",
        "signal_type": "EventGrid",
        "source": OFFLINE_SOURCE,
    }


def resolved_patch(device_id, alert_row_key, when):
    return {
        "PartitionKey": device_id,
        "RowKey": alert_row_key,
        "condition": "Resolved",
        "resolved_at": as_utc(when).isoformat(),
    }


def purge_due(entity, cutoff):
    """Resolved alert older than the retention cutoff?"""
    if entity.get("condition") != "Resolved":
        return False
    resolved = as_utc(entity.get("resolved_at"))
    return resolved is not None and resolved < cutoff
