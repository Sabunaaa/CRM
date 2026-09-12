variable "project_id" { type = string }
variable "region" { type = string default = "europe-west1" }
variable "api_image" { type = string description = "Full Artifact Registry image URL for the API/frontend container." }
variable "collector_image" { type = string description = "Full Artifact Registry image URL for the collector container." }
variable "team_password_hash" { type = string sensitive = true description = "Argon2id hash of the shared team password." }
variable "service_name" { type = string default = "instatrack-crm" }
