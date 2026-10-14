variable "project_name" {
  description = "Short project identifier used in resource names."
  type        = string
  default     = "invoice"
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "location" {
  description = "Azure region for all resources."
  type        = string
  default     = "East US"
}

# ---------------------------------------------------------------------------
# App Service / Docker
# ---------------------------------------------------------------------------
variable "docker_image" {
  description = "Docker image name and tag (e.g. yourdockerhubuser/invoice-api:latest)."
  type        = string
}

variable "docker_registry_username" {
  description = "Docker Hub username for pulling the image."
  type        = string
}

variable "docker_registry_password" {
  description = "Docker Hub password or access token."
  type        = string
  sensitive   = true
}

# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------
variable "postgres_admin_user" {
  description = "PostgreSQL administrator username."
  type        = string
  default     = "pgadmin"
}

variable "postgres_admin_password" {
  description = "PostgreSQL administrator password."
  type        = string
  sensitive   = true
}

variable "postgres_db_name" {
  description = "Application database name."
  type        = string
  default     = "invoice_db"
}

# ---------------------------------------------------------------------------
# Microsoft Entra ID
# ---------------------------------------------------------------------------
variable "azure_tenant_id" {
  description = "Microsoft Entra ID Tenant ID."
  type        = string
}

variable "azure_client_id" {
  description = "Microsoft Entra ID Application (Client) ID."
  type        = string
}
