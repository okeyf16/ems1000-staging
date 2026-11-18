# API and data reference

What the API offers, what the Function stores and where, how IoT Hub routes messages, and which
settings each app reads. For the device side of the contract (telemetry fields, direct-method
replies, twin settings), see the
[device build reference](https://github.com/okeyf16/ems1000-device-sim/blob/main/docs/BUILD_REFERENCE.md).

## API

FastAPI on App Service. Interactive docs and a test page are at `<api-base-url>/docs` (the base
URL is the Terraform output `api_base_url`). There is no authentication yet
([known issue 2](RUNBOOK.md#known-issues)).

| Method | Path | Returns | Reads from |
|---|---|---|---|
| GET | `/healthz` | `{"status":"healthy"}`; App Service's health probe | — |
| GET | `/api/devices/{id}/state` | The last message received from the device | Table `DeviceState` |
| GET | `/api/devices/{id}/telemetry?limit=N` | The last N messages, newest first (N 1–500, default 20) | Table `DeviceTelemetry` |
| POST | `/api/devices/{id}/command` | Sends a direct method and mirrors the device's reply | IoT Hub (device must be online) |
| GET | `/api/devices/{id}/live` | The device's status right now (`get_status`) | IoT Hub (device must be online) |
| GET | `/api/devices/{id}/twin` | Desired settings, reported properties, connection state | IoT Hub twin (works while offline) |
| PATCH | `/api/devices/{id}/twin` | Changes desired settings (only those sent) | IoT Hub twin |
| DELETE | `/api/devices/{id}/twin/settings[?name=…]` | Clears all six settings, or one, so the device returns to its config values | IoT Hub twin |

### Commands

Body: `{"command": "<name>"}`. The API maps app commands to the firmware's direct-method names;
the method names are accepted too.

| `command` | Direct method sent |
|---|---|
| `start` | `remote_start` |
| `stop` | `remote_stop` |
| `reset_fault` | `reset_fault` |
| `get_status` | `get_status` |

Each call carries a random `command_id` in its payload and waits up to 15 s for the device.

| HTTP | Meaning |
|---|---|
| 200 | Device accepted; body has `status` and the device's `payload` |
| 400 | Device refused as invalid now (e.g. already running), or the command name is unknown |
| 404 | Device doesn't know the method |
| 409 | Safety interlock or fault lockout; the device's reason is in `detail.payload` |
| 502 | Device answered with an error (5xx) |
| 504 | Device offline or no answer within 15 s |

### Twin settings

| Setting | Range | Whole number |
|---|---|---|
| `telemetry_interval_s` | 10–3600 | Yes |
| `load_pct` | 0–150 | No |
| `low_fuel_warn_pct` | 1–50 | No |
| `overtemp_c` | 85–120 | No |
| `min_start_battery_v` | 10.0–12.5 | No |
| `speed` | 1–3600 | No |

`null` clears a setting. The API rejects unknown names and out-of-range values with 400 and
sends nothing. The device applies a change within seconds, or when it next connects; confirm with
`GET /twin` → `reported.settings_status`. Note: FastAPI drops `null` from the `/docs` examples,
so use `DELETE …/twin/settings` to clear from the test page.

## Data model

### Table `DeviceState`: latest per device

| Key | Value |
|---|---|
| PartitionKey | Device ID (the telemetry `id` field) |
| RowKey | `latest` (one row per device, overwritten by each message) |
| Columns | Every telemetry field as sent (`sv`, `ts`, `boot`, `seq`, `v`, `i`, `f`, `fuel`, `oil`, `t`, `vb`, `flt`, `run`, `rssi`), plus `enqueued_time` (when IoT Hub received it, UTC) |

`Timestamp` is reserved by Table Storage, so the receive time is stored as `enqueued_time`.
Integers outside 32 bits (`ts`, epoch milliseconds) are stored as Int64.

### Table `DeviceTelemetry`: every message

| Key | Value |
|---|---|
| PartitionKey | Device ID |
| RowKey | `<reversed enqueue ms, 13 digits>_<boot, 6 digits>_<seq, 8 digits>` (falls back to the message ID if `boot`/`seq` are missing) |
| Columns | Same as `DeviceState` |

Reversed time makes a plain query return newest first. The key is the same for a redelivered
message, so a retry overwrites rather than duplicates. `history_purge` deletes rows older than
`HISTORY_RETENTION_DAYS` (9) every day at 01:15 UTC, in batches of 100 per device.

### Table `Alerts`: alerts for the panel and the app

Created by Terraform. The API writes a row for each Azure Monitor alert it receives through its
webhook, and serves them at `GET /api/alerts` (both arrive with the API change that follows).
Predictive-maintenance alerts will be written here too.

### Blob containers

| Container | Written by | Content | Kept |
|---|---|---|---|
| `telemetry-archive` | IoT Hub storage endpoint (route `telemetry-to-archive`) | Raw messages as JSON, batched every 300 s, path `{iothub}/{partition}/{YYYY}/{MM}/{DD}/{HH}/{mm}` | 30 days (lifecycle rule) |
| `dead-letter` | `telemetry_ingest` | Messages that failed to parse or store: error, message ID, enqueue time, raw body. Path `YYYY/MM/DD/HH/<message-id>.json` | No rule yet |

The archive stores readable JSON only if devices send with content type `application/json` and
encoding `utf-8` (the topic's `$.ct` and `$.ce` properties); otherwise bodies are base64.

## IoT Hub routing

| Route | Source | Condition | Endpoint | Feeds |
|---|---|---|---|---|
| `telemetry-to-builtin` | Device messages | `true` | `events` (built-in, Event Hub-compatible) | `telemetry_ingest` |
| `telemetry-to-archive` | Device messages | `true` | `telemetry-archive` (storage container) | Raw archive |

Each message goes to every matching route. Routes are separate `azurerm_iothub_route`
resources; don't add inline route blocks to the hub (changing those can force the hub to be
recreated). Diagnostics (`Connections`, `DeviceTelemetry` logs and all metrics) go to Log
Analytics.

## Functions

| Function | Trigger | Does |
|---|---|---|
| `telemetry_ingest` | Built-in events endpoint (`%IOTHUB_EVENTS_PATH%`) | Parses the JSON, upserts `DeviceState`, upserts a `DeviceTelemetry` row, records `ingest_latency_ms`; sends failures to `dead-letter` |
| `history_purge` | Timer `0 15 1 * * *` (01:15 UTC daily) | Deletes `DeviceTelemetry` rows older than the retention |
| `device_lifecycle_trigger` | Event Grid | Would set `connection_status` on connect/disconnect; not wired up ([known issue 5](RUNBOOK.md#known-issues)) |

## App settings

Set by Terraform; secrets are built from other resources' keys at apply time and never stored
in the repo.

**Function App**

| Setting | Value |
|---|---|
| `IOTHUB_EVENTS_CONNECTION_STRING` | Event Hub-compatible connection string for the hub's built-in endpoint |
| `IOTHUB_EVENTS_PATH` | Event Hub-compatible name; must equal the `EntityPath` in the string above |
| `AzureWebJobsStorage` | Storage account connection string (tables and dead-letter) |
| `HISTORY_TABLE_NAME`, `HISTORY_RETENTION_DAYS` | `DeviceTelemetry`, `9` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | App Insights |
| `WEBSITE_RUN_FROM_PACKAGE` | Set by the deploy workflow; Terraform ignores it |

**Web App (API)**

| Setting | Value |
|---|---|
| `STORAGE_CONN_STR` | Storage account connection string |
| `IOTHUB_CONNECTION_STRING` | Hub connection string (direct methods, twins) |
| `HISTORY_TABLE_NAME` | `DeviceTelemetry` |
| `ALERTS_TABLE_NAME` | `Alerts` |
| `ALERT_WEBHOOK_TOKEN` | Secret the action group sends in the webhook URL (`?token=…`); generated by Terraform |
| `APPLICATIONINSIGHTS_CONNECTION_STRING`, `OTEL_SERVICE_NAME` | App Insights; service name `ems-api` |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` (App Service installs `requirements.txt`) |

Start command: `gunicorn -w 2 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 main:app`.

## Monitoring

| Item | Detail |
|---|---|
| Log Analytics | 30-day retention, daily cap `daily_quota_gb` (1 GB) |
| Application Insights | Workspace-based; Function and API send traces, exceptions, dependencies |
| Custom metric | `ingest_latency_ms`: IoT Hub enqueue to Function processing |
| Alert: latency | p95 `ingest_latency_ms` above 60 s over 15 min (severity 2) |
| Alert: missing telemetry | One alert per device that sent telemetry in the last day but nothing for 15 min (severity 1); based on the Function's per-message log line; resolves when the device sends again |
| Action group | `ag-<app>-<env>`: both alerts call `https://<api>/api/alerts/azure-monitor?token=…` with the common alert schema |
| Workbook | "EMS1000 Pipeline Health" (placeholder content) |
