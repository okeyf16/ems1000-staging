# Microsoft Entra sign-in for the API (App Service authentication).
#
# Anyone with an account in this tenant (members and guests, e.g. Paul) can sign in and use
# the API through /docs. The "EMS API user" app role is defined but not enforced yet: set
# api_require_role_assignment = true to allow only people assigned to it (Enterprise
# applications -> EMS1000 API -> Users and groups).
#
# One-time setup for the HCP Terraform service principal (see docs/RUNBOOK.md, "Microsoft
# sign-in"): Microsoft Graph application permission Application.ReadWrite.OwnedBy, with
# admin consent. It then manages only the app registrations it creates.

provider "azuread" {}

data "azuread_client_config" "current" {}

locals {
  # Same name the app_service module gives the Web App; computed here so the app
  # registration's redirect URI doesn't depend on the Web App resource (no cycle).
  api_hostname    = "app-${var.app_name}-${var.env_prefix}-${random_string.suffix.result}.azurewebsites.net"
  graph_app_id    = "00000003-0000-0000-c000-000000000000"
  graph_user_read = "e1fe6dd8-ba31-4d61-89e7-88639da4683d" # delegated User.Read (sign-in and profile)
}

resource "random_uuid" "api_user_role" {}

resource "azuread_application" "api" {
  display_name     = "EMS1000 API (${var.env_prefix})"
  sign_in_audience = "AzureADMyOrg" # this tenant only
  owners           = [data.azuread_client_config.current.object_id]

  web {
    redirect_uris = ["https://${local.api_hostname}/.auth/login/aad/callback"]
    logout_url    = "https://${local.api_hostname}/.auth/logout"

    implicit_grant {
      id_token_issuance_enabled = true
    }
  }

  app_role {
    id                   = random_uuid.api_user_role.result
    allowed_member_types = ["User"]
    display_name         = "EMS API user"
    description          = "Can use the EMS1000 API and its docs (enforced when api_require_role_assignment is true)"
    value                = "EMS.Api.User"
    enabled              = true
  }

  required_resource_access {
    resource_app_id = local.graph_app_id
    resource_access {
      id   = local.graph_user_read
      type = "Scope"
    }
  }
}

resource "azuread_service_principal" "api" {
  client_id                    = azuread_application.api.client_id
  app_role_assignment_required = var.api_require_role_assignment
  owners                       = [data.azuread_client_config.current.object_id]
}

# Client secret for App Service sign-in, replaced every 180 days by a normal apply
resource "time_rotating" "api_secret" {
  rotation_days = 180
}

resource "azuread_application_password" "api" {
  application_id = azuread_application.api.id
  display_name   = "app-service-sign-in"
  end_date       = timeadd(time_rotating.api_secret.id, "4800h") # 200 days: overlaps the next rotation

  rotate_when_changed = {
    rotation = time_rotating.api_secret.id
  }
}
