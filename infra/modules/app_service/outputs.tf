output "web_app_id" {
  value = azurerm_linux_web_app.web_app.id
}

output "web_app_name" {
  value = azurerm_linux_web_app.web_app.name
}

output "web_app_default_hostname" {
  value = azurerm_linux_web_app.web_app.default_hostname
}

output "api_base_url" {
  value = "https://${azurerm_linux_web_app.web_app.default_hostname}"
}
