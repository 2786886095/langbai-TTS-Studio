const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const path = require("node:path");
const python = process.env.PYTHON || "C:\\Python314\\python.exe";
const printed = spawnSync(python, ["-c", "from studio_gradio_bridge import browser_preflight_js; print(browser_preflight_js())"], { cwd: __dirname, encoding: "utf8" });
if (printed.status !== 0) throw new Error(printed.stderr);
const guard = new Function(`return (${printed.stdout});`)();
const banner = { textContent: "", style: {}, scrollIntoView() {} };
global.document = { getElementById: () => banner };
const args = Array.from({ length: 11 }, (_, index) => index);
async function main() {
  global.fetch = async () => ({ ok: true, json: async () => ({ owner: null, residents: [] }) });
  assert.deepEqual(await guard(...args), args);
  assert.deepEqual(await guard(), []);
  global.fetch = async () => ({ ok: true, json: async () => ({ owner: { engine: "breeze" }, residents: [] }) });
  await assert.rejects(() => guard(...args), /Studio 正在使用显卡/);
  assert.match(banner.textContent, /不会自动结束/);
  global.fetch = async () => ({ ok: true, json: async () => ({ owner: null, residents: [{ id: "breeze", running: true, warmed: true }] }) });
  await assert.rejects(() => guard(...args), /Studio 正在使用显卡/);
  global.fetch = async () => { throw new Error("Studio offline"); };
  assert.deepEqual(await guard(...args), args, "offline Studio must retain the original short-clip workflow");
  console.log("Browser GPU guard passed: owner, hot resident, offline fallback, 11/0 inputs preserved.");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
