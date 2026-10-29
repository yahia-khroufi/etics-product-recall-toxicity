locals {
  name   = "${var.project_name}-aca-${var.environment}"
  suffix = substr(sha256("${var.subscription_id}-${local.name}"), 0, 8)
  tags   = merge(var.tags, { project = var.project_name, environment = var.environment, managed_by = "terraform" })
  shares = {
    data    = "/app/data",
    backups = "/app/backups",
    runtime = "/app/runtime"
  }
  common_env = {
    ETICS_CONTAINER         = "1"
    POSTGRES_HOST           = azurerm_postgresql_flexible_server.etics.fqdn
    POSTGRES_PORT           = "5432"
    POSTGRES_DB             = azurerm_postgresql_flexible_server_database.etics.name
    POSTGRES_USER           = "eticsadmin"
    PGSSLMODE               = "verify-full"
    PGSSLROOTCERT           = "/etc/ssl/certs/ca-certificates.crt"
    PGCONNECT_TIMEOUT       = "5"
    OPENAI_MODEL            = var.llm_model
    OPENROUTER_BASE_URL     = var.llm_base_url
    LLM_API_TIMEOUT_SECONDS = "120"
    LLM_MAX_OUTPUT_TOKENS   = "1500"
  }
}
resource "azurerm_resource_group" "etics" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}
resource "azurerm_virtual_network" "etics" {
  name                = "vnet-${local.name}"
  address_space       = ["10.42.0.0/16"]
  location            = var.location
  resource_group_name = azurerm_resource_group.etics.name
  tags                = local.tags
}
resource "azurerm_subnet" "containers" {
  name                 = "containers"
  resource_group_name  = azurerm_resource_group.etics.name
  virtual_network_name = azurerm_virtual_network.etics.name
  address_prefixes     = ["10.42.0.0/23"]
  service_endpoint {
    service = "Microsoft.Storage"
  }
  delegation {
    name = "container-apps"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}
resource "azurerm_subnet" "postgres" {
  name                 = "postgres"
  resource_group_name  = azurerm_resource_group.etics.name
  virtual_network_name = azurerm_virtual_network.etics.name
  address_prefixes     = ["10.42.2.0/24"]
  delegation {
    name = "postgres"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}
resource "azurerm_log_analytics_workspace" "etics" {
  name                = "logs-${local.name}"
  location            = var.location
  resource_group_name = azurerm_resource_group.etics.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  daily_quota_gb      = 1
  tags                = local.tags
}
resource "azurerm_container_app_environment" "etics" {
  name                       = "env-${local.name}"
  location                   = var.location
  resource_group_name        = azurerm_resource_group.etics.name
  infrastructure_subnet_id   = azurerm_subnet.containers.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.etics.id
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
  tags = local.tags
}
resource "azurerm_container_registry" "etics" {
  name                = "acretics${local.suffix}"
  location            = var.location
  resource_group_name = azurerm_resource_group.etics.name
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.tags
}
resource "azurerm_user_assigned_identity" "etics" {
  name                = "id-${local.name}"
  location            = var.location
  resource_group_name = azurerm_resource_group.etics.name
  tags                = local.tags
}
resource "azurerm_role_assignment" "pull" {
  scope                = azurerm_container_registry.etics.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.etics.principal_id
}
resource "azurerm_role_assignment" "secrets" {
  scope                = local.key_vault_id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.etics.principal_id
  lifecycle {
    precondition {
      condition     = var.key_vault_id == "" ? true : data.azurerm_key_vault.existing[0].rbac_authorization_enabled
      error_message = "Le coffre existant doit utiliser Azure RBAC ; aucune conversion automatique n'est effectuée."
    }
  }
}
resource "azurerm_storage_account" "files" {
  name                            = "stetics${local.suffix}"
  location                        = var.location
  resource_group_name             = azurerm_resource_group.etics.name
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  network_rules {
    default_action             = "Deny"
    bypass                     = ["None"]
    virtual_network_subnet_ids = [azurerm_subnet.containers.id]
  }
  tags = local.tags
  lifecycle { prevent_destroy = true }
}
resource "azurerm_storage_share" "etics" {
  for_each           = local.shares
  name               = each.key
  storage_account_id = azurerm_storage_account.files.id
  enabled_protocol   = "SMB"
  quota              = 10
  lifecycle { prevent_destroy = true }
}
resource "azurerm_container_app_environment_storage" "etics" {
  for_each                     = local.shares
  name                         = each.key
  container_app_environment_id = azurerm_container_app_environment.etics.id
  account_name                 = azurerm_storage_account.files.name
  share_name                   = azurerm_storage_share.etics[each.key].name
  access_key                   = azurerm_storage_account.files.primary_access_key
  access_mode                  = "ReadWrite"
}
