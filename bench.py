"""Records page.html for SECONDS and prints one JSON line: video fps, CPU cores of the browser's processes, frame size.

bench.py PATH GPU W H RATE CHROME_DIR SHELL_DIR
  PATH screencast: PR 25075's delivery (ack at once, one encoder call at a time, Uint8Array.fromBase64) into the
       encoder page of T3 (encoder.js), in chrome-headless-shell as T3 launches it.
  PATH tab: the tabCapture extension in ext/ records the tab in Chrome for Testing (headless), drawing a cursor onto
       each frame (MediaStreamTrackProcessor -> OffscreenCanvas -> VideoFrame with the source timestamp).
  GPU off adds --disable-gpu (T3's flags); on leaves Chromium's default GPU (ANGLE on D3D11 on Windows).
The same measurement as rec3.mjs / recext.mjs on the Linux host; fps comes from the MP4's own sample count and
durations (count_mp4), which matches ffprobe on the host's files.
"""
import asyncio
import base64
import json
import os
import struct
import sys
import tempfile
import threading
import http.server
from pathlib import Path

import psutil
from playwright.async_api import async_playwright

HERE = Path(__file__).parent
EXT_ID = "jnfbigmgibkkdpnjepeghidhammpmohn"
SECONDS = 4
MEASURED = {}
WARMUP = 0.5


def boxes(data, start, end):
    while start + 8 <= end:
        size, kind = struct.unpack(">I4s", data[start : start + 8])
        header = 8
        if size == 1:
            size = struct.unpack(">Q", data[start + 8 : start + 16])[0]
            header = 16
        elif size == 0:
            size = end - start
        yield kind.decode("latin1"), start + header, start + size
        start += size


def count_mp4(data):
    """Frames and seconds of the first video track: moov's sample table, plus every fragment's trun."""
    timescale, frames, ticks, width, height = 0, 0, 0, 0, 0
    default_duration = 0

    def walk(start, end):
        nonlocal timescale, frames, ticks, width, height, default_duration
        for kind, body, stop in boxes(data, start, end):
            if kind in ("moov", "trak", "mdia", "minf", "stbl", "moof", "traf", "mvex"):
                walk(body, stop)
            elif kind == "mdhd":
                version = data[body]
                timescale = struct.unpack(">I", data[body + (20 if version else 12) : body + (24 if version else 16)])[0]
            elif kind == "tkhd":
                version = data[body]
                off = body + (88 if version else 76)
                w, h = struct.unpack(">II", data[off : off + 8])
                if w and not width:
                    width, height = w >> 16, h >> 16
            elif kind == "trex":
                default_duration = struct.unpack(">I", data[body + 12 : body + 16])[0]
            elif kind == "stts":
                count = struct.unpack(">I", data[body + 4 : body + 8])[0]
                for i in range(count):
                    n, d = struct.unpack(">II", data[body + 8 + 8 * i : body + 16 + 8 * i])
                    frames += n
                    ticks += n * d
            elif kind == "tfhd":
                flags = int.from_bytes(data[body + 1 : body + 4], "big")
                off = body + 8 + (8 if flags & 1 else 0) + (4 if flags & 2 else 0)
                if flags & 8:
                    default_duration = struct.unpack(">I", data[off : off + 4])[0]
            elif kind == "trun":
                flags = int.from_bytes(data[body + 1 : body + 4], "big")
                n = struct.unpack(">I", data[body + 4 : body + 8])[0]
                off = body + 8 + (4 if flags & 1 else 0) + (4 if flags & 4 else 0)
                per = sum(4 for bit in (0x100, 0x200, 0x400, 0x800) if flags & bit)
                for i in range(n):
                    d = struct.unpack(">I", data[off : off + 4])[0] if flags & 0x100 else default_duration
                    off += per
                    frames += 1
                    ticks += d

    walk(0, len(data))
    return frames, ticks / timescale if timescale else 0, width, height


def cpu_seconds(root):
    total = 0.0
    for p in psutil.process_iter(["exe"]):
        try:
            if p.info["exe"] and p.info["exe"].lower().startswith(root.lower()):
                t = p.cpu_times()
                total += t.user + t.system
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return total


def serve():
    page = (HERE / "page.html").read_bytes()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


def busy_seconds():
    t = psutil.cpu_times()
    return sum(t) - t.idle


async def measure(root, record, stop):
    await asyncio.sleep(WARMUP)
    try:
        import gpu
        engines = gpu.engines()
    except ImportError:
        engines = None
    c0, b0 = cpu_seconds(root), busy_seconds()
    t0 = asyncio.get_running_loop().time()
    await asyncio.sleep(SECONDS)
    elapsed = asyncio.get_running_loop().time() - t0
    cores = (cpu_seconds(root) - c0) / elapsed
    MEASURED["others"] = round((busy_seconds() - b0) / elapsed - cores, 2)
    if engines:
        MEASURED["gpu"] = engines(gpu.chrome_pids(root))
    return cores, await stop()


async def screencast(pw, url, gpu, w, h, rate, shell_dir):
    exe = str(next(Path(shell_dir).rglob("chrome-headless-shell.exe")))
    script = (HERE / "encoder.js").read_text(encoding="utf-8")
    script = script.replace("Uint8Array.from(atob(base64), (char) => char.charCodeAt(0))", "Uint8Array.fromBase64(base64)")
    script = script.replace("canvas.captureStream(30)", f"canvas.captureStream({rate})").replace("* 30 * 0.05", f"* {rate} * 0.05")
    browser = await pw.chromium.launch(executable_path=exe, headless=True, ignore_default_args=["--enable-automation"],
        args=(["--disable-gpu"] if gpu == "off" else []) + ["--force-device-scale-factor=2", "--disable-blink-features=AutomationControlled"])
    ctx = await browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=2)
    tab = await ctx.new_page()
    await tab.goto(url + "?lines=20")
    enc = await (await browser.new_context()).new_page()
    await enc.evaluate(script)
    cdp = await ctx.new_cdp_session(tab)
    state = {"pending": None, "busy": False}

    async def deliver():
        state["busy"] = True
        while state["pending"]:
            data, state["pending"] = state["pending"], None
            try:
                await enc.evaluate("([d, w]) => window.__t3Recorder.frame(d, w)", [data, w])
            except Exception:
                pass
        state["busy"] = False

    def on_frame(f):
        asyncio.ensure_future(cdp.send("Page.screencastFrameAck", {"sessionId": f["sessionId"]}))
        state["pending"] = f["data"]
        if not state["busy"]:
            asyncio.ensure_future(deliver())

    cdp.on("Page.screencastFrame", on_frame)
    await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 90, "everyNthFrame": 1})

    async def stop():
        await cdp.send("Page.stopScreencast")
        while state["busy"]:
            await asyncio.sleep(0.01)
        st = await enc.evaluate("() => window.__t3Recorder.stop()")
        out = b""
        for i in range(st["count"]):
            out += base64.b64decode(await enc.evaluate("(i) => window.__t3Recorder.chunk(i)", i))
        return out

    cores, video = await measure(str(Path(exe).parent), None, stop)
    await browser.close()
    return cores, video


async def tab_capture(pw, url, gpu, w, h, rate, chrome_dir, overlay="proc"):
    exe = str(next(Path(chrome_dir).rglob("chrome.exe")))
    # The Windows runners copy a payload directory's files into the payload root.
    ext = str(HERE / "ext" if (HERE / "ext" / "manifest.json").exists() else HERE)
    profile = tempfile.mkdtemp(prefix="t3-recbench-")
    ctx = await pw.chromium.launch_persistent_context(profile, executable_path=exe, headless=True, no_viewport=True,
        ignore_default_args=["--disable-extensions", "--enable-automation"],
        args=(["--disable-gpu"] if gpu == "off" else []) + ["--force-device-scale-factor=2", "--disable-blink-features=AutomationControlled",
              f"--disable-extensions-except={ext}", f"--load-extension={ext}", f"--allowlisted-extension-id={EXT_ID}"])
    tab = ctx.pages[0] if ctx.pages else await ctx.new_page()
    cdp = await ctx.new_cdp_session(tab)
    window = (await cdp.send("Browser.getWindowForTarget"))["windowId"]
    ow, oh, iw, ih = await tab.evaluate("[outerWidth, outerHeight, innerWidth, innerHeight]")
    await cdp.send("Browser.setWindowBounds", {"windowId": window, "bounds": {"width": w + ow - iw, "height": h + oh - ih}})
    await tab.goto(url + "?lines=20")
    inner = await tab.evaluate("[innerWidth, innerHeight]")
    await tab.evaluate("document.title = 'T3TARGET'")
    enc = await ctx.new_page()
    await enc.goto(f"chrome-extension://{EXT_ID}/enc.html")
    await enc.evaluate("([w, h, r, o]) => window.startRecording('T3TARGET', w, h, r, o)", [w * 2, h * 2, rate, overlay])
    await tab.bring_to_front()

    async def stop():
        return base64.b64decode(await enc.evaluate("() => window.stopRecording()"))

    cores, video = await measure(str(Path(exe).parent), None, stop)
    await ctx.close()
    return cores, video, inner


async def main():
    path, gpu, w, h, rate, chrome_dir, shell_dir = sys.argv[1:8]
    overlay = sys.argv[8] if len(sys.argv) > 8 else "proc"
    w, h, rate = int(w), int(h), int(rate)
    server, url = serve()
    async with async_playwright() as pw:
        inner = None
        if path == "screencast":
            cores, video = await screencast(pw, url, gpu, w, h, rate, shell_dir)
        else:
            cores, video, inner = await tab_capture(pw, url, gpu, w, h, rate, chrome_dir, overlay)
    server.shutdown()
    frames, seconds, fw, fh = count_mp4(video)
    print(json.dumps({"path": "screencast" if path == "screencast" else {"proc": "tab+cursor", "none": "tab", "canvas": "tab+canvas-cursor"}[overlay], "gpu": gpu, "rate": rate,
        "size": f"{fw}x{fh}", "inner": inner, "videoFps": round(frames / seconds, 1) if seconds else None,
        "frames": frames, "cores": round(cores, 2), "otherCores": MEASURED.get("others"), "gpuEngines": MEASURED.get("gpu"), "kbps": round(len(video) * 8 / 1024 / seconds) if seconds else None}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
