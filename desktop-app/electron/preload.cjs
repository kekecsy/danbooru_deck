const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('desktopAPI', {
  app: {
    getContext: () => ipcRenderer.invoke('app:get-context'),
    getPaths: () => ipcRenderer.invoke('app:get-paths'),
    revealFolder: (key) => ipcRenderer.invoke('app:reveal-folder', key)
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
    toThumbUrl: (targetPath, size) => ipcRenderer.invoke('file:to-thumb-url', { targetPath, size }),
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
    read: (imagePath) => ipcRenderer.invoke('caption:read', imagePath),
    save: (imagePath, entry) => ipcRenderer.invoke('caption:save', { imagePath, entry }),
    listForDate: (date) => ipcRenderer.invoke('caption:list-for-date', date),
    copyImage: (imagePath, maxEdge) => ipcRenderer.invoke('caption:copy-image', { imagePath, maxEdge })
  },
  pose: {
    read: (imagePath) => ipcRenderer.invoke('pose:read', imagePath),
    save: (imagePath, entry) => ipcRenderer.invoke('pose:save', { imagePath, entry }),
    listForDate: (date) => ipcRenderer.invoke('pose:list-for-date', date)
  },
  // 订阅主进程"右键复制成功"事件，返回 unsubscribe 函数供 onBeforeUnmount 解绑
  onContextCopy: (callback) => {
    const handler = (_e, data) => callback(data);
    ipcRenderer.on('context-menu:copied', handler);
    return () => ipcRenderer.removeListener('context-menu:copied', handler);
  },
  // 剪贴板读写：navigator.clipboard 的主进程 fallback
  clipboard: {
    readText: () => ipcRenderer.invoke('clipboard:read-text'),
    writeText: (text) => ipcRenderer.invoke('clipboard:write-text', text)
  }
});
