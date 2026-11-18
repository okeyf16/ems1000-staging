# Smoke tests after a deploy

Quick checks that a change landed and the pipeline still works end to end. Run the group that
matches what you changed; run all of them after first-time setup or a key rotation. Record results
in the **Test log** tab of the milestones sheet (IDs `S-…`).

The full device-side test plan (commands, faults, twin, panel) lives in the
[simulator's live test guide](https://github.com/okeyf16/ems1000-device-sim/blob/main/docs/TESTING.md).

## Before you start

| What | Where |
|---|---|
| A device sending telemetry | The [device simulator](https://github.com/okeyf16/ems1000-device-sim) in its Codespace: `python cli.py run --interval 10` |
| API test page | `<api-base-url>/docs` (Terraform output `api_base_url`) |
| API key | Terraform output `api_keys` (or Web App → **Environment variables** → `API_KEYS`). On `/docs` select **Authorize** once; every call below except `/healthz` needs it |
| Logs | Function App → **Application Insights** → **Logs** |
| Message budget | Each test below uses a handful of the hub's 8,000 daily messages; stop the simulator (Ctrl+C) between groups |

`<id>` is your test device (e.g. the simulator's device ID).

## After an infrastructure apply

| ID | Check | How | Expected |
|---|---|---|---|
| S-01 | Apply succeeded with no surprises | HCP Terraform run | "Apply complete"; no resource replaced or destroyed unless intended |
| S-02 | Routes in place | IoT Hub → **Message routing** | `telemetry-to-builtin` (endpoint `events`) and `telemetry-to-archive`, both enabled |
| S-03 | App settings intact | Function App and Web App → **Environment variables** | Settings as in [REFERENCE.md](REFERENCE.md#app-settings); Function still has `WEBSITE_RUN_FROM_PACKAGE` |
| S-04 | Pipeline still flows | Run the simulator for 3 messages, then `GET /api/devices/<id>/state` | `enqueued_time` within the last minute |
| S-05 | Alerts wired to the action group | Azure Monitor → **Alerts** → **Alert rules** | Both rules enabled, each with action group `ag-<app>-<env>`; the group has webhook `api-alerts` |
| S-06 | Missing-telemetry alert fires | Send a few messages, stop the device for 20–30 min | Monitor → **Alerts** shows a *Fired* alert for `<id>`; it resolves after the device sends again |

## After a Function deploy

| ID | Check | How | Expected |
|---|---|---|---|
| S-10 | Workflow green | GitHub **Actions** → *Deploy Azure Function* | All steps succeed |
| S-11 | Functions indexed | Function App → **Overview** → Functions list | `telemetry_ingest`, `history_purge`, `device_lifecycle_trigger` |
| S-12 | Messages stored | Send 3 messages; `GET /api/devices/<id>/telemetry?limit=3` | 3 rows, newest first, `seq` increasing as you go back up |
| S-13 | No errors | Logs: `exceptions \| where timestamp > ago(15m)` | No rows |
| S-14 | Dead-letter path works (optional) | Send one non-JSON device message: `az iot device send-d2c-message --hub-name <hub> --device-id <id> --data 'not json'` | Blob appears under `dead-letter/YYYY/MM/DD/HH/` with the error and raw body |

## After an API deploy

| ID | Check | How | Expected |
|---|---|---|---|
| S-20 | Workflow green | GitHub **Actions** → *Deploy FastAPI App Service* | All steps succeed |
| S-21 | API up | `GET <api-base-url>/healthz` (first call after idle can take ~30 s on F1) | `{"status":"healthy",…}` |
| S-22 | State | `/docs` → `GET /api/devices/<id>/state` | 200 with the last message |
| S-23 | Command round trip | Simulator running; `POST …/command {"command":"get_status"}` | 200 with the device's status payload |
| S-24 | Offline handling | Stop the simulator; repeat S-23 | 504 "Device unreachable…" |
| S-25 | Twin read | `GET /api/devices/<id>/twin` | 200; `connection_state` and `reported` present |
| S-26 | Twin validation | `PATCH …/twin {"speed": 0}` | 400 with `must be between 1 and 3600`; nothing sent |
| S-27 | Key required | `GET /api/devices/<id>/state` without a key, then with one (on `/docs`: **Authorize**) | 401, then 200 |
| S-28 | New state shape | `GET /api/devices/<id>/state` | `source: stored`, `readings`/`status`/`link`, `received_at` ending `+01:00`, `soc_pct`, `load_pct` |
| S-29 | Live state | Device running; `GET …/state?live=true`; also the old `/live` | `source: device`, `age_s` 0, same shape; `/live` marked deprecated in `/docs` |

## Alerts (after PR 1 and the API change)

| ID | Check | How | Expected |
|---|---|---|---|
| S-35 | Webhook rejects strangers | `POST <api>/api/alerts/azure-monitor?token=wrong` with `{}` | 401 |
| S-36 | Fired alert reaches the API | Run S-06 (stop the device 20–30 min) | `GET /api/alerts?status=active` lists `alert-missing-telemetry-…` for `<id>`, times in WAT |
| S-37 | Resolved updates the same alert | Start the device again; wait up to 15 min | Same `id`, now `status: resolved` with `resolved_at` |
| S-38 | Panel shows alerts | Simulator panel, Alerts box | The alert appears, then shows resolved |

## Retention (run once after setup, then occasionally)

| ID | Check | How | Expected |
|---|---|---|---|
| S-30 | Raw archive written | Storage account → **Storage browser** → `telemetry-archive` (wait 5–10 min after sending) | Blobs under `<hub>/<partition>/YYYY/MM/DD/HH/mm`, readable JSON |
| S-31 | History purge runs | Function App → `history_purge` → **Code + Test** → **Test/Run**, or check the run after 01:15 UTC | Succeeds; log line "history_purge: deleted N rows…" |
| S-32 | Archive lifecycle rule | Storage account → **Lifecycle management** | Rule `telemetry-archive-expiry`, delete after 30 days |

## Tester access (after adding someone to `testers.auto.tfvars`)

Run by the tester, signed in with their guest account.

| ID | Check | How | Expected |
|---|---|---|---|
| S-40 | Can see the hub | Portal → the hub → **Devices** | Device list loads (portal uses their Entra account, not keys) |
| S-41 | Can create a device | **Add device** `ems-<name>-01` | Created |
| S-42 | Can set a twin and call a method | Device → **Device twin** edit desired; **Direct method** `get_status` (device online) | Saved; method returns (or 504-style timeout if offline) |
| S-43 | Can't delete | Select their device → **Delete** | Refused: authorization failed (403) |
| S-44 | Can't see keys | Hub → **Shared access policies** | Keys not shown / access denied |

## Command line alternative

The same checks without the portal (needs `az login` and the `azure-iot` extension):

```bash
# Messages used today (F1 limit 8,000)
az monitor metrics list --resource <hub-resource-id> --metric dailyMessageQuotaUsed --interval PT1H -o table

# Watch messages arrive at the hub
az iot hub monitor-events --hub-name <hub> --device-id <id>

# Call a direct method straight on the hub
az iot hub invoke-device-method --hub-name <hub> --device-id <id> --method-name get_status --method-payload '{}'

# API
curl -s <api-base-url>/healthz
curl -s -H "X-API-Key: <key>" <api-base-url>/api/devices/<id>/state
```
