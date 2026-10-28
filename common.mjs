import fs from "node:fs";
import http from "node:http";

// T3's recording encoder page (encoder.js) with PR #25075's decode line and the given frame rate.
export const encoderScript = ({ decode = "main", rate = 30 } = {}) => {
  let script = fs.readFileSync(new URL("./encoder.js", import.meta.url), "utf8");
  if (decode === "fromBase64")
    script = script.replace("Uint8Array.from(atob(base64), (char) => char.charCodeAt(0))", "Uint8Array.fromBase64(base64)");
  return script.replace("canvas.captureStream(30)", `canvas.captureStream(${rate})`).replace("* 30 * 0.05", `* ${rate} * 0.05`);
};

export const servePage = async () => {
  const html = fs.readFileSync(new URL("./page.html", import.meta.url));
  const server = http.createServer((_, res) => res.end(html)).listen(0, "127.0.0.1");
  await new Promise((resolve) => server.once("listening", resolve));
  return { url: `http://127.0.0.1:${server.address().port}/`, close: () => server.close() };
};

// CPU seconds used so far by this Node process and every process it started (Linux /proc).
export const cpuSeconds = () => {
  const ps = [];
  for (const d of fs.readdirSync("/proc")) {
    if (!/^\d+$/.test(d)) continue;
    try { const st = fs.readFileSync(`/proc/${d}/stat`, "utf8"); const f = st.slice(st.lastIndexOf(")") + 2).split(" "); ps.push({ pid: +d, ppid: +f[1], t: (+f[11] + +f[12]) / 100 }); } catch {}
  }
  const k = new Set([process.pid]);
  for (let grew = true; grew; ) { grew = false; for (const p of ps) if (!k.has(p.pid) && k.has(p.ppid)) { k.add(p.pid); grew = true; } }
  return ps.filter((p) => k.has(p.pid)).reduce((a, p) => a + p.t, 0);
};

export const outDir = () => fs.mkdtempSync(`${process.env.OUT ?? "."}/out-`);
