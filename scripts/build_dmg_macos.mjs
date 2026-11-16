import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const root = resolve(__dirname, "..");
const nodeGypCli = require.resolve("node-gyp/bin/node-gyp.js");
const tauriConfig = JSON.parse(
  readFileSync(resolve(root, "apps/desktop/src-tauri/tauri.conf.json"), "utf8"),
);

const productName = tauriConfig.productName;
const version = tauriConfig.version;
const arch = process.arch === "arm64" ? "aarch64" : process.arch;
const bundleRoot = resolve(root, "apps/desktop/src-tauri/target/release/bundle");
const appPath = resolve(bundleRoot, "macos", `${productName}.app`);
const dmgDir = resolve(bundleRoot, "dmg");
const workDir = resolve(dmgDir, ".appdmg");
const dmgPath = resolve(dmgDir, `${productName}_${version}_${arch}.dmg`);
const background2x = resolve(workDir, "background@2x.png");
const background = resolve(workDir, "background.png");
const sourceBanner = resolve(root, "assets/banner.png");
const iconPath = resolve(root, "apps/desktop/src-tauri/icons/icon.icns");

function resolvePackageDir(specifier) {
  try {
    return dirname(require.resolve(`${specifier}/package.json`));
  } catch (error) {
    const pnpmLinkedPackage = resolve(root, "node_modules/.pnpm/node_modules", specifier, "package.json");
    if (existsSync(pnpmLinkedPackage)) {
      return dirname(pnpmLinkedPackage);
    }

    fail(`missing ${specifier} package; run pnpm install first. ${error.message}`);
  }
}

const nativeAddons = [
  {
    name: "macos-alias",
    dir: resolvePackageDir("macos-alias"),
    artifactName: "volume.node",
  },
  {
    name: "fs-xattr",
    dir: resolvePackageDir("fs-xattr"),
    artifactName: "xattr.node",
  },
].map((addon) => ({
  ...addon,
  artifact: resolve(addon.dir, "build/Release", addon.artifactName),
}));

function fail(message) {
  console.error(`[logic-loom:dmg] ${message}`);
  process.exit(1);
}

function run(command, args) {
  const result = spawnSync(command, args, {
    stdio: "inherit",
  });

  if (result.error) {
    console.error(`[logic-loom:dmg] failed to run ${command}:`, result.error);
    process.exit(1);
  }

  if ((result.status ?? 1) !== 0) {
    process.exit(result.status ?? 1);
  }
}

function runInDir(cwd, command, args) {
  const result = spawnSync(command, args, {
    cwd,
    stdio: "inherit",
  });

  if (result.error) {
    console.error(`[logic-loom:dmg] failed to run ${command} in ${cwd}:`, result.error);
    process.exit(1);
  }

  if ((result.status ?? 1) !== 0) {
    process.exit(result.status ?? 1);
  }
}

function probeAddon(addon) {
  return spawnSync(process.execPath, ["-e", `require(${JSON.stringify(addon.artifact)});`], {
    encoding: "utf8",
    stdio: "pipe",
  });
}

function describeProbeFailure(result) {
  return [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
}

function shouldRebuildAddon(addon) {
  if (!existsSync(addon.artifact)) {
    console.log(`[logic-loom:dmg] missing native addon for ${addon.name}`);
    return true;
  }

  const result = probeAddon(addon);
  if (result.error) {
    fail(`failed to probe ${addon.name}: ${result.error.message}`);
  }

  if ((result.status ?? 1) === 0) {
    return false;
  }

  const output = describeProbeFailure(result);
  if (
    output.includes("NODE_MODULE_VERSION") ||
    output.includes("ERR_DLOPEN_FAILED") ||
    output.includes("Module version mismatch")
  ) {
    console.log(
      `[logic-loom:dmg] ${addon.name} was built for a different Node ABI; rebuilding for ${process.version}`,
    );
    return true;
  }

  fail(`failed to load ${addon.name} native addon:\n${output || "unknown error"}`);
}

function rebuildAddon(addon) {
  console.log(`[logic-loom:dmg] rebuilding native addon for ${addon.name}`);
  runInDir(addon.dir, process.execPath, [nodeGypCli, "rebuild"]);

  if (!existsSync(addon.artifact)) {
    fail(`expected built addon artifact at ${addon.artifact}`);
  }

  const result = probeAddon(addon);
  if ((result.status ?? 1) !== 0) {
    fail(
      `rebuilt ${addon.name} but it still failed to load:\n${describeProbeFailure(result) || "unknown error"}`,
    );
  }
}

function ensureNativeAddons() {
  for (const addon of nativeAddons) {
    if (!existsSync(addon.dir)) {
      fail(`missing ${addon.name} package at ${addon.dir}; run pnpm install first.`);
    }

    if (shouldRebuildAddon(addon)) {
      rebuildAddon(addon);
    }
  }
}

if (process.platform !== "darwin") {
  fail("custom DMG generation only runs on macOS.");
}

if (!existsSync(appPath)) {
  fail(`expected app bundle at ${appPath}. Run the app bundle build first.`);
}

ensureNativeAddons();
const appdmg = require("appdmg");

mkdirSync(workDir, { recursive: true });
rmSync(dmgPath, { force: true });

console.log("[logic-loom:dmg] preparing retina background assets");
run("sips", ["-c", "840", "1320", sourceBanner, "--out", background2x]);
run("sips", ["-z", "420", "660", background2x, "--out", background]);

console.log(`[logic-loom:dmg] creating ${dmgPath}`);

const emitter = appdmg({
  target: dmgPath,
  basepath: workDir,
  specification: {
    title: productName,
    icon: iconPath,
    background,
    "icon-size": 144,
    window: {
      position: { x: 160, y: 120 },
      size: { width: 660, height: 420 },
    },
    format: "UDZO",
    contents: [
      {
        x: 180,
        y: 300,
        type: "file",
        path: appPath,
      },
      {
        x: 480,
        y: 300,
        type: "link",
        path: "/Applications",
      },
    ],
  },
});

await new Promise((resolvePromise, rejectPromise) => {
  emitter.on("progress", (info) => {
    if (info.type === "step-begin" && info.title) {
      console.log(`[logic-loom:dmg] ${info.title}`);
      return;
    }

    if (info.type === "step-end" && info.title) {
      console.log(`[logic-loom:dmg] ${info.title}: ${info.status}`);
    }
  });

  emitter.once("error", rejectPromise);
  emitter.once("finish", resolvePromise);
});

console.log(`[logic-loom:dmg] finished: ${dmgPath}`);
