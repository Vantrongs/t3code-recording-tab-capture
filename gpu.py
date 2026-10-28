"""Why the GPU does not help tab capture here: per Chrome flag variant, what GPU Chrome runs on and which H.264
encoders it offers, then 3200x1800 recordings at 60 with MediaRecorder or a WebCodecs VideoEncoder, with the CPU of
Chrome's processes and the utilization of each GPU engine (3D, VideoEncode, Copy, ...) per adapter.

gpu.py CHROME_DIR VARIANTS_FILE   (each line: name|flag flag ...)
"""
import asyncio
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

import psutil
import win32pdh
from playwright.async_api import async_playwright

import bench

W, H, RATE, SECONDS = 1600, 900, 60, 4
# overlay "dom": the cursor is an element in the recorded page, which is then captured as is.
DOM_CURSOR = """() => { const c = document.createElement("div"); c.id = "__t3cursor";
  c.style.cssText = "position:fixed;left:0;top:0;width:40px;height:40px;border-radius:50%;background:#2563eb;pointer-events:none;z-index:2147483647;will-change:transform";
  document.documentElement.append(c);
  const move = () => { c.style.transform = `translate(${200 + (performance.now() / 10) % 400}px, 150px)`; requestAnimationFrame(move); }; move(); }"""


def engines():
    query = win32pdh.OpenQuery()
    counter = win32pdh.AddEnglishCounter(query, r"\GPU Engine(*)\Utilization Percentage")
    # The CPU's actual clock as a percentage of its base clock: CPU seconds at a lowered clock overstate the work.
    perf = win32pdh.AddEnglishCounter(query, r"\Processor Information(_Total)\% Processor Performance")
    win32pdh.CollectQueryData(query)

    def read(pids):
        win32pdh.CollectQueryData(query)
        total = {}
        try:
            values = win32pdh.GetFormattedCounterArray(counter, win32pdh.PDH_FMT_DOUBLE)
        except Exception as e:
            return {"error": str(e)}
        try:
            total["cpuClockPct"] = round(win32pdh.GetFormattedCounterValue(perf, win32pdh.PDH_FMT_DOUBLE)[1])
        except Exception as e:
            total["cpuClockPct"] = str(e)[:80]
        for name, value in values.items():
            parts = name.split("_")
            pid, luid, kind = int(parts[1]), "_".join(parts[3:5])[-8:], name.split("engtype_")[-1]
            key = f"{'chrome' if pid in pids else 'other'} {luid} {kind}"
            total[key] = round(total.get(key, 0) + value, 1)
        win32pdh.CloseQuery(query)
        return {k: v for k, v in sorted(total.items()) if not isinstance(v, (int, float)) or v >= 0.5}

    return read


def cpu_by_type(root):
    """CPU seconds per Chrome process type (browser, renderer, gpu-process, utility, ...)."""
    out = {}
    for p in psutil.process_iter(["exe", "cmdline"]):
        try:
            if p.info["exe"] and p.info["exe"].lower().startswith(root.lower()):
                kind = next((a.split("=", 1)[1] for a in p.info["cmdline"] or [] if a.startswith("--type=")), "browser")
                if kind == "utility":
                    kind = next((a.split("=", 1)[1].split(".")[-1] for a in p.info["cmdline"] if a.startswith("--utility-sub-type=")), kind)
                t = p.cpu_times()
                out[kind] = out.get(kind, 0) + t.user + t.system
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return out


def chrome_pids(root):
    pids = set()
    for p in psutil.process_iter(["exe"]):
        try:
            if p.info["exe"] and p.info["exe"].lower().startswith(root.lower()):
                pids.add(p.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return pids


async def variant(pw, exe, name, flags, url, combos, probe=True, seconds=SECONDS, W=W, H=H):
    ext = str(bench.HERE / "ext" if (bench.HERE / "ext" / "manifest.json").exists() else bench.HERE)
    profile = tempfile.mkdtemp(prefix="t3-recbench-")
    ctx = await pw.chromium.launch_persistent_context(profile, executable_path=exe, headless=True, no_viewport=True,
        ignore_default_args=["--disable-extensions", "--enable-automation"],
        args=flags + ["--force-device-scale-factor=2", "--disable-blink-features=AutomationControlled", "--remote-debugging-port=0",
              f"--disable-extensions-except={ext}", f"--load-extension={ext}", f"--allowlisted-extension-id={bench.EXT_ID}"])
    port = (Path(profile) / "DevToolsActivePort").read_text().split()[0]
    cdp_browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    info = (await (await cdp_browser.new_browser_cdp_session()).send("SystemInfo.getInfo"))["gpu"]
    aux = info.get("auxAttributes", {})
    print(json.dumps({"variant": name, "flags": flags,
        "devices": [f"{d['vendorString'] or hex(int(d['vendorId']))} {d['deviceString'] or hex(int(d['deviceId']))} {d.get('driverVersion', '')}" for d in info["devices"]],
        "aux": {k: aux.get(k) for k in ("glRenderer", "glVendor", "glImplementationParts", "displayType", "softwareRendering", "inProcessGpu", "passthroughCmdDecoder", "directComposition", "supportsOverlays")},
        "featureStatus": info.get("featureStatus"),
        "videoEncoding": [f"{e['profile']} max {e['maxResolution']['width']}x{e['maxResolution']['height']}" for e in info.get("videoEncoding", [])]}), flush=True)
    tab = ctx.pages[0] if ctx.pages else await ctx.new_page()
    cdp = await ctx.new_cdp_session(tab)
    window = (await cdp.send("Browser.getWindowForTarget"))["windowId"]
    ow, oh, iw, ih = await tab.evaluate("[outerWidth, outerHeight, innerWidth, innerHeight]")
    await cdp.send("Browser.setWindowBounds", {"windowId": window, "bounds": {"width": W + ow - iw, "height": H + oh - ih}})
    await tab.goto(url + "?lines=20")
    await tab.evaluate("document.title = 'T3TARGET'")
    enc = await ctx.new_page()
    await enc.goto(f"chrome-extension://{bench.EXT_ID}/enc.html")
    if probe:
        print(json.dumps({"variant": name, "probe": await enc.evaluate("(s) => window.probe(s)", [[3200, 1800], [1920, 1080], [1600, 900]])}), flush=True)
    root = str(Path(exe).parent)
    for overlay, encoder in combos:
        result = {"variant": name, "overlay": overlay, "encoder": encoder, "frame": f"{W * 2}x{H * 2}"}
        try:
            if overlay == "dom":
                await tab.evaluate(DOM_CURSOR)
            await enc.evaluate("([w, h, r, o, e]) => window.startRecording('T3TARGET', w, h, r, o, e)", [W * 2, H * 2, RATE, "none" if overlay == "dom" else overlay, encoder])
            await tab.bring_to_front()
            await asyncio.sleep(bench.WARMUP)
            gpu = engines()
            k0 = cpu_by_type(root)
            c0, b0, t0 = bench.cpu_seconds(root), bench.busy_seconds(), asyncio.get_running_loop().time()
            await asyncio.sleep(seconds)
            elapsed = asyncio.get_running_loop().time() - t0
            cores = (bench.cpu_seconds(root) - c0) / elapsed
            k1 = cpu_by_type(root)
            result["byType"] = {k: round((v - k0.get(k, 0)) / elapsed, 2) for k, v in k1.items() if (v - k0.get(k, 0)) / elapsed >= 0.05}
            result.update(cores=round(cores, 2), otherCores=round((bench.busy_seconds() - b0) / elapsed - cores, 2), gpu=gpu(chrome_pids(root)))
            out = await enc.evaluate("() => window.stopRecording()")
            if encoder == "mr":
                video = base64.b64decode(out)
                if os.environ.get("SAVE_DIR"):
                    Path(os.environ["SAVE_DIR"], f"{name}-{overlay}.mp4").write_bytes(video)
                frames, seconds, fw, fh = bench.count_mp4(video)
                result.update(fps=round(frames / seconds, 1) if seconds else None, size=f"{fw}x{fh}", kbps=round(len(video) * 8 / 1024 / seconds) if seconds else None)
            else:
                s = json.loads(out)
                span = (s["last"] - s["first"]) / 1e6 if s["first"] is not None else 0
                result.update(fps=round((s["frames"] - 1) / span, 1) if span else None, frames=s["frames"], dropped=s["dropped"],
                    kbps=round(s["bytes"] * 8 / 1024 / span) if span else None, error=s["error"])
        except Exception as e:
            result["exception"] = str(e).splitlines()[0][:300]
        if overlay == "dom":
            await tab.evaluate("() => document.getElementById('__t3cursor')?.remove()")
        print(json.dumps(result), flush=True)
    await cdp_browser.close()
    await ctx.close()


async def main():
    chrome_dir, variants_file = sys.argv[1:3]
    exe = str(next(Path(chrome_dir).rglob("chrome.exe")))
    server, url = bench.serve()
    async with async_playwright() as pw:
        for line in Path(variants_file).read_text().splitlines():
            if not line.strip():
                continue
            # name|flags[|overlay:encoder,...|probe]
            name, flags, *rest = line.split("|")
            flags = flags.split()
            sw = "--disable-gpu" in flags
            combos = [("none", "mr"), ("proc", "mr"), ("canvas", "mr"), ("none", "wc-sw" if sw else "wc-hw"), ("proc", "wc-sw" if sw else "wc-hw")]
            if not sw:
                combos.append(("none", "wc-sw"))
            if rest:
                combos = [tuple(c.split(":")) for c in rest[0].split(",")]
            probe = rest[1] == "1" if len(rest) > 1 else True
            seconds = float(rest[2]) if len(rest) > 2 else SECONDS
            w, h = map(int, rest[3].split("x")) if len(rest) > 3 else (W, H)
            try:
                await variant(pw, exe, name, flags, url, combos, probe, seconds, w, h)
            except Exception as e:
                print(json.dumps({"variant": name, "exception": str(e).splitlines()[0][:300]}), flush=True)
    server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
