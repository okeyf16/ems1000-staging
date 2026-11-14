import os
import logging
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from azure.data.tables import TableServiceClient, EntityProperty
from azure.iot.hub import IoTHubRegistryManager
from azure.iot.hub.models import CloudToDeviceMethod
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

class CommandPayload(BaseModel):
    command: str  # "start", "stop", "reset_fault" or "get_status"


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

@app.post("/api/devices/{device_id}/command")
def send_device_command(device_id: str, payload: CommandPayload):
    """Invoke a Direct Method on the device.

    HTTP status mirrors the device's reply: 200 accepted, 400/409 refused (with the
    device's reason), 404 unknown method; 502 if the device itself errors and 504 if
    it is offline or does not answer in time.
    """
    method_name = COMMAND_METHODS.get(payload.command)
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
        logger.error(f"Failed to send command to {device_id}: {e}")
        # 504 Gateway Timeout is used if the device is offline and misses the command window
        raise HTTPException(status_code=504, detail=f"Device unreachable or failed to respond: {str(e)}")

    result = {
        "device_id": device_id,
        "command": payload.command,
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
