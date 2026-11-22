# Troubleshooting

Symptoms, likely causes and fixes for the cloud side. Device-side problems (the simulator, its
panel, local config) are in the
[simulator's troubleshooting guide](https://github.com/okeyf16/ems1000-device-sim/blob/main/docs/TROUBLESHOOTING.md).

**Where to look first:** Function App or Web App → **Application Insights** → **Logs**, then:

```kusto
traces     | where timestamp > ago(30m) | order by timestamp desc
exceptions | where timestamp > ago(30m) | order by timestamp desc
```

Function → **Monitor** → **Invocations** lists each run with its log. The API's console output
is in Web App → **Log stream**. IoT Hub metrics lag 5–10 minutes.

## Telemetry doesn't arrive

Check each hop in order: device → hub → route → Function → table.

| Symptom | Likely cause | Fix |
|---|---|---|
| Device says "sent", hub **Metrics** show nothing | Metrics delay | Wait 10 min; granularity 1 minute, aggregation Sum |
| Device can't connect at all | Wrong connection string, disabled device, or daily quota used up | Check the device identity is enabled; IoT Hub → **Metrics** → *Total number of messages used* (F1 stops at 8,000 until 00:00 UTC) |
| Hub receives, but "Routing: messages delivered" is 0 or "dropped" rises | No route to the built-in endpoint. With any custom route defined, messages only go where a route sends them | Route `telemetry-to-builtin` (endpoint `events`) must exist and be enabled; re-apply Terraform |
| Routed, but the Function never runs | Function not deployed or not indexed | Function App → Functions list must show `telemetry_ingest`, `history_purge`, `device_lifecycle_trigger` and `offline_check`; if empty, see the next row |
| Functions list is empty after a deploy | Python packages failed to import (built for the wrong platform, or a missing dependency) | Packages must be built for `manylinux2014_x86_64` / CPython 3.11, as the workflow does. Look for `ModuleNotFoundError` or `ImportError` in `traces`/`exceptions` |
| Function runs but the trigger never fires | `IOTHUB_EVENTS_PATH` doesn't match the `EntityPath` in `IOTHUB_EVENTS_CONNECTION_STRING` | Both come from Terraform; re-apply rather than editing by hand |
| Runs fail with `can't subtract offset-naive and offset-aware datetimes` | The trigger's enqueue time has no timezone | Handled by `_as_utc` in the Function; redeploy if it reappears |
| Runs fail on the table write ("out of range" / type error) | Large integers (`ts` in ms) sent as 32-bit | Handled by Int64 `EntityProperty` in `_to_table_value`; redeploy if it reappears |
| `DeviceState` updates but history is empty | `DeviceTelemetry` table missing, or `HISTORY_*` settings absent | Terraform creates both; re-apply and check the Function's settings |
| Messages land in `dead-letter` | Body isn't valid JSON (or the table write failed) | Open the blob: it holds the error and the raw body. Fix the sender |
| Data filed under the wrong device | The telemetry `id` field differs from the IoT Hub device ID | Make the firmware send its device ID as `id` |
| Archive blobs are unreadable (base64) | Device doesn't set content type and encoding | Send with `$.ct=application%2Fjson&$.ce=utf-8` on the topic |

## API

| Symptom | Likely cause | Fix |
|---|---|---|
| 401 "Missing or invalid API key" | No `X-API-Key` header, or an old key after rotation | Send a current key (output `api_keys`); on `/docs` use **Authorize** |
| 503 "API keys are not configured" | `API_KEYS` missing on the Web App | Re-apply Terraform |
| Terraform apply fails creating `azuread_application.api` (Authorization_RequestDenied / insufficient privileges) | The HCP service principal lacks the Graph permission | RUNBOOK, *Microsoft sign-in → One-time setup*, then re-run the apply |
| Microsoft login says the account isn't in the tenant (AADSTS50020) | Signing in with an account from another directory | Invite that person as a guest in your tenant first |
| Login error AADSTS50011 (redirect URI mismatch) | The Web App's name changed | Re-apply: the redirect URI is built from the same name |
| `/docs` opens without asking to sign in | You're already signed in (session cookie) | `/.auth/logout` to sign out |
| Signed in, but calls give 401 | `ENTRA_SIGNIN` missing (apply not done yet) | Apply in HCP Terraform; meanwhile use **Authorize** with an API key |
| `/api/access` misses the last minute | Counts are written at most once a minute per worker | Wait a minute |
| First request takes ~30 s | F1 has no always-on: the app sleeps when idle | Expected on F1. B1 or above enables always-on automatically |
| 503 from every endpoint | App failed to start (import error after a deploy) or the F1 daily CPU quota is used up | Web App → **Log stream**; App Service → **Quotas**. Redeploy or wait for the quota reset |
| Alerts never appear in `GET /api/alerts` | Webhook token mismatch (401 in Web App logs), or the action group not attached | Re-apply Terraform (it sets the token on both sides); Monitor → **Alert rules** → check the action group |
| `/state` returns 404 | No telemetry yet for that device ID, or a typo | Send a message; check the ID matches the device identity |
| `/state` data is old | The device is stopped, or telemetry isn't reaching the Function | Check the [telemetry section](#telemetry-doesnt-arrive). `/live` asks the device directly |
| `/telemetry` returns 500 | History table missing or `HISTORY_TABLE_NAME` wrong | Re-apply Terraform |

`POST /api/devices/{id}/command` mirrors the device's answer:

| Code | Meaning | Usually |
|---|---|---|
| 200 | Device accepted | Fine |
| 400 | Device refused as invalid now ("already running"), or unknown command | Check the device state; valid commands are `start`, `stop`, `reset_fault`, `get_status` |
| 404 | Device doesn't know the method | Old firmware, or a typo in a method name sent from the portal |
| 409 | Safety interlock or fault lockout (e.g. "Low Oil Level Warning") | Clear the condition, then `reset_fault` |
| 502 | Device returned an error | Check the device's own logs |
| 504 | Device offline or no reply within 15 s | Start or reconnect the device and retry |

## Device twin

| Symptom | Likely cause | Fix |
|---|---|---|
| PATCH returns 200 but nothing changes | Device offline (applies on next connect), or it rejected the value | `GET /twin`: check `connection_state` and `reported.settings_status` |
| PATCH returns 400 | Unknown setting or out of range | Ranges are in [REFERENCE.md](REFERENCE.md#twin-settings) |
| "Clear settings" example arrives empty | FastAPI drops `null` from `/docs` examples | Use `DELETE …/twin/settings` |
| 404 "not found in IoT Hub" | No device identity with that ID | Create it ([runbook](RUNBOOK.md#adding-a-device)) |
| 502 "IoT Hub twin … failed" | Hub unreachable or the API's key is wrong (e.g. after rotation without an apply) | Re-apply Terraform; check `IOTHUB_CONNECTION_STRING` on the Web App |

## IoT Hub quota

| Symptom | Likely cause | Fix |
|---|---|---|
| Portal banner "You've reached your daily message quota"; devices get 403 | 8,000 messages used today (telemetry, method calls and twin traffic in 0.5 KB blocks, all devices and the API together) | Wait for 00:00 UTC (01:00 WAT). Find the cause: IoT Hub → **Metrics** split by type, and the `Connections` query in the runbook |
| Thousands of `deviceConnect`/`deviceDisconnect` in a few minutes | Two clients with the same device ID knocking each other off | Stop the extra client (simulator copy, container, another Codespace). The simulator now refuses a second copy and stops itself on a reconnect loop |
| Quota alert at 70 % early in the day | Short heartbeat interval, frequent `?live=true` or twin calls, or a loop | Check the metrics; use `--interval 60` on the simulator |

## Connection state and Device offline

| Symptom | Likely cause | Fix |
|---|---|---|
| `link.connection_status` stays `null` | No connect/disconnect event yet, or the Event Grid subscription is missing | Restart the device once; check IoT Hub → **Events** for `device-lifecycle-to-function` |
| Terraform apply fails creating the Event Grid subscription ("endpoint validation" / function not found) | The function wasn't deployed when the subscription was created | Merge so *Deploy Azure Function* runs, then re-run the HCP Terraform apply |
| Status says Offline while the device is sending | A Connected event was missed | `offline_check` corrects it within a minute once telemetry arrives; nothing to do |
| Offline shows several minutes after a power cut | IoT Hub waits for the MQTT keep-alive to expire before it reports the drop | Expected; use a 60–120 s keep-alive on devices |
| Too many Device offline alerts from quick reconnects | Grace period shorter than typical blips | Raise `offline_grace_minutes` |
| Device offline alert never resolves | Reconnect event missed and no telemetry since | It resolves on the next connect or message; if the device is really gone, it stays active (by design) |
| Event Grid deliveries failing | Function errors or cold start timeouts | IoT Hub → **Events** → the subscription → **Metrics** (*Delivery failed*); Function `exceptions` in App Insights |

## Deploy workflows

| Symptom | Likely cause | Fix |
|---|---|---|
| *Azure Login* step fails | `AZURE_CREDENTIALS` missing or the service principal's secret expired | Create a new secret; update the GitHub secret |
| Deploy step says the app wasn't found | `FUNCTION_APP_NAME` or `WEB_APP_NAME` variable wrong | Set them to the names in the Terraform outputs / portal |
| Function deploy succeeds but code is the old version | Run-from-package URL unchanged, or Terraform removed `WEBSITE_RUN_FROM_PACKAGE` | Terraform ignores that setting on purpose; rerun the workflow |
| API deploy succeeds, then 503 | A dependency failed to install on App Service | **Log stream** during start-up; pin the package in `src/api/requirements.txt` |

## Terraform (HCP Terraform)

| Symptom | Cause | Fix |
|---|---|---|
| Plan wants to **replace** the IoT Hub | Drift on an attribute that forces a new hub (e.g. `min_tls_version`) | Pin the value in code to match Azure. `prevent_destroy` makes such a plan fail rather than apply |
| Plan changes `WEBSITE_RUN_FROM_PACKAGE` every run | The deploy workflow sets it outside Terraform | `ignore_changes` on that setting (already in place) |
| Merge done but nothing changed in Azure | HCP Terraform plans automatically but waits for you to apply | Workspace → run → **Confirm & apply** |
| Plan fails: "No value for required variable env_prefix" | Workspace variables not set; HCP Terraform doesn't read `environments/*.tfvars` by itself | Set the variables in the workspace, or `TF_CLI_ARGS_plan` (see [runbook](RUNBOOK.md#first-time-setup)) |
| Apply fails creating an F1 IoT Hub | Only one F1 hub is allowed per subscription | Use the existing hub, or a different SKU or subscription |
| Apply fails on the storage account name | Names must be 3–24 lowercase letters and digits, globally unique | Shorten `app_name`/`env_prefix`; the random suffix handles uniqueness |
| Resource group won't delete | `prevent_deletion_if_contains_resources` is on | Delete the resources (or the IoT Hub's `prevent_destroy`) deliberately first |
