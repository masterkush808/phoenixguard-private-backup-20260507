# PhoenixGuard 808Fx Web

Next.js commercial website, onboarding flow, and operator portal for the 808Fx Standard Hybrid System powered by the PhoenixGuard Engine.

## Local run

```powershell
cd web
npm install
npm run dev
```

Set `NEXT_PUBLIC_API_BASE_URL` for auth/onboarding API calls and `NEXT_PUBLIC_TRACKER_DASHBOARD_URL` for the protected tracker iframe:

```powershell
$env:NEXT_PUBLIC_API_BASE_URL="http://127.0.0.1:18181"
$env:NEXT_PUBLIC_TRACKER_DASHBOARD_URL="http://127.0.0.1:8793/dashboard/live/pocket-live-8788"
npm run dev
```

## Checks

```powershell
npm run typecheck
npm run build
npm run test:smoke
```
