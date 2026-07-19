const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('langbaiDesktop', {
  getRuntimeInfo: () => ipcRenderer.invoke('app:runtime-info'),
  chooseFile: (options) => ipcRenderer.invoke('dialog:open-file', options),
  readTextFile: () => ipcRenderer.invoke('dialog:read-text-file'),
  chooseDirectory: () => ipcRenderer.invoke('dialog:open-directory'),
  showItemInFolder: (targetPath) => ipcRenderer.invoke('shell:show-item', targetPath),
  openExternal: (targetUrl) => ipcRenderer.invoke('shell:open-external', targetUrl),
  getAudioUrl: (targetPath) => ipcRenderer.invoke('media:grant-audio', targetPath),
  exportAudio: (targetPath) => ipcRenderer.invoke('dialog:export-audio', targetPath),
  setZoomFactor: (factor) => ipcRenderer.invoke('app:set-zoom-factor', factor),
  checkForUpdates: (channel) => ipcRenderer.invoke('updates:check', channel),
  downloadUpdate: () => ipcRenderer.invoke('updates:download'),
  installUpdate: () => ipcRenderer.invoke('updates:install'),
  onCommand: (callback) => {
    const listener = (_event, command) => callback(command);
    ipcRenderer.on('app:command', listener);
    return () => ipcRenderer.removeListener('app:command', listener);
  },
  onUpdateEvent: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on('updates:event', listener);
    return () => ipcRenderer.removeListener('updates:event', listener);
  },
});
