resource "azurerm_private_dns_zone" "postgres" {
  name                = "${local.name}.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.etics.name
  tags                = local.tags
}
resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  name                = "postgres"
  private_dns_zone_id = azurerm_private_dns_zone.postgres.id
  virtual_network_id  = azurerm_virtual_network.etics.id
  tags                = local.tags
}
resource "azurerm_postgresql_flexible_server" "etics" {
  name                              = "${local.name}-pg-${local.suffix}"
  resource_group_name               = azurerm_resource_group.etics.name
  location                          = var.location
  version                           = "16"
  administrator_login               = "eticsadmin"
  administrator_password_wo         = var.postgres_admin_password
  administrator_password_wo_version = var.postgres_password_version
  sku_name                          = var.postgres_sku
  storage_mb                        = 32768
  backup_retention_days             = 7
  delegated_subnet_id               = azurerm_subnet.postgres.id
  private_dns_zone_id               = azurerm_private_dns_zone.postgres.id
  public_network_access_enabled     = false
  tags                              = local.tags
  depends_on                        = [azurerm_private_dns_zone_virtual_network_link.postgres]
  lifecycle { prevent_destroy = true }
}
resource "azurerm_postgresql_flexible_server_database" "etics" {
  name      = "etics_db"
  server_id = azurerm_postgresql_flexible_server.etics.id
  charset   = "UTF8"
  collation = "en_US.utf8"
  lifecycle { prevent_destroy = true }
}
