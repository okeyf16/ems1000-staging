import hmac
import os
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from azure.data.tables import TableServiceClient, EntityProperty, UpdateMode
from azure.iot.hub import IoTHubRegistryManager
from azure.iot.hub.models import CloudToDeviceMethod, Twin, TwinProperties
from azure.monitor.opentelemetry import configure_azure_monitor

# 1. Configure OpenTelemetry for Application Insights
# This automatically intercepts incoming requests, Table SDK calls, and IoT Hub SDK calls.
if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    configure_azure_monitor()

app = FastAPI(
    title="EMS1000 API",
    description=(
        "Every endpoint except /healthz needs an API key in the X-API-Key header "
        "(use Authorize above). Times are West Africa Time (WAT, UTC+1); Azure itself stores UTC."
    ),
)
logger = logging.getLogger(__name__)

# 2. Load Environment Variables injected by Terraform
STORAGE_CONN_STR = os.getenv("STORAGE_CONN_STR")
IOTHUB_CONN_STR = os.getenv("IOTHUB_CONNECTION_STRING")
TABLE_NAME = "DeviceState"
HISTORY_TABLE_NAME = os.getenv("HISTORY_TABLE_NAME", "DeviceTelemetry")
ALERTS_TABLE_NAME = os.getenv("ALERTS_TABLE_NAME", "Alerts")

# Generator rating used for load %: 3.0 kVA at 220 V = ~13.6 A (ECO4990ES)
RATED_CURRENT_A = float(os.getenv("RATED_CURRENT_A", "13.6"))

# Display time zone. WAT is UTC+1 all year (no daylight saving), so a fixed offset is exact
# and needs no tz database. Storage stays UTC; only API replies use WAT.
WAT = timezone(timedelta(hours=1), "WAT")

# Initialize Azure Clients
table_client = history_client = alerts_client = iot_registry_manager = None
try:
    table_client = TableServiceClient.from_connection_string(conn_str=STORAGE_CONN_STR).get_table_client(table_name=TABLE_NAME)
    iot_registry_manager = IoTHubRegistryManager(IOTHUB_CONN_STR)
except Exception as e:
    logger.error(f"Failed to initialize Azure clients: {e}")

try:
    history_client = TableServiceClient.from_connection_string(conn_str=STORAGE_CONN_STR).get_table_client(table_name=HISTORY_TABLE_NAME)
except Exception as e:
    logger.error(f"Failed to initialize history table client: {e}")

try:
    alerts_client = TableServiceClient.from_connection_string(conn_str=STORAGE_CONN_STR).get_table_client(table_name=ALERTS_TABLE_NAME)
except Exception as e:
    logger.error(f"Failed to initialize alerts table client: {e}")


# ---- API key ------------------------------------------------------------------------
# API_KEYS holds one or more keys, comma-separated (Terraform sets two so either can be
# rotated without downtime). No keys configured = every protected call is refused.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False,
                              description="API key (Terraform output api_keys)")


def _configured_keys() -> list[str]:
    return [k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip()]


def require_api_key(key: str | None = Security(api_key_header)) -> None:
    keys = _configured_keys()
    if not keys:
        logger.error("API_KEYS is not set; refusing all protected requests")
        raise HTTPException(status_code=503, detail="API keys are not configured on the server.")
    if not key or not any(hmac.compare_digest(key.encode(), k.encode()) for k in keys):
        raise HTTPException(status_code=401, detail="Missing or invalid API key (X-API-Key header).",
                            headers={"WWW-Authenticate": "ApiKey"})


# Every device and alert endpoint goes on this router, so none can skip the key check.
api = APIRouter(dependencies=[Depends(require_api_key)])


# ---- Times ---------------------------------------------------------------------------
def _as_utc(value) -> datetime | None:
    """datetime (naive = UTC), ISO string or None -> aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def to_wat(value) -> str | None:
    """Any timestamp -> ISO 8601 in WAT with its offset, e.g. 2026-11-18T23:10:05+01:00."""
    dt = _as_utc(value)
    return dt.astimezone(WAT).isoformat(timespec="seconds") if dt else None


def _epoch_ms_to_wat(ts) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).astimezone(WAT).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _plain(entity):
    """Int64 columns (e.g. 'ts' in epoch ms) come back as EntityProperty; return plain values."""
    return {k: (v.value if isinstance(v, EntityProperty) else v) for k, v in entity.items()}


# ---- Telemetry decoding (schema v1) --------------------------------------------------
RUN_STATES = {0: "Off", 1: "Cranking", 2: "Running", 3: "Stopping", 4: "Fault"}
FAULT_BITS = {0: "low_oil", 1: "low_fuel", 2: "over_temperature", 3: "low_battery"}  # latched
WARNING_BITS = {4: "voltage", 5: "frequency"}                                         # alerts only
OIL_STATES = {0: "ok", 1: "low", 2: "critical"}

# 12 V lead-acid state of charge from resting voltage (estimate). Only reliable with the
# engine off; while cranking, running or stopping the alternator or starter skews it.
_SOC_POINTS = [(11.8, 0), (12.0, 25), (12.2, 50), (12.4, 75), (12.5, 90), (12.7, 100)]


def estimate_soc(vb) -> int | None:
    if not isinstance(vb, (int, float)) or isinstance(vb, bool):
        return None
    if vb <= _SOC_POINTS[0][0]:
        return 0
    if vb >= _SOC_POINTS[-1][0]:
        return 100
    for (v1, s1), (v2, s2) in zip(_SOC_POINTS, _SOC_POINTS[1:]):
        if v1 <= vb <= v2:
            return round(s1 + (s2 - s1) * (vb - v1) / (v2 - v1))
    return None


def _bits(value, names: dict) -> list[str]:
    return [name for bit, name in names.items() if isinstance(value, int) and value & (1 << bit)]


def shape_state(device_id: str, fields: dict, source: str, received) -> dict:
    """One reply shape for stored (/state) and live (?live=true) status."""
    received_utc = _as_utc(received)
    run = fields.get("run")
    flt = fields.get("flt")
    i = fields.get("i")
    vb = fields.get("vb")
    oil = fields.get("oil")
    load_pct = round(i / RATED_CURRENT_A * 100, 1) if isinstance(i, (int, float)) and RATED_CURRENT_A > 0 else None
    return {
        "device_id": device_id,
        "source": source,
        "received_at": to_wat(received_utc),
        "age_s": max(0, int((datetime.now(timezone.utc) - received_utc).total_seconds())) if received_utc else None,
        "readings": {
            "v": fields.get("v"), "i": i, "f": fields.get("f"),
            "fuel": fields.get("fuel"), "oil": oil, "oil_state": OIL_STATES.get(oil),
            "t": fields.get("t"), "vb": vb,
            "soc_pct": estimate_soc(vb),
            "soc_reliable": run in (0, 4),
            "load_pct": load_pct,
        },
        "status": {
            "run": run,
            "run_state": RUN_STATES.get(run),
            "flt": flt,
            "faults": _bits(flt, FAULT_BITS),
            "warnings": _bits(flt, WARNING_BITS),
        },
        "link": {
            "rssi": fields.get("rssi"),
            "boot": fields.get("boot"),
            "seq": fields.get("seq"),
            "sv": fields.get("sv"),
            "ts": fields.get("ts"),
            "device_time": _epoch_ms_to_wat(fields.get("ts")),
            # From IoT Hub connect/disconnect events (Event Grid); null until the first event
            "connection_status": fields.get("connection_status"),
            "last_status_change": to_wat(fields.get("last_status_change")),
        },
    }


APP_COMMANDS = ["start", "stop", "reset_fault", "get_status"]


class CommandPayload(BaseModel):
    # Validated in the endpoint (unknown values -> 400), so the canonical method
    # names stay accepted; the enum documents the app commands in /docs and OpenAPI.
    command: str = Field(
        ...,
        description="start | stop | reset_fault | get_status",
        json_schema_extra={"enum": APP_COMMANDS},
    )


# Selectable request bodies on the /docs test page ("Examples" dropdown)
COMMAND_EXAMPLES = {
    "start": {"summary": "Start the generator", "value": {"command": "start"}},
    "stop": {"summary": "Stop the generator", "value": {"command": "stop"}},
    "reset_fault": {"summary": "Clear a fault lockout", "value": {"command": "reset_fault"}},
    "get_status": {"summary": "Live status from the device", "value": {"command": "get_status"}},
}


# App command -> device direct-method name (the EMS-1000 firmware contract).
# The canonical method names are accepted too.
COMMAND_METHODS = {
    "start": "remote_start",
    "stop": "remote_stop",
    "reset_fault": "reset_fault",
    "get_status": "get_status",
}
COMMAND_METHODS.update({m: m for m in list(COMMAND_METHODS.values())})

@app.get("/healthz")
def health_check():
    """Health probe endpoint used by Azure App Service to verify the container is alive. No key needed."""
    return {"status": "healthy", "version": "1.1.0"}


@api.get("/api/devices/{device_id}/state")
def get_device_state(device_id: str, live: bool = Query(
        False, description="false: last stored message (free, works offline). "
                           "true: ask the device now (uses the IoT Hub message budget; 504 if offline).")):
    """Device status in one shape, whether read from storage or asked live.

    `source` says which (`stored` or `device`), `age_s` how old it is. Readings include
    derived `soc_pct` (battery state of charge, estimated from voltage; `soc_reliable` is
    true only with the engine off) and `load_pct` (current / rated current).
    `link.connection_status` (Online/Offline) and `link.last_status_change` come from IoT Hub
    connect/disconnect events. Times are WAT (UTC+1)."""
    if live:
        result = _invoke_device_method(device_id, "get_status")
        payload = result["payload"] if isinstance(result["payload"], dict) else {}
        fields = dict(payload.get("readings") or {})
        for key in ("run", "flt", "boot", "seq", "sv", "ts", "rssi"):
            if payload.get(key) is not None:
                fields[key] = payload[key]
        return shape_state(device_id, fields, "device", datetime.now(timezone.utc))
    try:
        entity = table_client.get_entity(partition_key=device_id, row_key="latest")
    except Exception as e:
        logger.error(f"Failed to fetch state for {device_id}: {e}")
        raise HTTPException(status_code=404, detail="Device state not found.")
    fields = _plain(entity)
    received = fields.get("enqueued_time") or getattr(entity, "metadata", {}).get("timestamp")
    return shape_state(device_id, fields, "stored", received)


@api.get("/api/devices/{device_id}/live", deprecated=True)
def get_device_live(device_id: str):
    """Deprecated: use GET /api/devices/{device_id}/state?live=true (same reply)."""
    return get_device_state(device_id, live=True)


@api.get("/api/devices/{device_id}/telemetry")
def get_device_telemetry(device_id: str, limit: int = Query(20, ge=1, le=500)):
    """Return the device's most recent telemetry messages, newest first.

    Fields are as sent (schema v1); `received_at` and `device_time` are added in WAT."""
    try:
        # RowKeys are reversed timestamps, so the table already returns newest first
        pages = history_client.query_entities(
            query_filter="PartitionKey eq @pk",
            parameters={"pk": device_id},
            results_per_page=limit,
        )
        items = []
        for entity in pages:
            item = _plain(entity)
            item["received_at"] = to_wat(item.pop("enqueued_time", None))
            item["device_time"] = _epoch_ms_to_wat(item.get("ts"))
            items.append(item)
            if len(items) >= limit:
                break
        return {"device_id": device_id, "count": len(items), "items": items}
    except Exception as e:
        logger.error(f"Failed to fetch telemetry history for {device_id}: {e}")
        raise HTTPException(status_code=500, detail="Could not read telemetry history.")


def _invoke_device_method(device_id: str, command: str) -> dict:
    """Send one direct method and mirror the device's reply as HTTP.

    200 -> result; 400/404/409 refused (device reply in detail); 502 if the device
    errors; 504 if it is offline or does not answer within 15 s.
    """
    method_name = COMMAND_METHODS.get(command)
    if method_name is None:
        raise HTTPException(status_code=400, detail="Invalid command. Must be one of: start, stop, reset_fault, get_status.")

    try:
        # Construct the Direct Method call
        method = CloudToDeviceMethod(
            method_name=method_name,
            payload={"command_id": os.urandom(4).hex()}, # Traceable command ID
            response_timeout_in_seconds=15,
            connect_timeout_in_seconds=15
        )

        # Invoke the method synchronously on the device
        response = iot_registry_manager.invoke_device_method(device_id, method)
    except Exception as e:
        logger.error(f"Failed to send {command} to {device_id}: {e}")
        # 504 Gateway Timeout is used if the device is offline and misses the command window
        raise HTTPException(status_code=504, detail=f"Device unreachable or failed to respond: {str(e)}")

    result = {
        "device_id": device_id,
        "command": command,
        "method": method_name,
        "status": response.status,
        "payload": response.payload,
    }
    # Mirror the device's refusal so the app can show the reason (e.g. 409 low oil)
    if response.status is not None and 400 <= response.status < 500:
        raise HTTPException(status_code=response.status, detail=result)
    if response.status is not None and response.status >= 500:
        raise HTTPException(status_code=502, detail=result)
    return result


@api.post("/api/devices/{device_id}/command")
def send_device_command(device_id: str, payload: CommandPayload = Body(..., openapi_examples=COMMAND_EXAMPLES)):
    """Invoke a Direct Method on the device (start, stop, reset_fault, get_status).

    HTTP status mirrors the device's reply: 200 accepted, 400/409 refused (with the
    device's reason), 404 unknown method; 502 if the device itself errors and 504 if
    it is offline or does not answer in time.
    """
    return _invoke_device_method(device_id, payload.command)


# ---- Alerts ----------------------------------------------------------------------------
# Azure Monitor posts alerts (common alert schema) to the webhook below; they are stored in
# the Alerts table and listed by GET /api/alerts for the simulator panel and the app.
# Rows: PartitionKey = device ID (from the alert's device_id dimension) or "_system";
# RowKey = the alert's ID, so a "Resolved" call updates the row its "Fired" call made.
def _alert_row(body: dict) -> dict:
    data = body.get("data") or {}
    ess = data.get("essentials") or {}
    alert_id = ess.get("alertId") or ""
    row_key = alert_id.rstrip("/").split("/")[-1] or os.urandom(8).hex()
    device_id = "_system"
    for cond in ((data.get("alertContext") or {}).get("condition") or {}).get("allOf") or []:
        for dim in cond.get("dimensions") or []:
            if dim.get("name") == "device_id" and dim.get("value"):
                device_id = str(dim["value"])
    for bad in "/\\#?":
        device_id = device_id.replace(bad, "_")
    return {
        "PartitionKey": device_id,
        "RowKey": row_key,
        "rule": ess.get("alertRule"),
        "severity": ess.get("severity"),
        "condition": ess.get("monitorCondition"),
        "fired_at": ess.get("firedDateTime"),
        "resolved_at": ess.get("resolvedDateTime"),
        "description": ess.get("description"),
        "signal_type": ess.get("signalType"),
        "source": "azure-monitor",
        "alert_id": alert_id,
    }


def _alert_view(entity) -> dict:
    e = _plain(entity)
    active = e.get("condition") == "Fired"
    return {
        "id": e.get("RowKey"),
        "device_id": None if e.get("PartitionKey") == "_system" else e.get("PartitionKey"),
        "rule": e.get("rule"),
        "severity": e.get("severity"),
        "status": "active" if active else "resolved",
        "description": e.get("description"),
        "source": e.get("source"),
        "fired_at": to_wat(e.get("fired_at")),
        "resolved_at": to_wat(e.get("resolved_at")),
    }


@app.post("/api/alerts/azure-monitor", include_in_schema=False)
def receive_azure_monitor_alert(token: str = Query(""), body: dict = Body(...)):
    """Webhook for the action group. Authenticated by ?token= (Azure Monitor can't send headers)."""
    expected = os.getenv("ALERT_WEBHOOK_TOKEN", "")
    if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid webhook token.")
    row = _alert_row(body)
    try:
        alerts_client.upsert_entity(entity={k: v for k, v in row.items() if v is not None}, mode=UpdateMode.MERGE)
    except Exception as e:
        logger.error(f"Failed to store alert {row['RowKey']}: {e}")
        raise HTTPException(status_code=500, detail="Could not store the alert.")
    logger.info(f"Alert {row['condition']} stored: {row['rule']} ({row['PartitionKey']})")
    return {"stored": True, "id": row["RowKey"], "device_id": row["PartitionKey"], "condition": row["condition"]}


@api.get("/api/alerts")
def list_alerts(device_id: str | None = Query(None, description="Only this device's alerts"),
                status: str = Query("all", pattern="^(active|all)$", description="active = still firing"),
                limit: int = Query(50, ge=1, le=500)):
    """Alerts, newest first. Pipeline-wide alerts have device_id null. Times are WAT."""
    try:
        if device_id:
            entities = alerts_client.query_entities(query_filter="PartitionKey eq @pk", parameters={"pk": device_id})
        else:
            entities = alerts_client.list_entities()
        alerts = [_alert_view(e) for e in entities]
    except Exception as e:
        logger.error(f"Failed to read alerts: {e}")
        raise HTTPException(status_code=500, detail="Could not read alerts.")
    if status == "active":
        alerts = [a for a in alerts if a["status"] == "active"]
    alerts.sort(key=lambda a: a["fired_at"] or "", reverse=True)
    return {"count": min(len(alerts), limit), "alerts": alerts[:limit]}


# ---- Device twin (M5) ---------------------------------------------------------------
# Remotely changeable settings and their allowed ranges. The device validates them too
# and reports "applied" or "rejected: <reason>" for each in reported.settings_status.
TWIN_SETTINGS = {
    "telemetry_interval_s": (10, 3600, True),
    "load_pct": (0, 150, False),
    "low_fuel_warn_pct": (1, 50, False),
    "overtemp_c": (85, 120, False),
    "min_start_battery_v": (10.0, 12.5, False),
    "speed": (1, 3600, False),
}

TWIN_EXAMPLES = {
    "demo_interval": {"summary": "Telemetry every 10 s (demo)", "value": {"telemetry_interval_s": 10}},
    "normal_interval": {"summary": "Telemetry every 60 s (normal)", "value": {"telemetry_interval_s": 60}},
    "load": {"summary": "Set load to 60 %", "value": {"load_pct": 60}},
    "overload": {"summary": "Overload: 110 % (breaker trips after 5 s)", "value": {"load_pct": 110}},
    "thresholds": {"summary": "Alert thresholds", "value": {"low_fuel_warn_pct": 20, "overtemp_c": 100,
                                                            "min_start_battery_v": 11.5}},
    "fast": {"summary": "Model speed 60x", "value": {"speed": 60}},
}
# No "clear" example: FastAPI drops null values from /docs examples, so it would
# arrive as {}. Use DELETE .../twin/settings, or type {"load_pct": null} by hand.


def _strip_metadata(value):
    """Drop IoT Hub's $metadata blocks; keep $version."""
    if isinstance(value, dict):
        return {k: _strip_metadata(v) for k, v in value.items() if k != "$metadata"}
    return value


def _validate_twin_patch(patch) -> dict:
    if not isinstance(patch, dict) or not patch:
        raise HTTPException(status_code=400, detail="Body must be a JSON object with at least one setting.")
    errors = {}
    for key, value in patch.items():
        if key not in TWIN_SETTINGS:
            errors[key] = f"unknown setting; use one of {sorted(TWIN_SETTINGS)}"
            continue
        if value is None:
            continue  # null clears the setting (device goes back to its config value)
        low, high, integer = TWIN_SETTINGS[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors[key] = "must be a number or null"
        elif integer and float(value) != int(value):
            errors[key] = "must be a whole number"
        elif not low <= value <= high:
            errors[key] = f"must be between {low:g} and {high:g}"
    if errors:
        raise HTTPException(status_code=400, detail={"invalid": errors})
    return patch


def _twin_error(device_id: str, action: str, e: Exception):
    status = getattr(getattr(e, "response", None), "status_code", None)
    if status == 404:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found in IoT Hub.")
    logger.error(f"Failed to {action} twin for {device_id}: {e}")
    raise HTTPException(status_code=502, detail=f"IoT Hub twin {action} failed: {e}")


def _twin_view(device_id: str, twin) -> dict:
    props = twin.properties
    desired = _strip_metadata(dict(props.desired or {})) if props else {}
    reported = _strip_metadata(dict(props.reported or {})) if props else {}
    return {
        "device_id": device_id,
        "connection_state": twin.connection_state,
        "last_activity_time": to_wat(twin.last_activity_time),
        "desired": desired,
        "reported": reported,
    }


@api.get("/api/devices/{device_id}/twin")
def get_device_twin(device_id: str):
    """Device twin: desired settings, what the device reports (state, last fault,
    settings in use and whether each desired setting was applied), and whether it is
    connected. Works while the device is offline (last reported values)."""
    try:
        twin = iot_registry_manager.get_twin(device_id)
    except Exception as e:
        _twin_error(device_id, "read", e)
    return _twin_view(device_id, twin)


@api.patch("/api/devices/{device_id}/twin")
def update_device_twin(device_id: str, patch: dict = Body(..., openapi_examples=TWIN_EXAMPLES)):
    """Change desired settings: telemetry_interval_s (10-3600), load_pct (0-150),
    low_fuel_warn_pct (1-50), overtemp_c (85-120), min_start_battery_v (10-12.5),
    speed (1-3600). null clears a setting (or use DELETE .../twin/settings).
    Only the settings sent are changed.

    400 for an unknown setting or out-of-range value. The device applies the change
    within seconds (or when it next connects); check GET .../twin, reported.settings
    and reported.settings_status, to confirm."""
    patch = _validate_twin_patch(patch)
    try:
        twin = iot_registry_manager.update_twin(
            device_id, Twin(properties=TwinProperties(desired=patch)), etag="*")
    except Exception as e:
        _twin_error(device_id, "update", e)
    view = _twin_view(device_id, twin)
    return {"device_id": device_id, "sent": patch,
            "desired_version": view["desired"].get("$version"),
            "connection_state": view["connection_state"],
            "desired": view["desired"],
            "note": "Check GET /twin: reported.settings_status shows whether the device applied each setting."}


@api.delete("/api/devices/{device_id}/twin/settings")
def clear_device_twin_settings(device_id: str, name: str | None = Query(
        None, description="Clear only this setting; leave empty to clear all six")):
    """Clear desired settings so the device goes back to its config values.

    Without `name`: clears all six settings. With `name` (e.g. load_pct): clears just
    that one. Same as PATCH with null values, which the /docs examples can't carry."""
    if name is not None and name not in TWIN_SETTINGS:
        raise HTTPException(status_code=400,
                            detail={"invalid": {name: f"unknown setting; use one of {sorted(TWIN_SETTINGS)}"}})
    patch = {name: None} if name else {key: None for key in TWIN_SETTINGS}
    try:
        twin = iot_registry_manager.update_twin(
            device_id, Twin(properties=TwinProperties(desired=patch)), etag="*")
    except Exception as e:
        _twin_error(device_id, "update", e)
    view = _twin_view(device_id, twin)
    return {"device_id": device_id, "cleared": sorted(patch),
            "desired_version": view["desired"].get("$version"),
            "connection_state": view["connection_state"],
            "desired": view["desired"]}


# Registered last so every route above is on it
app.include_router(api)
