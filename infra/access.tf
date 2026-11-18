# Test access to the IoT Hub for external collaborators (e.g. the firmware engineer).
#
# Testers sign in to the Azure portal or CLI with their own Microsoft Entra (guest)
# account. They get:
#   - Reader on the hub, so the hub shows in the portal (Reader can't read or
#     regenerate the shared access keys the Function and API use)
#   - "EMS IoT Hub Device Tester": create and update devices, read and write twins,
#     invoke direct methods, send cloud-to-device messages. No device delete, no bulk
#     import/export jobs (which can delete), no configurations.
# Role assignments can't be narrower than the whole hub, so a tester can also update
# other devices (e.g. regenerate the simulator's key). Re-provision a device if needed.
#
# Testers are listed in testers.auto.tfvars (name => Entra object ID). Remove a line and
# apply to revoke access.

variable "hub_testers" {
  type        = map(string)
  description = "IoT Hub testers: short name => Microsoft Entra object ID"
  default     = {}
}

resource "azurerm_role_definition" "hub_device_tester" {
  name        = "EMS IoT Hub Device Tester (${var.env_prefix})"
  scope       = module.iothub.iothub_id
  description = "Create and manage IoT Hub devices, twins, direct methods and cloud-to-device messages. Cannot delete devices or run bulk jobs."

  permissions {
    actions = []
    data_actions = [
      "Microsoft.Devices/IotHubs/devices/read",
      "Microsoft.Devices/IotHubs/devices/write",
      "Microsoft.Devices/IotHubs/twins/read",
      "Microsoft.Devices/IotHubs/twins/write",
      "Microsoft.Devices/IotHubs/directMethods/invoke/action",
      "Microsoft.Devices/IotHubs/cloudToDeviceMessages/send/action",
      "Microsoft.Devices/IotHubs/cloudToDeviceMessages/feedback/action",
      "Microsoft.Devices/IotHubs/statistics/read",
    ]
    not_data_actions = []
  }

  assignable_scopes = [module.iothub.iothub_id]
}

resource "azurerm_role_assignment" "hub_tester_reader" {
  for_each             = var.hub_testers
  scope                = module.iothub.iothub_id
  role_definition_name = "Reader"
  principal_id         = each.value
}

resource "azurerm_role_assignment" "hub_tester_devices" {
  for_each           = var.hub_testers
  scope              = module.iothub.iothub_id
  role_definition_id = azurerm_role_definition.hub_device_tester.role_definition_resource_id
  principal_id       = each.value
}

# Separate consumer group for watching live messages (az iot hub monitor-events --cg testing),
# so testers never compete with the Function, which reads $Default.
resource "azurerm_iothub_consumer_group" "testing" {
  name                   = "testing"
  iothub_name            = module.iothub.iothub_name
  eventhub_endpoint_name = "events"
  resource_group_name    = azurerm_resource_group.rg.name
}
