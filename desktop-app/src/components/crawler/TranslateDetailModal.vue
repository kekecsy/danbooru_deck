<script setup>
// 单条角色翻译编辑弹窗。
// 状态由父组件持有（state prop 传 ref，自动 unwrap），网络操作（拉 wiki / 保存）在父组件里。
// 纯客户端的复制 / JSON 解析放在这里，省一次 round-trip。
import { computed } from 'vue';

const props = defineProps({
  state: { type: Object, required: true },
  // { open, tag, fallbackName, source:{description,other_names,exists},
  //   manualPrompt, matchedTranslationKey, pasteText, parseError, saving,
  //   fetchBusy, fetchMsg, form:{has_chinese,chinese_name,source_hint,translated_description_zh} }
});
const emit = defineEmits(['update:open', 'refresh-wiki', 'save', 'notify']);

const open = computed({
  get: () => props.state.open,
  set: (v) => emit('update:open', v),
});

function close() { open.value = false; }

async function copyManualPrompt() {
  try {
    await navigator.clipboard.writeText(props.state.manualPrompt || '');
    emit('notify', { message: 'Prompt 已复制到剪贴板', type: 'success' });
  } catch (err) {
    emit('notify', { message: '复制失败: ' + (err.message || err), type: 'error' });
  }
}

function parsePastedJson() {
  const raw = (props.state.pasteText || '').trim();
  props.state.parseError = '';
  if (!raw) {
    props.state.parseError = '请先粘贴大模型返回的 JSON';
    return;
  }
  let text = raw;
  // 兼容大模型偶尔输出的 ```json ``` 包裹
  if (text.startsWith('```json')) text = text.slice(7);
  if (text.startsWith('```')) text = text.slice(3);
  if (text.endsWith('```')) text = text.slice(0, -3);
  text = text.trim();
  try {
    const obj = JSON.parse(text);
    props.state.form = {
      has_chinese: !!obj.has_chinese,
      chinese_name: String(obj.chinese_name || ''),
      source_hint: String(obj.source_hint || '').toLowerCase(),
      translated_description_zh: String(obj.translated_description_zh || ''),
    };
    emit('notify', { message: '已解析填表', type: 'success' });
  } catch (err) {
    props.state.parseError = 'JSON 解析失败: ' + err.message + '，可手动改下方字段';
  }
}
</script>

<template>
  <div v-if="state.open" class="viewer-overlay translation-overlay" @click.self="close" style="z-index: 10010;">
    <div class="translation-card translation-detail-card">
      <div class="translation-head">
        <div style="min-width: 0;">
          <h3 style="margin: 0; color: var(--accent-deep); font-size: 17px; word-break: break-all;">{{ state.tag }}</h3>
          <p class="muted compact-text" style="margin: 4px 0 0;">回退名: {{ state.fallbackName }}</p>
        </div>
        <button class="ghost" @click="close" style="color: var(--muted);">×</button>
      </div>

      <div v-if="!state.source.exists" class="translation-fetch-banner">
        <span style="flex: 1; min-width: 0;">
          character.json 中没有这条记录。可使用下方"重新拉取 Wiki 信息"补充描述，结果会写入 character_supplement.json。
          <span v-if="state.fetchMsg" style="display: block; color: #9d2c2c; margin-top: 4px;">{{ state.fetchMsg }}</span>
        </span>
      </div>

      <div class="translation-detail-section translation-description-pinned">
        <div class="translation-detail-section-head static">
          <span>英文描述与候选名（{{ state.source.other_names.length }} 个候选）</span>
          <button
            class="secondary translation-wiki-refresh"
            @click="emit('refresh-wiki')"
            :disabled="state.fetchBusy"
            title="忽略本地缓存，重新请求 Danbooru Wiki 并覆盖增量资料"
          >{{ state.fetchBusy ? '正在拉取...' : '重新拉取 Wiki 信息' }}</button>
        </div>
        <div class="translation-detail-section-body">
          <div v-if="state.source.other_names.length" style="margin-bottom: 8px;">
            <strong style="font-size: 12px;">候选名: </strong>
            <span class="muted compact-text">{{ state.source.other_names.slice(0, 30).join(' / ') }}</span>
          </div>
          <pre class="translation-desc">{{ state.source.description || '(无描述，可点上方"重新拉取 Wiki 信息")' }}</pre>
          <p v-if="state.matchedTranslationKey && state.matchedTranslationKey !== state.tag" class="translation-inherit-note">
            当前显示继承自 {{ state.matchedTranslationKey }}。保存后会为 {{ state.tag }} 创建精确覆盖，适合修正多皮肤同名问题。
          </p>
        </div>
      </div>

      <div class="translation-detail-body">
        <div class="translation-mode-body">
          <div style="display: flex; gap: 10px; align-items: center; margin-bottom: 8px; flex-wrap: wrap;">
            <button class="secondary" @click="copyManualPrompt" style="min-width: 130px;">复制 Prompt</button>
            <span class="muted compact-text">粘贴到你的大模型，把返回的 JSON 贴到下方</span>
          </div>
          <textarea
            v-model="state.pasteText"
            placeholder='把大模型返回的 JSON 粘贴到这里，例如：{"has_chinese": true, "chinese_name": "...", ...}'
            class="translation-paste"
          ></textarea>
          <div style="display: flex; gap: 10px; align-items: center; margin-top: 6px; flex-wrap: wrap;">
            <button class="secondary" @click="parsePastedJson" style="min-width: 130px;">解析填表</button>
            <span v-if="state.parseError" class="error-text" style="margin: 0;">{{ state.parseError }}</span>
          </div>
        </div>

        <div class="translation-form">
          <label class="translation-form-row">
            <input type="checkbox" v-model="state.form.has_chinese" />
            <span>有中文名</span>
          </label>
          <label class="translation-form-field">
            <span>中文名</span>
            <input v-model="state.form.chinese_name" type="text" placeholder="例如：初音未来" />
          </label>
          <label class="translation-form-field">
            <span>source_hint（小写英文，例如 vocaloid / touhou）</span>
            <input v-model="state.form.source_hint" type="text" placeholder="例如：touhou" />
          </label>
          <label class="translation-form-field">
            <span>中文简介</span>
            <textarea
              v-model="state.form.translated_description_zh"
              placeholder="可选：角色的中文简介，会显示在画廊详情里"
              class="translation-desc-input"
            ></textarea>
          </label>
        </div>
      </div>

      <div class="translation-foot">
        <span class="muted compact-text">保存后会写入 character_chinese_search.json</span>
        <div style="display: flex; gap: 8px;">
          <button class="ghost" @click="close" style="color: var(--accent-deep);">取消</button>
          <button
            @click="emit('save')"
            :disabled="state.saving"
            style="min-width: 110px;"
          >{{ state.saving ? '保存中...' : '保存到字典' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>
