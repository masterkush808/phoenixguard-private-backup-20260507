const defaultApiBaseUrl = "http://127.0.0.1:8000";
const defaultTrackerDashboardUrl = "http://127.0.0.1:8793/v3/mobile/window-tracker/dashboard/pocket-live-8788";

export function getApiBaseUrl() {
  return (process.env.NEXT_PUBLIC_API_BASE_URL || defaultApiBaseUrl).replace(/\/+$/, "");
}

export function getTrackerDashboardUrl() {
  return (
    process.env.NEXT_PUBLIC_TRACKER_DASHBOARD_URL ||
    defaultTrackerDashboardUrl
  ).replace(/\/+$/, "");
}
