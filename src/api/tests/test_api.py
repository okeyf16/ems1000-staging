from types import SimpleNamespace

from conftest import KEY2, TOKEN, stored_row


# ---- API key -------------------------------------------------------------------------
def test_healthz_needs_no_key(api):
    assert api.client.get("/healthz").status_code == 200


def test_missing_key_is_401(api):
    r = api.client.get("/api/devices/ems-sim-01/state")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "ApiKey"


def test_wrong_key_is_401(api):
    r = api.client.get("/api/devices/ems-sim-01/state", headers={"X-API-Key": "nope"})
    assert r.status_code == 401


def test_second_key_works(api):
    api.state.rows[("ems-sim-01", "latest")] = stored_row()
    r = api.client.get("/api/devices/ems-sim-01/state", headers={"X-API-Key": KEY2})
    assert r.status_code == 200


def test_no_keys_configured_refuses(api, monkeypatch):
    monkeypatch.setenv("API_KEYS", "")
    r = api.client.get("/api/devices/ems-sim-01/state", headers=api.headers)
    assert r.status_code == 503


def test_every_device_route_is_protected(api):
    routes = [("get", "/api/devices/x/state"), ("get", "/api/devices/x/live"),
              ("get", "/api/devices/x/telemetry"), ("post", "/api/devices/x/command"),
              ("get", "/api/devices/x/twin"), ("patch", "/api/devices/x/twin"),
              ("delete", "/api/devices/x/twin/settings"), ("get", "/api/alerts")]
    for method, path in routes:
        assert getattr(api.client, method)(path).status_code == 401, path


def test_docs_offer_authorize(api):
    schema = api.client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["APIKeyHeader"]["name"] == "X-API-Key"
    assert "/api/alerts/azure-monitor" not in schema["paths"]


def test_docs_need_password_when_set(api, monkeypatch):
    import base64
    monkeypatch.setenv("DOCS_USERNAME", "ems-docs")
    monkeypatch.setenv("DOCS_PASSWORD", "docs-pass-123")
    for path in ("/docs", "/redoc", "/openapi.json"):
        r = api.client.get(path)
        assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    wrong = base64.b64encode(b"ems-docs:nope").decode()
    assert api.client.get("/docs", headers={"Authorization": f"Basic {wrong}"}).status_code == 401
    good = base64.b64encode(b"ems-docs:docs-pass-123").decode()
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert api.client.get(path, headers={"Authorization": f"Basic {good}"}).status_code == 200


def test_docs_password_does_not_replace_api_key(api, monkeypatch):
    import base64
    monkeypatch.setenv("DOCS_PASSWORD", "docs-pass-123")
    good = base64.b64encode(b"ems-docs:docs-pass-123").decode()
    r = api.client.get("/api/alerts", headers={"Authorization": f"Basic {good}"})
    assert r.status_code == 401                 # docs login alone opens no API endpoint
    assert api.client.get("/healthz").status_code == 200


# ---- /state ----------------------------------------------------------------------------
def test_stored_state_shape_and_wat(api):
    api.state.rows[("ems-sim-01", "latest")] = stored_row()
    body = api.client.get("/api/devices/ems-sim-01/state", headers=api.headers).json()
    assert body["source"] == "stored"
    assert body["received_at"] == "2026-11-18T23:10:05+01:00"
    assert body["readings"]["v"] == 229.8
    assert body["readings"]["oil_state"] == "ok"
    assert body["readings"]["load_pct"] == 50.0           # 6.8 A of 13.6 A
    assert body["readings"]["soc_pct"] in (82, 83)      # 12.45 V, halfway between 75 % and 90 %
    assert body["readings"]["soc_reliable"] is False      # engine running
    assert body["status"] == {"run": 2, "run_state": "Running", "flt": 0, "faults": [], "warnings": []}
    assert body["link"]["rssi"] == -71 and body["link"]["boot"] == 7 and body["link"]["sv"] == 1
    assert body["link"]["device_time"].endswith("+01:00")
    assert body["age_s"] >= 0


def test_connection_status_from_event_grid(api):
    api.state.rows[("ems-sim-01", "latest")] = stored_row(
        connection_status="Offline", last_status_change="2026-11-21T17:41:32.1234567+00:00")
    link = api.client.get("/api/devices/ems-sim-01/state", headers=api.headers).json()["link"]
    assert link["connection_status"] == "Offline"
    assert link["last_status_change"] == "2026-11-21T18:41:32+01:00"


def test_connection_status_null_before_first_event(api):
    api.state.rows[("ems-sim-01", "latest")] = stored_row()
    link = api.client.get("/api/devices/ems-sim-01/state", headers=api.headers).json()["link"]
    assert link["connection_status"] is None and link["last_status_change"] is None


def test_offline_alert_from_function_is_listed(api):
    # Row shape written by the Function's offline_check (lifecycle.offline_alert_row)
    api.alerts.rows[("ems-sim-01", "offline-1795282892000")] = {
        "PartitionKey": "ems-sim-01", "RowKey": "offline-1795282892000", "rule": "device-offline",
        "severity": "Sev1", "condition": "Fired", "fired_at": "2026-11-21T17:41:32+00:00",
        "description": "Device not connected to IoT Hub for at least 2 minutes.", "source": "event-grid"}
    alerts = api.client.get("/api/alerts?device_id=ems-sim-01&status=active", headers=api.headers).json()["alerts"]
    assert alerts[0]["rule"] == "device-offline" and alerts[0]["status"] == "active"
    assert alerts[0]["fired_at"] == "2026-11-21T18:41:32+01:00" and alerts[0]["source"] == "event-grid"


def test_faults_and_warnings_decoded(api):
    api.state.rows[("ems-sim-01", "latest")] = stored_row(flt=0b110001, run=4, oil=2)
    body = api.client.get("/api/devices/ems-sim-01/state", headers=api.headers).json()
    assert body["status"]["faults"] == ["low_oil"]
    assert body["status"]["warnings"] == ["voltage", "frequency"]
    assert body["status"]["run_state"] == "Fault"
    assert body["readings"]["oil_state"] == "critical"
    assert body["readings"]["soc_reliable"] is True


def test_unknown_device_404(api):
    r = api.client.get("/api/devices/nobody/state", headers=api.headers)
    assert r.status_code == 404


def test_live_state_same_shape(api):
    api.registry.reply = SimpleNamespace(status=200, payload={
        "result": "ok", "state": "OFF", "run": 0, "flt": 2,
        "readings": {"v": 0, "i": 0, "f": 0, "fuel": 3.0, "oil": 0, "t": 30.0, "vb": 12.7, "rssi": -80}})
    body = api.client.get("/api/devices/ems-sim-01/state?live=true", headers=api.headers).json()
    assert body["source"] == "device"
    assert body["age_s"] == 0
    assert body["readings"]["soc_pct"] == 100 and body["readings"]["soc_reliable"] is True
    assert body["status"]["faults"] == ["low_fuel"]
    assert body["link"]["rssi"] == -80
    assert api.registry.calls == [("ems-sim-01", "get_status")]


def test_live_alias_matches(api):
    api.registry.reply = SimpleNamespace(status=200, payload={"run": 0, "flt": 0, "readings": {"vb": 12.2}})
    body = api.client.get("/api/devices/ems-sim-01/live", headers=api.headers).json()
    assert body["source"] == "device" and body["readings"]["soc_pct"] == 50


def test_live_offline_504(api):
    api.registry.error = RuntimeError("device offline")
    r = api.client.get("/api/devices/ems-sim-01/state?live=true", headers=api.headers)
    assert r.status_code == 504


def test_soc_curve_edges(api):
    soc = api.main.estimate_soc
    assert soc(11.5) == 0 and soc(11.8) == 0 and soc(12.0) == 25 and soc(13.8) == 100
    assert soc(None) is None


# ---- telemetry history -----------------------------------------------------------------
def test_telemetry_times_in_wat(api):
    row = stored_row()
    row["RowKey"] = "0001"
    api.history.rows[("ems-sim-01", "0001")] = row
    body = api.client.get("/api/devices/ems-sim-01/telemetry?limit=5", headers=api.headers).json()
    item = body["items"][0]
    assert item["received_at"] == "2026-11-18T23:10:05+01:00"
    assert "enqueued_time" not in item


# ---- alerts ----------------------------------------------------------------------------
def _alert(condition, device="ems-sim-01", alert_id="/subscriptions/s/alerts/abc-123"):
    ess = {"alertId": alert_id, "alertRule": "alert-missing-telemetry-stg", "severity": "Sev1",
           "monitorCondition": condition, "firedDateTime": "2026-11-18T22:00:00.000Z",
           "description": "No telemetry for 15 minutes", "signalType": "Log"}
    if condition == "Resolved":
        ess["resolvedDateTime"] = "2026-11-18T22:30:00.000Z"
    ctx = {"condition": {"allOf": [{"dimensions": [{"name": "device_id", "value": device}]}]}} if device else {}
    return {"schemaId": "azureMonitorCommonAlertSchema", "data": {"essentials": ess, "alertContext": ctx}}


def test_webhook_rejects_bad_token(api):
    r = api.client.post("/api/alerts/azure-monitor?token=wrong", json=_alert("Fired"))
    assert r.status_code == 401
    r = api.client.post("/api/alerts/azure-monitor", json=_alert("Fired"))
    assert r.status_code == 401


def test_webhook_needs_no_api_key_and_stores(api):
    r = api.client.post(f"/api/alerts/azure-monitor?token={TOKEN}", json=_alert("Fired"))
    assert r.status_code == 200
    assert ("ems-sim-01", "abc-123") in api.alerts.rows


def test_fired_then_resolved_updates_one_row(api):
    api.client.post(f"/api/alerts/azure-monitor?token={TOKEN}", json=_alert("Fired"))
    active = api.client.get("/api/alerts?status=active", headers=api.headers).json()
    assert active["count"] == 1 and active["alerts"][0]["fired_at"] == "2026-11-18T23:00:00+01:00"
    api.client.post(f"/api/alerts/azure-monitor?token={TOKEN}", json=_alert("Resolved"))
    assert len(api.alerts.rows) == 1
    assert api.client.get("/api/alerts?status=active", headers=api.headers).json()["count"] == 0
    a = api.client.get("/api/alerts", headers=api.headers).json()["alerts"][0]
    assert a["status"] == "resolved" and a["resolved_at"] == "2026-11-18T23:30:00+01:00"


def test_system_alert_without_device(api):
    api.client.post(f"/api/alerts/azure-monitor?token={TOKEN}",
                    json=_alert("Fired", device=None, alert_id="/subscriptions/s/alerts/lat-1"))
    a = api.client.get("/api/alerts", headers=api.headers).json()["alerts"][0]
    assert a["device_id"] is None and a["id"] == "lat-1"


def test_alerts_filter_by_device(api):
    api.client.post(f"/api/alerts/azure-monitor?token={TOKEN}", json=_alert("Fired"))
    api.client.post(f"/api/alerts/azure-monitor?token={TOKEN}",
                    json=_alert("Fired", device="ems-paul-01", alert_id="/a/xyz"))
    body = api.client.get("/api/alerts?device_id=ems-paul-01", headers=api.headers).json()
    assert body["count"] == 1 and body["alerts"][0]["device_id"] == "ems-paul-01"


def test_alerts_status_validated(api):
    assert api.client.get("/api/alerts?status=bogus", headers=api.headers).status_code == 422


# ---- Microsoft sign-in and who's calling ---------------------------------------------------
import base64 as _b64  # noqa: E402
import json as _json  # noqa: E402

PAUL = {"X-MS-CLIENT-PRINCIPAL-ID": "0b6e-paul-oid", "X-MS-CLIENT-PRINCIPAL-NAME": "paul@example.com",
        "X-MS-CLIENT-PRINCIPAL": _b64.b64encode(_json.dumps(
            {"claims": [{"typ": "name", "val": "Paul Somoye"}]}).encode()).decode()}


def test_signed_in_person_needs_no_key(api, monkeypatch):
    monkeypatch.setenv("ENTRA_SIGNIN", "true")
    r = api.client.get("/api/whoami", headers=PAUL)
    assert r.status_code == 200
    assert r.json() == {"who": "Paul Somoye (paul@example.com)", "kind": "entra"}


def test_principal_headers_ignored_when_sign_in_is_off(api):
    assert api.client.get("/api/whoami", headers=PAUL).status_code == 401


def test_key_callers_are_named_by_key(api):
    assert api.client.get("/api/whoami", headers=api.headers).json() == {"who": "API key (primary)", "kind": "api-key"}
    second = api.client.get("/api/whoami", headers={"X-API-Key": "test-key-2"}).json()
    assert second["who"] == "API key (secondary)"


def test_access_is_recorded_and_listed(api, monkeypatch):
    monkeypatch.setenv("ENTRA_SIGNIN", "true")
    api.client.get("/api/alerts", headers=PAUL)
    api.client.get("/api/alerts", headers=PAUL)
    api.client.get("/api/alerts", headers=api.headers)
    r = api.client.get("/api/access?days=1", headers=PAUL)
    assert r.status_code == 200
    users = {u["who"]: u for u in r.json()["users"]}
    paul = users["Paul Somoye (paul@example.com)"]
    assert paul["kind"] == "entra" and paul["calls"] >= 2
    assert paul["last_seen"].endswith("+01:00")
    assert users["API key (primary)"]["calls"] == 1
    assert api.client.get("/api/access?days=8", headers=PAUL).status_code == 422


def test_unauthenticated_call_is_not_recorded(api):
    api.client.get("/api/alerts")
    assert api.access.rows == {}


def test_docs_send_you_to_microsoft_sign_in(api, monkeypatch):
    monkeypatch.setenv("ENTRA_SIGNIN", "true")
    monkeypatch.setenv("DOCS_PASSWORD", "ignored-when-entra-is-on")
    r = api.client.get("/docs", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/.auth/login/aad?post_login_redirect_uri=/docs"
    assert api.client.get("/openapi.json").status_code == 401
    assert api.client.get("/docs", headers=PAUL).status_code == 200
    assert api.client.get("/openapi.json", headers=PAUL).status_code == 200
