import os
import json
import logging
import datetime
import uuid
import azure.functions as func
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableServiceClient, EntityProperty, EdmType, UpdateMode
from azure.storage.blob import BlobServiceClient
from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import metrics

import lifecycle

# 1. Configure OpenTelemetry for Application Insights
# Captures dependency tracing, exceptions, and worker logs[cite: 2].
if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    configure_azure_monitor()

# 2. Setup Custom Metrics
# Allows tracking of the delay between IoT Hub and Function execution[cite: 2].
meter = metrics.get_meter_provider().get_meter("ems1000_ingest")
latency_histogram = meter.create_histogram(
    "ingest_latency_ms", 
    unit="ms", 
    description="Latency between IoT Hub enqueue and Function processing"
)

# 3. Initialize Azure Storage Clients (one service client shared by all tables)
# Using the default storage account connection string injected by Terraform.
STORAGE_CONN_STR = os.getenv("AzureWebJobsStorage")
TABLE_NAME = "DeviceState"
DEAD_LETTER_CONTAINER = "dead-letter"
# Per-message history (one row per message, newest first); purged daily by history_purge
HISTORY_TABLE_NAME = os.getenv("HISTORY_TABLE_NAME", "DeviceTelemetry")
HISTORY_RETENTION_DAYS = int(os.getenv("HISTORY_RETENTION_DAYS", "9"))
# Connection state (Event Grid) and the "Device offline" alert
ALERTS_TABLE_NAME = os.getenv("ALERTS_TABLE_NAME", "Alerts")
OFFLINE_PENDING_TABLE_NAME = os.getenv("OFFLINE_PENDING_TABLE_NAME", "OfflinePending")
OFFLINE_GRACE_MINUTES = int(os.getenv("OFFLINE_GRACE_MINUTES", "2"))
ALERTS_RETENTION_DAYS = int(os.getenv("ALERTS_RETENTION_DAYS", "30"))

try:
    table_service = TableServiceClient.from_connection_string(conn_str=STORAGE_CONN_STR)
    table_client = table_service.get_table_client(table_name=TABLE_NAME)
    history_client = table_service.get_table_client(table_name=HISTORY_TABLE_NAME)
    alerts_client = table_service.get_table_client(table_name=ALERTS_TABLE_NAME)
    offline_client = table_service.get_table_client(table_name=OFFLINE_PENDING_TABLE_NAME)
    blob_service_client = BlobServiceClient.from_connection_string(conn_str=STORAGE_CONN_STR)
    dead_letter_client = blob_service_client.get_container_client(DEAD_LETTER_CONTAINER)
except Exception as e:
    logging.error(f"Failed to initialize Azure Storage clients: {e}")

app = func.FunctionApp()

@app.event_hub_message_trigger(
    arg_name="event",
    # Must equal the EntityPath inside the connection string: the IoT Hub's
    # Event Hub-compatible name, injected by Terraform (not the literal "events").
    event_hub_name="%IOTHUB_EVENTS_PATH%",
    connection="IOTHUB_EVENTS_CONNECTION_STRING"
)
def telemetry_ingest(event: func.EventHubEvent):
    try:
        _ingest(event)
    except Exception:
        # Unexpected failure outside the dead-letter path: log the full traceback so it
        # shows in Log stream / App Insights, then fail the invocation.
        logging.exception("telemetry_ingest failed unexpectedly")
        raise


_INT32_MIN, _INT32_MAX = -(2**31), 2**31 - 1
_RESERVED_KEYS = {"PartitionKey", "RowKey", "Timestamp"}


def _as_utc(dt):
    """The worker may hand over enqueued_time without tzinfo; it is UTC (EnqueueTimeUtc)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def _to_table_value(value):
    """Map JSON values to Table Storage types. Ints beyond 32 bits (e.g. 'ts' in epoch ms)
    must be sent as Edm.Int64; nested objects/arrays are stored as JSON strings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not (_INT32_MIN <= value <= _INT32_MAX):
        return EntityProperty(value, EdmType.INT64)
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


# Reversed-time RowKeys sort newest first. 13 digits of epoch ms covers dates up to year 2286.
_MAX_EPOCH_MS = 9_999_999_999_999


def _reversed_ms(dt):
    return _MAX_EPOCH_MS - int(dt.timestamp() * 1000)


def _history_row_key(enqueued_time, payload, message_id):
    """<reversed enqueue ms>_<boot>_<seq>: newest first, unique per message, and the same
    for a redelivered message (so an upsert never creates a duplicate row)."""
    boot, seq = payload.get("boot"), payload.get("seq")
    if isinstance(boot, int) and isinstance(seq, int) and not isinstance(boot, bool) and not isinstance(seq, bool):
        suffix = f"{boot:06d}_{seq:08d}"
    else:
        suffix = message_id
    return f"{_reversed_ms(enqueued_time):013d}_{suffix}"


def _ingest(event: func.EventHubEvent):
    enqueued_time = _as_utc(event.enqueued_time)
    now = datetime.datetime.now(datetime.timezone.utc)

    # Calculate and record ingest latency[cite: 2]
    latency_ms = 0
    if enqueued_time:
        latency_ms = (now - enqueued_time).total_seconds() * 1000
        latency_histogram.record(latency_ms)

    raw_body = event.get_body().decode('utf-8')
    trigger_metadata = event.metadata or {}
    message_id = trigger_metadata.get("MessageId") or str(uuid.uuid4())

    logging.info(f"Processing message {message_id} with latency {latency_ms:.2f}ms")

    try:
        # Parse telemetry JSON format expected from the ESP32[cite: 2]
        payload = json.loads(raw_body)
        device_id = payload.get("id", "unknown_device")

        # Upsert into DeviceState Table. 'Timestamp' is a reserved, service-managed
        # property, so the enqueue time is stored as 'enqueued_time' instead.
        entity = {
            k: _to_table_value(v)
            for k, v in payload.items()
            if k not in _RESERVED_KEYS and v is not None
        }
        entity["PartitionKey"] = device_id
        entity["RowKey"] = "latest"
        if enqueued_time:
            entity["enqueued_time"] = enqueued_time

        table_client.upsert_entity(entity=entity)
        logging.info(f"Successfully updated DeviceState for {device_id}")

        # Append to the history table: same fields, one row per message
        history_time = enqueued_time or now
        history_entity = dict(entity)
        history_entity["RowKey"] = _history_row_key(history_time, payload, message_id)
        history_entity["enqueued_time"] = history_time
        history_client.upsert_entity(entity=history_entity)
        logging.info(f"Added history row {history_entity['RowKey']} for {device_id}")

    except Exception as e:
        logging.error(f"Failed to process telemetry payload. Sending to dead-letter blob. Error: {e}")
        
        # Route bad payloads to the dead-letter container[cite: 2]
        try:
            blob_name = f"{datetime.datetime.now(datetime.timezone.utc).strftime('%Y/%m/%d/%H')}/{message_id}.json"
            blob_client = dead_letter_client.get_blob_client(blob_name)
            
            dead_letter_data = {
                "error": str(e),
                "message_id": message_id,
                "enqueued_time": enqueued_time.isoformat() if enqueued_time else None,
                "raw_payload": raw_body
            }
            blob_client.upload_blob(json.dumps(dead_letter_data), overwrite=True)
        except Exception as dl_error:
            logging.error(f"CRITICAL: Failed to write to dead-letter blob: {dl_error}")

#########History retention#######
@app.timer_trigger(schedule="0 15 1 * * *", arg_name="timer", run_on_startup=False, use_monitor=True)
def history_purge(timer: func.TimerRequest):
    """Daily at 01:15 UTC: delete DeviceTelemetry rows older than HISTORY_RETENTION_DAYS."""
    try:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=HISTORY_RETENTION_DAYS)
        # Older rows have LARGER reversed-time keys, so everything past the cutoff key goes
        query_filter = f"RowKey gt '{_reversed_ms(cutoff):013d}'"
        by_partition = {}
        for row in history_client.query_entities(query_filter=query_filter, select=["PartitionKey", "RowKey"]):
            by_partition.setdefault(row["PartitionKey"], []).append(row["RowKey"])

        deleted = 0
        for partition, row_keys in by_partition.items():
            # A table transaction holds at most 100 operations, all in one partition
            for i in range(0, len(row_keys), 100):
                batch = [("delete", {"PartitionKey": partition, "RowKey": rk}) for rk in row_keys[i:i + 100]]
                history_client.submit_transaction(batch)
                deleted += len(batch)
        logging.info(f"history_purge: deleted {deleted} rows older than {cutoff.isoformat()} "
                     f"({HISTORY_RETENTION_DAYS} days) across {len(by_partition)} device(s)")

        # Resolved alerts older than ALERTS_RETENTION_DAYS (active ones are always kept)
        alert_cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=ALERTS_RETENTION_DAYS)
        purged = 0
        for alert in alerts_client.query_entities(query_filter="condition eq 'Resolved'"):
            if lifecycle.purge_due(alert, alert_cutoff):
                alerts_client.delete_entity(partition_key=alert["PartitionKey"], row_key=alert["RowKey"])
                purged += 1
        logging.info(f"history_purge: deleted {purged} resolved alerts older than {ALERTS_RETENTION_DAYS} days")
    except Exception:
        logging.exception("history_purge failed")
        raise


@app.event_grid_trigger(arg_name="event")
def device_lifecycle_trigger(event: func.EventGridEvent):
    """IoT Hub connect/disconnect events (via Event Grid) -> connection_status in DeviceState.

    A disconnect also opens an entry in OfflinePending; offline_check turns it into a
    "Device offline" alert after OFFLINE_GRACE_MINUTES. A connect clears the entry and
    resolves the alert. Late or out-of-order events (older sequence number) are ignored."""
    try:
        info = lifecycle.parse_event(event.event_type, event.subject, event.get_json(), event.event_time)
        if info is None:
            logging.info(f"Ignoring Event Grid event {event.event_type} for {event.subject}")
            return
        device_id = info["device_id"]

        try:
            stored = table_client.get_entity(partition_key=device_id, row_key="latest")
        except ResourceNotFoundError:
            stored = {}
        if not lifecycle.is_newer(info["seq"], stored.get("connection_seq")):
            logging.info(f"Stale lifecycle event for {device_id} ignored (seq {info['seq']})")
            return

        table_client.upsert_entity(entity=lifecycle.status_patch(device_id, info), mode=UpdateMode.MERGE)
        status = "Online" if info["online"] else "Offline"
        logging.info(f"Connection status for {device_id}: {status} at {info['time'].isoformat()}")

        if info["online"]:
            _clear_offline(device_id, info["time"])
        else:
            try:
                # Keep an existing entry (and its alert) if a Connected event was missed
                offline_client.create_entity(entity=lifecycle.pending_row(device_id, info["time"]))
            except ResourceExistsError:
                pass
    except Exception:
        logging.exception("Failed to process Event Grid lifecycle event")
        raise


def _clear_offline(device_id, when):
    """Device is back: resolve its offline alert (if one was raised) and drop the entry."""
    try:
        pending = offline_client.get_entity(partition_key=lifecycle.PENDING_PARTITION, row_key=device_id)
    except ResourceNotFoundError:
        return
    if pending.get("alerted") and pending.get("alert_row"):
        alerts_client.upsert_entity(
            entity=lifecycle.resolved_patch(device_id, pending["alert_row"], when), mode=UpdateMode.MERGE)
        logging.info(f"Device offline alert resolved for {device_id}")
    offline_client.delete_entity(partition_key=lifecycle.PENDING_PARTITION, row_key=device_id)


@app.timer_trigger(schedule="0 */1 * * * *", arg_name="timer", run_on_startup=False, use_monitor=False)
def offline_check(timer: func.TimerRequest):
    """Every minute: raise "Device offline" for devices disconnected longer than the grace
    period, and clear entries for devices whose telemetry shows they're back. Reads only the
    OfflinePending list, so the cost stays flat as the fleet grows. Logs only when it acts."""
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        try:
            rows = list(offline_client.query_entities(
                query_filter="PartitionKey eq @pk", parameters={"pk": lifecycle.PENDING_PARTITION}))
        except ResourceNotFoundError:
            return  # OfflinePending table not created yet (code deployed before the Terraform apply)
        for row in rows:
            device_id = row["RowKey"]
            since = row.get("since")
            try:
                state = table_client.get_entity(partition_key=device_id, row_key="latest")
            except ResourceNotFoundError:
                state = {}
            back_at = state.get("enqueued_time")
            if lifecycle.telemetry_after(back_at, since):
                # Telemetry after the disconnect: the Connected event was missed. Mark it
                # online without touching connection_seq, so later events still apply.
                table_client.upsert_entity(entity={
                    "PartitionKey": device_id, "RowKey": "latest",
                    "connection_status": "Online",
                    "last_status_change": lifecycle.as_utc(back_at).isoformat(),
                }, mode=UpdateMode.MERGE)
                _clear_offline(device_id, back_at)
                logging.info(f"{device_id} is sending telemetry again; marked Online")
                continue
            if not row.get("alerted") and lifecycle.is_due(since, now, OFFLINE_GRACE_MINUTES):
                alert = lifecycle.offline_alert_row(device_id, since, OFFLINE_GRACE_MINUTES)
                alerts_client.upsert_entity(entity=alert, mode=UpdateMode.MERGE)
                offline_client.update_entity(entity={
                    "PartitionKey": lifecycle.PENDING_PARTITION, "RowKey": device_id,
                    "alerted": True, "alert_row": alert["RowKey"],
                }, mode=UpdateMode.MERGE)
                logging.warning(f"Device offline alert raised for {device_id} (disconnected since {since})")
    except Exception:
        logging.exception("offline_check failed")
        raise
