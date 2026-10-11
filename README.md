# Film Camera

A compact Figma plugin that applies one **Gold 800-inspired** film treatment to a selected image fill.

## Use it in Figma

1. In Figma, choose **Plugins → Development → Import plugin from manifest…**.
2. Select this folder’s `manifest.json`.
3. Select one layer that has an image fill, then run **Film Camera**.
4. Click **Apply film effect**.

The treatment uses warm highlights, lifted shadows, a restrained vignette and fine 800-speed grain. It keeps the selected layer’s image-fill settings (crop, opacity and blend mode) intact, and can be undone with Figma’s normal Undo command.

Images above 4096px on their longest side are scaled down during processing to keep the plugin responsive.
