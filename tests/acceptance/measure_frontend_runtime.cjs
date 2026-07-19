const { app, BrowserWindow } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const projectRoot = path.resolve(__dirname, "..", "..");
const distIndex = path.join(projectRoot, "frontend", "dist", "index.html");
const output = path.resolve(process.argv[2] || path.join(projectRoot, "docs", "audit", "deep-user-current", "performance-after.json"));
const backendUrl = process.env.LANGBAI_PERF_BACKEND || "http://127.0.0.1:18765";
const sampleMs = Number(process.env.LANGBAI_PERF_SAMPLE_MS || 25000);
const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

app.commandLine.appendSwitch("force-device-scale-factor", "1");

app.whenReady().then(async () => {
  const window = new BrowserWindow({
    show: false,
    useContentSize: true,
    width: 1464,
    height: 901,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true, backgroundThrottling: false },
  });
  const requests = [];
  const pending = new Map();
  await window.loadFile(distIndex, { query: { backendUrl } });
  await window.webContents.executeJavaScript(process.env.LANGBAI_CAPTURE_ONBOARDING === "1"
    ? "localStorage.removeItem('langbai-onboarding-complete'); localStorage.setItem('langbai-density', 'comfortable')"
    : "localStorage.setItem('langbai-onboarding-complete', '1'); localStorage.setItem('langbai-density', 'comfortable')");
  await window.loadFile(distIndex, { query: { backendUrl } });
  await sleep(1200);
  window.show();
  window.focus();
  await sleep(300);
  if (process.env.LANGBAI_CAPTURE_ONBOARDING === "1") {
    const image = await window.webContents.capturePage();
    fs.mkdirSync(path.dirname(output), { recursive: true });
    fs.writeFileSync(path.join(path.dirname(output), "onboarding-fixed.png"), image.toPNG());
    window.destroy();
    app.quit();
    return;
  }
  if (process.env.LANGBAI_CAPTURE_VALIDATION === "1") {
    const result = await window.webContents.executeJavaScript(`(async () => {
      const button = [...document.querySelectorAll('button')]
        .find(element => element.textContent.includes('使用 IndexTTS 2 生成'));
      if (!button) throw new Error('找不到 IndexTTS2 生成按钮');
      button.click();
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      return {
        notice: document.querySelector('.notice')?.textContent?.trim() || '',
        activeId: document.activeElement?.id || '',
      };
    })()`);
    const image = await window.webContents.capturePage();
    fs.mkdirSync(path.dirname(output), { recursive: true });
    fs.writeFileSync(path.join(path.dirname(output), "required-reference-validation.png"), image.toPNG());
    fs.writeFileSync(output, JSON.stringify(result, null, 2));
    window.destroy();
    app.quit();
    return;
  }

  window.webContents.debugger.attach("1.3");
  await window.webContents.debugger.sendCommand("Network.enable");
  window.webContents.debugger.on("message", (_event, method, params) => {
    if (method === "Network.requestWillBeSent" && params.request.url.startsWith(`${backendUrl}/api/`)) {
      pending.set(params.requestId, { url: params.request.url, started: params.timestamp });
    }
    if (method === "Network.responseReceived" && pending.has(params.requestId)) {
      const item = pending.get(params.requestId);
      pending.delete(params.requestId);
      requests.push({
        url: item.url.replace(backendUrl, ""),
        status: params.response.status,
        durationMs: Math.round((params.timestamp - item.started) * 10000) / 10,
        at: Date.now(),
      });
    }
  });

  const sample = async (name, label) => {
    const navigationMs = await window.webContents.executeJavaScript(`(async () => {
      const label = ${JSON.stringify(label)};
      const button = [...document.querySelectorAll('nav button, .sidebar-settings')]
        .find(element => element.textContent.includes(label));
      if (!button) throw new Error('找不到导航入口：' + label);
      const started = performance.now();
      button.click();
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      return Math.round((performance.now() - started) * 10) / 10;
    })()`);
    await sleep(800);
    const image = await window.webContents.capturePage();
    fs.mkdirSync(path.dirname(output), { recursive: true });
    fs.writeFileSync(path.join(path.dirname(output), `${name}.png`), image.toPNG());
    requests.length = 0;
    const started = Date.now();
    await sleep(sampleMs);
    const scoped = requests.filter(item => item.at >= started);
    const grouped = Object.fromEntries([...new Set(scoped.map(item => item.url))].map(url => {
      const matches = scoped.filter(item => item.url === url);
      return [url, {
        count: matches.length,
        averageMs: matches.length ? Math.round(matches.reduce((sum, item) => sum + item.durationMs, 0) / matches.length * 10) / 10 : 0,
        maximumMs: matches.length ? Math.max(...matches.map(item => item.durationMs)) : 0,
      }];
    }));
    return { name, navigationMs, durationMs: Date.now() - started, requestCount: scoped.length, requests: grouped };
  };

  const result = {
    capturedAt: new Date().toISOString(),
    backendUrl,
    sampleMs,
    samples: [
      await sample("studio-idle", "创作台"),
      await sample("queue-idle", "任务队列"),
      await sample("library-idle", "音频库"),
      await sample("history-idle", "历史记录"),
    ],
  };
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, JSON.stringify(result, null, 2));
  window.destroy();
  app.quit();
}).catch(error => {
  process.stderr.write(`${error.stack || error}\n`);
  app.exit(1);
});
