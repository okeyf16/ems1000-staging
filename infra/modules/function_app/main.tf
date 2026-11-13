resource "azurerm_service_plan" "function_plan" {
  name                = "plan-func-${var.app_name}-${var.env_prefix}"
  resource_group_name = var.resource_group_name
  location            = var.location
  os_type             = "Linux"
  sku_name            = "Y1" # Serverless consumption plan
  tags                = var.tags
}

resource "azurerm_linux_function_app" "function_app" {
  name                       = "func-${var.app_name}-${var.env_prefix}"
  resource_group_name        = var.resource_group_name
  location                   = var.location
  service_plan_id            = azurerm_service_plan.function_plan.id
  storage_account_name       = var.storage_account_name
  storage_account_access_key = var.storage_primary_access_key
  tags                       = var.tags

  site_config {
    application_stack {
      python_version = "3.11"
    }
  }

  app_settings = {
    # Core Function configuration
    "FUNCTIONS_WORKER_RUNTIME"              = "python"
    "AzureWebJobsStorage"                   = var.storage_primary_connection_string
    "APPLICATIONINSIGHTS_CONNECTION_STRING" = var.app_insights_connection_string
    "PYTHON_ENABLE_WORKER_EXTENSIONS"       = "1"

    # Construct the Event Hub-compatible connection string for the IoT Hub trigger
    "IOTHUB_EVENTS_CONNECTION_STRING" = "Endpoint=${var.iothub_events_endpoint};SharedAccessKeyName=iothubowner;SharedAccessKey=${var.iothub_owner_key};EntityPath=${var.iothub_events_path}"

    # Event Hub-compatible name of the built-in endpoint; the trigger reads it as
    # event_hub_name="%IOTHUB_EVENTS_PATH%" so it always matches the EntityPath above
    "IOTHUB_EVENTS_PATH" = var.iothub_events_path
  }

  lifecycle {
    # Set by the deploy-function workflow (package URL); Terraform must not remove it,
    # or the deployed code is unmounted until the next deploy.
    ignore_changes = [
      app_settings["WEBSITE_RUN_FROM_PACKAGE"],
    ]
  }
}
