# T3 Code agent recordings: JPEG screencast vs in-browser tab capture

Reproduces [pingdotgg/t3code#26683](https://github.com/pingdotgg/t3code/issues/26683) (fix proposed in [#26684](https://github.com/pingdotgg/t3code/pull/26684)): agent recordings (`preview_recording_start` / `preview_recording_stop`) of 3200×1800 frames at the 60 fps setting fall well below 60 fps and keep most CPU threads busy, because every frame goes through a JPEG screencast, base64 and a second decode before `MediaRecorder` encodes it. Recording the tab inside Chrome with `chrome.tabCapture` reaches about 60 fps for less CPU.

The scripts run T3's server recording pipeline outside the app, with the browsers and launch flags T3 uses:

- `encoder.js` is T3's recording encoder page (`recordingEncoderScript` in `apps/server/src/preview/ServerBrowserPage.ts`, MIT license). `common.mjs` / `bench.py` apply PR #25075's decode line (`Uint8Array.fromBase64`) and the frame rate to it.
- `page.html` is the recorded page: 20 lines of text and a box that moves every frame.
- `ext/` is a Manifest V3 extension with `tabCapture`. Its page records the tab whose title contains a token, drawing a cursor onto the frames in one of three ways (`none`, `canvas` as the desktop's `recordingCompositor`, `proc` through `MediaStreamTrackProcessor`), with `MediaRecorder` or a WebCodecs `VideoEncoder`.

## Linux

Needs Node 24, `ffprobe`, and Chrome for Testing 156.0.8078.70 from [Chrome for Testing](https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json): `chrome-headless-shell` (T3's pinned browser; T3 installs it under `~/.t3/tools/chrome-headless-shell/linux64/156.0.8078.70/`) and the full `chrome`.

```sh
npm install
export CHROME_SHELL=~/.t3/tools/chrome-headless-shell/linux64/156.0.8078.70/chrome-headless-shell
export CHROME_FULL=/path/to/chrome-linux64/chrome
node record.mjs                       # the screencast as T3 records it, with PR #25075's delivery
OVERLAY=proc node record-ext.mjs      # tab capture through the extension, cursor drawn on each frame
```

Each prints one JSON line: the MP4's frame rate (`ffprobe -count_frames` / duration), the CPU cores of Node and the browser's processes, the frame size and bitrate. Variables: `W`, `H` (viewport in CSS px; frames are twice that), `RATE` (30 or 60), `SECONDS` (4), `GPU` (`off` adds `--disable-gpu` as T3 does; `vulkan` uses ANGLE on Vulkan), `OUT` (where the MP4s go). `record.mjs` also takes `PATH_=tab` (getDisplayMedia from a page in the tab's context; see the issue for its infobar) and `PACE=1` (acks paced to the frame rate).

## Windows

Needs [uv](https://docs.astral.sh/uv/); it fetches Python 3.12, Playwright and psutil itself. `run.ps1` downloads both Chrome for Testing 156.0.8078.70 builds for win64 into a temporary folder, records with `bench.py`, prints one JSON line per run and removes the folder:

```powershell
powershell -ExecutionPolicy Bypass -File run.ps1 -Payload (Get-Location)
```

By default it records the screencast and tab capture with a cursor at three sizes, 30 and 60 fps, GPU off and on, twice. Copy `variants-gpu.txt` or `variants-cursor.txt` to `gpu.txt` to run `gpu.py` instead: per Chrome flag variant, the GPU Chrome uses, the H.264 encoders it offers (`VideoEncoder.isConfigSupported`), and recordings with each cursor variant, with the utilization of each GPU engine from Windows performance counters (needs `pywin32`, which `run.ps1` adds). A `screencast.txt` holding `W H` adds T3's screencast at that viewport.

## Results

See the issue in `pingdotgg/t3code` for the measurements on three machines and what they show.
