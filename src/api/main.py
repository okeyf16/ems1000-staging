import os
import logging
from fastapi import Body, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from azure.data.tables import TableServiceClient, EntityProperty
from azure.iot.hub import IoTHubRegistryManager
from azure.iot.hub.models import CloudToDeviceMethod, Twin, TwinProperties
from azure.monitor.opentelemetry import configure_azure_monitor

# 1. Configure OpenTelemetry for Application Insights
# This automatically intercepts incoming requests, Table SDK calls, and IoT Hub SDK calls.
if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    configure_azure_monitor()

app = FastAPI(title="EMS1000 API")
logger = logging.getLogger(__name__)

# 2. Load Environment Variables injected by Terraform
STORAGE_CONN_STR = os.getenv("STORAGE_CONN_STR")
IOTHUB_CONN_STR = os.getenv("IOTHUB_CONNECTION_STRING")
TABLE_NAME = "DeviceState"
HISTORY_TABLE_NAME = os.getenv("HISTORY_TABLE_NAME", "DeviceTelemetry")

# Initialize Azure Clients
try:
    table_client = TableServiceClient.from_connection_string(conn_str=STORAGE_CONN_STR).get_table_client(table_name=TABLE_NAME)
    iot_registry_manager = IoTHubRegistryManager(IOTHUB_CONN_STR)
except Exception as e:
    logger.error(f"Failed to initialize Azure clients: {e}")

try:
    history_client = TableServiceClient.from_connection_string(conn_str=STORAGE_CONN_STR).get_table_client(table_name=HISTORY_TABLE_NAME)
except Exception as e:
    logger.error(f"Failed to initialize history table client: {e}")


def _plain(entity):
    """Int64 columns (e.g. 'ts' in epoch ms) come back as EntityProperty; return plain values."""
    return {k: (v.value if isinstance(v, EntityProperty) else v) for k, v in entity.items()}

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
    """Health probe endpoint used by Azure App Service to verify the container is alive."""
    return {"status": "healthy", "version": "1.0.0"}

@app.get("/api/devices/{device_id}/state")
def get_device_state(device_id: str):
    """Retrieve the latest known state of a device from Table Storage."""
    try:
        # Assuming PartitionKey is the device_id and RowKey is fixed (e.g., "latest") 
        # or queried for the most recent entry.
        entity = table_client.get_entity(partition_key=device_id, row_key="latest")
        return {"device_id": device_id, "state": _plain(entity)}
    except Exception as e:
        logger.error(f"Failed to fetch state for {device_id}: {e}")
        raise HTTPException(status_code=404, detail="Device state not found.")

@app.get("/api/devices/{device_id}/telemetry")
def get_device_telemetry(device_id: str, limit: int = Query(20, ge=1, le=500)):
    """Return the device's most recent telemetry messages, newest first."""
    try:
        # RowKeys are reversed timestamps, so the table already returns newest first
        pages = history_client.query_entities(
            query_filter="PartitionKey eq @pk",
            parameters={"pk": device_id},
            results_per_page=limit,
        )
        items = []
        for entity in pages:
            items.append(_plain(entity))
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


@app.post("/api/devices/{device_id}/command")
def send_device_command(device_id: str, payload: CommandPayload = Body(..., openapi_examples=COMMAND_EXAMPLES)):
    """Invoke a Direct Method on the device (start, stop, reset_fault, get_status).

    HTTP status mirrors the device's reply: 200 accepted, 400/409 refused (with the
    device's reason), 404 unknown method; 502 if the device itself errors and 504 if
    it is offline or does not answer in time.
    """
    return _invoke_device_method(device_id, payload.command)


@app.get("/api/devices/{device_id}/live")
def get_device_live(device_id: str):
    """Ask the device for its status right now (direct method get_status).

    Unlike /state (the last stored report), this needs the device online and returns
    504 if it is not. The reply comes from the device, not from storage.
    """
    result = _invoke_device_method(device_id, "get_status")
    live = result["payload"] if isinstance(result["payload"], dict) else {"payload": result["payload"]}
    return {"device_id": device_id, "source": "device", **live}


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
    "clear": {"summary": "Clear settings (back to device config)",
              "value": {"telemetry_interval_s": None, "load_pct": None, "low_fuel_warn_pct": None,
                        "overtemp_c": None, "min_start_battery_v": None, "speed": None}},
}


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
        "last_activity_time": twin.last_activity_time.isoformat() if twin.last_activity_time else None,
        "desired": desired,
        "reported": reported,
    }


@app.get("/api/devices/{device_id}/twin")
def get_device_twin(device_id: str):
    """Device twin: desired settings, what the device reports (state, last fault,
    settings in use and whether each desired setting was applied), and whether it is
    connected. Works while the device is offline (last reported values)."""
    try:
        twin = iot_registry_manager.get_twin(device_id)
    except Exception as e:
        _twin_error(device_id, "read", e)
    return _twin_view(device_id, twin)


@app.patch("/api/devices/{device_id}/twin")
def update_device_twin(device_id: str, patch: dict = Body(..., openapi_examples=TWIN_EXAMPLES)):
    """Change desired settings: telemetry_interval_s (10-3600), load_pct (0-150),
    low_fuel_warn_pct (1-50), overtemp_c (85-120), min_start_battery_v (10-12.5),
    speed (1-3600). null clears a setting. Only the settings sent are changed.

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
