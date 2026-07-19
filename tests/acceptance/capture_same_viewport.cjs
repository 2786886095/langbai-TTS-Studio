const { app, BrowserWindow } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const projectRoot = path.resolve(__dirname, "..", "..");
const output = path.join(projectRoot, "docs", "audit", "commercial-current", "01-studio-1464x901.png");
const distIndex = path.join(projectRoot, "frontend", "dist", "index.html");

app.commandLine.appendSwitch("force-device-scale-factor", "1");

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

app.whenReady().then(async () => {
  const window = new BrowserWindow({
    show: false,
    useContentSize: true,
    width: 1464,
    height: 901,
    backgroundColor: "#eef2f6",
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  await window.loadFile(distIndex);
  await window.webContents.executeJavaScript("localStorage.setItem('langbai-onboarding-complete', '1'); localStorage.setItem('langbai-density', 'comfortable')");
  await window.loadFile(distIndex);
  await window.webContents.setZoomFactor(1);
  await sleep(800);
  await window.webContents.executeJavaScript("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
  const image = await window.webContents.capturePage();
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, image.toPNG());
  window.destroy();
  app.quit();
}).catch(error => {
  process.stderr.write(String(error?.stack || error));
  app.exit(1);
});
