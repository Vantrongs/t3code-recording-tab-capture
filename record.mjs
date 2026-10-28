// Records page.html for N seconds three ways and reports video fps, CPU cores of the browser tree, size.
// PATH_=screencast: PR 25075's delivery (ack at once, one encoder call at a time, fromBase64) into T3's encoder page.
// PATH_=tab: the encoder page captures the tab itself with getDisplayMedia (as T3 desktop does) into MediaRecorder.
// BIN=shell|full (chrome-headless-shell / Chrome for Testing, both 156.0.8078.70), GPU=off|vulkan, RATE, W, H, SECONDS.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import { chromium } from "playwright-core";
import { cpuSeconds as cpu, encoderScript, outDir, servePage } from "./common.mjs";
const env = process.env;
const path = env.PATH_ ?? "screencast", rate = +(env.RATE ?? 60), width = +(env.W ?? 1600), height = +(env.H ?? 900), seconds = +(env.SECONDS ?? 4);
const bin = env.BIN ?? "shell", gpu = env.GPU ?? "off";
const exe = bin === "full" ? env.CHROME_FULL : env.CHROME_SHELL;
if (!exe) throw new Error(`Set ${bin === "full" ? "CHROME_FULL" : "CHROME_SHELL"} (see README).`);
const site = await servePage();
const browser = await chromium.launch({ executablePath: exe, headless: true, ignoreDefaultArgs: ["--enable-automation"],
  args: [...(gpu === "vulkan" ? ["--use-angle=vulkan", "--enable-features=Vulkan,DefaultANGLEVulkan,VulkanFromANGLE"] : ["--disable-gpu"]),
    "--force-device-scale-factor=2", "--disable-blink-features=AutomationControlled", "--auto-select-tab-capture-source-by-title=T3TARGET"] });
// WINDOW=1: no viewport emulation; the real window is sized instead (tab capture records the window's surface).
const ctx = await browser.newContext(env.WINDOW ? { viewport: null } : { viewport: { width, height }, deviceScaleFactor: 2 });
const tab = await ctx.newPage();
if (env.WINDOW) { const c = await ctx.newCDPSession(tab); const { windowId } = await c.send("Browser.getWindowForTarget");
  await c.send("Browser.setWindowBounds", { windowId, bounds: { width, height } }); await c.detach();
  console.error("inner", JSON.stringify(await tab.evaluate(() => [innerWidth, innerHeight, devicePixelRatio]))); }
await tab.goto(`${site.url}?lines=20`);
await tab.evaluate(() => { document.title = "T3TARGET"; });
const encoder = await (path === "tab" ? ctx : await browser.newContext()).newPage();
await encoder.goto(site.url + "?lines=0");
let received = 0, delivered = 0, stopCapture;
if (path === "tab") {
  await encoder.evaluate((o) => { window.OVERLAY = o; }, env.OVERLAY ?? "none");
  await encoder.evaluate(([rate, w, h]) => {
    document.body.innerHTML = "<button id=b>go</button>";
    window.go = new Promise((res, rej) => document.getElementById("b").onclick = () => navigator.mediaDevices.getDisplayMedia({ audio: false, selfBrowserSurface: "exclude",
      video: { frameRate: { ideal: rate, max: rate }, width: { ideal: w }, height: { ideal: h } } }).then((stream) => {
        const s = stream.getVideoTracks()[0].getSettings();
        const mimeType = ["video/mp4;codecs=avc1.640033", "video/webm;codecs=vp9"].find((t) => MediaRecorder.isTypeSupported(t));
        const chunks = [];
        let out = stream;
        if (window.OVERLAY === "canvas") {
          // As T3's client recordingCompositor: video -> requestVideoFrameCallback -> canvas + cursor -> captureStream.
          const canvas = document.createElement("canvas"); canvas.width = s.width; canvas.height = s.height;
          const ctx = canvas.getContext("2d", { alpha: false });
          const video = document.createElement("video"); video.muted = true; video.srcObject = stream;
          const draw = () => { ctx.drawImage(video, 0, 0, canvas.width, canvas.height); ctx.fillStyle = "#2563eb"; ctx.beginPath(); ctx.arc(400 + (performance.now() / 5) % 800, 300, 20, 0, 7); ctx.fill(); video.requestVideoFrameCallback(draw); };
          video.requestVideoFrameCallback(draw); video.play(); out = canvas.captureStream(rate);
        } else if (window.OVERLAY === "proc") {
          // Insertable streams: each captured VideoFrame gets the cursor drawn and keeps its timestamp.
          const proc = new MediaStreamTrackProcessor({ track: stream.getVideoTracks()[0] });
          const gen = new MediaStreamTrackGenerator({ kind: "video" });
          const canvas = new OffscreenCanvas(s.width, s.height); const ctx = canvas.getContext("2d", { alpha: false });
          proc.readable.pipeThrough(new TransformStream({ transform(frame, c) {
            if (canvas.width !== frame.displayWidth) { canvas.width = frame.displayWidth; canvas.height = frame.displayHeight; }
            ctx.drawImage(frame, 0, 0); ctx.fillStyle = "#2563eb"; ctx.beginPath(); ctx.arc(400 + (performance.now() / 5) % 800, 300, 20, 0, 7); ctx.fill();
            const next = new VideoFrame(canvas, { timestamp: frame.timestamp }); frame.close(); c.enqueue(next); } })).pipeTo(gen.writable);
          out = new MediaStream([gen]);
        }
        const rec = new MediaRecorder(out, { mimeType, videoBitsPerSecond: Math.min(50e6, Math.max(2.5e6, s.width * s.height * rate * 0.05)) });
        rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
        window.stopRec = () => new Promise((r) => { rec.onstop = async () => { const b = new Uint8Array(await new Blob(chunks).arrayBuffer()); let bin = ""; for (let i = 0; i < b.length; i += 0x8000) bin += String.fromCharCode(...b.subarray(i, i + 0x8000)); stream.getTracks().forEach((t) => t.stop()); r(btoa(bin)); }; rec.stop(); });
        rec.start(1000); res(s);
      }, rej));
  }, [rate, width * 2, height * 2]);
  await encoder.click("#b");
  console.error(JSON.stringify(await encoder.evaluate(() => window.go)));
  await tab.bringToFront();
  stopCapture = async () => Buffer.from(await encoder.evaluate(() => window.stopRec()), "base64");
} else {
  await encoder.evaluate(encoderScript({ decode: "fromBase64", rate }));
  const cdp = await ctx.newCDPSession(tab);
  let pending = null, busy = false, lastAck = 0;
  const deliver = async () => { busy = true; while (pending) { const d = pending; pending = null; await encoder.evaluate(([d, w]) => window.__t3Recorder.frame(d, w), [d, width]).catch(() => {}); delivered++; } busy = false; };
  cdp.on("Page.screencastFrame", (f) => { received++; const ack = () => void cdp.send("Page.screencastFrameAck", { sessionId: f.sessionId }).catch(() => {});
    if (env.PACE) { const due = lastAck + 1000 / rate; lastAck = Math.max(performance.now(), due); setTimeout(ack, Math.max(0, due - performance.now())); } else ack();
    pending = f.data; if (!busy) void deliver(); });
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 90, everyNthFrame: 1 });
  stopCapture = async () => { await cdp.send("Page.stopScreencast"); while (busy) await new Promise((r) => setTimeout(r, 10));
    const st = await encoder.evaluate(() => window.__t3Recorder.stop()); const c = [];
    for (let i = 0; i < st.count; i++) c.push(Buffer.from(await encoder.evaluate((i) => window.__t3Recorder.chunk(i), i), "base64"));
    return Buffer.concat(c); };
}
await new Promise((r) => setTimeout(r, 500));
const c0 = cpu(), t0 = Date.now();
await new Promise((r) => setTimeout(r, seconds * 1000));
const cores = (cpu() - c0) / ((Date.now() - t0) / 1000);
const out = `${outDir()}/${env.WINDOW ? "win-" : ""}${path}${env.OVERLAY ?? ""}${env.PACE ?? ""}-${bin}-${gpu}-${rate}-${width * 2}.mp4`;
fs.writeFileSync(out, await stopCapture());
const [stream, duration] = execFileSync("ffprobe", ["-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames,width,height:format=duration", "-of", "csv=p=0", out]).toString().trim().split("\n");
const [w, h, frames] = stream.split(",").map(Number);
console.log(JSON.stringify({ path: path + (path === "tab" && env.OVERLAY ? "+" + env.OVERLAY : "") + (env.PACE ? "+pace" : ""), bin, gpu, rate, size: `${w}x${h}`, videoFps: +(frames / +duration).toFixed(1), cores: +cores.toFixed(2), kbps: Math.round(fs.statSync(out).size * 8 / 1024 / +duration) }));
await browser.close(); site.close();
