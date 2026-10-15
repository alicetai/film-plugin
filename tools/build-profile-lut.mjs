#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const uiPath = path.join(root, 'ui.html');
const args = process.argv.slice(2);
const inputAt = args.indexOf('--input');
const input = inputAt >= 0 ? path.resolve(process.cwd(), args[inputAt + 1]) : null;

function clamp(value) { return Math.max(0, Math.min(1, value)); }
function smoothstep(low, high, value) {
  const t = clamp((value - low) / (high - low));
  return t * t * (3 - 2 * t);
}
function starterColour(r, g, b) {
  const luma = r * .2126 + g * .7152 + b * .0722;
  const shadows = 1 - smoothstep(.06, .42, luma);
  const highlights = smoothstep(.56, .98, luma);
  // Conservative reference-oriented starter only. Calibration replaces this cube.
  let outR = r * (.97 + .025 * highlights) + .016 * g - .010 * b;
  let outG = g * (.955 + .012 * highlights) + .009 * r - .012 * b;
  let outB = b * (.925 - .018 * highlights) + .008 * g;
  const greenDominance = Math.max(0, g - Math.max(r, b));
  outR += greenDominance * .055;
  outG -= greenDominance * .025;
  // Keep blacks anchored while rendering the scan's slightly denser dark values.
  const density = smoothstep(.03, .24, 1 - luma) * .018;
  outR -= density * .75; outG -= density; outB -= density * .85;
  // Preserve bright paper/sky roll-off without the old global fade.
  outR = outR * (1 - .035 * highlights) + highlights * .035;
  outG = outG * (1 - .026 * highlights) + highlights * .026;
  outB = outB * (1 - .014 * highlights) + highlights * .014;
  return [clamp(outR), clamp(outG), clamp(outB)];
}
function createStarter() {
  const size = 33;
  const bytes = new Uint8Array(size * size * size * 3);
  let index = 0;
  for (let b = 0; b < size; b++) for (let g = 0; g < size; g++) for (let r = 0; r < size; r++) {
    const colour = starterColour(r / (size - 1), g / (size - 1), b / (size - 1));
    bytes[index++] = Math.round(colour[0] * 255);
    bytes[index++] = Math.round(colour[1] * 255);
    bytes[index++] = Math.round(colour[2] * 255);
  }
  return { version: 1, size, order: 'r-fastest, then g, then b', encoding: 'base64-rgb-u8', calibrated: false, data: Buffer.from(bytes).toString('base64') };
}
let profile = input ? JSON.parse(fs.readFileSync(input, 'utf8')) : createStarter();
if (profile.size !== 33 || profile.encoding !== 'base64-rgb-u8' || typeof profile.data !== 'string') {
  throw new Error('Expected a 33³ base64-rgb-u8 LUT JSON profile.');
}
const expectedLength = 33 * 33 * 33 * 3;
if (Buffer.from(profile.data, 'base64').length !== expectedLength) throw new Error('LUT payload has an unexpected byte length.');
const payload = `// LUT_PAYLOAD_START\nwindow.FILM_PROFILE = ${JSON.stringify(profile)};\n// LUT_PAYLOAD_END`;
const ui = fs.readFileSync(uiPath, 'utf8');
const marker = /\/\/ LUT_PAYLOAD_START[\s\S]*?\/\/ LUT_PAYLOAD_END/;
if (!marker.test(ui)) throw new Error('Could not find LUT payload markers in ui.html.');
const updated = ui.replace(marker, payload);
fs.writeFileSync(uiPath, updated);
console.log(`Embedded ${profile.calibrated ? 'calibrated' : 'starter'} LUT in ui.html.`);
