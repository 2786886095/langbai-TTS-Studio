const { app, BrowserWindow } = require("electron");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");

const projectRoot = path.resolve(__dirname, "..", "..");
const outputRoot = path.resolve(process.argv[2] || path.join(projectRoot, "docs", "audit", "multispeaker-current"));
const distIndex = path.join(projectRoot, "frontend", "dist", "index.html");
const backendUrl = process.env.LANGBAI_UI_BACKEND_URL || "http://127.0.0.1:18769";
const isolatedUserData = fs.mkdtempSync(path.join(os.tmpdir(), "langbai-multispeaker-ui-"));
const isolatedBackendData = fs.mkdtempSync(path.join(os.tmpdir(), "langbai-multispeaker-backend-"));
let backendProcess = null;

app.setPath("userData", isolatedUserData);
app.commandLine.appendSwitch("force-device-scale-factor", "1");

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function ensureBackend() {
  if (process.env.LANGBAI_UI_BACKEND_URL) return;
  backendProcess = spawn(path.join(projectRoot, "backend", ".venv", "Scripts", "python.exe"), ["run.py"], {
    cwd: path.join(projectRoot, "backend"),
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
    env: {
      ...process.env,
      LANGBAI_TTS_MOCK: "1",
      LANGBAI_TTS_DATA: isolatedBackendData,
      LANGBAI_TTS_PORT: "18769",
    },
  });
  let errorText = "";
  backendProcess.stderr.on("data", chunk => { errorText += chunk.toString(); });
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const response = await fetch(`${backendUrl}/health`);
      if (response.ok) return;
    } catch { /* startup is still in progress */ }
    await sleep(100);
  }
  throw new Error(`Mock backend did not start: ${errorText}`);
}

function stopBackend() {
  if (backendProcess && !backendProcess.killed) backendProcess.kill();
  backendProcess = null;
}
async function settle(window) {
  await sleep(650);
  await window.webContents.executeJavaScript("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
}

async function seedVoice(name, index) {
  const response = await fetch(`${backendUrl}/api/voice-profiles`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name,
      engine: "gpt_sovits",
      parameters: {
        gpt_weights_path: `D:\\Models\\${index}.ckpt`,
        sovits_weights_path: `D:\\Models\\${index}.pth`,
        ref_audio_path: `D:\\Voices\\${index}.wav`,
        prompt_text: `${name}的参考文本`,
        prompt_lang: "中文",
        version: "v4",
      },
    }),
  });
  if (!response.ok) throw new Error(`Unable to seed voice ${name}: ${await response.text()}`);
}

async function enterMultiSpeaker(window) {
  const entered = await window.webContents.executeJavaScript(`(() => {
    const engine = Array.from(document.querySelectorAll('.engine-option')).find(button => button.textContent.includes('GPT-SoVITS'));
    if (!engine) return false;
    engine.click();
    return true;
  })()`);
  if (!entered) throw new Error("GPT-SoVITS engine button was not found");
  await settle(window);
  const switched = await window.webContents.executeJavaScript(`(() => {
    const button = Array.from(document.querySelectorAll('.creation-mode-switch button')).find(item => item.textContent.includes('多人配音'));
    if (!button) return false;
    button.click();
    return true;
  })()`);
  if (!switched) throw new Error("Multi-speaker mode button was not found");
  await settle(window);
  await window.webContents.executeJavaScript(`(() => {
    const selects = Array.from(document.querySelectorAll('.multi-role-row select'));
    selects.forEach((select, index) => {
      if (index % 2 !== 0 || select.options.length < 2) return;
      select.value = select.options[Math.min(Math.floor(index / 2) + 1, select.options.length - 1)].value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
  })()`);
  await settle(window);
}

async function metrics(window) {
  return window.webContents.executeJavaScript(`(() => {
    const rect = selector => {
      const item = document.querySelector(selector)?.getBoundingClientRect();
      return item ? { x: item.x, y: item.y, width: item.width, height: item.height } : null;
    };
    const visibleText = Array.from(document.querySelectorAll('.multi-role-row legend')).map(item => item.textContent.trim());
    const readiness = document.querySelector('.multi-readiness');
    const isVisible = element => {
      const style = getComputedStyle(element);
      const box = element.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0;
    };
    const undersizedText = Array.from(document.querySelectorAll('.multi-speaker-panel *'))
      .filter(element => isVisible(element) && Array.from(element.childNodes).some(node => node.nodeType === 3 && node.textContent.trim()))
      .map(element => ({ text: element.textContent.trim().replace(/\\s+/g, ' ').slice(0, 80), fontSize: Number.parseFloat(getComputedStyle(element).fontSize) }))
      .filter(item => item.fontSize < 12);
    const undersizedTargets = Array.from(document.querySelectorAll('.multi-speaker-panel button, .multi-speaker-panel input, .multi-speaker-panel select, .multi-speaker-panel textarea'))
      .filter(isVisible)
      .map(element => { const box = element.getBoundingClientRect(); return { label: (element.getAttribute('aria-label') || element.textContent || element.value || '').trim().slice(0, 80), width: box.width, height: box.height }; })
      .filter(item => item.width < 40 || item.height < 40);
    return {
      viewport: { width: innerWidth, height: innerHeight },
      bodyClientWidth: document.body.clientWidth,
      bodyScrollWidth: document.body.scrollWidth,
      bodyClientHeight: document.body.clientHeight,
      bodyScrollHeight: document.body.scrollHeight,
      panel: rect('.multi-speaker-panel'),
      workspace: rect('.multi-speaker-workspace'),
      script: rect('.multi-script-card'),
      mapping: rect('.multi-mapping-card'),
      roles: visibleText,
      readiness: readiness ? { className: readiness.className, backgroundColor: getComputedStyle(readiness).backgroundColor, color: getComputedStyle(readiness).color, text: readiness.textContent.trim() } : null,
      undersizedText,
      undersizedTargets,
      generateDisabled: Boolean(document.querySelector('.top-actions .primary-button')?.disabled),
    };
  })()`);
}

async function capture(window, filename) {
  const image = await window.webContents.capturePage();
  fs.writeFileSync(path.join(outputRoot, filename), image.toPNG());
}

app.whenReady().then(async () => {
  fs.mkdirSync(outputRoot, { recursive: true });
  await ensureBackend();
  await seedVoice("旁白 · 沉稳", 1);
  await seedVoice("小明 · 青年", 2);
  await seedVoice("小红 · 明快", 3);
  const window = new BrowserWindow({
    show: false,
    useContentSize: true,
    width: 1920,
    height: 1080,
    backgroundColor: "#eef2f6",
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  await window.loadFile(distIndex, { query: { backendUrl } });
  await window.webContents.executeJavaScript(`
    localStorage.setItem('langbai-onboarding-complete', '1');
    localStorage.setItem('langbai-density', 'comfortable');
    localStorage.setItem('langbai-parameter-presets-v1', JSON.stringify([
      { id: 'preset-natural', name: '自然稳定', engine: 'gpt_sovits', updatedAt: new Date().toISOString(), parameters: { top_p: 0.7, temperature: 0.8 } },
      { id: 'preset-emotion', name: '情绪增强', engine: 'gpt_sovits', updatedAt: new Date().toISOString(), parameters: { top_p: 0.75, temperature: 0.9 } }
    ]));
  `);
  await window.loadFile(distIndex, { query: { backendUrl } });
  await window.webContents.setZoomFactor(1);
  await settle(window);

  const report = { capturedAt: new Date().toISOString(), singleWide: await metrics(window) };
  await capture(window, "00-studio-1920x1080.png");
  window.setContentSize(1180, 720);
  await settle(window);
  report.singleMinimum = await metrics(window);
  await capture(window, "00-studio-1180x720.png");
  window.setContentSize(1920, 1080);
  await settle(window);
  await enterMultiSpeaker(window);

  report.wide = await metrics(window);
  await capture(window, "01-multispeaker-1920x1080.png");

  window.setContentSize(1180, 720);
  await settle(window);
  report.minimum = await metrics(window);
  await capture(window, "02-multispeaker-1180x720.png");

  fs.writeFileSync(path.join(outputRoot, "multispeaker-metrics.json"), JSON.stringify(report, null, 2));
  window.destroy();
  stopBackend();
  app.exit(0);
}).catch(error => {
  stopBackend();
  process.stderr.write(String(error?.stack || error));
  app.exit(1);
});
