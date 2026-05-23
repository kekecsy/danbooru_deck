const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('desktopAPI', {
  app: {
    getContext: () => ipcRenderer.invoke('app:get-context')
  },
  gallery: {
    getByDate: (date) => ipcRenderer.invoke('gallery:get-by-date', date),
    openLocalFile: (localPath) => ipcRenderer.invoke('gallery:open-local-file', localPath)
  },
  crawler: {
    ensureService: () => ipcRenderer.invoke('crawler:ensure-service'),
    start: (payload) => ipcRenderer.invoke('crawler:start', payload),
    pause: () => ipcRenderer.invoke('crawler:pause'),
    resume: () => ipcRenderer.invoke('crawler:resume'),
    stop: () => ipcRenderer.invoke('crawler:stop'),
    status: () => ipcRenderer.invoke('crawler:status'),
    setSafeMode: (safe) => ipcRenderer.invoke('crawler:set-safe-mode', !!safe)
  },
  external: {
    open: (url) => ipcRenderer.invoke('external:open', url)
  },
  dialog: {
    selectImage: () => ipcRenderer.invoke('dialog:select-image')
  },
  file: {
    exists: (targetPath) => ipcRenderer.invoke('file:exists', targetPath),
    readDataUrl: (targetPath) => ipcRenderer.invoke('file:read-data-url', targetPath),
    toFileUrl: (targetPath) => ipcRenderer.invoke('file:to-file-url', targetPath),
    toLocalUrl: (targetPath) => ipcRenderer.invoke('file:to-local-url', targetPath),
    savePng: (suggestedName, uint8Array) => ipcRenderer.invoke('file:save-png', {
      suggestedName,
      bytes: Array.from(uint8Array || [])
    }),
    copyPng: (uint8Array) => ipcRenderer.invoke('file:copy-png', {
      bytes: Array.from(uint8Array || [])
    })
  },
  preset: {
    list: () => ipcRenderer.invoke('preset:list')
  },
  caption: {
    generate: (imagePath, withArtist) => ipcRenderer.invoke('caption:generate', { imagePath, withArtist: !!withArtist }),
    read: (imagePath) => ipcRenderer.invoke('caption:read', imagePath),
    save: (imagePath, entry) => ipcRenderer.invoke('caption:save', { imagePath, entry }),
    listForDate: (date) => ipcRenderer.invoke('caption:list-for-date', date)
  }
});
