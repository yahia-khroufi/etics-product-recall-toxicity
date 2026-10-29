variable "subscription_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.subscription_id))
    error_message = "Indiquer explicitement l'abonnement Azure cible."
  }
}
variable "project_name" {
  type    = string
  default = "etics"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,9}$", var.project_name))
    error_message = "Utiliser 3 à 10 caractères minuscules, chiffres ou tirets."
  }
}
variable "environment" {
  type    = string
  default = "dev"
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environnement inconnu."
  }
}
variable "location" {
  type    = string
  default = "francecentral"
}
variable "tags" {
  type    = map(string)
  default = {}
}
variable "key_vault_id" {
  type        = string
  default     = ""
  description = "Vide : créer un Key Vault RBAC. Sinon : réutiliser ce coffre existant sans le gérer."
  validation {
    condition     = var.key_vault_id == "" || can(regex("(?i)^/subscriptions/${var.subscription_id}/resourceGroups/[^/\\s]+/providers/Microsoft.KeyVault/vaults/[a-z0-9-]{3,24}$", var.key_vault_id))
    error_message = "Laisser vide pour créer le coffre, ou fournir l'ID ARM d'un coffre du même abonnement sur une seule ligne."
  }
}
variable "postgres_password_secret_id" {
  type        = string
  default     = ""
  description = "Vide : calculer l'URI postgres-password du coffre choisi. Le secret doit être renseigné avant le job."
  validation {
    condition     = var.postgres_password_secret_id == "" || can(regex("^https://[a-z0-9-]+\\.vault\\.azure\\.net/secrets/[a-zA-Z0-9-]+(/[^/]+)?$", var.postgres_password_secret_id))
    error_message = "Fournir une URI de secret Key Vault."
  }
}
variable "llm_api_key_secret_id" {
  type        = string
  default     = ""
  description = "Vide : calculer l'URI llm-api-key du coffre choisi. Fournir sa valeur séparément, jamais dans Terraform."
  validation {
    condition     = var.llm_api_key_secret_id == "" || can(regex("^https://[a-z0-9-]+\\.vault\\.azure\\.net/secrets/[a-zA-Z0-9-]+(/[^/]+)?$", var.llm_api_key_secret_id))
    error_message = "Fournir une URI de secret Key Vault pour la clé LLM."
  }
}
variable "postgres_admin_password" {
  type        = string
  sensitive   = true
  ephemeral   = true
  description = "Fourni par environnement/GitHub Secret et transmis au champ write-only PostgreSQL."
  validation {
    condition     = length(var.postgres_admin_password) >= 16
    error_message = "Utiliser un mot de passe d'au moins 16 caractères."
  }
}
variable "postgres_password_version" {
  type        = number
  default     = 1
  description = "Incrémenter lors d'une rotation coordonnée du mot de passe et du secret Key Vault."
}
variable "postgres_sku" {
  type    = string
  default = "B_Standard_B1ms"
}
variable "app_image" {
  type        = string
  default     = ""
  description = "Image existante dans l'ACR créé, référencée par digest immuable. Vide : fondation uniquement."
  validation {
    condition     = var.app_image == "" || can(regex("^[a-z0-9]+\\.azurecr\\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$", var.app_image))
    error_message = "Utiliser une image ACR par digest @sha256, jamais :latest."
  }
}
variable "application_enabled" {
  type        = bool
  default     = false
  description = "Activer les deux applications après succès du job d'initialisation."
  validation {
    condition     = !var.application_enabled || (var.app_image != "" && var.database_initialized)
    error_message = "Une image et une initialisation vérifiée sont requises avant de démarrer les applications."
  }
}
variable "database_initialized" {
  type        = bool
  default     = false
  description = "Attestation opérateur ; le workflow la renseigne après vérification du job."
}
variable "dashboard_source_cidr" {
  type        = string
  description = "IPv4 autorisée à consulter le dashboard HTTPS, en /32."
  validation {
    condition     = can(cidrhost(var.dashboard_source_cidr, 0)) && endswith(var.dashboard_source_cidr, "/32") && !strcontains(var.dashboard_source_cidr, ":")
    error_message = "Limiter le dashboard à une IPv4 /32 ; pas d'ouverture à Internet entier."
  }
}
variable "llm_model" {
  type    = string
  default = "openai/gpt-4o"
}
variable "llm_base_url" {
  type    = string
  default = "https://openrouter.ai/api/v1"
}
