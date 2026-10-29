output "resource_group_name" { value = azurerm_resource_group.etics.name }
output "registry_name" { value = azurerm_container_registry.etics.name }
output "registry_login_server" { value = azurerm_container_registry.etics.login_server }
output "container_environment_name" { value = azurerm_container_app_environment.etics.name }
output "postgres_host" { value = azurerm_postgresql_flexible_server.etics.fqdn }
output "files_storage_account" { value = azurerm_storage_account.files.name }
output "key_vault_id" { value = local.key_vault_id }
output "key_vault_name" { value = local.key_vault_name }
output "key_vault_uri" { value = local.key_vault_uri }
output "postgres_password_secret_id" { value = local.postgres_password_secret_id }
output "llm_api_key_secret_id" { value = local.llm_api_key_secret_id }
output "initialization_job_name" {
  value = try(azurerm_container_app_job.initialize[0].name, "")
}
output "dashboard_url" {
  value       = var.application_enabled ? "https://${azurerm_container_app.etics["dashboard"].ingress[0].fqdn}" : ""
  description = "Accessible uniquement depuis l'IPv4 autorisée. Vide avant activation des applications."
}
