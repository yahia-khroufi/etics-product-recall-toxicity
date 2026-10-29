mock_provider "azurerm" {
  mock_resource "azurerm_container_registry" {
    defaults = { login_server = "eticstest.azurecr.io" }
  }
  mock_resource "azurerm_key_vault" {
    override_during = plan
    defaults = {
      id        = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-etics-aca-dev/providers/Microsoft.KeyVault/vaults/eticstest"
      vault_uri = "https://eticstest.vault.azure.net/"
    }
  }
  mock_data "azurerm_client_config" {
    defaults = { tenant_id = "00000000-0000-0000-0000-000000000001" }
  }
  mock_data "azurerm_key_vault" {
    defaults = {
      vault_uri                  = "https://existingtest.vault.azure.net/"
      rbac_authorization_enabled = true
    }
  }
}
variables {
  subscription_id         = "00000000-0000-0000-0000-000000000000"
  postgres_admin_password = "FAKE-test-only-Password123!"
  dashboard_source_cidr   = "203.0.113.10/32"
}
run "foundation_has_private_postgres_and_no_workloads" {
  command = plan
  assert {
    condition     = !azurerm_postgresql_flexible_server.etics.public_network_access_enabled && azurerm_postgresql_flexible_server.etics.version == "16"
    error_message = "PostgreSQL 16 doit rester privé."
  }
  assert {
    condition     = length(azurerm_container_app.etics) == 0 && length(azurerm_container_app_job.initialize) == 0
    error_message = "Sans image publiée, ne pas créer d'application avec une fausse image."
  }
  assert {
    condition     = length(azurerm_storage_share.etics) == 3 && !azurerm_container_registry.etics.admin_enabled
    error_message = "Trois partages persistants et un registre sans compte administrateur sont requis."
  }
  assert {
    condition     = length(azurerm_key_vault.etics) == 1 && length(data.azurerm_key_vault.existing) == 0 && azurerm_key_vault.etics[0].rbac_authorization_enabled && azurerm_key_vault.etics[0].purge_protection_enabled && azurerm_key_vault.etics[0].soft_delete_retention_days == 90
    error_message = "La fondation doit créer un coffre RBAC avec protection contre la purge."
  }
  assert {
    condition     = azurerm_key_vault.etics[0].tenant_id == data.azurerm_client_config.current.tenant_id && azurerm_key_vault.etics[0].resource_group_name == azurerm_resource_group.etics.name && startswith(azurerm_key_vault.etics[0].name, "kv-etics-")
    error_message = "Le coffre doit appartenir au tenant courant et au groupe applicatif."
  }
}
run "reuse_existing_vault_without_creating_another" {
  command = plan
  variables {
    key_vault_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/secrets/providers/Microsoft.KeyVault/vaults/existingtest"
  }
  assert {
    condition     = length(azurerm_key_vault.etics) == 0 && azurerm_role_assignment.secrets.scope == var.key_vault_id && output.key_vault_uri == "https://existingtest.vault.azure.net/"
    error_message = "Un coffre fourni doit être réutilisé sans création ni conversion."
  }
  assert {
    condition     = output.postgres_password_secret_id == "https://existingtest.vault.azure.net/secrets/postgres-password" && output.llm_api_key_secret_id == "https://existingtest.vault.azure.net/secrets/llm-api-key"
    error_message = "Les références doivent provenir du coffre existant."
  }
}
run "reject_existing_vault_without_rbac" {
  command = plan
  variables {
    key_vault_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/secrets/providers/Microsoft.KeyVault/vaults/existingtest"
  }
  override_data {
    target = data.azurerm_key_vault.existing[0]
    values = {
      vault_uri                  = "https://existingtest.vault.azure.net/"
      rbac_authorization_enabled = false
    }
  }
  expect_failures = [azurerm_role_assignment.secrets]
}
run "reject_vault_from_another_subscription" {
  command = plan
  variables {
    key_vault_id = "/subscriptions/00000000-0000-0000-0000-000000000002/resourceGroups/secrets/providers/Microsoft.KeyVault/vaults/existingtest"
  }
  expect_failures = [var.key_vault_id]
}
run "image_prepares_initialization_without_starting_apps" {
  command = plan
  variables {
    app_image = "eticstest.azurecr.io/etics@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  assert {
    condition     = length(azurerm_container_app_job.initialize) == 1 && length(azurerm_container_app.etics) == 0
    error_message = "Le job doit être créé et exécuté avant les applications."
  }
  assert {
    condition     = azurerm_container_app_job.initialize[0].manual_trigger_config[0].parallelism == 1 && azurerm_container_app_job.initialize[0].replica_retry_limit == 0
    error_message = "Les migrations doivent être déclenchées explicitement, sans reprises cachées."
  }
  assert {
    condition     = one(azurerm_container_app_job.initialize[0].secret).key_vault_secret_id == output.postgres_password_secret_id && output.postgres_password_secret_id == "https://eticstest.vault.azure.net/secrets/postgres-password"
    error_message = "Le job doit utiliser l'URI PostgreSQL calculée pour le nouveau coffre."
  }
}
run "enabled_apps_have_persistent_storage_and_restricted_https" {
  command = plan
  variables {
    app_image            = "eticstest.azurecr.io/etics@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    application_enabled  = true
    database_initialized = true
  }
  assert {
    condition     = azurerm_container_app.etics["worker"].template[0].min_replicas == 1 && azurerm_container_app.etics["worker"].template[0].max_replicas == 1 && length(azurerm_container_app.etics["worker"].ingress) == 0
    error_message = "Le worker doit rester actif sans exposer d'entrée réseau."
  }
  assert {
    condition     = !azurerm_container_app.etics["dashboard"].ingress[0].allow_insecure_connections && azurerm_container_app.etics["dashboard"].ingress[0].target_port == 8501
    error_message = "Streamlit doit passer par l'ingress HTTPS."
  }
  assert {
    condition     = alltrue([for v in azurerm_container_app.etics["worker"].template[0].volume : v.storage_type == "AzureFile" && strcontains(v.mount_options, "uid=1000")]) && length(azurerm_container_app.etics["worker"].template[0].volume) == 3
    error_message = "Les documents doivent être partagés et accessibles à l'UID applicatif."
  }
  assert {
    condition     = !contains([for e in azurerm_container_app.etics["dashboard"].template[0].container[0].env : e.name], "OPENAI_API_KEY") && contains([for e in azurerm_container_app.etics["worker"].template[0].container[0].env : e.name], "OPENAI_API_KEY")
    error_message = "La clé LLM ne doit être injectée que dans le worker."
  }
  assert {
    condition     = one([for s in azurerm_container_app.etics["worker"].secret : s.key_vault_secret_id if s.name == "llm-api-key"]) == output.llm_api_key_secret_id && output.llm_api_key_secret_id == "https://eticstest.vault.azure.net/secrets/llm-api-key"
    error_message = "La clé LLM du worker doit référencer le coffre sélectionné."
  }
}
run "reuse_custom_secret_names" {
  command = plan
  variables {
    postgres_password_secret_id = "https://eticstest.vault.azure.net/secrets/pg-custom"
    llm_api_key_secret_id       = "https://eticstest.vault.azure.net/secrets/api-custom"
  }
  assert {
    condition     = output.postgres_password_secret_id == var.postgres_password_secret_id && output.llm_api_key_secret_id == var.llm_api_key_secret_id
    error_message = "Préserver les références explicites, notamment les versions ou noms existants."
  }
}
run "reject_uninitialized_applications" {
  command = plan
  variables { application_enabled = true }
  expect_failures = [var.application_enabled]
}
run "reject_public_dashboard" {
  command = plan
  variables { dashboard_source_cidr = "0.0.0.0/0" }
  expect_failures = [var.dashboard_source_cidr]
}
run "reject_mutable_image" {
  command = plan
  variables { app_image = "eticstest.azurecr.io/etics:latest" }
  expect_failures = [var.app_image]
}
run "reject_short_database_password" {
  command = plan
  variables { postgres_admin_password = "short" }
  expect_failures = [var.postgres_admin_password]
}
run "reject_inline_api_key" {
  command = plan
  variables { llm_api_key_secret_id = "not-a-key-vault-secret-uri" }
  expect_failures = [var.llm_api_key_secret_id]
}
