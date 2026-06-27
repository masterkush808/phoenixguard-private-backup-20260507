# Compact Live-State Latency Hardening Report

Date: 2026-06-27

## Clear Answer

The compact live-state endpoint was still shipping the hidden `overlays.all_objects` pool to the browser. That pool is useful internally for fast overlay-mode projection, but it is not needed by the frontend for the current compact render.

The public compact API response now strips:

```text
overlays.all_objects
live_visual_state
```

Internal cache and projection still keep the full overlay pool for fast mode switching.

## Before

Endpoint:

```text
GET /v1/mobile/live/state/v3/pocket-live-8788?mode=CLEAN_LIVE&compact=1
```

Observed live samples:

```text
payload size: ~275 KB
p50 latency: ~1021.8 ms
max latency: ~1593.1 ms
overlays.objects: 11
overlays.all_objects: 90
```

## After

Observed live samples after API restart:

```text
payload size: ~49.7 KB
p50 latency: ~233.5 ms
max latency: ~1210.5 ms
overlays.objects: 11
overlays.all_objects: omitted from public response
```

Provider evidence:

```json
{
  "compact_public_payload_v3": true,
  "compact_public_all_objects_omitted_v3": 90,
  "compact_cache_signature_key_changed_v3": true
}
```

## Freshness Guard

The compact cache signature now tracks display frame/path movement again, but the API can reuse a previous same-session/same-mode compact overlay cache when source authority still matches.

The cache explicitly refuses to reuse a previous-signature `studying new pair` payload after overlays recover.

## Validation

```text
compileall changed files: PASS
pyright changed files: PASS, 0 errors
pytest compact/live-state cache tests: PASS, 10 passed
runtime_trace_v3.py: PASS alignment, models 7/7, SequenceContext COMPLETE
```

## Remaining Work

The 10-hour certification runner should now be resumed with the lighter compact endpoint, 15-second polling/capture, and existing MT4 bridge readiness monitoring.
