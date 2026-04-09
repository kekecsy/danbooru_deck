const state = {sourceFile:null,image:null,imageUrl:null,zoom:1,layers:[],selectedId:null,nextId:1,mode:null,handle:null,start:null,draft:null,fillMode:"mosaic",opacity:1,stripeText:"该信息已被管理员撤回",stripeFontFamily:"Times New Roman",stripeFontSize:25,stripeOrientation:"horizontal",imageDataUrl:null,imageName:"",zipFile:null,logUnread:0,logOpen:false,revealColor:"#000000",revealOpacity:0.8,revealRadius:50};
const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");
const workspace = document.getElementById("workspace");
const wrap = document.getElementById("canvas-wrap");
const empty = document.getElementById("empty-state");
const logBox = document.getElementById("log-box");
const logPopover = document.getElementById("log-popover");
const logBadge = document.getElementById("log-badge");
const logEmpty = document.getElementById("log-empty");
const contextMenu = document.getElementById("context-menu");
const imageInput = document.getElementById("image-input");
const overlayInput = document.getElementById("overlay-input");
const zipInput = document.getElementById("zip-input");
const $ = id => document.getElementById(id);
const APP_BASE = window.location.pathname === "/" ? "" : window.location.pathname.replace(/\/$/, "");
const stamp = () => new Date().toLocaleTimeString("zh-CN", {hour12:false});
const status = msg => $("status-text").textContent = msg;

state.stripeText = "该信息已被管理员撤回";
state.sourceMeta = {artist:"",characters:"",postUrl:"",imageName:""};

function syncOpacityUIFromState() {
    const v = String(Math.round(state.opacity * 100));
    const ids = ["opacity-range", "opacity-range-stripe", "opacity-range-image"];
    ids.forEach(id => { const el = $(id); if (el) el.value = v; });
    ["opacity-text", "opacity-text-stripe", "opacity-text-image"].forEach(id => {
        const el = $(id);
        if (el) el.textContent = `${v}%`;
    });
}

function syncRevealUIFromState() {
    const pct = Math.round(state.revealOpacity * 100);
    const ro = $("reveal-opacity");
    if (ro) ro.value = String(pct);
    const rot = $("reveal-opacity-text");
    if (rot) rot.textContent = `${pct}%`;
    const rc = $("reveal-color");
    if (rc) rc.value = state.revealColor;
    const rr = $("reveal-radius");
    if (rr) rr.value = String(state.revealRadius);
}

function updateModePanels() {
    const m = state.fillMode;
    ["mosaic", "stripe", "image", "reveal"].forEach(mode => {
        const el = $(`panel-mode-${mode}`);
        if (el) el.classList.toggle("hidden", mode !== m);
    });
}

function syncLogUi() {
    logPopover.classList.toggle("hidden", !state.logOpen);
    const hasUnread = state.logUnread > 0;
    logBadge.classList.toggle("hidden", !hasUnread);
    logBadge.textContent = state.logUnread > 99 ? "99+" : String(state.logUnread);
    logEmpty.classList.toggle("hidden", logBox.textContent.trim().length > 0);
}

function setLogOpen(isOpen) {
    state.logOpen = isOpen;
    if (isOpen) state.logUnread = 0;
    syncLogUi();
}

function setContextMenuOpen(isOpen, left, top) {
    contextMenu.classList.toggle("hidden", !isOpen);
    if (isOpen) {
        if (typeof left === "number") contextMenu.style.left = `${left}px`;
        if (typeof top === "number") contextMenu.style.top = `${top}px`;
    }
}

const log = msg => {
    const current = logBox.textContent.trim();
    if (!current || current === "等待加载...") {
        logBox.textContent = "";
    }
    logBox.textContent += `[${stamp()}] ${msg}\n`;
    logBox.scrollTop = logBox.scrollHeight;
    if (!state.logOpen) state.logUnread += 1;
    syncLogUi();
};

function setWorkspaceEmptyState(isEmpty) {
    empty.classList.toggle("hidden", !isEmpty);
    wrap.classList.toggle("hidden", isEmpty);
    empty.style.display = isEmpty ? "grid" : "none";
    wrap.style.display = isEmpty ? "none" : "inline-block";
    empty.setAttribute("aria-hidden", String(!isEmpty));
    wrap.setAttribute("aria-hidden", String(isEmpty));
}

function syncStats() {
    $("size-text").textContent = state.image ? `${state.image.width} x ${state.image.height}` : "无图片";
    $("zoom-text").textContent = `${Math.round(state.zoom * 100)}%`;
    $("layers-text").textContent = String(state.layers.length);
    $("selection-text").textContent = state.selectedId ? `已选中 #${state.selectedId}` : "未选中";
    const op = Math.round(state.opacity * 100);
    if ($("opacity-text")) $("opacity-text").textContent = `${op}%`;
    if ($("opacity-text-stripe")) $("opacity-text-stripe").textContent = `${op}%`;
    if ($("opacity-text-image")) $("opacity-text-image").textContent = `${op}%`;
}

function normalizeCharactersText(value) {
    return String(value || "").split(" ").filter(Boolean).join(", ");
}

function syncSourceMetaUi() {
    const artist = state.sourceMeta.artist || "未提供";
    const characters = normalizeCharactersText(state.sourceMeta.characters) || "未提供";
    const postUrl = state.sourceMeta.postUrl || "";
    const artistNode = $("source-artist");
    const charactersNode = $("source-characters");
    const postUrlNode = $("source-post-url");
    if (artistNode) artistNode.textContent = artist;
    if (charactersNode) charactersNode.textContent = characters;
    if (postUrlNode) {
        postUrlNode.textContent = postUrl || "未提供";
        postUrlNode.href = postUrl || "#";
        postUrlNode.classList.toggle("disabled", !postUrl);
        postUrlNode.tabIndex = postUrl ? 0 : -1;
        postUrlNode.setAttribute("aria-disabled", String(!postUrl));
    }
    const useArtistBtn = $("use-artist-text-btn");
    const useCharactersBtn = $("use-characters-text-btn");
    if (useArtistBtn) useArtistBtn.disabled = !state.sourceMeta.artist;
    if (useCharactersBtn) useCharactersBtn.disabled = !state.sourceMeta.characters;
}

function applyStripeTextPreset(value) {
    const text = String(value || "").trim();
    if (!text) return;
    state.stripeText = text;
    $("stripe-text").value = text;
    const layer = selectedLayer();
    if (layer) {
        applyControlsToLayer(layer);
        render();
    }
}

function selectedLayer() { return state.layers.find(x => x.id === state.selectedId) || null; }
function fileToDataUrl(file) { return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(file); }); }
function normalizeLayer(layer) {
    if (layer.width < 0) { layer.x += layer.width; layer.width = Math.abs(layer.width); }
    if (layer.height < 0) { layer.y += layer.height; layer.height = Math.abs(layer.height); }
    layer.width = Math.max(1, layer.width);
    layer.height = Math.max(1, layer.height);
    if (!state.image) return;
    layer.x = Math.max(0, Math.min(layer.x, state.image.width - 1));
    layer.y = Math.max(0, Math.min(layer.y, state.image.height - 1));
    layer.width = Math.min(layer.width, state.image.width - layer.x);
    layer.height = Math.min(layer.height, state.image.height - layer.y);
}

function applyControlsToLayer(layer) {
    layer.fillMode = state.fillMode;
    layer.opacity = state.opacity;
    layer.stripeText = state.stripeText;
    layer.stripeFontFamily = state.stripeFontFamily;
    layer.stripeFontSize = Number(state.stripeFontSize);
    layer.stripeOrientation = state.stripeOrientation;
    layer.imageDataUrl = state.imageDataUrl;
    layer.imageName = state.imageName;

    if (layer.fillMode === "reveal") {
        layer.revealColor = state.revealColor;
        layer.revealOpacity = state.revealOpacity;
    }

    normalizeLayer(layer);
}

function syncControlsFromSelected() {
    const layer = selectedLayer();
    if (layer) {
        state.fillMode = layer.fillMode;
        state.opacity = layer.opacity;
        state.stripeText = layer.stripeText;
        state.stripeFontFamily = layer.stripeFontFamily;
        state.stripeFontSize = layer.stripeFontSize;
        state.stripeOrientation = layer.stripeOrientation;
        state.imageDataUrl = layer.imageDataUrl || null;
        state.imageName = layer.imageName || "";
        if (layer.revealColor) state.revealColor = layer.revealColor;
        if (layer.fillMode === "reveal" && layer.revealOpacity != null) {
            state.revealOpacity = layer.revealOpacity;
        }
    }
    $("fill-mode").value = state.fillMode;
    syncOpacityUIFromState();
    $("stripe-text").value = state.stripeText;
    $("stripe-font-family").value = state.stripeFontFamily;
    $("stripe-font-size").value = String(state.stripeFontSize);
    $("stripe-orientation").value = state.stripeOrientation;
    $("overlay-name").textContent = state.imageName || "未选择贴图";
    if ($("reveal-color")) $("reveal-color").value = state.revealColor;
    if ($("reveal-radius")) $("reveal-radius").value = String(state.revealRadius ?? 50);
    syncRevealUIFromState();
    syncSourceMetaUi();
    updateModePanels();
    syncStats();
}

function setFillMode(mode) {
    state.fillMode = mode;
    $("fill-mode").value = mode;
    updateModePanels();
    const layer = selectedLayer();
    if (layer) applyControlsToLayer(layer);
    syncControlsFromSelected();
}

function setCanvasSize() {
    if (!state.image) return;
    canvas.width = Math.max(1, Math.round(state.image.width * state.zoom));
    canvas.height = Math.max(1, Math.round(state.image.height * state.zoom));
}

function fitToWindow() {
    if (!state.image) return;
    const width = Math.max(120, workspace.clientWidth - 40);
    const height = Math.max(120, workspace.clientHeight - 40);
    state.zoom = Math.min(width / state.image.width, height / state.image.height, 1);
    setCanvasSize();
}

async function loadImageFile(file) {
    if (!file) return;
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
        if (state.imageUrl) URL.revokeObjectURL(state.imageUrl);
        state.sourceFile = file;
        state.image = img;
        state.imageUrl = url;
        state.sourceMeta.imageName = file.name;
        state.layers = [];
        state.selectedId = null;
        state.nextId = 1;
        setWorkspaceEmptyState(false);
        fitToWindow();
        render();
        log(`已导入图片: ${file.name}`);
        status("拖拽创建打码区域，点击已有区域可编辑。");
    };
    img.onerror = () => { URL.revokeObjectURL(url); log("图片加载失败"); };
    img.src = url;
}

async function loadImageFromUrl(url, fileName) {
    if (!url) return;
    try {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`加载失败: ${response.status}`);
        const blob = await response.blob();
        const ext = (blob.type && blob.type.split("/")[1]) || "png";
        const safeName = fileName || `mosaic-source.${ext}`;
        await loadImageFile(new File([blob], safeName, {type: blob.type || "image/png"}));
        log(`已从链接载入图片: ${safeName}`);
    } catch (error) {
        log(`链接图片加载失败: ${error.message}`);
    }
}

function handles(layer) {
    const x = layer.x * state.zoom, y = layer.y * state.zoom, w = layer.width * state.zoom, h = layer.height * state.zoom, s = 10;
    return {nw:{x:x-s/2,y:y-s/2,s},ne:{x:x+w-s/2,y:y-s/2,s},sw:{x:x-s/2,y:y+h-s/2,s},se:{x:x+w-s/2,y:y+h-s/2,s}};
}

function pointInRect(px, py, rect) { return px >= rect.x && px <= rect.x + rect.width && py >= rect.y && py <= rect.y + rect.height; }
function canvasPoint(event) { const rect = canvas.getBoundingClientRect(); return {x:event.clientX - rect.left, y:event.clientY - rect.top}; }
function imagePoint(point) { return {x: point.x / state.zoom, y: point.y / state.zoom}; }
function findHandle(point, layer) {
    const map = handles(layer);
    for (const [name, h] of Object.entries(map)) if (point.x >= h.x && point.x <= h.x + h.s && point.y >= h.y && point.y <= h.y + h.s) return name;
    return null;
}
function topLayerAt(point) {
    for (let i = state.layers.length - 1; i >= 0; i -= 1) {
        const layer = state.layers[i];
        const rect = {x:layer.x * state.zoom, y:layer.y * state.zoom, width:layer.width * state.zoom, height:layer.height * state.zoom};
        if (pointInRect(point.x, point.y, rect)) return layer;
    }
    return null;
}
function selectLayer(id) { state.selectedId = id; syncControlsFromSelected(); render(); }
function addLayer(rect) {
    const layer = {
        id: state.nextId++,
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
        fillMode: state.fillMode,
        opacity: state.opacity,
        stripeText: state.stripeText,
        stripeFontFamily: state.stripeFontFamily,
        stripeFontSize: Number(state.stripeFontSize),
        stripeOrientation: state.stripeOrientation,
        imageDataUrl: state.imageDataUrl,
        imageName: state.imageName,
        revealColor: state.revealColor,
        revealOpacity: state.revealOpacity
    };
    applyControlsToLayer(layer);

    state.layers.push(layer);
    selectLayer(layer.id);
    log(`已新增${layer.fillMode === "reveal" ? "挖孔" : "打码"}层 #${layer.id}`);
}

function drawMosaic(layer) {
    const block = 15 * state.zoom;
    ctx.save();
    ctx.globalAlpha = layer.opacity;
    for (let y = 0; y < layer.height * state.zoom; y += block) {
        for (let x = 0; x < layer.width * state.zoom; x += block) {
            ctx.fillStyle = (((Math.floor(x / block) + Math.floor(y / block)) % 2) === 0) ? "rgb(180,180,180)" : "rgb(120,120,120)";
            ctx.fillRect(layer.x * state.zoom + x, layer.y * state.zoom + y, block, block);
        }
    }
    ctx.restore();
}

function drawStripe(layer) {
    const x = layer.x * state.zoom, y = layer.y * state.zoom, w = layer.width * state.zoom, h = layer.height * state.zoom;
    ctx.save();
    ctx.globalAlpha = layer.opacity;
    ctx.fillStyle = "#000";
    ctx.font = `700 ${Math.max(10, layer.stripeFontSize * state.zoom)}px "${layer.stripeFontFamily}"`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    if (layer.stripeOrientation === "vertical") {
        const chars = Array.from(layer.stripeText || "");
        const line = layer.stripeFontSize * state.zoom * 1.1;
        let start = y + h / 2 - (chars.length * line) / 2 + line / 2;
        chars.forEach((char, index) => ctx.fillText(char, x + w / 2, start + index * line));
    } else {
        ctx.fillText(layer.stripeText || "", x + w / 2, y + h / 2);
    }
    ctx.restore();
}

function drawImageFill(layer) {
    if (!layer.imageDataUrl) { drawMosaic(layer); return; }
    const img = new Image();
    img.src = layer.imageDataUrl;
    const paint = () => {
        const x = layer.x * state.zoom, y = layer.y * state.zoom, w = layer.width * state.zoom, h = layer.height * state.zoom;
        const r = Math.min(w / img.width, h / img.height);
        const dw = img.width * r, dh = img.height * r;
        const left = x + (w - dw) / 2, top = y + (h - dh) / 2;
        ctx.save();
        ctx.globalAlpha = layer.opacity;
        ctx.drawImage(img, left, top, dw, dh);
        ctx.restore();
    };
    if (img.complete) paint();
    else img.onload = () => render();
}

function drawSelection(layer) {
    const x = layer.x * state.zoom, y = layer.y * state.zoom, w = layer.width * state.zoom, h = layer.height * state.zoom;
    ctx.save();
    ctx.strokeStyle = "#1d7ef3";
    ctx.lineWidth = 2;
    ctx.strokeRect(x, y, w, h);
    ctx.fillStyle = "#1d7ef3";
    Object.values(handles(layer)).forEach(h => ctx.fillRect(h.x, h.y, h.s, h.s));
    ctx.restore();
}

function applyRevealMask(ctx, canvasWidth, canvasHeight, layers, scale, globalColor, globalOpacity) {
    if (!layers.some(l => l.fillMode === "reveal")) return;
    
    const maskCanvas = document.createElement("canvas");
    maskCanvas.width = canvasWidth;
    maskCanvas.height = canvasHeight;
    const mCtx = maskCanvas.getContext("2d");
    
    mCtx.fillStyle = globalColor;
    mCtx.globalAlpha = globalOpacity;
    mCtx.fillRect(0, 0, canvasWidth, canvasHeight);
    
    mCtx.globalCompositeOperation = "destination-out";
    mCtx.globalAlpha = 1;
    
    layers.forEach(layer => {
        if (layer.fillMode === "reveal") {
            mCtx.beginPath();
            const cx = (layer.x + layer.width / 2) * scale;
            const cy = (layer.y + layer.height / 2) * scale;
            const rx = (layer.width / 2) * scale;
            const ry = (layer.height / 2) * scale;
            mCtx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
            mCtx.fill();
        }
    });
    
    ctx.drawImage(maskCanvas, 0, 0);
}

function render() {
    syncStats();
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!state.image) return;
    ctx.drawImage(state.image, 0, 0, state.image.width * state.zoom, state.image.height * state.zoom);
    state.layers.forEach(layer => {
        if (layer.fillMode === "stripe") drawStripe(layer);
        else if (layer.fillMode === "image") drawImageFill(layer);
        else if (layer.fillMode === "mosaic") drawMosaic(layer);
    });

    const hasRevealLayers = state.layers.some(l => l.fillMode === "reveal");
    if (hasRevealLayers) {
        applyRevealMask(ctx, canvas.width, canvas.height, state.layers, state.zoom, state.revealColor, state.revealOpacity);
    }

    if (state.fillMode === "reveal" && state.mode === "draw" && state.start && state.current) {
        const dx = state.current.x - state.start.x;
        const dy = state.current.y - state.start.y;
        const x = dx >= 0 ? state.start.x : state.current.x;
        const y = dy >= 0 ? state.start.y : state.current.y;
        const w = Math.abs(dx);
        const h = Math.abs(dy);
        ctx.save();
        ctx.setLineDash([8, 6]);
        ctx.strokeStyle = "#1d7ef3";
        ctx.lineWidth = 2;
        ctx.strokeRect(x * state.zoom, y * state.zoom, w * state.zoom, h * state.zoom);
        ctx.restore();
    }

    if (state.draft) {
        ctx.save();
        ctx.setLineDash([8, 6]);
        ctx.strokeStyle = "#1d7ef3";
        ctx.lineWidth = 2;
        ctx.strokeRect(state.draft.x * state.zoom, state.draft.y * state.zoom, state.draft.width * state.zoom, state.draft.height * state.zoom);
        ctx.restore();
    }
    const layer = selectedLayer();
    if (layer) drawSelection(layer);
}

function drawMosaicOnContext(targetCtx, layer, scale) {
    const block = Math.max(3, 15 * scale);
    targetCtx.save();
    targetCtx.globalAlpha = layer.opacity;
    for (let y = 0; y < layer.height * scale; y += block) {
        for (let x = 0; x < layer.width * scale; x += block) {
            targetCtx.fillStyle = (((Math.floor(x / block) + Math.floor(y / block)) % 2) === 0) ? "rgb(180,180,180)" : "rgb(120,120,120)";
            targetCtx.fillRect(layer.x * scale + x, layer.y * scale + y, block, block);
        }
    }
    targetCtx.restore();
}

function drawStripeOnContext(targetCtx, layer, scale) {
    const x = layer.x * scale, y = layer.y * scale, w = layer.width * scale, h = layer.height * scale;
    targetCtx.save();
    targetCtx.globalAlpha = layer.opacity;
    targetCtx.fillStyle = "#000";
    targetCtx.font = `700 ${Math.max(10, layer.stripeFontSize * scale)}px "${layer.stripeFontFamily}"`;
    targetCtx.textAlign = "center";
    targetCtx.textBaseline = "middle";
    if (layer.stripeOrientation === "vertical") {
        const chars = Array.from(layer.stripeText || "");
        const line = layer.stripeFontSize * scale * 1.1;
        let start = y + h / 2 - (chars.length * line) / 2 + line / 2;
        chars.forEach((char, index) => targetCtx.fillText(char, x + w / 2, start + index * line));
    } else {
        targetCtx.fillText(layer.stripeText || "", x + w / 2, y + h / 2);
    }
    targetCtx.restore();
}

function drawImageFillOnContext(targetCtx, layer, scale, imageCache) {
    if (!layer.imageDataUrl) {
        drawMosaicOnContext(targetCtx, layer, scale);
        return;
    }
    const img = imageCache.get(layer.imageDataUrl);
    if (!img) {
        drawMosaicOnContext(targetCtx, layer, scale);
        return;
    }
    const x = layer.x * scale, y = layer.y * scale, w = layer.width * scale, h = layer.height * scale;
    const r = Math.min(w / img.width, h / img.height);
    const dw = img.width * r, dh = img.height * r;
    const left = x + (w - dw) / 2, top = y + (h - dh) / 2;
    targetCtx.save();
    targetCtx.globalAlpha = layer.opacity;
    targetCtx.drawImage(img, left, top, dw, dh);
    targetCtx.restore();
}

function loadImageElement(src) {
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error("贴图加载失败"));
        img.src = src;
    });
}

async function renderOnClient(maxEdgeOverride = null) {
    if (!state.image) throw new Error("请先导入图片");
    const maxEdge = maxEdgeOverride === null ? Number($("max-edge").value || 0) : Number(maxEdgeOverride || 0);
    const scale = maxEdge > 0 ? Math.min(1, maxEdge / Math.max(state.image.width, state.image.height)) : 1;
    const outWidth = Math.max(1, Math.round(state.image.width * scale));
    const outHeight = Math.max(1, Math.round(state.image.height * scale));
    const renderCanvas = document.createElement("canvas");
    renderCanvas.width = outWidth;
    renderCanvas.height = outHeight;
    const renderCtx = renderCanvas.getContext("2d");
    renderCtx.drawImage(state.image, 0, 0, outWidth, outHeight);

    const uniqueImageUrls = [...new Set(state.layers.filter(layer => layer.fillMode === "image" && layer.imageDataUrl).map(layer => layer.imageDataUrl))];
    const loadedImages = await Promise.all(uniqueImageUrls.map(async url => [url, await loadImageElement(url)]));
    const imageCache = new Map(loadedImages);

    state.layers.forEach(layer => {
        if (layer.fillMode === "stripe") drawStripeOnContext(renderCtx, layer, scale);
        else if (layer.fillMode === "image") drawImageFillOnContext(renderCtx, layer, scale, imageCache);
        else if (layer.fillMode === "mosaic") drawMosaicOnContext(renderCtx, layer, scale);
    });

    const hasRevealLayers = state.layers.some(l => l.fillMode === "reveal");
    if (hasRevealLayers) {
        applyRevealMask(renderCtx, outWidth, outHeight, state.layers, scale, state.revealColor, state.revealOpacity);
    }

    return new Promise((resolve, reject) => {
        renderCanvas.toBlob(blob => {
            if (!blob) {
                reject(new Error("本地渲染失败"));
                return;
            }
            resolve(blob);
        }, "image/png");
    });
}

canvas.addEventListener("mousedown", event => {
    if (!state.image) return;

    const point = canvasPoint(event);
    const hitLayer = topLayerAt(point);
    if (hitLayer) {
        if (state.selectedId !== hitLayer.id) selectLayer(hitLayer.id);
        const active = selectedLayer();
        const handle = findHandle(point, active);
        if (handle) {
            state.mode = "resize";
            state.handle = handle;
            state.start = imagePoint(point);
        } else {
            state.mode = "move";
            state.start = imagePoint(point);
        }
        return;
    }
    if (state.fillMode === "reveal") {
        state.selectedId = null;
        state.mode = "draw";
        state.start = imagePoint(point);
        state.current = { ...state.start };
        state.draft = null;
        render();
    } else {
        state.selectedId = null;
        state.mode = "draw";
        state.start = imagePoint(point);
        state.draft = { x: state.start.x, y: state.start.y, width: 0, height: 0 };
        render();
    }
});

canvas.addEventListener("contextmenu", event => {
    event.preventDefault();
    if (!state.image) return;
    const menuWidth = 206;
    const menuHeight = 144;
    const rect = workspace.getBoundingClientRect();
    const left = Math.min(event.clientX - rect.left, workspace.clientWidth - menuWidth);
    const top = Math.min(event.clientY - rect.top, workspace.clientHeight - menuHeight);
    setContextMenuOpen(true, Math.max(8, left), Math.max(8, top));
});

window.addEventListener("mousemove", event => {
    if (!state.mode || !state.image) return;
    const point = imagePoint(canvasPoint(event));

    if (state.mode === "draw" && state.fillMode === "reveal") {
        state.current = point;
        render();
        return;
    }

    if (state.mode === "draw" && state.draft) {
        state.draft.width = point.x - state.start.x;
        state.draft.height = point.y - state.start.y;
        const draft = { ...state.draft };
        normalizeLayer(draft);
        state.draft = draft;
        render();
        return;
    }

    const layer = selectedLayer();
    if (!layer) return;

    if (state.mode === "move") {
        layer.x += point.x - state.start.x;
        layer.y += point.y - state.start.y;
        state.start = point;
        normalizeLayer(layer);
        render();
        return;
    }

    if (state.mode === "resize") {
        const dx = point.x - state.start.x, dy = point.y - state.start.y;
        if (state.handle.includes("n")) { layer.y += dy; layer.height -= dy; }
        if (state.handle.includes("s")) layer.height += dy;
        if (state.handle.includes("w")) { layer.x += dx; layer.width -= dx; }
        if (state.handle.includes("e")) layer.width += dx;
        state.start = point;
        normalizeLayer(layer);
        render();
    }
});

window.addEventListener("mouseup", () => {
    if (state.mode === "draw") {
        if (state.fillMode === "reveal" && state.start && state.current) {
            const dx = state.current.x - state.start.x;
            const dy = state.current.y - state.start.y;
            const width = Math.abs(dx);
            const height = Math.abs(dy);
            if (width > 10 && height > 10) {
                const x = dx >= 0 ? state.start.x : state.current.x;
                const y = dy >= 0 ? state.start.y : state.current.y;
                const rect = { x, y, width, height };
                normalizeLayer(rect);
                addLayer(rect);
            }
        } else if (state.fillMode !== "reveal" && state.draft && Math.abs(state.draft.width) > 10 && Math.abs(state.draft.height) > 10) {
            const rect = { ...state.draft };
            normalizeLayer(rect);
            addLayer(rect);
        }
    }

    state.mode = null;
    state.handle = null;
    state.start = null;
    state.draft = null;
    render();
});

workspace.addEventListener("dragover", event => event.preventDefault());
workspace.addEventListener("drop", event => {
    event.preventDefault();
    const [file] = Array.from(event.dataTransfer.files || []);
    if (!file) return;
    if (file.type.startsWith("image/")) loadImageFile(file);
    else if (file.name.toLowerCase().endsWith(".zip")) {
        state.zipFile = file;
        $("zip-name").textContent = file.name;
        log(`已选择 ZIP: ${file.name}`);
    }
});

window.addEventListener("paste", async event => {
    const items = Array.from(event.clipboardData?.items || []);
    const imageItem = items.find(item => item.type.startsWith("image/"));
    if (!imageItem) return;
    const blob = imageItem.getAsFile();
    const ext = blob.type.split("/")[1] || "png";
    const file = new File([blob], `clipboard-image.${ext}`, {type: blob.type});
    await loadImageFile(file);
    log("已从剪贴板粘贴图片");
});

$("upload-btn").addEventListener("click", () => imageInput.click());
$("paste-btn").addEventListener("click", async () => {
    if (!navigator.clipboard?.read) { log("当前浏览器不支持主动读取剪贴板，请直接 Ctrl+V"); return; }
    try {
        const items = await navigator.clipboard.read();
        for (const item of items) {
            const type = item.types.find(x => x.startsWith("image/"));
            if (!type) continue;
            const blob = await item.getType(type);
            const ext = type.split("/")[1] || "png";
            await loadImageFile(new File([blob], `clipboard-image.${ext}`, {type}));
            log("已主动读取剪贴板图片");
            return;
        }
        log("剪贴板里没有图片");
    } catch (error) {
        log(`读取剪贴板失败: ${error.message}`);
    }
});
imageInput.addEventListener("change", async event => {
    const [file] = Array.from(event.target.files || []);
    await loadImageFile(file);
    event.target.value = "";
});

$("overlay-btn").addEventListener("click", () => overlayInput.click());
overlayInput.addEventListener("change", async event => {
    const [file] = Array.from(event.target.files || []);
    if (!file) return;
    clearPresetActive();
    state.imageDataUrl = await fileToDataUrl(file);
    state.imageName = file.name;
    state.fillMode = "image";
    syncControlsFromSelected();
    const layer = selectedLayer();
    if (layer) { applyControlsToLayer(layer); render(); }
    log(`已载入贴图: ${file.name}`);
    event.target.value = "";
});
$("overlay-clear-btn").addEventListener("click", () => {
    clearPresetActive();
    state.imageDataUrl = null;
    state.imageName = "";
    syncControlsFromSelected();
    const layer = selectedLayer();
    if (layer) { applyControlsToLayer(layer); render(); }
});

$("zip-btn").addEventListener("click", () => zipInput.click());
zipInput.addEventListener("change", event => {
    const [file] = Array.from(event.target.files || []);
    state.zipFile = file || null;
    $("zip-name").textContent = file ? file.name : "未选择 ZIP 文件";
    event.target.value = "";
});
$("zip-run-btn").addEventListener("click", async () => {
    if (!state.zipFile) { log("请先选择 ZIP 文件"); return; }
    const form = new FormData();
    form.append("zip_file", state.zipFile);
    log(`开始转换 ZIP: ${state.zipFile.name}`);
    try {
        const response = await fetch(`${APP_BASE}/api/zip-to-gif`, {method:"POST", body:form});
        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            throw new Error(payload.detail || "转换失败");
        }
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = state.zipFile.name.replace(/\.zip$/i, "") + ".gif";
        link.click();
        URL.revokeObjectURL(url);
        log("ZIP 转 GIF 完成");
    } catch (error) {
        log(`ZIP 转 GIF 失败: ${error.message}`);
    }
});

function onOpacityInput(event) {
    state.opacity = Number(event.target.value) / 100;
    syncOpacityUIFromState();
    const layer = selectedLayer();
    if (layer) { applyControlsToLayer(layer); render(); }
    syncStats();
}

["opacity-range", "opacity-range-stripe", "opacity-range-image"].forEach(id => {
    const el = $(id);
    if (el) el.addEventListener("input", onOpacityInput);
});

$("fill-mode").addEventListener("change", event => {
    setFillMode(event.target.value);
    render();
});
$("stripe-text").addEventListener("input", event => {
    state.stripeText = event.target.value;
    const layer = selectedLayer();
    if (layer) { applyControlsToLayer(layer); render(); }
});
$("stripe-font-family").addEventListener("change", event => {
    state.stripeFontFamily = event.target.value;
    const layer = selectedLayer();
    if (layer) { applyControlsToLayer(layer); render(); }
});
$("stripe-font-size").addEventListener("input", event => {
    state.stripeFontSize = Number(event.target.value || 25);
    const layer = selectedLayer();
    if (layer) { applyControlsToLayer(layer); render(); }
});
$("stripe-orientation").addEventListener("change", event => {
    state.stripeOrientation = event.target.value;
    const layer = selectedLayer();
    if (layer) { applyControlsToLayer(layer); render(); }
});
$("use-artist-text-btn").addEventListener("click", () => {
    applyStripeTextPreset(state.sourceMeta.artist);
    log("已将作者填入白条文字");
});
$("use-characters-text-btn").addEventListener("click", () => {
    applyStripeTextPreset(normalizeCharactersText(state.sourceMeta.characters));
    log("已将角色填入白条文字");
});

$("reveal-opacity").addEventListener("input", event => {
    state.revealOpacity = Number(event.target.value) / 100;
    syncRevealUIFromState();
    state.layers.forEach(layer => {
        if (layer.fillMode === "reveal") {
            layer.revealOpacity = state.revealOpacity;
        }
    });
    render();
});

$("reveal-color").addEventListener("input", event => {
    state.revealColor = event.target.value;
    state.layers.forEach(layer => {
        if (layer.fillMode === "reveal") {
            layer.revealColor = state.revealColor;
        }
    });
    render();
});

$("reveal-radius").addEventListener("input", event => {
    state.revealRadius = Number(event.target.value || 50);
});

$("undo-btn").addEventListener("click", () => {
    const layer = state.layers.pop();
    if (layer) {
        if (layer.id === state.selectedId) state.selectedId = null;
        log(`已撤销打码层 #${layer.id}`);
        render();
    }
});
$("clear-btn").addEventListener("click", () => {
    if (!state.layers.length) return;
    state.layers = [];
    state.selectedId = null;
    log("已清空全部打码层");
    render();
});
$("delete-btn").addEventListener("click", () => {
    if (!state.selectedId) return;
    state.layers = state.layers.filter(layer => layer.id !== state.selectedId);
    log(`已删除打码层 #${state.selectedId}`);
    state.selectedId = null;
    render();
});

$("fit-btn").addEventListener("click", () => { fitToWindow(); render(); });
$("actual-btn").addEventListener("click", () => {
    if (!state.image) return;
    state.zoom = 1;
    setCanvasSize();
    render();
});
$("zoom-btn").addEventListener("click", () => {
    if (!state.image) return;
    state.zoom = Math.min(5, state.zoom * 1.2);
    setCanvasSize();
    render();
});
workspace.addEventListener("wheel", event => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    if (!state.image) return;
    state.zoom = Math.min(5, Math.max(.2, state.zoom * (event.deltaY < 0 ? 1.1 : .9)));
    setCanvasSize();
    render();
}, {passive:false});
window.addEventListener("resize", () => {
    if (!state.image) return;
    setCanvasSize();
    render();
});
$("log-toggle-btn").addEventListener("click", event => {
    event.stopPropagation();
    setLogOpen(!state.logOpen);
});
$("log-close-btn").addEventListener("click", () => setLogOpen(false));
$("log-clear-btn").addEventListener("click", () => {
    logBox.textContent = "";
    syncLogUi();
});
document.addEventListener("click", event => {
    if (!state.logOpen) return;
    if (logPopover.contains(event.target) || $("log-toggle-btn").contains(event.target)) return;
    setLogOpen(false);
});
document.addEventListener("click", event => {
    if (contextMenu.classList.contains("hidden")) return;
    if (contextMenu.contains(event.target)) return;
    setContextMenuOpen(false);
});

function exportOps() {
    return state.layers.map(layer => ({
        x: layer.x,
        y: layer.y,
        width: layer.width,
        height: layer.height,
        fillMode: layer.fillMode,
        opacity: layer.opacity,
        stripeText: layer.stripeText,
        stripeFontFamily: layer.stripeFontFamily,
        stripeFontSize: layer.stripeFontSize,
        stripeOrientation: layer.stripeOrientation,
        imageDataUrl: layer.imageDataUrl || null
    }));
}

async function renderOnServer(maxEdgeOverride = null) {
    try {
        return await renderOnClient(maxEdgeOverride);
    } catch (clientError) {
        log(`本地渲染失败，回退服务端: ${clientError.message}`);
        if (!state.sourceFile) throw new Error("请先导入图片");
        const form = new FormData();
        form.append("source_image", state.sourceFile);
        form.append("operations_json", JSON.stringify(exportOps()));
        const maxEdge = maxEdgeOverride === null ? Number($("max-edge").value || 0) : Number(maxEdgeOverride || 0);
        form.append("max_edge", String(maxEdge));
        const response = await fetch(`${APP_BASE}/api/render`, {method:"POST", body:form});
        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            throw new Error(payload.detail || "导出失败");
        }
        return response.blob();
    }
}

async function copyRenderedBlob(blob, successMessage) {
    if (!navigator.clipboard || !window.ClipboardItem) {
        log("当前浏览器不支持复制图片");
        return;
    }
    if (!document.hasFocus()) {
        window.focus();
    }
    if (!document.hasFocus()) {
        throw new Error("窗口未聚焦，请先点击页面后再复制");
    }
    await navigator.clipboard.write([new ClipboardItem({[blob.type]: blob})]);
    log(successMessage);
}

$("export-btn").addEventListener("click", async () => {
    try {
        const blob = await renderOnServer();
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        const base = (state.sourceFile?.name || "mosaic").replace(/\.[^.]+$/, "");
        link.href = url;
        link.download = `${base}_mosaic.png`;
        link.click();
        URL.revokeObjectURL(url);
        log("已导出 PNG");
    } catch (error) {
        log(`导出失败: ${error.message}`);
    }
});

$("copy-btn").addEventListener("click", async () => {
    try {
        const blob = await renderOnServer();
        await copyRenderedBlob(blob, "已复制导出图片到剪贴板");
    } catch (error) {
        log(`复制失败: ${error.message}`);
    }
});
$("context-copy-btn").addEventListener("mousedown", event => {
    event.preventDefault();
});
$("context-copy-btn").addEventListener("click", async () => {
    try {
        const blob = await renderOnServer(0);
        await copyRenderedBlob(blob, "已复制原尺寸编辑图到剪贴板");
        setContextMenuOpen(false);
    } catch (error) {
        log(`右键复制失败: ${error.message}`);
    }
});
$("context-export-btn").addEventListener("click", async () => {
    try {
        const blob = await renderOnServer(0);
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        const base = (state.sourceFile?.name || "mosaic").replace(/\.[^.]+$/, "");
        link.href = url;
        link.download = `${base}_mosaic_full.png`;
        link.click();
        URL.revokeObjectURL(url);
        log("已导出原尺寸编辑图");
        setContextMenuOpen(false);
    } catch (error) {
        log(`右键导出失败: ${error.message}`);
    }
});
$("context-close-btn").addEventListener("click", () => setContextMenuOpen(false));

function clearPresetActive() { document.querySelectorAll(".preset").forEach(node => node.classList.remove("active")); }
async function loadPresetDataUrl(url) {
    const response = await fetch(url);
    const blob = await response.blob();
    return fileToDataUrl(new File([blob], "preset.png", {type: blob.type}));
}

async function loadPresets() {
    const grid = $("preset-grid");
    grid.innerHTML = "";
    try {
        const response = await fetch(`${APP_BASE}/api/presets`);
        const presets = await response.json();
        if (!presets.length) {
            grid.innerHTML = '<div class="hint">当前没有 preset 目录，后续把素材放到 `pic_web/present` 或 `mosaic_qt/present` 即可。</div>';
            return;
        }
        presets.forEach(preset => {
            const node = document.createElement("button");
            node.type = "button";
            node.className = "preset";
            node.innerHTML = `<img src="${preset.url}" alt="${preset.name}"><span>${preset.name}</span>`;
            node.addEventListener("click", async () => {
                clearPresetActive();
                node.classList.add("active");
                state.imageDataUrl = await loadPresetDataUrl(preset.url);
                state.imageName = preset.name;
                state.fillMode = "image";
                syncControlsFromSelected();
                const layer = selectedLayer();
                if (layer) { applyControlsToLayer(layer); render(); }
                log(`已选择预设贴图: ${preset.name}`);
            });
            grid.appendChild(node);
        });
    } catch (error) {
        grid.innerHTML = '<div class="hint">预设加载失败。</div>';
        log(`预设加载失败: ${error.message}`);
    }
}

async function bootstrap() {
    logBox.textContent = "";
    setWorkspaceEmptyState(true);
    syncLogUi();
    log("编辑器已启动");
    try {
        const response = await fetch(`${APP_BASE}/api/health`);
        const payload = await response.json();
        log(`服务状态: presets=${payload.presets}, ffmpeg=${payload.ffmpeg ? "ok" : "missing"}`);
    } catch (error) {
        log(`健康检查失败: ${error.message}`);
    }
    await loadPresets();
    syncControlsFromSelected();
    const params = new URLSearchParams(window.location.search);
    const imageUrl = params.get("image_url");
    const imageName = params.get("image_name");
    const artist = params.get("artist") || "";
    const characters = params.get("characters") || "";
    const postUrl = params.get("post_url") || "";
    state.sourceMeta.artist = artist;
    state.sourceMeta.characters = characters;
    state.sourceMeta.postUrl = postUrl;
    state.sourceMeta.imageName = imageName || "";
    if (artist) {
        applyStripeTextPreset(artist);
        log(`已带入来源作者: ${artist}`);
    }
    syncSourceMetaUi();
    if (imageUrl) await loadImageFromUrl(imageUrl, imageName);
}

bootstrap();
