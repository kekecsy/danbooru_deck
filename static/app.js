(function() {
    const ITEMS_PER_PAGE = 30;
    const DEFAULT_SERVER_BASE = 'http://127.0.0.1:8000';
    const SERVER_BASE = window.location.protocol === 'file:' ? DEFAULT_SERVER_BASE : window.location.origin;
    const IMAGE_EXTENSIONS = new Set(['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'avif']);

    const gallery = document.getElementById('image-gallery');
    const galleryTitle = document.getElementById('gallery-title');
    const galleryCount = document.getElementById('gallery-count');
    const pageInfo = document.getElementById('page-info');
    const pagePrev = document.getElementById('page-prev');
    const pageNext = document.getElementById('page-next');
    const tabLatest = document.getElementById('tab-latest');
    const tabLocal = document.getElementById('tab-local');
    const localDateControls = document.getElementById('local-date-controls');
    const localDatePrev = document.getElementById('local-date-prev');
    const localDateNext = document.getElementById('local-date-next');
    const localDateToday = document.getElementById('local-date-today');
    const localDateTrigger = document.getElementById('local-date-trigger');
    const localDateHint = document.getElementById('local-date-hint');
    const localDatePanel = document.getElementById('local-date-panel');
    const calendarMonthPrev = document.getElementById('calendar-month-prev');
    const calendarMonthNext = document.getElementById('calendar-month-next');
    const calendarMonthLabel = document.getElementById('calendar-month-label');
    const calendarGrid = document.getElementById('calendar-grid');
    const charSearch = document.getElementById('char-search');
    const logBox = document.getElementById('log-box');
    const btnStart = document.getElementById('btn-start');
    const btnPause = document.getElementById('btn-pause');
    const btnResume = document.getElementById('btn-resume');
    const btnStop = document.getElementById('btn-stop');
    const proxyStatus = document.getElementById('proxy-status');
    const mosaicHomeLink = document.getElementById('mosaic-home-link');
    const viewerModal = document.getElementById('viewer-modal');
    const viewerStage = viewerModal.querySelector('.viewer-stage');
    const viewerImage = document.getElementById('viewer-image');
    const viewerTitle = document.getElementById('viewer-title');
    const viewerCounter = document.getElementById('viewer-counter');
    const viewerPrev = document.getElementById('viewer-prev');
    const viewerNext = document.getElementById('viewer-next');
    const viewerClose = document.getElementById('viewer-close');
    const viewerPrevBottom = document.getElementById('viewer-prev-bottom');
    const viewerNextBottom = document.getElementById('viewer-next-bottom');
    const viewerOpenLocalBottom = document.getElementById('viewer-open-local-bottom');
    const viewerEditBottom = document.getElementById('viewer-edit-bottom');
    const viewerCloseBottom = document.getElementById('viewer-close-bottom');

    const galleryState = {
        latest: [],
        local: [],
        activeTab: 'latest',
        currentPage: 1,
        availableDates: [],
        selectedDate: '',
        today: '',
        calendarYear: 0,
        calendarMonth: 0
    };

    let currentViewerIndex = -1;

    function buildUrl(path) {
        if (!path) return '';
        if (/^https?:\/\//i.test(path)) return path;
        if (path.startsWith('/')) return `${SERVER_BASE}${path}`;
        return `${SERVER_BASE}/${path}`;
    }

    async function apiFetch(path, options) {
        return fetch(`${SERVER_BASE}${path}`, options);
    }

    function getFileExtension(path) {
        const raw = (path || '').split('?')[0].split('#')[0];
        const parts = raw.split('.');
        return parts.length > 1 ? parts.pop().toLowerCase() : '';
    }

    function buildPlaceholderDataUrl(label) {
        const safeLabel = (label || 'FILE').toUpperCase().slice(0, 8);
        const svg = `
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800">
                <defs>
                    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
                        <stop offset="0%" stop-color="#f7ede2"/>
                        <stop offset="100%" stop-color="#e2c9a6"/>
                    </linearGradient>
                </defs>
                <rect width="800" height="800" rx="48" fill="url(#g)"/>
                <rect x="72" y="72" width="656" height="656" rx="38" fill="rgba(255,255,255,0.52)" stroke="rgba(141,61,35,0.22)" stroke-width="6"/>
                <text x="400" y="430" text-anchor="middle" font-family="Segoe UI, Microsoft YaHei, sans-serif" font-size="140" font-weight="700" fill="#8d3d23">${safeLabel}</text>
            </svg>`;
        return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
    }

    function appendLog(msg) {
        if (logBox.innerText === '等待任务启动...') {
            logBox.innerText = '';
        }
        logBox.textContent += `${msg}\n`;
        setTimeout(() => {
            logBox.scrollTop = logBox.scrollHeight;
        }, 10);
        requestAnimationFrame(() => {
            logBox.scrollTop = logBox.scrollHeight;
        });
    }

    function normalizeImageData(imgData) {
        const imageSrc = buildUrl(imgData.web_url || imgData.file_url || '');
        const artist = imgData.artist || '未知';
        const filename = imgData.filename || `${artist || 'image'}.png`;
        const localPath = imgData.local_path || '';
        const tags = imgData.tags || {};
        const charString = tags.tag_string_character || '';
        const encodedImage = encodeURIComponent(imageSrc);
        const encodedName = encodeURIComponent(filename);
        const extension = getFileExtension(filename || localPath || imageSrc) || 'FILE';
        const isImage = IMAGE_EXTENSIONS.has(extension);

        return {
            ...imgData,
            artist,
            filename,
            local_path: localPath,
            web_url: imageSrc,
            post_url: imgData.post_url || '#',
            tags,
            characters: charString,
            mosaicUrl: `${SERVER_BASE}/mosaic?image_url=${encodedImage}&image_name=${encodedName}`,
            isImage,
            previewSrc: isImage ? imageSrc : buildPlaceholderDataUrl(extension),
            fileExtensionLabel: extension.toUpperCase()
        };
    }

    function mergeUniqueByUrl(existing, incoming) {
        const map = new Map(existing.map(item => [item.web_url, item]));
        incoming.map(normalizeImageData).forEach(item => {
            map.set(item.web_url, { ...(map.get(item.web_url) || {}), ...item });
        });
        return Array.from(map.values()).sort((a, b) => b.web_url.localeCompare(a.web_url));
    }

    function getActiveItems() {
        return galleryState[galleryState.activeTab];
    }

    function getFilteredItems() {
        const query = charSearch.value.toLowerCase().trim();
        const items = getActiveItems();
        if (!query) return items;
        return items.filter(item => (item.characters || '').toLowerCase().includes(query));
    }

    function getVisibleCards() {
        return Array.from(gallery.getElementsByClassName('img-card'));
    }

    function getSelectedDateLabel() {
        return galleryState.selectedDate || galleryState.today || '';
    }

    function parseDateParts(dateStr) {
        if (!dateStr) return null;
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr);
        if (!match) return null;
        return {
            year: Number(match[1]),
            month: Number(match[2]),
            day: Number(match[3])
        };
    }

    function setCalendarMonthFromDate(dateStr) {
        const parts = parseDateParts(dateStr);
        if (!parts) return;
        galleryState.calendarYear = parts.year;
        galleryState.calendarMonth = parts.month;
    }

    function formatDate(year, month, day) {
        const y = String(year).padStart(4, '0');
        const m = String(month).padStart(2, '0');
        const d = String(day).padStart(2, '0');
        return `${y}-${m}-${d}`;
    }

    function getAvailableDateSet() {
        return new Set(galleryState.availableDates);
    }

    function openCalendarPanel() {
        if (!galleryState.calendarYear || !galleryState.calendarMonth) {
            setCalendarMonthFromDate(getSelectedDateLabel());
        }
        renderCalendar();
        localDatePanel.classList.remove('hidden');
        localDatePanel.setAttribute('aria-hidden', 'false');
        localDateTrigger.setAttribute('aria-expanded', 'true');
    }

    function closeCalendarPanel() {
        localDatePanel.classList.add('hidden');
        localDatePanel.setAttribute('aria-hidden', 'true');
        localDateTrigger.setAttribute('aria-expanded', 'false');
    }

    function renderCalendar() {
        if (!galleryState.calendarYear || !galleryState.calendarMonth) {
            setCalendarMonthFromDate(getSelectedDateLabel());
        }

        const year = galleryState.calendarYear;
        const month = galleryState.calendarMonth;
        const availableDateSet = getAvailableDateSet();
        const selectedDate = getSelectedDateLabel();
        const today = galleryState.today;

        calendarMonthLabel.textContent = `${year}-${String(month).padStart(2, '0')}`;
        calendarGrid.innerHTML = '';

        const firstDay = new Date(year, month - 1, 1);
        const firstWeekday = (firstDay.getDay() + 6) % 7;
        const daysInMonth = new Date(year, month, 0).getDate();
        const prevMonthDays = new Date(year, month - 1, 0).getDate();

        const cells = [];

        for (let i = firstWeekday - 1; i >= 0; i -= 1) {
            cells.push({
                year: month === 1 ? year - 1 : year,
                month: month === 1 ? 12 : month - 1,
                day: prevMonthDays - i,
                otherMonth: true
            });
        }

        for (let day = 1; day <= daysInMonth; day += 1) {
            cells.push({ year, month, day, otherMonth: false });
        }

        while (cells.length < 42) {
            const day = cells.length - (firstWeekday + daysInMonth) + 1;
            cells.push({
                year: month === 12 ? year + 1 : year,
                month: month === 12 ? 1 : month + 1,
                day,
                otherMonth: true
            });
        }

        cells.forEach(cell => {
            const dateStr = formatDate(cell.year, cell.month, cell.day);
            const isAvailable = availableDateSet.has(dateStr);
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `calendar-day ${cell.otherMonth ? 'other-month' : ''} ${isAvailable ? 'enabled-day' : 'disabled-day'}`.trim();
            if (dateStr === selectedDate) button.classList.add('selected-day');
            if (dateStr === today) button.classList.add('today-day');
            button.textContent = String(cell.day);
            button.dataset.date = dateStr;
            button.title = isAvailable ? `查看 ${dateStr} 的图片` : `${dateStr} 没有已下载图片`;
            button.disabled = !isAvailable;
            button.addEventListener('click', function() {
                if (!isAvailable) return;
                loadGalleryByDate(dateStr);
                closeCalendarPanel();
            });
            calendarGrid.appendChild(button);
        });
    }

    function updateLocalDateControls() {
        const isLocalTab = galleryState.activeTab === 'local';
        localDateControls.classList.toggle('hidden', !isLocalTab);

        if (!galleryState.availableDates.length) {
            localDatePrev.disabled = true;
            localDateNext.disabled = true;
            localDateToday.disabled = true;
            localDateTrigger.disabled = true;
            localDateTrigger.textContent = '暂无日期';
            localDateHint.textContent = '还没有已下载的日期目录';
            closeCalendarPanel();
            return;
        }

        const currentDate = getSelectedDateLabel();
        const currentIndex = galleryState.availableDates.indexOf(currentDate);
        localDateTrigger.disabled = false;
        localDateTrigger.textContent = currentDate || '选择日期';
        localDatePrev.disabled = currentIndex < 0 || currentIndex >= galleryState.availableDates.length - 1;
        localDateNext.disabled = currentIndex <= 0;
        localDateToday.disabled = !galleryState.today || currentDate === galleryState.today || !galleryState.availableDates.includes(galleryState.today);
        localDateHint.textContent = `共 ${galleryState.availableDates.length} 个已下载日期`;
        renderCalendar();
    }

    function createCard(item) {
        const card = document.createElement('div');
        card.className = 'img-card';
        card.dataset.characters = (item.characters || '').toLowerCase();
        card.dataset.imageSrc = item.web_url;
        card.dataset.artist = item.artist;
        card.dataset.mosaicUrl = item.mosaicUrl;
        card.dataset.previewSrc = item.previewSrc;
        card.dataset.isImage = item.isImage ? '1' : '0';
        card.dataset.localPath = item.local_path || '';

        const charArray = (item.characters || '').split(' ').filter(Boolean);
        let tagsHtml = '';
        if (charArray.length) {
            charArray.forEach(char => {
                const safeChar = char.replace(/"/g, '&quot;');
                tagsHtml += `<span class="char-tag" data-char="${safeChar}" title="点击复制角色名">${char}</span>`;
            });
        } else {
            tagsHtml = '<span class="char-tag original" data-char="original">original</span>';
        }

        card.innerHTML = `
            <button class="image-trigger" type="button" aria-label="预览图片">
                <img src="${item.previewSrc}" loading="lazy" title="画师: ${item.artist}" class="${item.isImage ? '' : 'file-thumb'}">
            </button>
            <div class="img-info">
                <b title="${item.artist}">${item.artist}</b>
                <div class="tags-scroll-container">
                    ${tagsHtml}
                </div>
                <div class="card-actions">
                    <a href="${item.post_url || '#'}" target="_blank" rel="noopener noreferrer" class="card-link origin">查看原帖</a>
                    <a href="${item.mosaicUrl}" target="_blank" rel="noopener noreferrer" class="card-link edit">编辑打码</a>
                </div>
            </div>
        `;
        return card;
    }

    function renderGallery() {
        const filteredItems = getFilteredItems();
        const totalPages = Math.max(1, Math.ceil(filteredItems.length / ITEMS_PER_PAGE));
        if (galleryState.currentPage > totalPages) {
            galleryState.currentPage = totalPages;
        }
        const startIndex = (galleryState.currentPage - 1) * ITEMS_PER_PAGE;
        const pageItems = filteredItems.slice(startIndex, startIndex + ITEMS_PER_PAGE);

        gallery.innerHTML = '';
        if (!pageItems.length) {
            gallery.innerHTML = '<div class="gallery-empty">当前栏目没有可显示的图片</div>';
        } else {
            pageItems.forEach(item => gallery.appendChild(createCard(item)));
        }

        galleryTitle.textContent = galleryState.activeTab === 'latest'
            ? '最新抓取预览'
            : `本地已下载${getSelectedDateLabel() ? ` · ${getSelectedDateLabel()}` : ''}`;
        galleryCount.textContent = `共 ${filteredItems.length} 张`;
        pageInfo.textContent = `第 ${galleryState.currentPage} / ${totalPages} 页`;
        pagePrev.disabled = galleryState.currentPage <= 1;
        pageNext.disabled = galleryState.currentPage >= totalPages;
        tabLatest.classList.toggle('active', galleryState.activeTab === 'latest');
        tabLocal.classList.toggle('active', galleryState.activeTab === 'local');
        updateLocalDateControls();

        if (viewerModal.classList.contains('open')) {
            const currentSrc = viewerImage.getAttribute('src');
            const visibleCards = getVisibleCards();
            if (!visibleCards.length) {
                closeViewer();
            } else {
                const currentIndex = visibleCards.findIndex(card => card.dataset.imageSrc === currentSrc);
                renderViewer(currentIndex >= 0 ? currentIndex : 0);
            }
        }
    }

    function switchTab(tabName) {
        galleryState.activeTab = tabName;
        galleryState.currentPage = 1;
        if (tabName === 'local' && getSelectedDateLabel()) {
            loadGalleryByDate(getSelectedDateLabel());
            return;
        }
        renderGallery();
    }

    function applyViewerImageSize() {
        const naturalWidth = viewerImage.naturalWidth;
        const naturalHeight = viewerImage.naturalHeight;
        if (!naturalWidth || !naturalHeight) return;

        viewerImage.style.width = '';
        viewerImage.style.height = '';
        if (naturalWidth >= naturalHeight) {
            viewerImage.style.height = '1280px';
        } else {
            viewerImage.style.width = '1280px';
        }
    }

    function resetViewerScroll() {
        viewerStage.scrollTop = 0;
        viewerStage.scrollLeft = 0;
    }

    function closeViewer() {
        viewerModal.classList.remove('open');
        viewerModal.setAttribute('aria-hidden', 'true');
        viewerImage.src = '';
        viewerImage.style.width = '';
        viewerImage.style.height = '';
        viewerImage.classList.remove('file-thumb');
        currentViewerIndex = -1;
        document.body.style.overflow = 'hidden';
    }

    function renderViewer(index) {
        const cards = getVisibleCards();
        if (!cards.length) {
            closeViewer();
            return;
        }

        const safeIndex = Math.max(0, Math.min(index, cards.length - 1));
        const card = cards[safeIndex];
        currentViewerIndex = safeIndex;
        viewerImage.src = card.dataset.isImage === '1' ? card.dataset.imageSrc : card.dataset.previewSrc;
        viewerImage.classList.toggle('file-thumb', card.dataset.isImage !== '1');
        viewerImage.alt = card.dataset.artist || '未知';
        viewerTitle.textContent = card.dataset.artist || '未知';
        viewerCounter.textContent = `${safeIndex + 1} / ${cards.length}`;
        viewerEditBottom.href = card.dataset.mosaicUrl || `${SERVER_BASE}/mosaic`;
        viewerPrev.disabled = safeIndex === 0;
        viewerPrevBottom.disabled = safeIndex === 0;
        viewerNext.disabled = safeIndex === cards.length - 1;
        viewerNextBottom.disabled = safeIndex === cards.length - 1;
        resetViewerScroll();

        if (viewerImage.complete) {
            applyViewerImageSize();
        }
    }

    function openViewerByCard(card) {
        const cards = getVisibleCards();
        const index = cards.indexOf(card);
        if (index === -1) return;

        viewerModal.classList.add('open');
        viewerModal.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';
        renderViewer(index);
    }

    function showPreviousImage() {
        if (currentViewerIndex > 0) {
            renderViewer(currentViewerIndex - 1);
        }
    }

    function showNextImage() {
        const cards = getVisibleCards();
        if (currentViewerIndex >= 0 && currentViewerIndex < cards.length - 1) {
            renderViewer(currentViewerIndex + 1);
        }
    }

    function updateButtons(isRunning, isPaused) {
        btnStart.disabled = isRunning;
        btnPause.disabled = !isRunning || isPaused;
        btnResume.disabled = !isRunning || !isPaused;
        btnStop.disabled = !isRunning;
    }

    async function checkProxy() {
        proxyStatus.innerText = '正在检查...';
        proxyStatus.style.color = '';
        try {
            const res = await apiFetch('/api/proxy_check');
            if (!res.ok) throw new Error('网络响应异常');
            const data = await res.json();
            proxyStatus.innerText = data.msg || '未知';
            if (data.color === 'green') proxyStatus.style.color = '#4caf50';
            else if (data.color === 'orange') proxyStatus.style.color = '#ff9800';
            else proxyStatus.style.color = '#f44336';
        } catch (e) {
            proxyStatus.innerText = '请求失败';
            proxyStatus.style.color = '#f44336';
        }
    }

    async function startTask() {
        const startP = document.getElementById('start-page').value;
        const endP = document.getElementById('end-page').value;
        const tags = document.getElementById('filter-tags').value;
        try {
            await apiFetch('/api/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    start_page: parseInt(startP, 10) || 1,
                    end_page: parseInt(endP, 10) || 16,
                    tags
                })
            });
        } catch (e) {
            appendLog(`启动任务请求失败: ${e.message}`);
        }
    }

    async function pauseTask() {
        try {
            await apiFetch('/api/pause', { method: 'POST' });
        } catch (e) {
            appendLog('暂停失败');
        }
    }

    async function resumeTask() {
        try {
            await apiFetch('/api/resume', { method: 'POST' });
        } catch (e) {
            appendLog('继续失败');
        }
    }

    async function stopTask() {
        try {
            await apiFetch('/api/stop', { method: 'POST' });
        } catch (e) {
            appendLog(`停止失败: ${e.message}`);
        }
    }

    async function openCurrentLocalFile() {
        const cards = getVisibleCards();
        if (currentViewerIndex < 0 || currentViewerIndex >= cards.length) {
            appendLog('当前没有可打开的本地文件');
            return;
        }
        const localPath = cards[currentViewerIndex].dataset.localPath;
        if (!localPath) {
            appendLog('当前图片缺少本地路径');
            return;
        }
        try {
            const res = await apiFetch('/api/open_local', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ local_path: localPath })
            });
            const data = await res.json();
            appendLog(data.msg || '已尝试打开本地文件');
        } catch (e) {
            appendLog(`打开本地文件失败: ${e.message}`);
        }
    }

    function applyGalleryPayload(data) {
        galleryState.latest = (data.latest_images || []).map(normalizeImageData).reverse();
        galleryState.local = (data.local_images || []).map(normalizeImageData);
        galleryState.availableDates = data.available_dates || [];
        galleryState.selectedDate = data.selected_date || '';
        galleryState.today = data.today || '';
        if (!galleryState.calendarYear || !galleryState.calendarMonth) {
            setCalendarMonthFromDate(galleryState.selectedDate || galleryState.today);
        }
        const selectedParts = parseDateParts(galleryState.selectedDate || galleryState.today);
        if (selectedParts) {
            galleryState.calendarYear = selectedParts.year;
            galleryState.calendarMonth = selectedParts.month;
        }
        renderGallery();
    }

    async function loadGalleryByDate(dateValue) {
        try {
            const path = dateValue ? `/api/gallery_data/${encodeURIComponent(dateValue)}` : '/api/gallery_data';
            const res = await apiFetch(path);
            if (!res.ok) throw new Error('加载图库数据失败');
            const data = await res.json();
            applyGalleryPayload(data);
        } catch (e) {
            appendLog(`加载图库失败: ${e.message}`);
        }
    }

    async function loadInitialGalleryData() {
        await loadGalleryByDate('');
        if (window.location.protocol === 'file:') {
            appendLog('当前像是直接打开了 html，请先运行 python main.py，然后访问 http://127.0.0.1:8000');
        }
    }

    function getRelativeAvailableDate(step) {
        const currentIndex = galleryState.availableDates.indexOf(getSelectedDateLabel());
        if (currentIndex < 0) return '';
        return galleryState.availableDates[currentIndex + step] || '';
    }

    function setupTagCopyDelegation() {
        gallery.addEventListener('click', function(e) {
            const tag = e.target.closest('.char-tag');
            if (!tag || tag.classList.contains('original')) return;

            e.preventDefault();
            e.stopPropagation();
            const charName = tag.dataset.char;
            if (!charName) return;

            navigator.clipboard.writeText(charName).then(() => {
                tag.innerText = '已复制';
                tag.style.backgroundColor = '#4caf50';
                tag.style.color = 'white';
                setTimeout(() => {
                    tag.innerText = charName;
                    tag.style.backgroundColor = '';
                    tag.style.color = '';
                }, 800);
            }).catch(() => {
                appendLog('复制角色名失败');
            });
        });
    }

    function setupViewerDelegation() {
        gallery.addEventListener('click', function(e) {
            const trigger = e.target.closest('.image-trigger');
            if (!trigger) return;
            e.preventDefault();
            const card = trigger.closest('.img-card');
            if (card) openViewerByCard(card);
        });

        viewerPrev.addEventListener('click', showPreviousImage);
        viewerPrevBottom.addEventListener('click', showPreviousImage);
        viewerNext.addEventListener('click', showNextImage);
        viewerNextBottom.addEventListener('click', showNextImage);
        viewerClose.addEventListener('click', closeViewer);
        viewerCloseBottom.addEventListener('click', closeViewer);
        viewerOpenLocalBottom.addEventListener('click', openCurrentLocalFile);
        viewerModal.addEventListener('click', function(e) {
            if (e.target === viewerModal) closeViewer();
        });
        document.addEventListener('keydown', function(e) {
            if (!viewerModal.classList.contains('open')) return;
            if (e.key === 'Escape') closeViewer();
            if (e.key === 'ArrowLeft') showPreviousImage();
            if (e.key === 'ArrowRight') showNextImage();
        });
        viewerImage.addEventListener('load', function() {
            applyViewerImageSize();
            resetViewerScroll();
        });
    }

    function setupPaginationAndTabs() {
        tabLatest.addEventListener('click', function() {
            switchTab('latest');
        });
        tabLocal.addEventListener('click', function() {
            switchTab('local');
        });
        pagePrev.addEventListener('click', function() {
            if (galleryState.currentPage > 1) {
                galleryState.currentPage -= 1;
                renderGallery();
            }
        });
        pageNext.addEventListener('click', function() {
            const totalPages = Math.max(1, Math.ceil(getFilteredItems().length / ITEMS_PER_PAGE));
            if (galleryState.currentPage < totalPages) {
                galleryState.currentPage += 1;
                renderGallery();
            }
        });
        charSearch.addEventListener('input', function() {
            galleryState.currentPage = 1;
            renderGallery();
        });
    }

    function setupLocalDatePicker() {
        localDateTrigger.addEventListener('click', function(event) {
            event.stopPropagation();
            if (localDatePanel.classList.contains('hidden')) {
                openCalendarPanel();
            } else {
                closeCalendarPanel();
            }
        });
        localDatePrev.addEventListener('click', function() {
            const targetDate = getRelativeAvailableDate(1);
            if (targetDate) loadGalleryByDate(targetDate);
        });
        localDateNext.addEventListener('click', function() {
            const targetDate = getRelativeAvailableDate(-1);
            if (targetDate) loadGalleryByDate(targetDate);
        });
        localDateToday.addEventListener('click', function() {
            if (galleryState.today && galleryState.availableDates.includes(galleryState.today)) {
                loadGalleryByDate(galleryState.today);
                closeCalendarPanel();
            }
        });
        calendarMonthPrev.addEventListener('click', function(event) {
            event.stopPropagation();
            if (galleryState.calendarMonth === 1) {
                galleryState.calendarYear -= 1;
                galleryState.calendarMonth = 12;
            } else {
                galleryState.calendarMonth -= 1;
            }
            renderCalendar();
        });
        calendarMonthNext.addEventListener('click', function(event) {
            event.stopPropagation();
            if (galleryState.calendarMonth === 12) {
                galleryState.calendarYear += 1;
                galleryState.calendarMonth = 1;
            } else {
                galleryState.calendarMonth += 1;
            }
            renderCalendar();
        });
        document.addEventListener('click', function(event) {
            if (localDatePanel.classList.contains('hidden')) return;
            if (localDateControls.contains(event.target)) return;
            closeCalendarPanel();
        });
    }

    window.checkProxy = checkProxy;
    window.startTask = startTask;
    window.pauseTask = pauseTask;
    window.resumeTask = resumeTask;
    window.stopTask = stopTask;

    mosaicHomeLink.href = `${SERVER_BASE}/mosaic`;
    viewerEditBottom.href = `${SERVER_BASE}/mosaic`;

    const polling = setInterval(async function() {
        try {
            const res = await apiFetch('/api/status');
            if (!res.ok) return;
            const data = await res.json();
            if (data.new_logs?.length) {
                data.new_logs.forEach(log => appendLog(log));
            }
            if (data.new_images?.length) {
                galleryState.latest = mergeUniqueByUrl(galleryState.latest, data.new_images);
                if (galleryState.selectedDate === galleryState.today) {
                    galleryState.local = mergeUniqueByUrl(galleryState.local, data.new_images);
                }
                renderGallery();
            }
            updateButtons(data.is_running, data.is_paused);
        } catch (e) {
            // silent
        }
    }, 1000);

    setupTagCopyDelegation();
    setupViewerDelegation();
    setupPaginationAndTabs();
    setupLocalDatePicker();
    loadInitialGalleryData();
    appendLog('界面已加载。');

    window.addEventListener('beforeunload', function() {
        clearInterval(polling);
    });
})();
