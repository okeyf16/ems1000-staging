# Deploy and operate runbook

How changes reach Azure, how to set the system up from scratch, and the everyday operations:
adding a device, rotating keys, keeping costs inside the free tiers, rolling back. Known issues
are listed at the end.

Names below use the patterns from the [README](../README.md#what-is-deployed): `<hub>` is the
IoT Hub (`iot-<app>-<env>`), `<func>` the Function App, `<webapp>` the API's Web App and `<rg>`
the resource group. Never paste a key or connection string into a doc, issue or PR.

## How changes are deployed

| Area | Trigger | Runs | Applies automatically? |
|---|---|---|---|
| Infrastructure (`infra/**`) | PR opened or merged to `main` | HCP Terraform workspace (VCS-driven, working directory `infra`) | **No.** Plans run automatically; you confirm the apply |
| Function (`src/function/**`, or its workflow file) | Push to `main`, or manual run | `.github/workflows/deploy-function.yml` | Yes |
| API (`src/api/**`) | Push to `main`, or manual run | `.github/workflows/deploy-api.yml` | Yes |

### Infrastructure (HCP Terraform)

1. Branch, change `infra/`, open a PR. HCP Terraform posts a speculative plan on the PR.
2. Read the plan. Stop if it wants to **replace** or **destroy** anything you didn't intend (see
   [Troubleshooting → Terraform](TROUBLESHOOTING.md#terraform-hcp-terraform)).
3. Merge. HCP Terraform runs a plan on `main`.
4. HCP Terraform → workspace → the run → **Confirm & apply**.
5. Run the smoke tests in [TESTING.md](TESTING.md) that match what changed.

The IoT Hub has `prevent_destroy = true`: any plan that would delete it fails instead of
applying. Replacing the hub would delete every device identity and change every key.

### Function and API (GitHub Actions)

Merging to `main` deploys whatever changed under `src/function/` or `src/api/`. Watch the run in
the **Actions** tab, then run the matching smoke tests. To redeploy without a code change, open
the workflow in **Actions** → **Run workflow** on `main`.

The Function is built for the Azure Functions Linux image (`manylinux2014_x86_64`, CPython
3.11), not for the GitHub runner. Don't remove those `pip` flags: packages built for the runner
import-fail on Azure and the Function then shows no functions.

## First-time setup

Needed once per environment (or when moving to a new subscription).

1. **Azure identity for automation.** Create a service principal with **Contributor** on the
   subscription (or the resource group, once it exists), plus **User Access Administrator** on the
   resource group so Terraform can create the tester role and its assignments (`infra/access.tf`).
   Contributor alone can't write role definitions or assignments.
2. **HCP Terraform workspace.** VCS-driven, connected to this repo, working directory `infra`,
   auto-apply off. Set:
   - Environment variables (sensitive): `ARM_CLIENT_ID`, `ARM_CLIENT_SECRET`, `ARM_TENANT_ID`,
     `ARM_SUBSCRIPTION_ID`.
   - Terraform variables: at least `env_prefix`; optionally `location`, `app_name`,
     `daily_quota_gb`, `history_retention_days`, `archive_retention_days`.
   HCP Terraform does **not** read `infra/environments/*.tfvars` by itself. Either enter the
   values as workspace variables, or pass the file with the workspace environment variable
   `TF_CLI_ARGS_plan = -var-file=environments/<env>.tfvars`.
   The organisation and workspace names are in `infra/backend.tf`.
3. **Apply** the first run in HCP Terraform. Note the outputs: resource group, storage account,
   API base URL.
4. **GitHub repository settings** → Secrets and variables → Actions:
   - Secret `AZURE_CREDENTIALS`: the service principal as JSON (`clientId`, `clientSecret`,
     `subscriptionId`, `tenantId`).
   - Variable `FUNCTION_APP_NAME`: `<func>`.
   - Variable `WEB_APP_NAME`: `<webapp>`.
5. **Deploy the code:** run both workflows once from **Actions** → **Run workflow**.
6. **Add a device** (next section) and run [TESTING.md](TESTING.md) end to end.

## Adding a device

Device identities are created with the Azure CLI, not Terraform (the `azurerm` provider doesn't
manage them). Each generator gets its own identity and key; never share one between units.

```bash
az extension add --name azure-iot          # once
az iot hub device-identity create --hub-name <hub> --device-id <device-id>
az iot hub device-identity connection-string show --hub-name <hub> --device-id <device-id> -o tsv
```

Give the connection string to whoever provisions the device (for the simulator: the Codespace
secret `IOTHUB_DEVICE_CONNECTION_STRING`). The `id` field in the device's telemetry has to equal
`<device-id>`, or its data is filed under the wrong device.

Remove a device: `az iot hub device-identity delete --hub-name <hub> --device-id <device-id>`.
Its rows in `DeviceState` stay until deleted; history ages out after the retention period.

## Giving a tester access

External testers (e.g. the firmware engineer) use their own Microsoft Entra guest account; no
keys are shared for hub access. Defined in `infra/access.tf`, listed in
`infra/testers.auto.tfvars`.

1. Entra ID → **Users** → **Invite external user**. The guest account and its object ID exist
   at once; access starts when they accept.
2. Add a line to `infra/testers.auto.tfvars`: `name = "<object-id>"`. Open a PR, check the plan
   adds two role assignments, merge and apply.
3. Tell them: sign in at portal.azure.com, switch to your directory, open the hub. They can
   create their own devices (e.g. `ems-<name>-01`), set twins, call direct methods, and watch
   messages (see the note below).

| They can | They can't |
|---|---|
| See the hub (Reader); create and update devices; read and write twins; invoke direct methods; send cloud-to-device messages | Delete devices, run bulk import/export jobs, read or regenerate the hub's shared access keys, change routes or any Azure setting |

Role assignments can't be narrower than the hub, so a tester can also update other devices, for
example disable the simulator's device or regenerate its key. If that happens, re-provision it
(see [Adding a device](#adding-a-device)).

Watching live messages: the built-in events endpoint only accepts connection strings, not Entra
sign-in. Testers watch through the API's `/telemetry`, the simulator panel, or a
service-connect-only access policy you create for them (revocable) used with `--cg testing`.

Revoke: remove their line and apply, or delete the guest account (which removes its role
assignments too).

## Rotating keys

| Key | How | After |
|---|---|---|
| A device's key | `az iot hub device-identity renew-key --hub-name <hub> --device-id <id> --key-type primary` | Re-provision the device with the new connection string |
| `iothubowner` (used by the Function and the API) | IoT Hub → **Shared access policies** → `iothubowner` → regenerate the primary key | Run an HCP Terraform plan and apply: it rewrites both apps' settings from the new key |
| Storage account key | Storage account → **Access keys** → rotate key 1 | Plan and apply, as above |
| Automation service principal | Create a new client secret | Update `AZURE_CREDENTIALS` in GitHub and `ARM_CLIENT_SECRET` in HCP Terraform |
| API keys | HCP Terraform → new run → replace `random_password.api_key["secondary"]`; move clients to it; later replace `primary` the same way | Clients keep working on the other key throughout. Read keys from the sensitive output `api_keys` or Web App → **Environment variables** → `API_KEYS` |
| Alert webhook token | HCP Terraform → new run → replace `random_password.alert_webhook_token` | The apply updates both the API setting and the action group's webhook URL together |

Between regenerating a hub or storage key and the apply, the Function and API fail to
authenticate: do it in a quiet window and apply straight away.

## Data retention

| Data | Where | Kept | Set by |
|---|---|---|---|
| Latest state per device | Table `DeviceState` | Until overwritten | Function |
| Every message | Table `DeviceTelemetry` | 9 days (`history_retention_days`) | `history_purge`, daily 01:15 UTC |
| Raw messages | Blob `telemetry-archive` | 30 days (`archive_retention_days`) | Storage lifecycle rule |
| Unparseable messages | Blob `dead-letter` | Indefinitely (no rule yet) | Function |
| Resolved alerts | Table `Alerts` | 30 days (`alerts_retention_days`); active alerts kept | `history_purge`, daily 01:15 UTC |
| Disconnected devices | Table `OfflinePending` | Until the device reconnects | Function |
| Logs and traces | Log Analytics / App Insights | 30 days, capped at `daily_quota_gb` (1 GB) per day | Terraform |

## Costs and free-tier limits

The pilot is designed to cost close to nothing. The limits that matter:

| Service | Limit | What happens at the limit | Next step up |
|---|---|---|---|
| IoT Hub F1 | 8,000 messages a day, counted in 0.5 KB blocks; resets 00:00 UTC. One F1 hub per subscription | Further device messages are refused until reset | S1 (400,000 a day, billed in 4 KB blocks) |
| App Service F1 | No always-on (cold start after idle), 1 GB disk, a daily CPU quota | The API is stopped for the rest of the day if the quota is used | B1 (always-on enables itself; see `app_service` module) |
| Functions Consumption | Monthly free grant of executions and GB-seconds | Billed per use beyond it | Flex Consumption or Premium |
| Log Analytics | Daily cap 1 GB | Ingestion stops until the next day | Raise `daily_quota_gb` |
| Event Grid (Basic) | 100,000 operations a month free (each event = publish + delivery) | Billed per million operations beyond it; staging stays far below | No change needed until thousands of devices |

Message budget: a device sending every 60 s uses 1,440 messages a day, so F1 carries about five
devices. Direct methods, twin reads and twin updates count against the same 8,000. The device
simulator has a budget guard that stops at 7,500.

Check usage: IoT Hub → **Metrics** → *Total number of messages used*; App Service → **Quotas**;
Cost Management → **Cost analysis** filtered to `<rg>`.

## Rolling back

| What broke | Roll back by |
|---|---|
| Function or API code | Revert the PR on GitHub. The merge to `main` redeploys the previous code |
| Infrastructure | Revert the PR, let HCP Terraform plan, check it only undoes your change, then apply |
| A bad app setting changed by hand in the portal | Run an HCP Terraform plan and apply: Terraform restores what the code says (except `WEBSITE_RUN_FROM_PACKAGE`, which it ignores on purpose) |

## Tuning alerts

All in `infra/variables.tf` (or a workspace variable in HCP Terraform); apply to change.

| Variable | Default | Effect |
|---|---|---|
| `missing_telemetry_silence_minutes` | 5 | Silence before the missing-telemetry alert counts a device as quiet |
| `missing_telemetry_check_minutes` | 5 | How often that rule runs (1, 5, 10 or 15) |
| `offline_grace_minutes` | 2 | How long a device may stay disconnected before **Device offline** is raised. Raise it if quick reconnects (Wi-Fi blips, simulator restarts) cause noise |
| `latency_alert_evaluation_periods` / `latency_alert_failing_periods` | 2 / 2 | The latency alert fires when that many of the last 15-minute checks are slow; 1 / 1 fires on any single slow check |
| `alerts_retention_days` | 30 | How long resolved alerts stay in the `Alerts` table |

Devices should use an MQTT keep-alive of 60–120 s. IoT Hub only notices an unclean drop (power
cut) after about 1.5 × the keep-alive, so that time adds to `offline_grace_minutes`.

## Known issues

| # | Issue | Effect | Suggested fix | Status |
|---|---|---|---|---|
| 1 | `infra/environments/prod.tfvars` is a copy of the staging file (`env_prefix = "stg"`) | Using it for production would target the staging names | Before any production use: `env_prefix = "prd"`, its own HCP Terraform workspace, and its own subscription or resource group | **Fixed:** `prod.tfvars` removed until production is planned |
| 2 | The API has no authentication | Anyone with the URL can read data and send start/stop commands | Add an API key or Microsoft Entra ID sign-in before the URL is shared outside the team | **Fixed:** `X-API-Key` required on every endpoint except `/healthz`; two Terraform-generated keys. Move to Entra ID sign-in before customers |
| 3 | The two alert rules have no action group | They fire in the portal but notify nobody | Add an `azurerm_monitor_action_group` (email or push) and attach it to both rules | **Fixed:** action group `ag-<app>-<env>` calls the API's alerts webhook; alerts land in the `Alerts` table (shown once the API and panel changes ship) |
| 4 | The missing-telemetry alert counts a custom event `DeviceTelemetry` that the Function doesn't emit | The alert can't work as intended (it may fire constantly or never) | Base it on `traces` from `telemetry_ingest`, or have the Function emit that event per message | **Fixed:** rebuilt on the Function's per-message log line; one alert per silent device, auto-resolves |
| 5 | `device_lifecycle_trigger` (Event Grid) has no Event Grid subscription in Terraform | It never runs, so `connection_status` in `DeviceState` is never set. Connection state is available from the twin (`GET /twin`) | Add the Event Grid system topic and subscription for IoT Hub connect/disconnect events, or remove the trigger | **Fixed:** system topic `egst-iothub-<app>-<env>` and subscription `device-lifecycle-to-function`; `/state` shows `connection_status`; Device offline alert after a grace period |
| 6 | The Function and the API both use the `iothubowner` policy (full rights) | A leaked setting gives full control of the hub | Separate policies: the API with *service connect* + *registry read/write*, the Function with *service connect* only | Open |

Smaller tidy-ups: the `scaffold..yml` workflow was a one-off and can be removed; some Terraform
comments carry leftover `[cite: n]` markers. (The Function's duplicate `DeviceState` client
was removed with the Event Grid change.)

**Time zones:** everything in Azure (tables, logs, alerts, schedules) is UTC; the API, panel and
app show WAT (UTC+1). The purge at 01:15 UTC is 02:15 WAT; the IoT Hub quota resets at
00:00 UTC (01:00 WAT). The resource group is in North Europe while the Function runs in Sweden Central and the
API in France Central; that works, but keeping everything in one region is simpler when moving
off the free tiers.
