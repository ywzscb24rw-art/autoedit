// The only desktop powers the web UI gets: reveal a file, open a link, send feedback.
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('autoedit', {
  // Sandboxed preloads can't read files; main.js passes the version as an argument.
  version: (process.argv.find((a) => a.startsWith('--autoedit-version=')) || '=dev').split('=')[1],
  showInFolder: (p) => ipcRenderer.send('show-in-folder', String(p)),
  openExternal: (url) => ipcRenderer.send('open-external', String(url)),
  sendFeedback: () => ipcRenderer.send('send-feedback'),
})
