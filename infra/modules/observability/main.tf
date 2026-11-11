# Log Analytics Workspace with Strict Cost Controls[cite: 1]
resource "azurerm_log_analytics_workspace" "law" {
  name                = "law-ems1000-${var.env_prefix}"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = 30 # NDPR data minimization and cost control[cite: 1]
  daily_quota_gb      = var.daily_quota_gb
  tags                = var.tags
}

# Application Insights
resource "azurerm_application_insights" "appinsights" {
  name                = "appi-ems1000-${var.env_prefix}"
  location            = var.location
  resource_group_name = var.resource_group_name
  workspace_id        = azurerm_log_analytics_workspace.law.id
  application_type    = "web"
  tags                = var.tags
}

# Alert 1: High Ingestion Latency (p95 > 60 seconds)[cite: 1]
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "latency_alert" {
  name                = "alert-latency-${var.env_prefix}"
  resource_group_name = var.resource_group_name
  location            = var.location
  scopes              = [azurerm_application_insights.appinsights.id]
  description         = "Fires if ingestion latency p95 exceeds 60 seconds."
  severity            = 2
  evaluation_frequency = "PT15M"
  window_duration      = "PT15M"

  criteria {
    query                   = <<-KQL
      customMetrics
      | where name == "ingest_latency_ms"
      | summarize p95_latency = percentile(value, 95) by bin(timestamp, 5m)
    KQL
    time_aggregation_method = "Maximum"
    threshold               = 60000 # 60 seconds in ms
    operator                = "GreaterThan"
    metric_measure_column   = "p95_latency"
  }
}

# Alert 2: Missing Telemetry (No data for 3 intervals / 15 mins)[cite: 1]
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "missing_telemetry" {
  name                = "alert-missing-telemetry-${var.env_prefix}"
  resource_group_name = var.resource_group_name
  location            = var.location
  scopes              = [azurerm_application_insights.appinsights.id]
  description         = "Fires if a known device sends 0 messages in 15 minutes."
  severity            = 1
  evaluation_frequency = "PT15M"
  window_duration      = "PT15M"

  criteria {
    query                   = <<-KQL
      customEvents
      | where name == "DeviceTelemetry"
      | summarize Count = count() by tostring(customDimensions.device_id)
    KQL
    time_aggregation_method = "Count"
    threshold               = 0
    operator                = "LessThanOrEqual"
    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }
}

# Pipeline Health Dashboard (Azure Workbook)[cite: 1]
resource "random_uuid" "workbook" {}

resource "azurerm_application_insights_workbook" "pipeline_health" {
  name                = random_uuid.workbook.result
  resource_group_name = var.resource_group_name
  location            = var.location
  display_name        = "EMS1000 Pipeline Health (${var.env_prefix})"
  source_id           = lower(azurerm_log_analytics_workspace.law.id)

  data_json = jsonencode({
    version = "Notebook/1.0"
    items = [
      {
        type = 1
        content = {
          json = "## Pipeline Reconciliation\nCompare these counts to ensure no silent data drops."
        }
        name = "text - header"
      }
    ]
  })
}
