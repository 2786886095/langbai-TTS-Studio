"use strict";

// Build a small, explicit application tree. electron-builder's ASAR integrity
// walker descends into every directory in an extraResources source even when
// its file filter lists only six workers. Never point that walker at backend/.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const projectRoot = fs.realpathSync(path.resolve(__dirname, ".."));
const buildRoot = path.join(projectRoot, "build");
const appRoot = path.join(buildRoot, "desktop-app");
const resourceRoot = path.join(buildRoot, "desktop-resources");
const markerName = ".langbai-desktop-stage.json";
const markerOwner = "langbai-desktop-stage-v1";
const workers = ["engine_worker.py", "engine_runtime.py", "training_worker.py",
  "gpt_workbench_worker.py", "breeze_worker.py", "breeze_worker_ctl.py"];
const frontendExtensions = new Set([".html", ".js", ".css", ".png", ".svg", ".ico",
  ".webp", ".jpg", ".jpeg", ".woff", ".woff2", ".ttf", ".map"]);
const copiedFiles = [];
const copiedModules = new Map();

function hash(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function inside(parent, target) {
  const relative = path.relative(parent, target);
  return relative !== "" && !relative.startsWith(`..${path.sep}`)
    && relative !== ".." && !path.isAbsolute(relative);
}

function assertPlainPath(target) {
  if (fs.lstatSync(target).isSymbolicLink()) throw new Error(`Refusing symlink/junction: ${target}`);
}

function prepareGeneratedDirectory(target) {
  if (!inside(buildRoot, target) || ![appRoot, resourceRoot].includes(target)) {
    throw new Error(`Refusing to clean an unexpected staging path: ${target}`);
  }
  if (fs.existsSync(target)) {
    assertPlainPath(target);
    const marker = path.join(target, markerName);
    if (!fs.existsSync(marker)) throw new Error(`Staging directory has no ownership marker: ${target}`);
    const record = JSON.parse(fs.readFileSync(marker, "utf8"));
    if (record.owner !== markerOwner || record.projectRoot !== projectRoot) {
      throw new Error(`Staging ownership mismatch: ${target}`);
    }
    // Both absolute targets and the real parent were checked above. Only our
    // generated directories are removed; no cache ACL or source is changed.
    fs.rmSync(target, { recursive: true, force: true });
  }
  fs.mkdirSync(target, { recursive: true });
  fs.writeFileSync(path.join(target, markerName), JSON.stringify({ owner: markerOwner, projectRoot }, null, 2));
}

function copyFile(source, destination) {
  assertPlainPath(source);
  if (!fs.statSync(source).isFile()) throw new Error(`Expected a regular file: ${source}`);
  if (/\.(?:safetensors|ckpt|pth|pt|onnx|gguf|wav|mp3|flac|m4a|ogg)$/i.test(source)) {
    throw new Error(`Model weights and reference audio must not enter the desktop stage: ${source}`);
  }
  const original = hash(source);
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.copyFileSync(source, destination);
  if (hash(destination) !== original || hash(source) !== original) {
    throw new Error(`Source changed while staging, run again after the build settles: ${source}`);
  }
  copiedFiles.push({ path: path.relative(buildRoot, destination).replaceAll("\\", "/"),
    bytes: fs.statSync(destination).size, sha256: original });
}

function copyTree(source, destination, accept) {
  assertPlainPath(source);
  for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
    const input = path.join(source, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`Refusing symlink/junction: ${input}`);
    if (["node_modules", ".bin", ".git", ".cache", ".npm-cache", "__pycache__"].includes(entry.name)) continue;
    const output = path.join(destination, entry.name);
    if (entry.isDirectory()) copyTree(input, output, accept);
    else if (entry.isFile()) {
      if (accept && !accept(input)) throw new Error(`Unexpected file in a desktop build input: ${input}`);
      copyFile(input, output);
    }
  }
}

function resolveInstalledPackage(name, from) {
  if (!/^(?:@[a-z0-9_.-]+\/)?[a-z0-9_.-]+$/i.test(name)) throw new Error(`Invalid package name: ${name}`);
  let current = from;
  while (current === projectRoot || inside(projectRoot, current)) {
    if (path.basename(current) !== "node_modules") {
      const candidate = path.join(current, "node_modules", ...name.split("/"));
      if (fs.existsSync(path.join(candidate, "package.json"))) return candidate;
    }
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return null;
}

function stageDependency(name, from, optional = false) {
  // Electron is the runtime supplied by electron-builder, never an app dependency.
  if (name === "electron") return;
  const source = resolveInstalledPackage(name, from);
  if (source === null) {
    if (optional) return;
    throw new Error(`Missing installed production dependency ${name}; prepare dependencies before staging.`);
  }
  const relative = path.relative(projectRoot, source);
  if (!relative.startsWith(`node_modules${path.sep}`) || !inside(path.join(projectRoot, "node_modules"), source)) {
    throw new Error(`Dependency resolved outside the project's installed modules: ${source}`);
  }
  assertPlainPath(source);
  if (copiedModules.has(source)) return;
  const metadata = JSON.parse(fs.readFileSync(path.join(source, "package.json"), "utf8"));
  copiedModules.set(source, { name: metadata.name, version: metadata.version,
    path: relative.replaceAll("\\", "/") });
  // Preserve npm's nested layout so two installed dependency versions do not
  // overwrite one another. Nested node_modules are copied through this closure.
  copyTree(source, path.join(appRoot, relative));
  const optionalNames = new Set(Object.keys(metadata.optionalDependencies || {}));
  for (const dependency of Object.keys(metadata.dependencies || {})) {
    stageDependency(dependency, source, optionalNames.has(dependency));
  }
  for (const dependency of optionalNames) stageDependency(dependency, source, true);
  for (const dependency of Object.keys(metadata.peerDependencies || {})) {
    stageDependency(dependency, source, Boolean(metadata.peerDependenciesMeta?.[dependency]?.optional));
  }
}

function stage() {
  if (fs.existsSync(buildRoot)) {
    assertPlainPath(buildRoot);
    if (fs.realpathSync(buildRoot) !== buildRoot) throw new Error(`Unexpected real build path: ${buildRoot}`);
  } else fs.mkdirSync(buildRoot);
  const metadata = JSON.parse(fs.readFileSync(path.join(projectRoot, "package.json"), "utf8"));
  if (!fs.existsSync(path.join(projectRoot, "frontend", "dist", "index.html"))) {
    throw new Error("frontend/dist/index.html is missing; build the frontend first.");
  }
  prepareGeneratedDirectory(appRoot);
  prepareGeneratedDirectory(resourceRoot);
  for (const name of ["main.cjs", "preload.cjs", "update-policy.cjs"]) {
    copyFile(path.join(projectRoot, "electron", name), path.join(appRoot, "electron", name));
  }
  for (const name of ["LICENSE", "THIRD_PARTY_NOTICES.md"]) {
    copyFile(path.join(projectRoot, name), path.join(appRoot, name));
  }
  copyTree(path.join(projectRoot, "frontend", "dist"), path.join(appRoot, "frontend", "dist"),
    file => frontendExtensions.has(path.extname(file).toLowerCase()));
  const appMetadata = Object.fromEntries(["name", "version", "private", "description", "main",
    "author", "license", "repository", "dependencies"].filter(key => metadata[key] !== undefined)
    .map(key => [key, metadata[key]]));
  fs.writeFileSync(path.join(appRoot, "package.json"), JSON.stringify(appMetadata, null, 2) + "\n");
  for (const dependency of Object.keys(metadata.dependencies || {})) stageDependency(dependency, projectRoot);
  for (const worker of workers) {
    copyFile(path.join(projectRoot, "backend", worker), path.join(resourceRoot, "backend", worker));
  }
  const report = { version: metadata.version, generatedAt: new Date().toISOString(),
    appDirectory: path.relative(projectRoot, appRoot).replaceAll("\\", "/"),
    resourceDirectory: path.relative(projectRoot, resourceRoot).replaceAll("\\", "/"),
    files: copiedFiles, productionDependencies: [...copiedModules.values()],
    totalBytes: copiedFiles.reduce((total, file) => total + file.bytes, 0),
    excluded: ["backend/.venv", ".pytest_cache", "model weights", "reference audio", "user data"],
    asarIntegrityDisabled: false };
  fs.writeFileSync(path.join(buildRoot, "desktop-stage-report.json"), JSON.stringify(report, null, 2) + "\n");
  console.log(`Staged ${copiedFiles.length} files and ${copiedModules.size} production packages (${report.totalBytes} bytes).`);
  console.log(`App: ${appRoot}`);
  console.log(`Workers: ${path.join(resourceRoot, "backend")}`);
}

if (require.main === module) {
  try { stage(); } catch (error) { console.error(error.message); process.exitCode = 1; }
}

module.exports = { stage, resolveInstalledPackage };
