import os
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from azure.data.tables import TableServiceClient
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

# Initialize Azure Clients
try:
    table_client = TableServiceClient.from_connection_string(conn_str=STORAGE_CONN_STR).get_table_client(table_name=TABLE_NAME)
    iot_registry_manager = IoTHubRegistryManager(IOTHUB_CONN_STR)
except Exception as e:
    logger.error(f"Failed to initialize Azure clients: {e}")

class CommandPayload(BaseModel):
    command: str  # e.g., "start" or "stop"

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
        return {"device_id": device_id, "state": entity}
    except Exception as e:
        logger.error(f"Failed to fetch state for {device_id}: {e}")
        raise HTTPException(status_code=404, detail="Device state not found.")

@app.post("/api/devices/{device_id}/command")
def send_device_command(device_id: str, payload: CommandPayload):
    """Invoke a Direct Method on the IoT Hub to start or stop the generator."""
    if payload.command not in ["start", "stop"]:
        raise HTTPException(status_code=400, detail="Invalid command. Must be 'start' or 'stop'.")

    try:
        # Construct the Direct Method call
        method = CloudToDeviceMethod(
            method_name=payload.command,
            payload={"command_id": os.urandom(4).hex()}, # Traceable command ID
            response_timeout_in_seconds=15,
            connect_timeout_in_seconds=15
        )
        
        # Invoke the method synchronously on the device
        response = iot_registry_manager.invoke_device_method(device_id, method)
        
        return {
            "device_id": device_id,
            "command": payload.command,
            "status": response.status,
            "payload": response.payload
        }
    except Exception as e:
        logger.error(f"Failed to send command to {device_id}: {e}")
        # 504 Gateway Timeout is used if the device is offline and misses the command window
        raise HTTPException(status_code=504, detail=f"Device unreachable or failed to respond: {str(e)}")
