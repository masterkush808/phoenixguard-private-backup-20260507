# Release And Delivery Pipeline

## Goals

- Build repeatable EA and connector releases.
- Sign release artifacts.
- Deliver only to licensed customers.
- Revoke unsafe builds.
- Keep customer setup automated and auditable.

## Release Artifacts

Each release should produce:

- compiled MT4 EA.
- PhoenixGuard Connector installer.
- version manifest.
- checksums.
- release notes.
- setup guide.
- risk disclosure version included in package.

## Build Manifest

Example:

```json
{
  "release_id": "rel_2026_06_001",
  "channel": "stable",
  "ea_version": "808.2.0",
  "connector_version": "1.0.0",
  "minimum_connector_version": "1.0.0",
  "required_disclosure_version": "2026-06-01",
  "sha256": {
    "ea": "...",
    "connector_installer": "..."
  },
  "published_at": "2026-06-21T00:00:00Z"
}
```

## Signing

Sign:

- Windows connector installer with code-signing certificate.
- release manifest with backend signing key.
- command packets with dedicated packet signing key.

The command packet signing key should be separate from code signing.

## Delivery

1. Admin publishes release.
2. Release service stores artifact in object storage.
3. Customer portal shows latest allowed release.
4. Customer receives signed expiring download link.
5. Connector checks `/v1/releases/connector/latest`.
6. If update required, connector writes `UPDATE_REQUIRED` status until updated.

## Revocation

Build can be revoked when:

- security issue found.
- packet validation bug found.
- broker compatibility issue discovered.
- signing key rotated.
- dangerous EA logic detected.

Revocation action:

- mark build revoked.
- connector receives update required.
- license service refuses old connector command polling.
- EA can still fail closed because no signed executable command arrives.

## CI/CD Skeleton

```text
commit tagged
  -> run tests
  -> compile connector
  -> compile EA manually or through MT4 build runner
  -> generate checksums
  -> sign installer
  -> sign manifest
  -> upload artifacts
  -> create release row
  -> notify admin
```

## Manual Safety Gate

Before marking a release stable:

- validate dry-run mode.
- validate expired license rejection.
- validate signature rejection.
- validate duplicate packet rejection.
- validate market-closed handling.
- validate max trade limits.
- validate rollback path.

