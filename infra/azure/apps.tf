resource "azurerm_container_app_job" "initialize" {
  count                        = var.app_image == "" ? 0 : 1
  name                         = "init-${local.name}"
  location                     = var.location
  resource_group_name          = azurerm_resource_group.etics.name
  container_app_environment_id = azurerm_container_app_environment.etics.id
  replica_timeout_in_seconds   = 900
  replica_retry_limit          = 0
  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.etics.id]
  }
  registry {
    server   = azurerm_container_registry.etics.login_server
    identity = azurerm_user_assigned_identity.etics.id
  }
  secret {
    name                = "postgres-password"
    key_vault_secret_id = local.postgres_password_secret_id
    identity            = azurerm_user_assigned_identity.etics.id
  }
  template {
    dynamic "volume" {
      for_each = local.shares
      content {
        name          = volume.key
        storage_name  = volume.key
        storage_type  = "AzureFile"
        mount_options = "uid=1000,gid=1000,dir_mode=0770,file_mode=0660"
      }
    }
    container {
      name   = "initialize"
      image  = var.app_image
      cpu    = 0.5
      memory = "1Gi"
      args   = ["initialize"]
      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name        = "POSTGRES_PASSWORD"
        secret_name = "postgres-password"
      }
      dynamic "volume_mounts" {
        for_each = local.shares
        content {
          name = volume_mounts.key
          path = volume_mounts.value
        }
      }
    }
  }
  tags       = local.tags
  depends_on = [azurerm_container_app_environment_storage.etics, azurerm_role_assignment.pull, azurerm_role_assignment.secrets]
  lifecycle {
    prevent_destroy = true
    precondition {
      condition     = startswith(var.app_image, "${azurerm_container_registry.etics.login_server}/")
      error_message = "L'image doit provenir du registre ACR de ce déploiement."
    }
  }
}

resource "azurerm_container_app" "etics" {
  for_each                     = var.application_enabled ? toset(["dashboard", "worker"]) : toset([])
  name                         = "${each.key}-${local.name}"
  resource_group_name          = azurerm_resource_group.etics.name
  container_app_environment_id = azurerm_container_app_environment.etics.id
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.etics.id]
  }
  registry {
    server   = azurerm_container_registry.etics.login_server
    identity = azurerm_user_assigned_identity.etics.id
  }
  secret {
    name                = "postgres-password"
    key_vault_secret_id = local.postgres_password_secret_id
    identity            = azurerm_user_assigned_identity.etics.id
  }
  dynamic "secret" {
    for_each = each.key == "worker" ? [1] : []
    content {
      name                = "llm-api-key"
      key_vault_secret_id = local.llm_api_key_secret_id
      identity            = azurerm_user_assigned_identity.etics.id
    }
  }
  dynamic "ingress" {
    for_each = each.key == "dashboard" ? [1] : []
    content {
      external_enabled           = true
      allow_insecure_connections = false
      target_port                = 8501
      transport                  = "http"
      traffic_weight {
        latest_revision = true
        percentage      = 100
      }
      ip_security_restriction {
        name             = "administrator"
        action           = "Allow"
        ip_address_range = var.dashboard_source_cidr
      }
    }
  }
  template {
    min_replicas                     = 1
    max_replicas                     = 1
    termination_grace_period_seconds = 60
    dynamic "volume" {
      for_each = local.shares
      content {
        name          = volume.key
        storage_name  = volume.key
        storage_type  = "AzureFile"
        mount_options = "uid=1000,gid=1000,dir_mode=0770,file_mode=0660"
      }
    }
    container {
      name    = each.key
      image   = var.app_image
      cpu     = 0.5
      memory  = "1Gi"
      command = ["python", "-c", file("${path.module}/adapters/cloud_start.py")]
      dynamic "env" {
        for_each = merge(local.common_env, { ETICS_CLOUD_ROLE = each.key })
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name        = "POSTGRES_PASSWORD"
        secret_name = "postgres-password"
      }
      dynamic "env" {
        for_each = each.key == "worker" ? [1] : []
        content {
          name        = "OPENAI_API_KEY"
          secret_name = "llm-api-key"
        }
      }
      dynamic "volume_mounts" {
        for_each = local.shares
        content {
          name = volume_mounts.key
          path = volume_mounts.value
        }
      }
      dynamic "startup_probe" {
        for_each = each.key == "dashboard" ? [1] : []
        content {
          transport               = "HTTP"
          port                    = 8501
          path                    = "/_stcore/health"
          interval_seconds        = 10
          failure_count_threshold = 120
        }
      }
      dynamic "readiness_probe" {
        for_each = each.key == "dashboard" ? [1] : []
        content {
          transport        = "HTTP"
          port             = 8501
          path             = "/_stcore/health"
          interval_seconds = 10
        }
      }
    }
  }
  tags       = local.tags
  depends_on = [azurerm_container_app_environment_storage.etics, azurerm_role_assignment.pull, azurerm_role_assignment.secrets]
  lifecycle {
    prevent_destroy = true
    precondition {
      condition     = startswith(var.app_image, "${azurerm_container_registry.etics.login_server}/")
      error_message = "L'image doit provenir du registre ACR de ce déploiement."
    }
  }
}
