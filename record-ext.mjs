// Records page.html through the tabCapture extension; reports video fps, CPU cores of the browser tree, size.
// GPU=off|vulkan, RATE, W, H (CSS px; DPR 2), SECONDS, OVERLAY=none|canvas|proc (how the cursor is drawn, see ext/enc.js).
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import { chromium } from "playwright-core";
import { cpuSeconds as cpu, outDir, servePage } from "./common.mjs";
const env = process.env, rate = +(env.RATE ?? 60), W = +(env.W ?? 1600), H = +(env.H ?? 900), seconds = +(env.SECONDS ?? 4), gpu = env.GPU ?? "off", overlay = env.OVERLAY ?? "none";
const exe = env.CHROME_FULL, ext = new URL("./ext", import.meta.url).pathname, id = "jnfbigmgibkkdpnjepeghidhammpmohn";
if (!exe) throw new Error("Set CHROME_FULL (see README).");
const site = await servePage();
const dir = fs.mkdtempSync(`${os.tmpdir()}/t3-rec-profile-`);
const ctx = await chromium.launchPersistentContext(dir, { executablePath: exe, headless: true, viewport: null, ignoreDefaultArgs: ["--disable-extensions", "--enable-automation"],
  args: [...(gpu === "vulkan" ? ["--use-angle=vulkan", "--enable-features=Vulkan,DefaultANGLEVulkan,VulkanFromANGLE"] : ["--disable-gpu"]), "--force-device-scale-factor=2",
    "--disable-blink-features=AutomationControlled", `--disable-extensions-except=${ext}`, `--load-extension=${ext}`, `--allowlisted-extension-id=${id}`] });
const tab = ctx.pages()[0] ?? await ctx.newPage();
const c = await ctx.newCDPSession(tab); const { windowId } = await c.send("Browser.getWindowForTarget");
const [ow, oh, iw, ih] = await tab.evaluate(() => [outerWidth, outerHeight, innerWidth, innerHeight]);
await c.send("Browser.setWindowBounds", { windowId, bounds: { width: W + ow - iw, height: H + oh - ih } });
await tab.goto(`${site.url}?lines=20`);
await tab.evaluate(() => { document.title = "T3TARGET"; });
const enc = await ctx.newPage(); await enc.goto(`chrome-extension://${id}/enc.html`);
await enc.evaluate(([w, h, rate, overlay]) => window.startRecording("T3TARGET", w, h, rate, overlay), [W * 2, H * 2, rate, overlay]);
await tab.bringToFront();
await new Promise((r) => setTimeout(r, 500));
const c0 = cpu(), t0 = Date.now();
await new Promise((r) => setTimeout(r, seconds * 1000));
const cores = (cpu() - c0) / ((Date.now() - t0) / 1000);
const out = `${outDir()}/ext-${overlay}-${gpu}-${rate}-${W * 2}.mp4`;
fs.writeFileSync(out, Buffer.from(await enc.evaluate(() => window.stopRecording()), "base64"));
const [stream, duration] = execFileSync("ffprobe", ["-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames,width,height:format=duration", "-of", "csv=p=0", out]).toString().trim().split("\n");
const [w, h, frames] = stream.split(",").map(Number);
console.log(JSON.stringify({ path: "ext+" + overlay, bin: "full", gpu, rate, size: `${w}x${h}`, videoFps: +(frames / +duration).toFixed(1), cores: +cores.toFixed(2), kbps: Math.round(fs.statSync(out).size * 8 / 1024 / +duration) }));
await ctx.close(); site.close(); fs.rmSync(dir, { recursive: true, force: true });
