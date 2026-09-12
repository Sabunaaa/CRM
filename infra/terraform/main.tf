locals {
  required_apis = toset([
    "artifactregistry.googleapis.com",
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudscheduler.googleapis.com"
  ])
}

resource "google_project_service" "required" {
  for_each           = local.required_apis
  service            = each.value
  disable_on_destroy = false
}

resource "google_service_account" "api" {
  account_id   = "instatrack-api"
  display_name = "InstaTrack CRM API"
}

resource "google_service_account" "collector" {
  account_id   = "instatrack-collector"
  display_name = "InstaTrack collector"
}

resource "google_service_account" "scheduler" {
  account_id   = "instatrack-scheduler"
  display_name = "InstaTrack scheduler"
}

resource "random_password" "database" { length = 32 special = false }
resource "random_password" "session" { length = 64 special = false }

resource "google_sql_database_instance" "postgres" {
  name             = "${var.service_name}-postgres"
  region           = var.region
  database_version = "POSTGRES_16"
  deletion_protection = true
  settings {
    tier              = "db-custom-1-3840"
    availability_type = "ZONAL"
    disk_type         = "PD_SSD"
    disk_size         = 20
    disk_autoresize   = true
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "02:00"
      backup_retention_settings { retained_backups = 14 }
    }
    database_flags { name = "cloudsql.iam_authentication" value = "on" }
  }
  depends_on = [google_project_service.required]
}

resource "google_sql_database" "app" { name = "instatrack" instance = google_sql_database_instance.postgres.name }
resource "google_sql_user" "app" { name = "instatrack" instance = google_sql_database_instance.postgres.name password = random_password.database.result }

resource "google_secret_manager_secret" "database_url" {
  secret_id = "${var.service_name}-database-url"
  replication { auto {} }
  depends_on = [google_project_service.required]
}
resource "google_secret_manager_secret_version" "database_url" {
  secret      = google_secret_manager_secret.database_url.id
  secret_data = "postgresql+psycopg://instatrack:${urlencode(random_password.database.result)}@/instatrack?host=/cloudsql/${google_sql_database_instance.postgres.connection_name}"
}
resource "google_secret_manager_secret" "team_password_hash" { secret_id = "${var.service_name}-team-password-hash" replication { auto {} } }
resource "google_secret_manager_secret_version" "team_password_hash" { secret = google_secret_manager_secret.team_password_hash.id secret_data = var.team_password_hash }
resource "google_secret_manager_secret" "session_secret" { secret_id = "${var.service_name}-session-secret" replication { auto {} } }
resource "google_secret_manager_secret_version" "session_secret" { secret = google_secret_manager_secret.session_secret.id secret_data = random_password.session.result }

resource "google_project_iam_member" "api_sql" { project = var.project_id role = "roles/cloudsql.client" member = "serviceAccount:${google_service_account.api.email}" }
resource "google_project_iam_member" "collector_sql" { project = var.project_id role = "roles/cloudsql.client" member = "serviceAccount:${google_service_account.collector.email}" }
resource "google_secret_manager_secret_iam_member" "api_database" { secret_id = google_secret_manager_secret.database_url.id role = "roles/secretmanager.secretAccessor" member = "serviceAccount:${google_service_account.api.email}" }
resource "google_secret_manager_secret_iam_member" "collector_database" { secret_id = google_secret_manager_secret.database_url.id role = "roles/secretmanager.secretAccessor" member = "serviceAccount:${google_service_account.collector.email}" }
resource "google_secret_manager_secret_iam_member" "api_password" { secret_id = google_secret_manager_secret.team_password_hash.id role = "roles/secretmanager.secretAccessor" member = "serviceAccount:${google_service_account.api.email}" }
resource "google_secret_manager_secret_iam_member" "api_session" { secret_id = google_secret_manager_secret.session_secret.id role = "roles/secretmanager.secretAccessor" member = "serviceAccount:${google_service_account.api.email}" }

resource "google_cloud_run_v2_job" "collector" {
  name     = "${var.service_name}-collector"
  location = var.region
  deletion_protection = false
  template {
    task_count = 1
    template {
      service_account = google_service_account.collector.email
      timeout         = "3600s"
      max_retries     = 0
      containers {
        image = var.collector_image
        resources { limits = { cpu = "2", memory = "2Gi" } }
        env { name = "DATABASE_URL" value_source { secret_key_ref { secret = google_secret_manager_secret.database_url.secret_id version = "latest" } } }
        env { name = "MAX_PROFILES" value = "100" }
        env { name = "MAX_REELS_PER_PROFILE" value = "30" }
        env { name = "COLLECTION_DELAY_SECONDS" value = "5" }
        env { name = "COLLECTOR_ADAPTER" value = "scrapling" }
        env { name = "SCRAPLING_REEL_DELAY_SECONDS" value = "1" }
        env { name = "SCRAPLING_TIMEOUT_MS" value = "45000" }
        volume_mounts { name = "cloudsql" mount_path = "/cloudsql" }
      }
      volumes { name = "cloudsql" cloud_sql_instance { instances = [google_sql_database_instance.postgres.connection_name] } }
    }
  }
  depends_on = [google_project_service.required, google_secret_manager_secret_iam_member.collector_database]
}

resource "google_cloud_run_v2_service" "api" {
  name     = var.service_name
  location = var.region
  deletion_protection = false
  ingress = "INGRESS_TRAFFIC_ALL"
  template {
    service_account = google_service_account.api.email
    scaling { min_instance_count = 0 max_instance_count = 3 }
    containers {
      image = var.api_image
      ports { container_port = 8080 }
      resources { limits = { cpu = "1", memory = "512Mi" } }
      env { name = "DATABASE_URL" value_source { secret_key_ref { secret = google_secret_manager_secret.database_url.secret_id version = "latest" } } }
      env { name = "TEAM_PASSWORD_HASH" value_source { secret_key_ref { secret = google_secret_manager_secret.team_password_hash.secret_id version = "latest" } } }
      env { name = "SESSION_SECRET" value_source { secret_key_ref { secret = google_secret_manager_secret.session_secret.secret_id version = "latest" } } }
      env { name = "SECURE_COOKIES" value = "true" }
      env { name = "COLLECTOR_JOB_NAME" value = google_cloud_run_v2_job.collector.id }
      volume_mounts { name = "cloudsql" mount_path = "/cloudsql" }
      startup_probe { http_get { path = "/api/health" port = 8080 } initial_delay_seconds = 2 timeout_seconds = 2 period_seconds = 5 failure_threshold = 12 }
      liveness_probe { http_get { path = "/api/health" port = 8080 } period_seconds = 30 timeout_seconds = 2 failure_threshold = 3 }
    }
    volumes { name = "cloudsql" cloud_sql_instance { instances = [google_sql_database_instance.postgres.connection_name] } }
  }
  depends_on = [google_project_service.required, google_secret_manager_secret_iam_member.api_database, google_secret_manager_secret_iam_member.api_password, google_secret_manager_secret_iam_member.api_session]
}

resource "google_cloud_run_v2_service_iam_member" "public" { project = var.project_id location = var.region name = google_cloud_run_v2_service.api.name role = "roles/run.invoker" member = "allUsers" }
resource "google_cloud_run_v2_job_iam_member" "scheduler" { project = var.project_id location = var.region name = google_cloud_run_v2_job.collector.name role = "roles/run.invoker" member = "serviceAccount:${google_service_account.scheduler.email}" }
resource "google_cloud_run_v2_job_iam_member" "api_trigger" { project = var.project_id location = var.region name = google_cloud_run_v2_job.collector.name role = "roles/run.invoker" member = "serviceAccount:${google_service_account.api.email}" }

resource "google_cloud_scheduler_job" "twice_daily" {
  name        = "${var.service_name}-twice-daily"
  description = "Collect public Instagram metrics at midnight and noon Tbilisi time"
  schedule    = "0 0,12 * * *"
  time_zone   = "Asia/Tbilisi"
  region      = var.region
  attempt_deadline = "320s"
  retry_config { retry_count = 2 min_backoff_duration = "30s" max_backoff_duration = "300s" max_retry_duration = "600s" }
  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/${google_cloud_run_v2_job.collector.id}:run"
    body        = base64encode("{}")
    headers     = { "Content-Type" = "application/json" }
    oauth_token { service_account_email = google_service_account.scheduler.email scope = "https://www.googleapis.com/auth/cloud-platform" }
  }
  depends_on = [google_cloud_run_v2_job_iam_member.scheduler]
}
