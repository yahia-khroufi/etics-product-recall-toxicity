data "azurerm_client_config" "current" {}

# Empty ID means Terraform owns a new vault; an existing vault stays external.
data "azurerm_key_vault" "existing" {
  count               = var.key_vault_id == "" ? 0 : 1
  name                = element(reverse(split("/", var.key_vault_id)), 0)
  resource_group_name = try(regex("(?i)/resourceGroups/([^/]+)/", var.key_vault_id)[0], "invalid")
}

resource "azurerm_key_vault" "etics" {
  count                         = var.key_vault_id == "" ? 1 : 0
  name                          = "kv-etics-${local.suffix}"
  location                      = azurerm_resource_group.etics.location
  resource_group_name           = azurerm_resource_group.etics.name
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  soft_delete_retention_days    = 90
  purge_protection_enabled      = true
  public_network_access_enabled = true
  tags                          = local.tags

  lifecycle { prevent_destroy = true }
}

locals {
  key_vault_id                = var.key_vault_id == "" ? azurerm_key_vault.etics[0].id : var.key_vault_id
  key_vault_uri               = var.key_vault_id == "" ? azurerm_key_vault.etics[0].vault_uri : data.azurerm_key_vault.existing[0].vault_uri
  key_vault_name              = var.key_vault_id == "" ? azurerm_key_vault.etics[0].name : data.azurerm_key_vault.existing[0].name
  postgres_password_secret_id = var.postgres_password_secret_id != "" ? var.postgres_password_secret_id : "${trimsuffix(local.key_vault_uri, "/")}/secrets/postgres-password"
  llm_api_key_secret_id       = var.llm_api_key_secret_id != "" ? var.llm_api_key_secret_id : "${trimsuffix(local.key_vault_uri, "/")}/secrets/llm-api-key"
}
