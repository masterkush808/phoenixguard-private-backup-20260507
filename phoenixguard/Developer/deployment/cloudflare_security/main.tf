locals {
  dashboard_domain = var.dashboard_hostname
  frame_ingest_path_expr = "(http.host eq \"${local.dashboard_domain}\" and starts_with(http.request.uri.path, \"/v1/mobile/frame-ingest\"))"
  admin_path_expr        = "(http.host eq \"${local.dashboard_domain}\" and (starts_with(http.request.uri.path, \"/v1/admin\") or starts_with(http.request.uri.path, \"/admin\")))"
}

resource "cloudflare_zero_trust_access_application" "phoenixguard_dashboard" {
  account_id       = var.account_id
  name             = "PhoenixGuard Dashboard"
  domain           = local.dashboard_domain
  type             = "self_hosted"
  session_duration = "8h"
}

resource "cloudflare_zero_trust_access_policy" "phoenixguard_admin_humans" {
  account_id     = var.account_id
  application_id = cloudflare_zero_trust_access_application.phoenixguard_dashboard.id
  name           = "PhoenixGuard approved operators"
  decision       = "allow"
  precedence     = 1

  include = [
    {
      email = {
        email = var.admin_allowed_emails
      }
    }
  ]
}

resource "cloudflare_zero_trust_access_service_token" "frame_feed_agents" {
  account_id = var.account_id
  name       = var.feed_service_token_name
  duration   = "8760h"
}

resource "cloudflare_ruleset" "phoenixguard_waf_custom" {
  zone_id     = var.zone_id
  name        = "PhoenixGuard custom WAF rules"
  description = "PhoenixGuard endpoint protection before traffic reaches the VPS."
  kind        = "zone"
  phase       = "http_request_firewall_custom"

  rules = [
    {
      action      = "block"
      expression  = "${local.frame_ingest_path_expr} and http.request.method ne \"POST\""
      description = "Frame ingest only accepts POST uploads"
      enabled     = true
    },
    {
      action      = "block"
      expression  = "${local.admin_path_expr} and not http.request.headers[\"cf-access-jwt-assertion\"][0] exists"
      description = "Admin paths require Cloudflare Access identity"
      enabled     = true
    }
  ]
}

resource "cloudflare_ruleset" "phoenixguard_rate_limits" {
  zone_id     = var.zone_id
  name        = "PhoenixGuard frame-ingest rate limits"
  description = "Rate limits for uploaded chart frames."
  kind        = "zone"
  phase       = "http_ratelimit"

  rules = [
    {
      action      = "block"
      expression  = local.frame_ingest_path_expr
      description = "Limit frame-ingest upload bursts"
      enabled     = true
      ratelimit = {
        characteristics     = ["cf.colo.id", "ip.src"]
        period              = var.frame_ingest_rate_limit_period
        requests_per_period = var.frame_ingest_rate_limit_requests
        mitigation_timeout  = 120
      }
    }
  ]
}

output "frame_feed_service_token_id" {
  value     = cloudflare_zero_trust_access_service_token.frame_feed_agents.client_id
  sensitive = true
}

output "frame_feed_service_token_secret" {
  value     = cloudflare_zero_trust_access_service_token.frame_feed_agents.client_secret
  sensitive = true
}
