// Extension page: records the tab whose title contains `token` with chrome.tabCapture (no infobar).
// overlay: "none" records the capture track; "canvas" draws each captured frame plus a cursor into a canvas first,
// as T3's client recordingCompositor does; "proc" draws the cursor onto each frame through insertable streams.
// encoder: "mr" encodes with MediaRecorder (an MP4 comes back); "wc-hw" / "wc-sw" / "wc-any" encode the frames with
// WebCodecs VideoEncoder (prefer-hardware / prefer-software / no-preference) and return counts, not a file.
const cursor = (ctx) => { ctx.fillStyle = "#2563eb"; ctx.beginPath(); ctx.arc(400 + (performance.now() / 5) % 800, 300, 20, 0, 7); ctx.fill(); };

window.probe = async (sizes) => {
  const gl = new OffscreenCanvas(1, 1).getContext("webgl2");
  const dbg = gl?.getExtension("WEBGL_debug_renderer_info");
  const out = { webgl: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl ? "masked" : "none", encode: {} };
  for (const [w, h] of sizes) for (const hw of ["prefer-hardware", "prefer-software"]) {
    const config = { codec: "avc1.640033", width: w, height: h, bitrate: 20e6, framerate: 60, hardwareAcceleration: hw, latencyMode: "realtime" };
    let wc; try { wc = (await VideoEncoder.isConfigSupported(config)).supported; } catch (e) { wc = String(e); }
    out.encode[`${w}x${h} ${hw}`] = wc;
  }
  for (const [w, h] of sizes) {
    for (const [type, contentType] of [["record", 'video/mp4;codecs="avc1.640033"'], ["webrtc", "video/H264"]]) {
      try { out.encode[`${w}x${h} mediaCapabilities ${type}`] = await navigator.mediaCapabilities.encodingInfo({ type, video: { contentType, width: w, height: h, bitrate: 20e6, framerate: 60 } }); }
      catch (e) { out.encode[`${w}x${h} mediaCapabilities ${type}`] = String(e).slice(0, 80); }
    }
  }
  return out;
};

window.startRecording = async (token, w, h, rate, overlay, encoder = "mr") => {
  const [tab] = await chrome.tabs.query({ title: `*${token}*` });
  const id = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
  const stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: id,
    minWidth: w, maxWidth: w, minHeight: h, maxHeight: h, maxFrameRate: rate } } });
  const bitrate = Math.min(50e6, Math.max(2.5e6, w * h * rate * 0.05));
  if (encoder.startsWith("wc")) {
    const stats = { frames: 0, dropped: 0, bytes: 0, first: null, last: null, error: null, hw: { "wc-hw": "prefer-hardware", "wc-sw": "prefer-software", "wc-any": "no-preference" }[encoder] };
    const enc = new VideoEncoder({ output: (chunk) => { stats.frames++; stats.bytes += chunk.byteLength; stats.first ??= chunk.timestamp; stats.last = chunk.timestamp; },
      error: (e) => { stats.error = String(e); } });
    enc.configure({ codec: "avc1.640033", width: w, height: h, bitrate, framerate: rate, hardwareAcceleration: stats.hw, latencyMode: "realtime", avc: { format: "annexb" } });
    const canvas = overlay === "proc" ? new OffscreenCanvas(w, h) : null; const ctx = canvas?.getContext("2d", { alpha: false });
    const reader = new MediaStreamTrackProcessor({ track: stream.getVideoTracks()[0] }).readable.getReader();
    let n = 0, running = true;
    (async () => { while (running) { const { value: frame, done } = await reader.read(); if (done) break;
      // A recorder that keeps up never has more than a couple of frames queued; beyond that it drops, as MediaRecorder does.
      if (enc.state !== "configured" || enc.encodeQueueSize > 2) { stats.dropped++; frame.close(); continue; }
      let input = frame;
      if (ctx) { ctx.drawImage(frame, 0, 0); cursor(ctx); input = new VideoFrame(canvas, { timestamp: frame.timestamp }); frame.close(); }
      enc.encode(input, { keyFrame: n++ % (rate * 2) === 0 }); input.close(); } })();
    window.stopRecording = async () => { running = false; await enc.flush().catch((e) => { stats.error ??= String(e); });
      stream.getTracks().forEach((t) => t.stop()); return JSON.stringify(stats); };
    return { w, h };
  }
  let out = stream;
  if (overlay === "canvas") {
    const canvas = document.createElement("canvas"); canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext("2d", { alpha: false });
    const video = document.createElement("video"); video.muted = true; video.srcObject = stream;
    const draw = () => { ctx.drawImage(video, 0, 0, w, h); cursor(ctx); video.requestVideoFrameCallback(draw); };
    video.requestVideoFrameCallback(draw); await video.play(); out = canvas.captureStream(rate);
  }
  if (overlay.startsWith("proc")) {
    // Insertable streams: the cursor is drawn onto each captured frame, which keeps its own timestamp.
    // proc-sw asks for a canvas kept in CPU memory (willReadFrequently).
    const proc = new MediaStreamTrackProcessor({ track: stream.getVideoTracks()[0] });
    const gen = new MediaStreamTrackGenerator({ kind: "video" });
    const canvas = new OffscreenCanvas(w, h); const ctx = canvas.getContext("2d", { alpha: false, willReadFrequently: overlay === "proc-sw" });
    proc.readable.pipeThrough(new TransformStream({ transform(frame, c) {
      ctx.drawImage(frame, 0, 0); cursor(ctx);
      const next = new VideoFrame(canvas, { timestamp: frame.timestamp }); frame.close(); c.enqueue(next); } })).pipeTo(gen.writable);
    out = new MediaStream([gen]);
  }
  const mimeType = ["video/mp4;codecs=avc1.640033", "video/webm;codecs=vp9"].find((t) => MediaRecorder.isTypeSupported(t));
  const chunks = [];
  const rec = new MediaRecorder(out, { mimeType, videoBitsPerSecond: bitrate });
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  rec.start(1000);
  window.stopRecording = () => new Promise((r) => { rec.onstop = async () => { const b = new Uint8Array(await new Blob(chunks).arrayBuffer());
    stream.getTracks().forEach((t) => t.stop()); r(b.toBase64()); }; rec.stop(); });
  return { w, h };
};
