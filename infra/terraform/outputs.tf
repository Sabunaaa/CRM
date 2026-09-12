output "application_url" { value = google_cloud_run_v2_service.api.uri }
output "collector_job_name" { value = google_cloud_run_v2_job.collector.name }
output "database_instance" { value = google_sql_database_instance.postgres.connection_name }
