const { app, BrowserWindow } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const projectRoot = path.resolve(__dirname, "..", "..");
const outputRoot = path.resolve(
  process.argv[2] || path.join(projectRoot, "docs", "audit", "commercial-current"),
);
const distIndex = path.join(projectRoot, "frontend", "dist", "index.html");
const views = ["创作台", "任务队列", "音频库", "历史记录", "设置与路径"];
const slugs = ["studio", "tasks", "audio-library", "history", "settings"];

const DEFAULT_VIEWPORT = { width: 1920, height: 1080 };
const MINIMUM_VIEWPORT = { width: 1180, height: 720 };

app.commandLine.appendSwitch("force-device-scale-factor", "1");

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function settle(window) {
  await sleep(600);
  await window.webContents.executeJavaScript(
    "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
  );
}

async function inspectCurrentView(window) {
  return window.webContents.executeJavaScript(`(() => {
    const visible = element => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
    };
    const identify = element => {
      const id = element.id ? "#" + element.id : "";
      const classes = Array.from(element.classList || []).slice(0, 3).map(name => "." + name).join("");
      return element.tagName.toLowerCase() + id + classes;
    };
    const textElements = Array.from(document.querySelectorAll("body *"))
      .filter(element => visible(element) && Array.from(element.childNodes).some(node => node.nodeType === 3 && node.textContent.trim()));
    const undersizedText = textElements.map(element => ({
      selector: identify(element),
      text: element.textContent.trim().replace(/\\s+/g, " ").slice(0, 80),
      fontSize: Number.parseFloat(getComputedStyle(element).fontSize),
    })).filter(item => item.fontSize < 12);
    const interactive = Array.from(document.querySelectorAll(
      "button, a, input, select, textarea, [role='button'], [role='switch'], [role='checkbox']",
    )).filter(visible).map(element => {
      const rect = element.getBoundingClientRect();
      return {
        selector: identify(element),
        label: (element.getAttribute("aria-label") || element.title || element.textContent || element.value || "")
          .trim().replace(/\\s+/g, " ").slice(0, 80),
        width: Math.round(rect.width * 10) / 10,
        height: Math.round(rect.height * 10) / 10,
      };
    });
    const undersizedTargets = interactive.filter(item => item.width < 40 || item.height < 40);
    const actionFontSize = element => {
      const textNodes = [element, ...element.querySelectorAll("*")]
        .filter(candidate => visible(candidate) && Array.from(candidate.childNodes)
          .some(node => node.nodeType === 3 && node.textContent.trim()));
      return Math.max(...textNodes.map(candidate => Number.parseFloat(getComputedStyle(candidate).fontSize)));
    };
    const textualActions = Array.from(document.querySelectorAll("button, a, [role='button']"))
      .filter(element => visible(element) && element.textContent.trim())
      .map(element => ({
        selector: identify(element),
        text: element.textContent.trim().replace(/\\s+/g, " ").slice(0, 80),
        fontSize: actionFontSize(element),
      }));
    const primaryActions = Array.from(document.querySelectorAll(".primary-button, .secondary-button, .danger-button"))
      .filter(element => visible(element) && element.textContent.trim())
      .map(element => ({
        selector: identify(element),
        text: element.textContent.trim().replace(/\\s+/g, " ").slice(0, 80),
        fontSize: actionFontSize(element),
      }));
    const viewport = { width: window.innerWidth, height: window.innerHeight, devicePixelRatio: window.devicePixelRatio };
    return {
      title: document.querySelector("main h1")?.textContent?.trim() || "",
      pageKind: document.querySelector(".data-page") ? "data-page" : document.querySelector(".manager-page") ? "settings" : "studio",
      bodyText: document.body.innerText.replace(/\\s+/g, " ").slice(0, 5000),
      bodyScrollWidth: document.body.scrollWidth,
      bodyClientWidth: document.body.clientWidth,
      bodyScrollHeight: document.body.scrollHeight,
      bodyClientHeight: document.body.clientHeight,
      viewport,
      appFontSize: Number.parseFloat(getComputedStyle(document.querySelector(".app-shell") || document.body).fontSize),
      undersizedText,
      undersizedTargets,
      undersizedActionText: textualActions.filter(item => item.fontSize < 14),
      undersizedPrimaryActionText: primaryActions.filter(item => item.fontSize < 15),
    };
  })()`);
}

async function inspectView(window, label) {
  const found = await window.webContents.executeJavaScript(`(() => {
    const target = Array.from(document.querySelectorAll("nav button, .sidebar-settings"))
      .find(element => element.textContent.includes(${JSON.stringify(label)}));
    if (!target) return false;
    target.click();
    return true;
  })()`);
  if (!found) throw new Error(`找不到导航入口：${label}`);
  await settle(window);
  return inspectCurrentView(window);
}

async function focusTrace(window) {
  await window.webContents.executeJavaScript("document.body.focus()");
  const trace = [];
  for (let index = 0; index < 18; index += 1) {
    window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Tab" });
    window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Tab" });
    await sleep(40);
    trace.push(await window.webContents.executeJavaScript(`(() => {
      const element = document.activeElement;
      return {
        tag: element?.tagName?.toLowerCase() || "",
        label: (element?.getAttribute?.("aria-label") || element?.title || element?.textContent || "")
          .trim().replace(/\\s+/g, " ").slice(0, 80),
        focusVisible: Boolean(element?.matches?.(":focus-visible")),
      };
    })()`));
  }
  return trace;
}

async function capture(window, filename) {
  const image = await window.webContents.capturePage();
  fs.writeFileSync(path.join(outputRoot, filename), image.toPNG());
}

app.whenReady().then(async () => {
  fs.mkdirSync(outputRoot, { recursive: true });
  const window = new BrowserWindow({
    show: false,
    useContentSize: true,
    width: DEFAULT_VIEWPORT.width,
    height: DEFAULT_VIEWPORT.height,
    backgroundColor: "#eef2f6",
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });

  await window.loadFile(distIndex);
  await window.webContents.executeJavaScript("localStorage.setItem('langbai-onboarding-complete', '1'); localStorage.setItem('langbai-density', 'comfortable')");
  await window.loadFile(distIndex);
  await window.webContents.setZoomFactor(1);
  await settle(window);

  const report = {
    capturedAt: new Date().toISOString(),
    defaultViewport: DEFAULT_VIEWPORT,
    minimumViewport: MINIMUM_VIEWPORT,
    views: {},
    zoom150: {},
    minimumViews: {},
    focusTrace: [],
  };

  for (let index = 0; index < views.length; index += 1) {
    const label = views[index];
    report.views[label] = await inspectView(window, label);
    await capture(window, `${String(index + 1).padStart(2, "0")}-${slugs[index]}-1920x1080.png`);
  }

  await window.setContentSize(DEFAULT_VIEWPORT.width, DEFAULT_VIEWPORT.height);
  await window.webContents.setZoomFactor(1.5);
  report.zoom150 = await inspectView(window, "创作台");
  await capture(window, "06-studio-1920x1080-zoom150.png");

  await window.webContents.setZoomFactor(1);
  window.setContentSize(MINIMUM_VIEWPORT.width, MINIMUM_VIEWPORT.height);
  await settle(window);
  for (let index = 0; index < views.length; index += 1) {
    const label = views[index];
    report.minimumViews[label] = await inspectView(window, label);
    await capture(window, `${String(index + 7).padStart(2, "0")}-${slugs[index]}-1180x720.png`);
  }

  window.setContentSize(DEFAULT_VIEWPORT.width, DEFAULT_VIEWPORT.height);
  await window.webContents.setZoomFactor(1);
  await inspectView(window, "创作台");
  window.show();
  window.focus();
  await sleep(250);
  report.focusTrace = await focusTrace(window);
  window.hide();

  fs.writeFileSync(path.join(outputRoot, "ui-metrics.json"), JSON.stringify(report, null, 2));
  window.destroy();
  app.quit();
}).catch(error => {
  process.stderr.write(String(error?.stack || error));
  app.exit(1);
});
