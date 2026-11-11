output "function_app_id" {
  value = azurerm_linux_function_app.function_app.id
}

output "function_app_hostname" {
  value = azurerm_linux_function_app.function_app.default_hostname
}
