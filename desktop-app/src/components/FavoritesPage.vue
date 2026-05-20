<script setup>
import { computed, onMounted, ref } from 'vue';

const API_BASE = 'http://127.0.0.1:8000';
const ALL_KEY = '__all__';

const groups = ref({});       // {groupName: [artist, ...]}
const loading = ref(false);
const saving = ref(false);
const selectedGroup = ref(ALL_KEY);   // 当前过滤
const search = ref('');

const toast = ref({ show: false, msg: '', type: 'info' });
function showToast(msg, type = 'info') {
  toast.value = { show: true, msg, type };
  setTimeout(() => { toast.value.show = false; }, 2500);
}

// 全部画师扁平化 + 反向索引（用于显示每个画师属于哪些分组）
const allArtists = computed(() => {
  const m = new Map();   // artist -> Set(groupName)
  for (const [g, arts] of Object.entries(groups.value)) {
    for (const a of arts) {
      if (!m.has(a)) m.set(a, new Set());
      m.get(a).add(g);
    }
  }
  return Array.from(m.entries())
    .map(([artist, groupSet]) => ({ artist, groups: Array.from(groupSet) }))
    .sort((a, b) => a.artist.localeCompare(b.artist));
});

const visibleArtists = computed(() => {
  const kw = search.value.trim().toLowerCase();
  const filter = selectedGroup.value;
  return allArtists.value.filter(it => {
    if (filter !== ALL_KEY && !it.groups.includes(filter)) return false;
    if (kw && !it.artist.toLowerCase().includes(kw)) return false;
    return true;
  });
});

const groupList = computed(() => {
  return Object.entries(groups.value)
    .map(([name, arts]) => ({ name, count: arts.length }))
    .sort((a, b) => a.name.localeCompare(b.name));
});

async function loadFavorites() {
  loading.value = true;
  try {
    const res = await fetch(`${API_BASE}/api/artist_favorites`);
    const data = await res.json();
    if (data.ok) {
      groups.value = data.groups || {};
    }
  } catch (err) {
    showToast('加载失败: ' + err.message, 'error');
  } finally {
    loading.value = false;
  }
}

async function persist() {
  saving.value = true;
  try {
    const res = await fetch(`${API_BASE}/api/artist_favorites`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ groups: groups.value }),
    });
    const data = await res.json();
    if (!data.ok) {
      showToast('保存失败: ' + (data.msg || ''), 'error');
      return false;
    }
    groups.value = data.groups || {};
    return true;
  } catch (err) {
    showToast('保存失败: ' + err.message, 'error');
    return false;
  } finally {
    saving.value = false;
  }
}

// ----- 分组 CRUD -----
async function createGroup() {
  const name = prompt('新建分组名称');
  if (name === null) return;
  const trimmed = name.trim();
  if (!trimmed) { showToast('分组名不能为空', 'error'); return; }
  if (groups.value[trimmed]) { showToast('分组已存在', 'error'); return; }
  groups.value = { ...groups.value, [trimmed]: [] };
  if (await persist()) {
    selectedGroup.value = trimmed;
    showToast(`已创建分组「${trimmed}」`, 'success');
  }
}

async function renameGroup(oldName) {
  const name = prompt('重命名分组', oldName);
  if (name === null) return;
  const trimmed = name.trim();
  if (!trimmed || trimmed === oldName) return;
  if (groups.value[trimmed]) { showToast('目标分组名已存在', 'error'); return; }
  const next = {};
  for (const [k, v] of Object.entries(groups.value)) {
    next[k === oldName ? trimmed : k] = v;
  }
  groups.value = next;
  if (await persist()) {
    if (selectedGroup.value === oldName) selectedGroup.value = trimmed;
    showToast('已重命名', 'success');
  }
}

async function deleteGroup(name) {
  const arts = groups.value[name] || [];
  if (!confirm(`确定删除分组「${name}」（含 ${arts.length} 个画师）？此操作不删除画师本身，仅从该分组中移除。`)) return;
  const next = { ...groups.value };
  delete next[name];
  groups.value = next;
  if (await persist()) {
    if (selectedGroup.value === name) selectedGroup.value = ALL_KEY;
    showToast('已删除分组', 'success');
  }
}

// ----- 画师 CRUD -----
const manualAdd = ref({
  open: false,
  text: '',
  selectedGroups: [],
});

function openManualAdd() {
  manualAdd.value.open = true;
  manualAdd.value.text = '';
  // 默认勾选当前过滤的分组（若是"全部"则不预选）
  manualAdd.value.selectedGroups = selectedGroup.value !== ALL_KEY ? [selectedGroup.value] : [];
}
function closeManualAdd() { manualAdd.value.open = false; }

function toggleManualGroup(name) {
  const idx = manualAdd.value.selectedGroups.indexOf(name);
  if (idx >= 0) manualAdd.value.selectedGroups.splice(idx, 1);
  else manualAdd.value.selectedGroups.push(name);
}

async function submitManualAdd() {
  const names = manualAdd.value.text
    .split(/[\s,，;；\n]+/)
    .map(s => s.trim())
    .filter(Boolean);
  if (!names.length) { showToast('请输入画师名', 'error'); return; }
  if (!manualAdd.value.selectedGroups.length) { showToast('请至少勾选一个分组', 'error'); return; }
  const next = { ...groups.value };
  for (const g of manualAdd.value.selectedGroups) {
    const arr = next[g] ? [...next[g]] : [];
    for (const n of names) {
      if (!arr.includes(n)) arr.push(n);
    }
    next[g] = arr;
  }
  groups.value = next;
  if (await persist()) {
    showToast(`已添加 ${names.length} 个画师到 ${manualAdd.value.selectedGroups.length} 个分组`, 'success');
    closeManualAdd();
  }
}

// 编辑一个画师所属的分组（多选）
const editArtist = ref({
  open: false,
  artist: '',
  selectedGroups: [],
});

function openEditArtist(item) {
  editArtist.value.open = true;
  editArtist.value.artist = item.artist;
  editArtist.value.selectedGroups = [...item.groups];
}
function closeEditArtist() { editArtist.value.open = false; }

function toggleEditGroup(name) {
  const idx = editArtist.value.selectedGroups.indexOf(name);
  if (idx >= 0) editArtist.value.selectedGroups.splice(idx, 1);
  else editArtist.value.selectedGroups.push(name);
}

async function submitEditArtist() {
  const artist = editArtist.value.artist;
  const targetGroups = new Set(editArtist.value.selectedGroups);
  const next = {};
  for (const [g, arr] of Object.entries(groups.value)) {
    const has = arr.includes(artist);
    if (targetGroups.has(g) && !has) {
      next[g] = [...arr, artist];
    } else if (!targetGroups.has(g) && has) {
      next[g] = arr.filter(a => a !== artist);
    } else {
      next[g] = arr;
    }
  }
  groups.value = next;
  if (await persist()) {
    showToast('已更新分组', 'success');
    closeEditArtist();
  }
}

async function removeArtistFromAll(artist) {
  if (!confirm(`从所有分组中移除画师「${artist}」？`)) return;
  const next = {};
  for (const [g, arr] of Object.entries(groups.value)) {
    next[g] = arr.filter(a => a !== artist);
  }
  groups.value = next;
  if (await persist()) showToast('已移除', 'success');
}

async function removeArtistFromGroup(artist, group) {
  const next = { ...groups.value };
  next[group] = (next[group] || []).filter(a => a !== artist);
  groups.value = next;
  if (await persist()) showToast(`已从「${group}」移除`, 'success');
}

async function copyArtist(artist) {
  try {
    await navigator.clipboard.writeText(artist);
    showToast(`已复制：${artist}`, 'success');
  } catch (err) {
    showToast('复制失败: ' + err.message, 'error');
  }
}

onMounted(loadFavorites);
</script>

<template>
  <div class="favorites-layout">
    <!-- 左侧分组列表 -->
    <section class="panel card favorites-side">
      <div class="panel-head compact-head">
        <div>
          <h2>分组</h2>
          <p class="inline-note">共 {{ groupList.length }} 个 · {{ allArtists.length }} 个画师</p>
        </div>
        <button @click="createGroup" :disabled="saving">新建分组</button>
      </div>

      <div class="group-list">
        <button
          class="group-item"
          :class="{ active: selectedGroup === ALL_KEY }"
          @click="selectedGroup = ALL_KEY"
        >
          <span class="group-name">全部</span>
          <span class="group-count">{{ allArtists.length }}</span>
        </button>
        <div
          v-for="g in groupList"
          :key="g.name"
          class="group-item-wrap"
        >
          <button
            class="group-item"
            :class="{ active: selectedGroup === g.name }"
            @click="selectedGroup = g.name"
          >
            <span class="group-name">{{ g.name }}</span>
            <span class="group-count">{{ g.count }}</span>
          </button>
          <div class="group-actions">
            <button class="ghost icon-btn" @click="renameGroup(g.name)" title="重命名">✎</button>
            <button class="ghost icon-btn" @click="deleteGroup(g.name)" title="删除">×</button>
          </div>
        </div>
        <div v-if="!groupList.length" class="empty-hint">还没有分组，点右上「新建分组」开始</div>
      </div>
    </section>

    <!-- 右侧画师列表 -->
    <section class="panel card favorites-main">
      <div class="panel-head compact-head">
        <div>
          <h2>{{ selectedGroup === ALL_KEY ? '全部画师' : selectedGroup }}</h2>
          <p class="inline-note">
            {{ visibleArtists.length }} 个画师<span v-if="search"> · 已搜索</span> · 点击画师名复制到剪贴板
          </p>
        </div>
        <div style="display: flex; gap: 8px;">
          <input
            v-model="search"
            class="search-input"
            type="text"
            placeholder="搜索画师"
            style="width: 200px;"
          />
          <button @click="openManualAdd" :disabled="saving">手动添加画师</button>
        </div>
      </div>

      <div v-if="loading" class="gallery-empty">正在加载收藏...</div>
      <div v-else-if="!visibleArtists.length" class="gallery-empty">
        {{ allArtists.length ? '没有匹配的画师' : '还没有收藏画师 —— 去抓图页点画师 chip 的 ★ 加入，或在这里手动添加' }}
      </div>

      <div v-else class="favorites-grid">
        <article
          v-for="item in visibleArtists"
          :key="item.artist"
          class="artist-card"
        >
          <button
            class="artist-name"
            @click="copyArtist(item.artist)"
            :title="`点击复制：${item.artist}`"
          >{{ item.artist }}</button>
          <div class="artist-groups">
            <span
              v-for="g in item.groups"
              :key="g"
              class="group-tag"
              :title="`从「${g}」中移除`"
              @click.stop="removeArtistFromGroup(item.artist, g)"
            >{{ g }} ×</span>
          </div>
          <div class="artist-actions">
            <button class="secondary" @click.stop="openEditArtist(item)" style="padding: 4px 10px; font-size: 11px;">编辑分组</button>
            <button class="ghost" @click.stop="removeArtistFromAll(item.artist)" style="padding: 4px 10px; font-size: 11px; color: #9d2c2c;">全部移除</button>
          </div>
        </article>
      </div>
    </section>

    <!-- 手动添加画师 modal -->
    <div v-if="manualAdd.open" class="viewer-overlay fav-overlay" @click.self="closeManualAdd">
      <div class="fav-modal">
        <div class="fav-modal-head">
          <h3>手动添加画师</h3>
          <button class="ghost" @click="closeManualAdd" style="color: var(--muted);">×</button>
        </div>
        <label class="fav-field">
          <span>画师名（可一次粘多个，用空格 / 逗号 / 换行分隔）</span>
          <textarea
            v-model="manualAdd.text"
            class="fav-textarea"
            placeholder="例如：kantoku  mika_pikazo, sakimichan"
          ></textarea>
        </label>
        <label class="fav-field">
          <span>添加到分组（可多选）</span>
          <div class="fav-checkbox-list">
            <label v-for="g in groupList" :key="g.name" class="fav-checkbox-row">
              <input
                type="checkbox"
                :checked="manualAdd.selectedGroups.includes(g.name)"
                @change="toggleManualGroup(g.name)"
              />
              <span>{{ g.name }} <span class="muted compact-text">({{ g.count }})</span></span>
            </label>
            <div v-if="!groupList.length" class="muted compact-text">还没有分组，请先点左上「新建分组」</div>
          </div>
        </label>
        <div class="fav-modal-foot">
          <button class="ghost" @click="closeManualAdd" style="color: var(--accent-deep);">取消</button>
          <button @click="submitManualAdd" :disabled="saving">{{ saving ? '保存中...' : '确定添加' }}</button>
        </div>
      </div>
    </div>

    <!-- 编辑画师所属分组 modal -->
    <div v-if="editArtist.open" class="viewer-overlay fav-overlay" @click.self="closeEditArtist">
      <div class="fav-modal">
        <div class="fav-modal-head">
          <h3>编辑「{{ editArtist.artist }}」所属分组</h3>
          <button class="ghost" @click="closeEditArtist" style="color: var(--muted);">×</button>
        </div>
        <div class="fav-checkbox-list">
          <label v-for="g in groupList" :key="g.name" class="fav-checkbox-row">
            <input
              type="checkbox"
              :checked="editArtist.selectedGroups.includes(g.name)"
              @change="toggleEditGroup(g.name)"
            />
            <span>{{ g.name }} <span class="muted compact-text">({{ g.count }})</span></span>
          </label>
        </div>
        <div class="fav-modal-foot">
          <button class="ghost" @click="closeEditArtist" style="color: var(--accent-deep);">取消</button>
          <button @click="submitEditArtist" :disabled="saving">{{ saving ? '保存中...' : '保存' }}</button>
        </div>
      </div>
    </div>

    <div v-if="toast.show" class="toast-overlay" :class="toast.type">{{ toast.msg }}</div>
  </div>
</template>

<style scoped>
.favorites-layout {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: 16px;
  min-height: 100%;
}
.favorites-side {
  position: sticky;
  top: 16px;
  max-height: calc(100vh - 32px);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.favorites-main {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.group-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 4px 0;
}
.group-item {
  width: 100%;
  text-align: left;
  background: rgba(255, 255, 255, 0.55);
  color: var(--ink);
  border: 1px solid var(--line);
  padding: 8px 12px;
  border-radius: 10px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
  font-size: 13px;
  cursor: pointer;
}
.group-item:hover { background: rgba(243, 223, 212, 0.55); }
.group-item.active {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
  border-color: transparent;
}
.group-item.active .group-count { background: rgba(255, 255, 255, 0.25); color: #fff; }
.group-name { word-break: break-all; }
.group-count {
  flex-shrink: 0;
  margin-left: 8px;
  font-size: 11px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--soft);
  color: var(--accent-deep);
}

.group-item-wrap {
  position: relative;
}
.group-actions {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  display: none;
  gap: 4px;
  z-index: 2;
}
.group-item-wrap:hover .group-actions { display: flex; }
.icon-btn {
  width: 24px;
  height: 24px;
  padding: 0;
  border-radius: 6px;
  font-size: 12px;
  background: rgba(255, 255, 255, 0.9);
  color: var(--ink);
}
.group-item.active ~ .group-actions .icon-btn,
.group-item-wrap:hover .group-item.active + .group-actions .icon-btn {
  background: rgba(255, 255, 255, 0.95);
  color: var(--accent-deep);
}

.empty-hint {
  padding: 16px;
  text-align: center;
  color: var(--muted);
  font-size: 12px;
  border: 1px dashed var(--line);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.4);
}

.favorites-grid {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 10px;
  padding-right: 4px;
}
.artist-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.7);
}
.artist-name {
  width: 100%;
  text-align: left;
  background: linear-gradient(135deg, #fbf4eb, #f2e8db);
  color: var(--ink);
  font-family: Consolas, monospace;
  font-size: 13px;
  font-weight: 700;
  padding: 8px 10px;
  border-radius: 8px;
  word-break: break-all;
  cursor: copy;
}
.artist-name:hover {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
}
.artist-groups {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.group-tag {
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--soft);
  color: var(--accent-deep);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  user-select: none;
}
.group-tag:hover {
  background: rgba(157, 44, 44, 0.2);
  color: #9d2c2c;
}
.artist-actions {
  display: flex;
  gap: 6px;
  margin-top: auto;
}

.fav-overlay {
  z-index: 10000;
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 24px;
}
.fav-modal {
  width: 480px;
  max-width: 92vw;
  background: rgba(255, 250, 243, 0.98);
  border: 1px solid var(--line);
  border-radius: 18px;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.3);
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.fav-modal-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.fav-modal-head h3 {
  margin: 0;
  color: var(--accent-deep);
  font-size: 17px;
}
.fav-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
}
.fav-textarea {
  width: 100%;
  height: 80px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #fff;
  color: var(--ink);
  font-size: 13px;
  resize: vertical;
}
.fav-checkbox-list {
  max-height: 240px;
  overflow-y: auto;
  padding: 8px 10px;
  background: rgba(255, 255, 255, 0.55);
  border: 1px solid var(--line);
  border-radius: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.fav-checkbox-row {
  display: flex;
  gap: 8px;
  align-items: center;
  cursor: pointer;
  font-size: 13px;
  color: var(--ink);
}
.fav-checkbox-row input[type="checkbox"] {
  width: auto;
  margin: 0;
}
.fav-modal-foot {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.toast-overlay {
  position: fixed;
  top: 24px;
  left: 50%;
  transform: translateX(-50%);
  padding: 12px 24px;
  border-radius: 999px;
  font-weight: 700;
  font-size: 14px;
  z-index: 10100;
  box-shadow: 0 8px 24px rgba(0,0,0,0.15);
  pointer-events: none;
}
.toast-overlay.success { background: rgba(212, 237, 218, 0.95); color: #155724; border: 1px solid #c3e6cb; }
.toast-overlay.error { background: rgba(248, 215, 218, 0.95); color: #721c24; border: 1px solid #f5c6cb; }
.toast-overlay.info { background: rgba(209, 236, 241, 0.95); color: #0c5460; border: 1px solid #bee5eb; }

@media (max-width: 980px) {
  .favorites-layout { grid-template-columns: 1fr; }
  .favorites-side { position: static; max-height: none; }
}
</style>
