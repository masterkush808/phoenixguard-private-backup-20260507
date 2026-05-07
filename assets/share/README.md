Use `css-control/` as the preferred landing-background source for the protected share surface.

The share UI still falls back to this parent folder for backward compatibility, but new image sets should live in `css-control/` so the timed landing/login transitions stay organized.

Supported formats:
- `.png`
- `.jpg`
- `.jpeg`
- `.webp`
- `.bmp`
- `.tif`
- `.tiff`

Notes:
- The share UI automatically loads the first few images from `css-control/`.
- The preferred ordered filenames are:
  - `landing-transition-market-vision.png`
  - `landing-transition-market-vision-alt.png`
  - `landing-transition-lifestyle-suite.png`
  - `landing-transition-lifestyle-travel.png`
- You can override the image source with `PHOENIXGUARD_SHARE_BRAND_ASSET_DIR`.
- Images are resized for web delivery before being embedded into the protected share surface.
