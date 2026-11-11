import os
import json
import logging
import datetime
import uuid
import azure.functions as func
from azure.data.tables import TableServiceClient
from azure.storage.blob import BlobServiceClient
from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import metrics

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

# 3. Initialize Azure Storage Clients
# Using the default storage account connection string injected by Terraform.
STORAGE_CONN_STR = os.getenv("AzureWebJobsStorage")
TABLE_NAME = "DeviceState"
DEAD_LETTER_CONTAINER = "dead-letter"

try:
    table_client = TableServiceClient.from_connection_string(conn_str=STORAGE_CONN_STR).get_table_client(table_name=TABLE_NAME)
    blob_service_client = BlobServiceClient.from_connection_string(conn_str=STORAGE_CONN_STR)
    dead_letter_client = blob_service_client.get_container_client(DEAD_LETTER_CONTAINER)
except Exception as e:
    logging.error(f"Failed to initialize Azure Storage clients: {e}")

app = func.FunctionApp()

@app.event_hub_message_trigger(
    arg_name="event",
    event_hub_name="events", # The default IoT Hub built-in endpoint name
    connection="IOTHUB_EVENTS_CONNECTION_STRING"
)
def telemetry_ingest(event: func.EventHubEvent):
    enqueued_time = event.enqueued_time
    now = datetime.datetime.now(datetime.timezone.utc)
    
    # Calculate and record ingest latency[cite: 2]
    latency_ms = 0
    if enqueued_time:
        latency_ms = (now - enqueued_time).total_seconds() * 1000
        latency_histogram.record(latency_ms)

    raw_body = event.get_body().decode('utf-8')
    message_id = event.metadata.get("MessageId", str(uuid.uuid4()))
    
    logging.info(f"Processing message {message_id} with latency {latency_ms:.2f}ms")

    try:
        # Parse telemetry JSON format expected from the ESP32[cite: 2]
        payload = json.loads(raw_body)
        device_id = payload.get("id", "unknown_device")

        # Upsert into DeviceState Table
        entity = {
            "PartitionKey": device_id,
            "RowKey": "latest",
            "Timestamp": enqueued_time,
            **payload
        }
        
        table_client.upsert_entity(entity=entity)
        logging.info(f"Successfully updated DeviceState for {device_id}")

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

#########Eventgrid Module#######
# Initialize Table Client (reusing connection string from environment)
STORAGE_CONN_STR = os.getenv("AzureWebJobsStorage")
TABLE_NAME = "DeviceState"

try:
    table_client = TableServiceClient.from_connection_string(conn_str=STORAGE_CONN_STR).get_table_client(table_name=TABLE_NAME)
except Exception as e:
    logging.error(f"Failed to initialize Table client for Event Grid: {e}")

@app.event_grid_trigger(arg_name="event")
def device_lifecycle_trigger(event: func.EventGridEvent):
    """
    Handles device connect and disconnect events routed from IoT Hub via Event Grid.
    Updates the online/offline status in the DeviceState Table.
    """
    try:
        event_data = event.get_json()
        subject = event.subject  # Format: devices/<device_id>
        event_type = event.event_type  # e.g., Microsoft.Devices.DeviceConnected / DeviceDisconnected
        
        # Extract device_id from the subject string
        device_id = subject.split("/")[-1] if subject else "unknown_device"
        
        # Determine connection status based on event type
        is_connected = "DeviceConnected" in event_type
        status_string = "Online" if is_connected else "Offline"
        
        logging.info(f"Lifecycle event for device {device_id}: {status_string} (Type: {event_type})")

        # Fetch existing entity or initialize a new one to preserve other telemetry fields
        try:
            entity = table_client.get_entity(partition_key=device_id, row_key="latest")
        except Exception:
            # If no telemetry row exists yet, create a baseline entity
            entity = {
                "PartitionKey": device_id,
                "RowKey": "latest"
            }

        # Update connection status and timestamp
        entity["connection_status"] = status_string
        entity["last_status_change"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Upsert the updated state back into Table Storage[cite: 1, 2]
        table_client.upsert_entity(entity=entity)
        logging.info(f"Successfully updated connection status to {status_string} for {device_id}")

    except Exception as e:
        logging.error(f"Failed to process Event Grid lifecycle event: {e}")
        raise e

