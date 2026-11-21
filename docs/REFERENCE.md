# API and data reference

What the API offers, what the Function stores and where, how IoT Hub routes messages, and which
settings each app reads. For the device side of the contract (telemetry fields, direct-method
replies, twin settings), see the
[device build reference](https://github.com/okeyf16/ems1000-device-sim/blob/main/docs/BUILD_REFERENCE.md).

## API

FastAPI on App Service. Interactive docs and a test page are at `<api-base-url>/docs` (the base
URL is the Terraform output `api_base_url`).

**API key.** Every endpoint except `/healthz` needs a key in the `X-API-Key` header; on `/docs`,
select **Authorize** and paste it. Two keys are valid at once (Terraform output `api_keys`:
`primary`, `secondary`), so either can be replaced without downtime ([runbook](RUNBOOK.md#rotating-keys)).
Missing or wrong key: **401**. No keys configured on the server: **503**.

**Times are WAT** (West Africa Time, UTC+1, no daylight saving) in every reply, written with
their offset, e.g. `2026-11-18T23:10:05+01:00`. Storage, logs and the Azure portal stay in UTC:
add one hour to compare. Day boundaries for daily figures use midnight WAT (23:00 UTC).

| Method | Path | Returns | Reads from |
|---|---|---|---|
| GET | `/healthz` | `{"status":"healthy"}`; App Service's health probe. No key | — |
| GET | `/api/devices/{id}/state` | The last stored status, in the [state shape](#state-reply) | Table `DeviceState` (free, works offline) |
| GET | `/api/devices/{id}/state?live=true` | The device's status right now, same shape | IoT Hub `get_status` (uses the message budget; 504 if offline) |
| GET | `/api/devices/{id}/live` | **Deprecated** alias of `state?live=true` | IoT Hub |
| GET | `/api/devices/{id}/telemetry?limit=N` | The last N messages as sent, newest first (N 1–500, default 20), plus `received_at` and `device_time` in WAT | Table `DeviceTelemetry` |
| POST | `/api/devices/{id}/command` | Sends a direct method and mirrors the device's reply | IoT Hub (device must be online) |
| GET | `/api/devices/{id}/twin` | Desired settings, reported properties, connection state | IoT Hub twin (works while offline) |
| PATCH | `/api/devices/{id}/twin` | Changes desired settings (only those sent) | IoT Hub twin |
| DELETE | `/api/devices/{id}/twin/settings[?name=…]` | Clears all six settings, or one, so the device returns to its config values | IoT Hub twin |
| GET | `/api/alerts[?device_id=&status=active\|all&limit=]` | Alerts, newest first ([alerts](#alerts)) | Table `Alerts` |
| POST | `/api/alerts/azure-monitor?token=…` | Webhook for Azure Monitor; not for clients, hidden from `/docs` | Writes `Alerts` |

### State reply

`/state` and `/state?live=true` return the same shape; `source` says which.

```json
{
  "device_id": "ems-sim-01",
  "source": "stored",
  "received_at": "2026-11-18T23:10:05+01:00",
  "age_s": 42,
  "readings": {
    "v": 229.8, "i": 6.8, "f": 50.4, "fuel": 64.0, "oil": 0, "oil_state": "ok",
    "t": 71.5, "vb": 12.45, "soc_pct": 82, "soc_reliable": false, "load_pct": 50.0
  },
  "status": { "run": 2, "run_state": "Running", "flt": 0, "faults": [], "warnings": [] },
  "link": { "rssi": -71, "boot": 7, "seq": 120, "sv": 1, "ts": 1795030205000,
            "device_time": "2026-11-18T23:10:05+01:00",
            "connection_status": "Online", "last_status_change": "2026-11-18T22:58:41+01:00" }
}
```

| Group | Field | Meaning |
|---|---|---|
| top | `source` | `stored` (last message in the table) or `device` (asked live) |
| top | `received_at`, `age_s` | When the cloud received it (WAT) and how many seconds ago; live replies are 0 s old |
| readings | `v`, `i`, `f`, `fuel`, `t`, `vb` | Volts, amps, hertz, fuel %, engine temperature °C, starter battery volts, as sent |
| readings | `oil`, `oil_state` | Oil switch: 0 `ok`, 1 `low`, 2 `critical` (a float switch, not a measured level) |
| readings | `soc_pct` | Battery state of charge, **estimated** from `vb` on a 12 V lead-acid curve (11.8 V = 0 %, 12.2 V = 50 %, 12.7 V = 100 %) |
| readings | `soc_reliable` | `true` only with the engine off (`run` 0 or 4); cranking or charging skews the voltage |
| readings | `load_pct` | `i` as a percentage of rated current (`RATED_CURRENT_A`, default 13.6 A = 3.0 kVA at 220 V) |
| status | `run`, `run_state` | 0 Off, 1 Cranking, 2 Running, 3 Stopping, 4 Fault |
| status | `flt`, `faults`, `warnings` | The fault bitmask and its decoded names (see [the four non-reading fields](#the-four-non-reading-fields)) |
| link | `rssi`, `boot`, `seq`, `sv`, `ts`, `device_time` | Connection and message bookkeeping; `device_time` is `ts` in WAT. A live reply only has what the device reports |
| link | `connection_status`, `last_status_change` | `Online`/`Offline` from IoT Hub connect/disconnect events (Event Grid) and when it last changed (WAT). `null` until the device's first event, and in live replies. A power cut shows as Offline only once IoT Hub's keep-alive expires (about 1.5 × the device's MQTT keep-alive) |

### The four non-reading fields

Telemetry carries four fields that aren't gauges but matter to the cloud:

| Field | What it is | Values | Why the cloud needs it |
|---|---|---|---|
| `sv` | Schema version of the message | `1` today | Lets the cloud read old and new devices side by side when telemetry v2 arrives (predictive-maintenance summaries). Without it a format change breaks every deployed unit |
| `flt` | Fault and warning bitmask | Bits 0–3 latched faults: 0 low oil, 1 low fuel, 2 over-temperature, 3 low battery. Bits 4–5 warnings: 4 voltage, 5 frequency out of range. `0` = all clear | Drives the start pre-check, fault lockout, alerts, the "why won't it start?" card and the panel lamps. The API decodes it into `faults` and `warnings` |
| `boot` | Restart counter, +1 each power-up (kept in flash) | 1, 2, 3, … | With `seq` it makes every message unique, which is how history avoids duplicates; a `seq` gap within one boot means lost messages; frequent boots point to power or brown-out problems |
| `rssi` | Mobile signal strength, dBm | About −50 (excellent) to −110 (unusable); below about −100 expect drop-outs | Explains why a unit goes offline, warns before it goes silent, and helps choose where to mount it |

`seq` (message counter, restarts at 1 each boot) and `ts` (device clock, epoch milliseconds, UTC)
complete the set.

### Alerts

`GET /api/alerts` returns `{"count", "alerts": [...]}`, newest first. Each alert:
`id`, `device_id` (`null` for pipeline-wide alerts such as latency), `rule`, `severity`
(`Sev0`–`Sev4`), `status` (`active` or `resolved`), `description`, `source`, `fired_at`,
`resolved_at` (WAT). `status=active` lists only alerts still firing.

| `rule` | `source` | Raised when | Resolves when |
|---|---|---|---|
| `alert-missing-telemetry-<env>` | `azure-monitor` | A device that sent in the last day has been silent for `missing_telemetry_silence_minutes` | It sends again (three clean checks) |
| `alert-latency-<env>` | `azure-monitor` (`device_id` null) | p95 ingest latency over 60 s in `latency_alert_failing_periods` of `latency_alert_evaluation_periods` 15-minute checks (default 2 of 2) | A check is healthy again |
| `device-offline` | `event-grid` | Disconnected (Event Grid) for `offline_grace_minutes` (default 2) | It reconnects, or sends telemetry (a missed Connected event) |

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
| Columns | Every telemetry field as sent (`sv`, `ts`, `boot`, `seq`, `v`, `i`, `f`, `fuel`, `oil`, `t`, `vb`, `flt`, `run`, `rssi`), plus `enqueued_time` (when IoT Hub received it, UTC). From connection events: `connection_status`, `last_status_change` (UTC), `connection_seq` (IoT Hub's sequence number, so a late event can't overwrite a newer one) |

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

| Key | Value |
|---|---|
| PartitionKey | Device ID from the alert's `device_id` dimension, or `_system` |
| RowKey | The Azure Monitor alert ID (last path segment), so "Resolved" updates the row "Fired" created; `offline-<disconnect epoch ms>` for Device offline alerts |
| Columns | `rule`, `severity`, `condition` (`Fired`/`Resolved`), `fired_at`, `resolved_at` (UTC), `description`, `signal_type`, `source`, `alert_id` |

Written by the webhook (token in `ALERT_WEBHOOK_TOKEN`) and by the Function's `offline_check`;
read by `GET /api/alerts`. `history_purge` deletes resolved alerts older than
`ALERTS_RETENTION_DAYS` (30); active ones are kept. Predictive-maintenance alerts will be
written here too.

### Table `OfflinePending`: devices currently disconnected

| Key | Value |
|---|---|
| PartitionKey | `pending` |
| RowKey | Device ID |
| Columns | `since` (disconnect time, UTC), `alerted` (has the Device offline alert been raised), `alert_row` (its RowKey in `Alerts`) |

A row is added on DeviceDisconnected and removed on reconnect (or when telemetry shows the
device is back). `offline_check` reads only this partition each minute, so its cost doesn't
grow with the number of devices.

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
| `history_purge` | Timer `0 15 1 * * *` (01:15 UTC daily) | Deletes `DeviceTelemetry` rows older than the retention, and resolved alerts older than `ALERTS_RETENTION_DAYS` |
| `device_lifecycle_trigger` | Event Grid subscription `device-lifecycle-to-function` (DeviceConnected, DeviceDisconnected) | Sets `connection_status`, `last_status_change`, `connection_seq` in `DeviceState`, ignoring events older than the stored sequence number. Disconnect: adds the device to `OfflinePending`. Connect: removes it and resolves its Device offline alert |
| `offline_check` | Timer `0 */1 * * * *` (every minute) | For each `OfflinePending` row: if telemetry arrived after the disconnect, marks the device Online; else after `OFFLINE_GRACE_MINUTES` writes a `device-offline` alert. Logs only when it acts. Also keeps the Function warm, which cuts cold-start latency |

## App settings

Set by Terraform; secrets are built from other resources' keys at apply time and never stored
in the repo.

**Function App**

| Setting | Value |
|---|---|
| `IOTHUB_EVENTS_CONNECTION_STRING` | Event Hub-compatible connection string for the hub's built-in endpoint |
| `IOTHUB_EVENTS_PATH` | Event Hub-compatible name; must equal the `EntityPath` in the string above |
| `AzureWebJobsStorage` | Storage account connection string (tables and dead-letter); set by the provider from the storage account name and key |
| `HISTORY_TABLE_NAME`, `HISTORY_RETENTION_DAYS` | `DeviceTelemetry`, `9` |
| `ALERTS_TABLE_NAME`, `ALERTS_RETENTION_DAYS` | `Alerts`, `30` |
| `OFFLINE_PENDING_TABLE_NAME`, `OFFLINE_GRACE_MINUTES` | `OfflinePending`, `2` (`offline_grace_minutes`) |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | App Insights; set through `site_config` |
| `WEBSITE_RUN_FROM_PACKAGE` | Set by the deploy workflow; Terraform ignores it |
| `WEBSITE_ENABLE_SYNC_UPDATE_SITE` | Added by the deploy workflow; may be missing until the next Function deploy (not needed at runtime); Terraform ignores it |

**Web App (API)**

| Setting | Value |
|---|---|
| `STORAGE_CONN_STR` | Storage account connection string |
| `IOTHUB_CONNECTION_STRING` | Hub connection string (direct methods, twins) |
| `HISTORY_TABLE_NAME` | `DeviceTelemetry` |
| `ALERTS_TABLE_NAME` | `Alerts` |
| `ALERT_WEBHOOK_TOKEN` | Secret the action group sends in the webhook URL (`?token=…`); generated by Terraform |
| `API_KEYS` | The two API keys, comma-separated; generated by Terraform |
| `RATED_CURRENT_A` | Optional; rated current for `load_pct` (default 13.6) |
| `APPLICATIONINSIGHTS_CONNECTION_STRING`, `OTEL_SERVICE_NAME` | App Insights; service name `ems-api` |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` (App Service installs `requirements.txt`) |

Start command: `gunicorn -w 2 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 main:app`.

## Monitoring

| Item | Detail |
|---|---|
| Log Analytics | 30-day retention, daily cap `daily_quota_gb` (1 GB) |
| Application Insights | Workspace-based; Function and API send traces, exceptions, dependencies |
| Custom metric | `ingest_latency_ms`: IoT Hub enqueue to Function processing |
| Alert: latency | p95 `ingest_latency_ms` above 60 s in `latency_alert_failing_periods` of the last `latency_alert_evaluation_periods` 15-minute checks (default 2 of 2, so about 30 min of slowness); severity 2; auto-resolves at the next healthy check. Set both to 1 for the old one-check behaviour |
| Alert: device offline | Not an Azure Monitor rule: the Function writes it after `offline_grace_minutes` (2) of disconnection; severity 1; resolves on reconnect. About 2–4 min after a clean disconnect; after a power cut add IoT Hub's keep-alive timeout |
| Alert: missing telemetry | One alert per device that sent telemetry in the last day but nothing for 5 min (`missing_telemetry_silence_minutes`), checked every 5 min (`missing_telemetry_check_minutes`); severity 1; based on the Function's per-message log line. Fires about 7–12 min after a device goes quiet and resolves about 15 min after it returns (Azure needs three clean checks). Keep the silence above 3× the heartbeat interval |
| Action group | `ag-<app>-<env>`: both alerts call `https://<api>/api/alerts/azure-monitor?token=…` with the common alert schema |
| Workbook | "EMS1000 Pipeline Health" (placeholder content) |
