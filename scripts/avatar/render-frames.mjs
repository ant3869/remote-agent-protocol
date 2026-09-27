// Render Jess's avatar frames to transparent 512x512 WebP files.
//
//   node scripts/avatar/render-frames.mjs [out-dir] [--sheet sheet.png]
//
// Needs Playwright's Chromium (npm i -D playwright, or a global install; set
// PLAYWRIGHT_CHROMIUM to a browser executable to use a specific one). The
// default output is the runtime folder the web app serves.

import { mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { EFFECT_FRAMES, PORTRAIT_FRAMES, jessSvg } from "./jess-portrait.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..", "..");
const args = process.argv.slice(2);
const sheetIndex = args.indexOf("--sheet");
const sheetPath = sheetIndex >= 0 ? args.splice(sheetIndex, 2)[1] : null;
const outDir = path.resolve(
  args[0] || path.join(repo, "remote_agent_protocol/web_app/assets/avatars/jess/runtime_512_v1"),
);

async function loadPlaywright() {
  try {
    return await import("playwright");
  } catch {
    const globalRoot = process.env.NODE_PATH || path.join(path.dirname(process.execPath), "..", "lib", "node_modules");
    const require = createRequire(path.join(globalRoot, "noop.js"));
    return require("playwright");
  }
}

const { chromium } = await loadPlaywright();
const browser = await chromium.launch(
  process.env.PLAYWRIGHT_CHROMIUM ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM } : {},
);
try {
  const page = await browser.newPage();
  await page.setContent("<!doctype html><body></body>");
  const encode = (svg, type, size = 512) => page.evaluate(async ({ svg, type, size }) => {
    const image = new Image();
    image.src = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = size;
    canvas.getContext("2d").drawImage(image, 0, 0, size, size);
    URL.revokeObjectURL(image.src);
    return canvas.toDataURL(type, 0.92).split(",")[1];
  }, { svg, type, size });

  await mkdir(outDir, { recursive: true });
  const names = [...PORTRAIT_FRAMES, ...EFFECT_FRAMES];
  for (const name of names) {
    const data = await encode(jessSvg(name), "image/webp");
    await writeFile(path.join(outDir, `${name}.webp`), Buffer.from(data, "base64"));
  }
  console.log(`Rendered ${names.length} frames to ${outDir}`);

  if (sheetPath) {
    // A contact sheet of every frame on the stage background, for review.
    const columns = 8;
    const cell = 160;
    const rows = Math.ceil(names.length / columns);
    // Each frame is drawn from its own SVG: one shared document would let
    // frames' clip and filter ids collide.
    const data = await page.evaluate(async ({ svgs, names, columns, cell, rows }) => {
      const canvas = document.createElement("canvas");
      canvas.width = columns * cell;
      canvas.height = rows * cell;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#07111f";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.font = "11px monospace";
      for (let index = 0; index < svgs.length; index += 1) {
        const image = new Image();
        image.src = URL.createObjectURL(new Blob([svgs[index]], { type: "image/svg+xml" }));
        await image.decode();
        const x = (index % columns) * cell;
        const y = Math.floor(index / columns) * cell;
        ctx.drawImage(image, x, y, cell, cell);
        ctx.fillStyle = "#cbd5e1";
        ctx.fillText(names[index], x + 4, y + cell - 6);
        URL.revokeObjectURL(image.src);
      }
      return canvas.toDataURL("image/png").split(",")[1];
    }, { svgs: names.map(jessSvg), names, columns, cell, rows });
    await writeFile(path.resolve(sheetPath), Buffer.from(data, "base64"));
    console.log(`Contact sheet: ${path.resolve(sheetPath)}`);
  }
} finally {
  await browser.close();
}
