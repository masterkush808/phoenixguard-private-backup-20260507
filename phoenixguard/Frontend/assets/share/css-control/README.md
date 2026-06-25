Place the landing-page transition images for the protected share surface in this folder.

Preferred filenames:
- `landing-transition-market-vision.png`
- `landing-transition-market-vision-alt.png`
- `landing-transition-lifestyle-suite.png`
- `landing-transition-lifestyle-travel.png`

Behavior:
- These images are used as the animated, blurred background plane for the login hero and public landing hero.
- The share surface loads them in the preferred filename order first, then any additional supported images alphabetically.
- If this folder is empty, the UI falls back to gradient-based slides and then to the parent `assets/share` directory for compatibility.
