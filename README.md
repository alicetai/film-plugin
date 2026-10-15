# Film Camera

A compact Figma plugin that applies one custom film-reference treatment to a selected image fill.

## Use it in Figma

1. In Figma, choose **Plugins → Development → Import plugin from manifest…**.
2. Select this folder’s `manifest.json`.
3. Select one layer that has an image fill, then run **Film Camera**.
4. Click **Apply film effect**.

The plugin evaluates an embedded 33³ colour LUT locally through WebGL, then applies restrained scan softness and seeded, luminance-dependent grain. It keeps the selected layer’s image-fill settings (crop, opacity and blend mode) intact, and can be undone with Figma’s normal Undo command.

Images above 4096px on their longest side are scaled down during processing to keep the plugin responsive.

## Calibrating the film profile

The bundled profile is fitted from the eight complete reference pairs currently in `calibration/`. Add more pairs and rerun the fitter to update it. At least eight matched digital/film scan pairs are required; more pairs provide better coverage of colours and lighting. Each pair must use the same filename stem, for example `calibration/digital/street-01.heic` and `calibration/film/street-01.jpg`. Different dimensions and JPG, PNG or HEIC extensions are fine; the fitter corrects orientation, converts embedded colour profiles to sRGB, and aligns each scan to its digital image.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-calibration.txt
.venv/bin/python tools/fit_lut.py \
  --digital-dir calibration/digital \
  --film-dir calibration/film \
  --output calibration/reference-profile.json
node tools/build-profile-lut.mjs --input calibration/reference-profile.json
```

The fitter aligns pairs, reserves two for holdout validation, reports RGB and Delta E 76 errors, and writes the 33³ profile. Review its generated report and visual comparisons before embedding the result.
