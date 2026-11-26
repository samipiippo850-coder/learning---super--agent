// ExamOS desktop shell: starts the local FastAPI backend, waits for health,
// then opens the dashboard in a native Electron window.
"use strict";

const { app, BrowserWindow, Menu, dialog, shell } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const BACKEND_DIR = path.join(REPO_ROOT, "backend");
const WEB_OUT = path.join(REPO_ROOT, "web", "out");
const VAR_DIR = path.join(BACKEND_DIR, "var");

let backendProcess = null;
let mainWindow = null;
let backendPort = 0;

function findPython() {
  const candidates = [
    process.env.EXAMOS_PYTHON,
    path.join(REPO_ROOT, ".venv", "Scripts", "python.exe"),
    path.join(REPO_ROOT, ".venv", "bin", "python"),
    "python",
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      if (candidate === "python" || fs.existsSync(candidate)) return candidate;
    } catch {
      /* ignore */
    }
  }
  return "python";
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

function waitHealthy(port, timeoutMs = 45000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const request = http.get(
        { host: "127.0.0.1", port, path: "/health", timeout: 1500 },
        (response) => {
          response.resume();
          if (response.statusCode === 200) resolve();
          else retry();
        },
      );
      request.on("error", retry);
      request.on("timeout", () => {
        request.destroy();
        retry();
      });
    };
    const retry = () => {
      if (Date.now() > deadline) {
        reject(new Error("ExamOS backend did not become ready"));
        return;
      }
      setTimeout(attempt, 250);
    };
    attempt();
  });
}

function startBackend(port) {
  const python = findPython();
  const env = {
    ...process.env,
    PYTHONPATH: BACKEND_DIR,
    PYTHONIOENCODING: "utf-8",
    EXAMOS_VAR_DIR: VAR_DIR,
    EXAMOS_DATABASE_URL: process.env.EXAMOS_DATABASE_URL || `sqlite:///${path.join(VAR_DIR, "examos.db").replace(/\\/g, "/")}`,
    TMP: path.join(REPO_ROOT, ".tmp"),
    TEMP: path.join(REPO_ROOT, ".tmp"),
  };
  fs.mkdirSync(env.TMP, { recursive: true });
  fs.mkdirSync(VAR_DIR, { recursive: true });

  backendProcess = spawn(
    python,
    ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(port), "--log-level", "warning"],
    { cwd: BACKEND_DIR, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] },
  );
  let stderr = "";
  backendProcess.stderr.on("data", (chunk) => {
    stderr += chunk.toString();
    if (stderr.length > 4000) stderr = stderr.slice(-4000);
  });
  backendProcess.stdout.on("data", () => {});
  backendProcess.on("exit", (code) => {
    if (code && mainWindow) {
      dialog.showErrorBox("ExamOS 后端已退出", stderr || `exit code ${code}`);
    }
  });
  return backendProcess;
}

function stopBackend() {
  if (backendProcess && !backendProcess.killed) {
    try {
      backendProcess.kill();
    } catch {
      /* ignore */
    }
    backendProcess = null;
  }
}

function buildMenu() {
  const template = [
    {
      label: "文件",
      submenu: [{ role: "quit", label: "退出 ExamOS" }],
    },
    {
      label: "视图",
      submenu: [
        { role: "reload", label: "重新加载" },
        { role: "forceReload", label: "强制重新加载" },
        { type: "separator" },
        { role: "resetZoom", label: "实际大小" },
        { role: "zoomIn", label: "放大" },
        { role: "zoomOut", label: "缩小" },
        { type: "separator" },
        { role: "togglefullscreen", label: "全屏" },
        { role: "toggleDevTools", label: "开发者工具" },
      ],
    },
    {
      label: "帮助",
      submenu: [
        {
          label: "打开 API 文档",
          click: () => shell.openExternal(`http://127.0.0.1:${backendPort}/docs`),
        },
        {
          label: "关于 ExamOS",
          click: () =>
            dialog.showMessageBox({
              title: "ExamOS",
              message: "ExamOS · 个人定制化考研应试智能体",
              detail: `v0.1 MVP\n后端端口: ${backendPort}\n数据目录: ${VAR_DIR}`,
            }),
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function createWindow() {
  backendPort = await freePort();
  startBackend(backendPort);
  try {
    await waitHealthy(backendPort);
  } catch (error) {
    dialog.showErrorBox(
      "无法启动 ExamOS 后端",
      `${error.message}\n\n请在仓库根目录执行：\n  powershell -File .\\scripts\\setup.ps1\n  powershell -File .\\scripts\\seed.ps1`,
    );
    app.quit();
    return;
  }

  mainWindow = new BrowserWindow({
    width: 1480,
    height: 940,
    minWidth: 1180,
    minHeight: 760,
    backgroundColor: "#12110E",
    title: "ExamOS · 个人定制化考研应试智能体",
    autoHideMenuBar: false,
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(`http://127.0.0.1:${backendPort}`)) return { action: "allow" };
    shell.openExternal(url);
    return { action: "deny" };
  });
  await mainWindow.loadURL(`http://127.0.0.1:${backendPort}/`);
}

app.whenReady().then(async () => {
  buildMenu();
  await createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  stopBackend();
  app.quit();
});

app.on("before-quit", stopBackend);
