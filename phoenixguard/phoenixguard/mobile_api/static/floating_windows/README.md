# Floating Windows

Frontend-only floating dashboard controls live here. These controls may tune local visual presentation and save browser-side preferences, but they must not mutate tracker geometry, market objects, overlay positions, execution state, or backend analysis.

`overlay_editor_settings.json` is the hard-saved visual preset consumed by the normal tracker dashboard. The floating editor writes this file through the mobile API; the public dashboard reads it while keeping the editor UI hidden unless an explicit editor query flag is used.

`model_strength_controls.js` builds the separate Model Strength floating window and the dashboard bridge that listens for live slider changes. It persists model confidence floors, overlay confidence filtering, Model Council execution thresholds, execution lane thresholds, and AI contribution strength multipliers through the mobile API.
