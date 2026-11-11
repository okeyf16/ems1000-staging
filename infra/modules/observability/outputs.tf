output "log_analytics_workspace_id" {
  value = azurerm_log_analytics_workspace.law.id
}

output "app_insights_id" {
  value = azurerm_application_insights.appinsights.id
}

output "app_insights_instrumentation_key" {
  value     = azurerm_application_insights.appinsights.instrumentation_key
  sensitive = true
}

output "app_insights_connection_string" {
  value     = azurerm_application_insights.appinsights.connection_string
  sensitive = true
}
