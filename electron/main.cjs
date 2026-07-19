const { app, BrowserWindow, dialog, ipcMain, shell, protocol, net: electronNet, screen } = require('electron');
const { autoUpdater } = require('electron-updater');
const { spawn, spawnSync } = require('node:child_process');
const { randomUUID } = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const nodeNet = require('node:net');
const { pathToFileURL } = require('node:url');

const BACKEND_HOST = '127.0.0.1';
const BACKEND_PORT = Number(process.env.LANGBAI_BACKEND_PORT || 18765);
const DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL || 'http://127.0.0.1:5173';

let mainWindow = null;
let backendProcess = null;
let appIsQuitting = false;
let desktopState = {};
let desktopLogStream = null;
const audioGrants = new Map();
const AUDIO_EXTENSIONS = new Set(['.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac']);

protocol.registerSchemesAsPrivileged([{
  scheme: 'langbai-audio',
  privileges: { secure: true, standard: true, supportFetchAPI: true, stream: true },
}]);

function desktopStatePath() {
  return path.join(app.getPath('userData'), 'desktop-state.json');
}

function desktopLogPath() {
  return path.join(app.getPath('userData'), 'logs', 'desktop.log');
}

function openDesktopLog() {
  const target = desktopLogPath();
  fs.mkdirSync(path.dirname(target), { recursive: true });
  desktopLogStream = fs.createWriteStream(target, { flags: 'a', encoding: 'utf8' });
}

function logDesktop(level, message) {
  const line = `${new Date().toISOString()} [${level}] ${message}`;
  desktopLogStream?.write(`${line}\n`);
  if (level === 'ERROR') console.error(line); else console.log(line);
}

function readDesktopState() {
  try {
    const parsed = JSON.parse(fs.readFileSync(desktopStatePath(), 'utf8'));
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function saveDesktopState() {
  try {
    const target = desktopStatePath();
    fs.mkdirSync(path.dirname(target), { recursive: true });
    const temp = `${target}.tmp`;
    fs.writeFileSync(temp, `${JSON.stringify(desktopState, null, 2)}\n`, 'utf8');
    fs.renameSync(temp, target);
  } catch (error) {
    logDesktop('ERROR', `Unable to save desktop state: ${error.message}`);
  }
}

function validWindowBounds(bounds) {
  if (!bounds || !Number.isFinite(bounds.width) || !Number.isFinite(bounds.height)) return null;
  if (bounds.width < 1180 || bounds.height < 720) return null;
  const intersects = screen.getAllDisplays().some(({ workArea }) => (
    bounds.x < workArea.x + workArea.width && bounds.x + bounds.width > workArea.x
    && bounds.y < workArea.y + workArea.height && bounds.y + bounds.height > workArea.y
  ));
  return intersects ? bounds : null;
}

function sendToRenderer(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(channel, payload);
}

function validateAudioPath(targetPath) {
  if (typeof targetPath !== 'string' || !path.isAbsolute(targetPath)) throw new Error('音频路径无效');
  if (!AUDIO_EXTENSIONS.has(path.extname(targetPath).toLowerCase())) throw new Error('不支持的音频格式');
  if (!fs.existsSync(targetPath) || !fs.statSync(targetPath).isFile()) throw new Error('音频文件不存在');
  return targetPath;
}

function configureUpdater() {
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.on('checking-for-update', () => sendToRenderer('updates:event', { state: 'checking' }));
  autoUpdater.on('update-available', (info) => sendToRenderer('updates:event', { state: 'available', info }));
  autoUpdater.on('update-not-available', (info) => sendToRenderer('updates:event', { state: 'current', info }));
  autoUpdater.on('download-progress', (progress) => sendToRenderer('updates:event', { state: 'downloading', progress }));
  autoUpdater.on('update-downloaded', (info) => sendToRenderer('updates:event', { state: 'downloaded', info }));
  autoUpdater.on('error', (error) => sendToRenderer('updates:event', { state: 'error', message: error.message }));
}

function projectRoot() {
  return app.isPackaged ? app.getAppPath() : path.resolve(__dirname, '..');
}

function backendRoot() {
  return app.isPackaged
    ? path.join(process.resourcesPath, 'backend')
    : path.join(projectRoot(), 'backend');
}

function firstExisting(paths) {
  return paths.find((candidate) => candidate && fs.existsSync(candidate));
}

function backendLaunch() {
  const root = backendRoot();
  const standalone = app.isPackaged
    ? path.join(process.resourcesPath, 'backend-runtime', 'langbai-tts-backend.exe')
    : null;
  if (standalone && fs.existsSync(standalone)) return { executable: standalone, args: [] };
  const executable = firstExisting([
    process.env.LANGBAI_BACKEND_PYTHON,
    path.join(root, '.venv', 'Scripts', 'python.exe'),
    path.join(root, 'venv', 'Scripts', 'python.exe'),
  ]) || 'python';
  return { executable, args: ['-m', 'uvicorn', 'app.main:app', '--host', BACKEND_HOST, '--port', String(BACKEND_PORT)] };
}

function portIsOpen(port, host) {
  return new Promise((resolve) => {
    const socket = nodeNet.createConnection({ port, host });
    const finish = (value) => {
      socket.removeAllListeners();
      socket.destroy();
      resolve(value);
    };
    socket.setTimeout(300);
    socket.once('connect', () => finish(true));
    socket.once('timeout', () => finish(false));
    socket.once('error', () => finish(false));
  });
}

function backendIsReady() {
  return new Promise((resolve) => {
    const request = http.get({ host: BACKEND_HOST, port: BACKEND_PORT, path: '/health', timeout: 500 }, (response) => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', (chunk) => { body += chunk; });
      response.on('end', () => {
        try {
          const payload = JSON.parse(body);
          resolve(response.statusCode === 200 && payload.service === 'langbai-TTS-Studio');
        } catch {
          resolve(false);
        }
      });
    });
    request.once('timeout', () => { request.destroy(); resolve(false); });
    request.once('error', () => resolve(false));
  });
}

async function startBackend() {
  if (await backendIsReady()) return;
  if (await portIsOpen(BACKEND_PORT, BACKEND_HOST)) {
    throw new Error(`端口 ${BACKEND_PORT} 已被其他程序占用`);
  }

  const root = backendRoot();
  const { executable, args } = backendLaunch();
  backendProcess = spawn(
    executable,
    args,
    {
      cwd: root,
      windowsHide: true,
      env: {
        ...process.env,
        PYTHONUTF8: '1',
        LANGBAI_TTS_PORT: String(BACKEND_PORT),
        LANGBAI_BACKEND_ROOT: root,
        LANGBAI_PROJECT_ROOT: app.isPackaged ? process.resourcesPath : projectRoot(),
        LANGBAI_TTS_DATA: process.env.LANGBAI_CAPTURE_DATA || path.join(app.getPath('documents'), 'langbai-TTS-Studio', 'data'),
        LANGBAI_INSTALL_ROOT: path.join(app.getPath('documents'), 'langbai-TTS-Studio', 'engines'),
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );

  backendProcess.stdout?.on('data', (chunk) => logDesktop('INFO', `[backend] ${String(chunk).trimEnd()}`));
  backendProcess.stderr?.on('data', (chunk) => logDesktop('ERROR', `[backend] ${String(chunk).trimEnd()}`));
  backendProcess.once('exit', (code, signal) => {
    backendProcess = null;
    if (!appIsQuitting) logDesktop('ERROR', `Backend exited unexpectedly: code=${code}, signal=${signal}`);
  });
  backendProcess.once('error', (error) => {
    backendProcess = null;
    logDesktop('ERROR', `Unable to start backend with ${executable}: ${error.message}`);
  });

  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (await backendIsReady()) return;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`Backend did not open ${BACKEND_HOST}:${BACKEND_PORT} within 10 seconds`);
}

function stopBackend() {
  if (!backendProcess) return;
  const pid = backendProcess.pid;
  if (process.platform === 'win32' && pid) {
    spawnSync('taskkill.exe', ['/pid', String(pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
  } else {
    backendProcess.kill();
  }
  backendProcess = null;
}

async function createWindow() {
  try {
    await startBackend();
  } catch (error) {
    logDesktop('ERROR', `Backend startup failed: ${error.message}`);
  }

  desktopState = readDesktopState();
  const savedBounds = validWindowBounds(desktopState.bounds);
  mainWindow = new BrowserWindow({
    ...(savedBounds || { width: 1560, height: 960 }),
    minWidth: 1180,
    minHeight: 720,
    show: false,
    backgroundColor: '#F8FAFC',
    icon: app.isPackaged
      ? path.join(process.resourcesPath, 'icon.ico')
      : path.join(projectRoot(), 'assets', 'icon', 'langbai.ico'),
    autoHideMenuBar: true,
    title: 'langbai-TTS-Studio',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  const initialZoom = Number(desktopState.zoomFactor);
  if (Number.isFinite(initialZoom) && initialZoom >= 0.8 && initialZoom <= 1.5) {
    mainWindow.webContents.setZoomFactor(initialZoom);
  }
  if (desktopState.maximized) mainWindow.maximize();
  mainWindow.on('close', () => {
    if (!mainWindow) return;
    if (!mainWindow.isMaximized() && !mainWindow.isMinimized()) desktopState.bounds = mainWindow.getBounds();
    desktopState.maximized = mainWindow.isMaximized();
    desktopState.zoomFactor = mainWindow.webContents.getZoomFactor();
    saveDesktopState();
  });
  mainWindow.webContents.on('before-input-event', (event, input) => {
    if (!input.control || input.type !== 'keyDown') return;
    const key = input.key.toLowerCase();
    const commands = {
      s: 'save-project',
      n: 'new-project',
      ',': 'open-settings',
      enter: 'generate',
    };
    const command = commands[key];
    if (command && (key !== 'enter' || !input.shift)) {
      event.preventDefault();
      sendToRenderer('app:command', command);
      return;
    }
    if (['+', '=', '-'].includes(key)) {
      event.preventDefault();
      const delta = key === '-' ? -0.1 : 0.1;
      const factor = Math.max(0.8, Math.min(1.5, mainWindow.webContents.getZoomFactor() + delta));
      mainWindow.webContents.setZoomFactor(factor);
      desktopState.zoomFactor = factor;
      saveDesktopState();
      sendToRenderer('app:zoom-changed', factor);
    }
  });

  mainWindow.once('ready-to-show', async () => {
    mainWindow?.show();
    const capturePath = process.env.LANGBAI_CAPTURE_PATH;
    if (capturePath && path.isAbsolute(capturePath) && mainWindow) {
      const captureView = process.env.LANGBAI_CAPTURE_VIEW;
      await mainWindow.webContents.executeJavaScript("localStorage.setItem('langbai-onboarding-complete', '1'); localStorage.setItem('langbai-density', 'comfortable')");
      await new Promise((resolve) => setTimeout(resolve, 250));
      await mainWindow.webContents.executeJavaScript("document.querySelector('.onboarding-close')?.click()");
      if (captureView === 'settings' || captureView === 'engine-manager') {
        await mainWindow.webContents.executeJavaScript("document.querySelector('.sidebar-settings')?.click()");
        if (captureView === 'engine-manager') {
          await new Promise((resolve) => setTimeout(resolve, 500));
          await mainWindow.webContents.executeJavaScript("Array.from(document.querySelectorAll('button')).find((button) => button.textContent.includes('引擎管理'))?.click()");
          await new Promise((resolve) => setTimeout(resolve, 1200));
          await mainWindow.webContents.executeJavaScript("document.querySelector('.existing-engine-callout, .runtime-license-list')?.scrollIntoView({ block: 'center' })");
        }
      } else if (captureView) {
        const labels = { tasks: '任务队列', audio: '音频库', history: '历史记录', studio: '创作台' };
        const label = labels[captureView];
        if (label) {
          await mainWindow.webContents.executeJavaScript(`Array.from(document.querySelectorAll('nav button')).find((button) => button.textContent.includes(${JSON.stringify(label)}))?.click()`);
        }
      }
      await new Promise((resolve) => setTimeout(resolve, captureView === 'engine-manager' ? 5500 : 800));
      const image = await mainWindow.webContents.capturePage();
      fs.writeFileSync(capturePath, image.toPNG());
      stopBackend();
      mainWindow.destroy();
      app.exit(0);
    }
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });

  if (!app.isPackaged) {
    await mainWindow.loadURL(DEV_SERVER_URL);
  } else {
    await mainWindow.loadFile(path.join(projectRoot(), 'frontend', 'dist', 'index.html'), {
      query: { backendUrl: `http://${BACKEND_HOST}:${BACKEND_PORT}` },
    });
  }
}

ipcMain.handle('dialog:open-file', async (_event, options = {}) => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile'],
    filters: Array.isArray(options.filters) ? options.filters : undefined,
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle('dialog:open-directory', async () => {
  const result = await dialog.showOpenDialog(mainWindow, { properties: ['openDirectory', 'createDirectory'] });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle('dialog:read-text-file', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile'],
    filters: [{ name: '文本与字幕', extensions: ['txt', 'md', 'srt', 'vtt'] }],
  });
  if (result.canceled) return null;
  const selected = result.filePaths[0];
  const stat = fs.statSync(selected);
  if (stat.size > 10 * 1024 * 1024) throw new Error('文本文件不能超过 10 MB');
  return { path: selected, name: path.basename(selected), content: fs.readFileSync(selected, 'utf8') };
});

ipcMain.handle('shell:show-item', (_event, targetPath) => {
  if (typeof targetPath === 'string' && path.isAbsolute(targetPath)) shell.showItemInFolder(targetPath);
});

ipcMain.handle('shell:open-external', (_event, targetUrl) => {
  if (typeof targetUrl !== 'string' || !/^https:\/\//i.test(targetUrl)) throw new Error('只允许打开 HTTPS 链接');
  return shell.openExternal(targetUrl);
});

ipcMain.handle('media:grant-audio', (_event, targetPath) => {
  validateAudioPath(targetPath);
  const token = randomUUID();
  audioGrants.set(token, targetPath);
  return `langbai-audio://media/${token}`;
});

ipcMain.handle('dialog:export-audio', async (_event, targetPath) => {
  const source = validateAudioPath(targetPath);
  const extension = path.extname(source).toLowerCase();
  const result = await dialog.showSaveDialog(mainWindow, {
    title: '导出音频',
    defaultPath: path.basename(source),
    filters: [{ name: `${extension.slice(1).toUpperCase()} 音频`, extensions: [extension.slice(1)] }],
  });
  if (result.canceled || !result.filePath) return null;
  const destination = path.resolve(result.filePath);
  if (!AUDIO_EXTENSIONS.has(path.extname(destination).toLowerCase())) throw new Error('导出目标格式无效');
  if (destination.toLowerCase() !== source.toLowerCase()) await fs.promises.copyFile(source, destination);
  return { path: destination, name: path.basename(destination) };
});

ipcMain.handle('app:set-zoom-factor', (_event, rawFactor) => {
  const factor = Math.max(0.8, Math.min(1.5, Number(rawFactor) || 1));
  mainWindow?.webContents.setZoomFactor(factor);
  desktopState.zoomFactor = factor;
  saveDesktopState();
  return factor;
});

ipcMain.handle('updates:check', async (_event, requestedChannel = 'stable') => {
  if (!app.isPackaged) return { supported: false, reason: '开发模式不检查更新' };
  const wantsBeta = requestedChannel === 'beta';
  autoUpdater.allowPrerelease = wantsBeta;
  autoUpdater.channel = wantsBeta ? 'beta' : 'latest';
  const result = await autoUpdater.checkForUpdates();
  return { supported: true, channel: wantsBeta ? 'beta' : 'stable', updateInfo: result?.updateInfo || null };
});

ipcMain.handle('updates:download', () => autoUpdater.downloadUpdate());
ipcMain.handle('updates:install', () => autoUpdater.quitAndInstall(false, true));

ipcMain.handle('app:runtime-info', () => ({
  backendUrl: `http://${BACKEND_HOST}:${BACKEND_PORT}`,
  platform: process.platform,
  version: app.getVersion(),
  packaged: app.isPackaged,
  zoomFactor: mainWindow?.webContents.getZoomFactor() || 1,
  desktopLogPath: desktopLogPath(),
}));

app.whenReady().then(async () => {
  app.setAppUserModelId('studio.langbai.tts');
  openDesktopLog();
  process.on('uncaughtException', (error) => logDesktop('ERROR', `Uncaught exception: ${error.stack || error.message}`));
  process.on('unhandledRejection', (reason) => logDesktop('ERROR', `Unhandled rejection: ${String(reason)}`));
  protocol.handle('langbai-audio', (request) => {
    const url = new URL(request.url);
    const token = url.pathname.replace(/^\//, '');
    const targetPath = audioGrants.get(token);
    if (!targetPath) return new Response('Not found', { status: 404 });
    return electronNet.fetch(pathToFileURL(targetPath).toString(), { headers: request.headers });
  });
  configureUpdater();
  await createWindow();
});
app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
app.on('before-quit', () => {
  appIsQuitting = true;
  stopBackend();
  desktopLogStream?.end();
});
app.on('window-all-closed', () => app.quit());
