variable "account_id" {
  description = "Cloudflare account id."
  type        = string
}

variable "zone_id" {
  description = "Cloudflare zone id for the PhoenixGuard domain."
  type        = string
}

variable "dashboard_hostname" {
  description = "Public PhoenixGuard dashboard/API hostname, for example phoenixguard.example.com."
  type        = string
}

variable "admin_allowed_emails" {
  description = "Human admin emails allowed into the dashboard/admin surfaces."
  type        = list(string)
  default     = []
}

variable "feed_service_token_name" {
  description = "Cloudflare Access service token name for machine frame-feed agents."
  type        = string
  default     = "PhoenixGuard Frame Feed Agents"
}

variable "frame_ingest_rate_limit_requests" {
  description = "Allowed frame-ingest requests per period per client identity."
  type        = number
  default     = 30
}

variable "frame_ingest_rate_limit_period" {
  description = "Cloudflare rate limit period in seconds."
  type        = number
  default     = 60
}
