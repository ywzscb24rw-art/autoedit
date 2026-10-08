// AutoEdit desktop shell: starts the bundled Python backend on 127.0.0.1 and shows the UI.
const { app, BrowserWindow, Menu, dialog, ipcMain, session, shell, systemPreferences, desktopCapturer } = require('electron')
const { spawn } = require('node:child_process')
const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')

const REPO = 'https://github.com/ywzscb24rw-art/autoedit'
const SUPPORT_DIR = path.join(app.getPath('appData'), 'AutoEdit')
const LOG_DIR = path.join(app.getPath('home'), 'Library', 'Logs', 'AutoEdit')
const LOG_FILE = path.join(LOG_DIR, 'autoedit.log')

// Packaged: everything lives in Contents/Resources. Dev (`npm start` in desktop/): the repo.
const RES = app.isPackaged ? process.resourcesPath : path.resolve(__dirname, '..')
const PYTHON = app.isPackaged ? path.join(RES, 'python', 'bin', 'python3') : path.join(RES, '.venv', 'bin', 'python')
const APP_DIR = app.isPackaged ? path.join(RES, 'app') : RES
const WEB_DIST = app.isPackaged ? path.join(RES, 'app', 'web-dist') : path.join(RES, 'web', 'dist')
const BIN_DIR = path.join(RES, 'bin')

// Keep Chromium's own profile out of the folder testers see (projects, models, config).
app.setPath('userData', path.join(SUPPORT_DIR, 'Electron'))

const token = crypto.randomBytes(24).toString('hex')
let backend = null
let port = null
let win = null
let quitting = false

fs.mkdirSync(LOG_DIR, { recursive: true })
const log = fs.createWriteStream(LOG_FILE, { flags: 'a' })
const logLine = (msg) => log.write(`[${new Date().toISOString()}] ${msg}\n`)

function startBackend() {
  return new Promise((resolve, reject) => {
    const env = {
      ...process.env,
      PATH: `${BIN_DIR}:/usr/bin:/bin:/usr/sbin:/sbin`,
      AUTOEDIT_DATA: path.join(SUPPORT_DIR, 'projects'),
      AUTOEDIT_CONFIG: path.join(SUPPORT_DIR, 'config.json'),
      HF_HOME: path.join(SUPPORT_DIR, 'models'),
      PYTHONDONTWRITEBYTECODE: '1',
      PYTHONUNBUFFERED: '1',
    }
    logLine(`starting backend: ${PYTHON}`)
    backend = spawn(PYTHON, ['-m', 'server.app', '--token', token, '--web', WEB_DIST], { cwd: APP_DIR, env })
    let buffered = ''
    backend.stdout.on('data', (d) => {
      const text = d.toString()
      log.write(text)
      buffered += text
      const m = buffered.match(/AUTOEDIT_PORT=(\d+)/)
      if (m && !port) {
        port = Number(m[1])
        resolve(port)
      }
    })
    backend.stderr.on('data', (d) => log.write(d))
    backend.on('exit', (code) => {
      logLine(`backend exited with ${code}`)
      if (!port) reject(new Error(`backend exited with ${code}`))
      else if (!quitting) {
        dialog.showErrorBox('AutoEdit stopped unexpectedly', 'Please reopen the app. If it keeps happening, use Help → Send feedback.')
        app.quit()
      }
    })
    setTimeout(() => !port && reject(new Error('backend did not start within 60s')), 60000)
  })
}

async function waitHealthy() {
  for (let i = 0; i < 120; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/api/health`)
      if (r.ok) return
    } catch {}
    await new Promise((r) => setTimeout(r, 250))
  }
  throw new Error('backend health check timed out')
}

// Feedback opens a pre-filled GitHub issue: version, macOS and the recent log are included,
// and nobody's email address ends up in the app.
function sendFeedback() {
  let tail = ''
  try {
    tail = fs.readFileSync(LOG_FILE, 'utf8').split('\n').slice(-30).join('\n')
    // Issues are public: don't publish the tester's macOS username via file paths.
    tail = tail.split(app.getPath('home')).join('~')
  } catch {}
  const body = [
    '> This issue will be public. Please leave out anything private.',
    '',
    '**What happened, or what would you like to see?**',
    '',
    '',
    '**Steps / video type / mode** (if it was a problem)',
    '',
    '',
    '---',
    `AutoEdit ${app.getVersion()} · macOS ${process.getSystemVersion()}`,
    '<details><summary>Recent log</summary>',
    '',
    '```',
    tail.slice(-4000),
    '```',
    '</details>',
  ].join('\n')
  const title = `Beta feedback (${app.getVersion()})`
  shell.openExternal(`${REPO}/issues/new?labels=beta-feedback&title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`)
}

function buildMenu() {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { role: 'appMenu' },
    { role: 'editMenu' },
    { role: 'viewMenu' },
    { role: 'windowMenu' },
    {
      role: 'help',
      submenu: [
        { label: 'Send feedback…', click: sendFeedback },
        { label: 'Open projects folder', click: () => shell.openPath(path.join(SUPPORT_DIR, 'projects')) },
        { label: 'Show logs', click: () => shell.showItemInFolder(LOG_FILE) },
        { type: 'separator' },
        { label: 'Check for updates…', click: () => shell.openExternal(`${REPO}/releases/latest`) },
      ],
    },
  ]))
}

function createWindow() {
  win = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    title: 'AutoEdit',
    backgroundColor: '#161615',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      sandbox: true,
      additionalArguments: [`--autoedit-version=${app.getVersion()}`],
    },
  })
  // Links to the outside world open in the user's browser, not inside the app.
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith(`http://127.0.0.1:${port}`)) {
      e.preventDefault()
      shell.openExternal(url)
    }
  })
  win.loadURL(`http://127.0.0.1:${port}/?token=${token}`)
}

ipcMain.on('show-in-folder', (_e, p) => {
  // Only reveal files inside AutoEdit's own folder.
  const resolved = path.resolve(p)
  if (resolved.startsWith(SUPPORT_DIR)) shell.showItemInFolder(resolved)
})
ipcMain.on('open-external', (_e, url) => {
  if (/^(https:|mailto:)/.test(url)) shell.openExternal(url)
})
ipcMain.on('send-feedback', sendFeedback)

app.whenReady().then(async () => {
  buildMenu()
  // Screen recording: macOS's own picker (window / screen / app), as in Safari or Zoom.
  session.defaultSession.setDisplayMediaRequestHandler(
    async (_req, callback) => {
      const sources = await desktopCapturer.getSources({ types: ['screen'] })
      callback({ video: sources[0], audio: 'loopback' })
    },
    { useSystemPicker: true },
  )
  session.defaultSession.setPermissionRequestHandler((_wc, permission, cb) => cb(['media', 'display-capture'].includes(permission)))
  if (systemPreferences.getMediaAccessStatus('microphone') !== 'granted') {
    systemPreferences.askForMediaAccess('microphone').catch(() => {})
  }
  try {
    await startBackend()
    await waitHealthy()
    createWindow()
  } catch (err) {
    logLine(`startup failed: ${err}`)
    const r = dialog.showMessageBoxSync({
      type: 'error',
      message: "AutoEdit couldn't start",
      detail: `${err.message}\n\nThe log file has details. Please send it with Help → Send feedback.`,
      buttons: ['Show log', 'Quit'],
    })
    if (r === 0) shell.showItemInFolder(LOG_FILE)
    app.quit()
  }
})

app.on('before-quit', () => {
  quitting = true
  if (backend && !backend.killed) backend.kill('SIGTERM')
})
app.on('window-all-closed', () => app.quit())
