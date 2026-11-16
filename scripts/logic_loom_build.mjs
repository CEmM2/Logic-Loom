import { spawnSync } from 'node:child_process';

const sidecarScripts = {
  darwin: 'sidecar:build:macos',
  linux: 'sidecar:build:linux',
  win32: 'sidecar:build:windows',
};

const sidecarScript = sidecarScripts[process.platform];

if (!sidecarScript) {
  console.error(`[logic-loom:build] unsupported platform: ${process.platform}`);
  process.exit(1);
}

const run = (command, args, extraEnv = {}) => {
  const result = spawnSync(command, args, {
    stdio: 'inherit',
    shell: process.platform === 'win32',
    env: {
      ...process.env,
      ...extraEnv,
    },
  });

  if (result.error) {
    console.error(`[logic-loom:build] failed to run ${command}:`, result.error);
    process.exit(1);
  }

  if ((result.status ?? 1) !== 0) {
    process.exit(result.status ?? 1);
  }
};

run('pnpm', [sidecarScript]);

if (process.platform === 'darwin') {
  run('pnpm', ['logic-loom:build:tauri:app']);
  run('pnpm', ['logic-loom:build:dmg:macos']);
} else {
  run('pnpm', ['logic-loom:build:tauri']);
}
