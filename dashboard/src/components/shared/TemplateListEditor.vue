<template>
  <div class="template-list-editor">
    <div class="d-flex align-center ga-2">
      <v-select v-model="selectedEntryIndex" :items="entryOptions" item-title="title" item-value="value" :label="selectLabel" :placeholder="emptyHintText" :disabled="entryOptions.length === 0" density="compact" variant="outlined" hide-details class="flex-grow-1" />
      <v-btn icon="mdi-pencil-outline" variant="text" size="small" :disabled="selectedEntryIndex === null" :title="editButtonText" :aria-label="editButtonText" @click="openEditDialog" />
      <v-btn icon="mdi-delete-outline" color="error" variant="text" size="small" :disabled="selectedEntryIndex === null" :title="deleteButtonText" :aria-label="deleteButtonText" @click="removeSelectedEntry" />
      <v-menu transition="fade-transition">
        <template #activator="{ props: menuProps }">
          <v-btn icon="mdi-plus" color="primary" variant="tonal" size="small" :title="addButtonText" :aria-label="addButtonText" v-bind="menuProps" />
        </template>
        <v-list density="compact">
          <v-list-item v-for="option in templateOptions" :key="option.value" @click="openCreateDialog(option.value)">
            <v-list-item-title>{{ option.label }}</v-list-item-title>
            <v-list-item-subtitle v-if="option.hint">{{ option.hint }}</v-list-item-subtitle>
          </v-list-item>
        </v-list>
      </v-menu>
    </div>

    <v-dialog v-model="editorOpen" max-width="960px" scrollable>
      <v-card>
        <v-card-title class="text-h6 d-flex align-center">
          {{ dialogTitle }}
          <v-spacer />
          <v-btn icon="mdi-close" variant="text" :aria-label="cancelButtonText" @click="closeEditor" />
        </v-card-title>
        <v-divider />
        <v-card-text class="pa-5">
          <v-alert v-if="!activeTemplate" type="error" variant="tonal" density="compact">{{ missingTemplateText }}</v-alert>
          <div v-else class="template-entry-body">
            <template v-for="(itemMeta, itemKey, metaIndex) in activeTemplate.items" :key="itemKey">
              <div v-if="itemMeta?.type === 'object' && !itemMeta?.invisible && shouldShowItem(itemMeta, draftEntry)" class="nested-container">
                <div class="config-section">
                  <div class="config-title">{{ templateItemText(activeTemplateKey, itemKey, 'description', itemMeta?.description) || itemKey }}</div>
                  <div v-if="itemMeta?.hint" class="config-hint">{{ templateItemText(activeTemplateKey, itemKey, 'hint', itemMeta.hint) }}</div>
                </div>
                <template v-for="(childMeta, childKey, childIndex) in itemMeta.items" :key="childKey">
                  <template v-if="!childMeta?.invisible && shouldShowItem(childMeta, draftEntry)">
                    <v-row class="config-row">
                      <v-col cols="12" md="5" class="property-info">
                        <div class="property-name">{{ templateItemText(activeTemplateKey, `${itemKey}.${childKey}`, 'description', childMeta?.description) || childKey }}</div>
                        <div v-if="childMeta?.hint" class="property-hint">{{ templateItemText(activeTemplateKey, `${itemKey}.${childKey}`, 'hint', childMeta.hint) }}</div>
                      </v-col>
                      <v-col cols="12" md="7" class="config-input">
                        <ConfigItemRenderer v-model="draftEntry[itemKey][childKey]" :item-meta="childMeta" :plugin-name="pluginName" :plugin-i18n="pluginI18n" :config-key="templateItemPath(activeTemplateKey, `${itemKey}.${childKey}`)" />
                      </v-col>
                    </v-row>
                    <v-divider v-if="hasVisibleItemsAfter(Object.entries(itemMeta.items), childIndex, draftEntry)" />
                  </template>
                </template>
              </div>

              <template v-else-if="!itemMeta?.invisible && shouldShowItem(itemMeta, draftEntry)">
                <v-row class="config-row">
                  <v-col cols="12" md="5" class="property-info">
                    <div class="property-name">{{ templateItemText(activeTemplateKey, itemKey, 'description', itemMeta?.description) || itemKey }} <span v-if="itemMeta?.description" class="property-key">({{ itemKey }})</span></div>
                    <div v-if="itemMeta?.hint" class="property-hint">{{ templateItemText(activeTemplateKey, itemKey, 'hint', itemMeta.hint) }}</div>
                  </v-col>
                  <v-col cols="12" md="7" class="config-input">
                    <ConfigItemRenderer v-model="draftEntry[itemKey]" :item-meta="itemMeta" :plugin-name="pluginName" :plugin-i18n="pluginI18n" :config-key="templateItemPath(activeTemplateKey, itemKey)" />
                  </v-col>
                </v-row>
                <v-divider v-if="hasVisibleItemsAfter(Object.entries(activeTemplate.items), metaIndex, draftEntry)" />
              </template>
            </template>
          </div>
        </v-card-text>
        <v-divider />
        <v-card-actions class="pa-4"><v-spacer /><v-btn variant="text" @click="closeEditor">{{ cancelButtonText }}</v-btn><v-btn color="primary" variant="flat" @click="saveEditor">{{ saveButtonText }}</v-btn></v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import ConfigItemRenderer from './ConfigItemRenderer.vue'
import { useI18n } from '@/i18n/composables'
import { useConfigTextResolver } from '@/composables/useConfigTextResolver'

const props = defineProps({
  modelValue: { type: Array, default: () => [] }, templates: { type: Object, default: () => ({}) },
  pluginName: { type: String, default: '' }, pluginI18n: { type: Object, default: () => ({}) }, configPath: { type: String, default: '' }
})
const emit = defineEmits(['update:modelValue'])
const { t } = useI18n()
const { resolveConfigText } = useConfigTextResolver(props)
const selectedEntryIndex = ref(null)
const editorOpen = ref(false)
const editingIndex = ref(null)
const draftEntry = ref({})

const texts = { addEntry: '添加条目', empty: '暂无条目，请选择模板添加', unknownTemplate: '未指定模板', selectEntry: '选择条目', editEntry: '编辑条目', deleteEntry: '删除条目', createEntry: '添加条目', save: '保存', cancel: '取消', missingTemplate: '找不到对应模板，请删除后重新添加。' }
const translatedText = (key) => { const value = t(`core.common.templateList.${key}`); return value && value !== `core.common.templateList.${key}` ? value : texts[key] }
const addButtonText = computed(() => translatedText('addEntry'))
const emptyHintText = computed(() => translatedText('empty'))
const selectLabel = computed(() => translatedText('selectEntry'))
const editButtonText = computed(() => translatedText('editEntry'))
const deleteButtonText = computed(() => translatedText('deleteEntry'))
const saveButtonText = computed(() => translatedText('save'))
const cancelButtonText = computed(() => translatedText('cancel'))
const missingTemplateText = computed(() => translatedText('missingTemplate'))
const dialogTitle = computed(() => editingIndex.value === null ? translatedText('createEntry') : translatedText('editEntry'))
const defaultValueMap = { int: 0, float: 0, bool: false, string: '', text: '', list: [], file: [], object: {}, dict: {}, template_list: [] }
const templateOptions = computed(() => Object.entries(props.templates || {}).map(([value, meta]) => ({ label: templateText(value, 'name', meta?.name || value), value, hint: templateText(value, 'hint', meta?.hint || meta?.description || '') })))
const entryOptions = computed(() => (props.modelValue || []).map((entry, index) => ({ value: index, title: entrySummary(entry, index) })))
const activeTemplateKey = computed(() => draftEntry.value?.__template_key || '')
const activeTemplate = computed(() => props.templates?.[activeTemplateKey.value] || null)

watch(entryOptions, (options) => {
  if (selectedEntryIndex.value !== null && !options.some(option => option.value === selectedEntryIndex.value)) selectedEntryIndex.value = options.length ? 0 : null
  if (selectedEntryIndex.value === null && options.length === 1) selectedEntryIndex.value = 0
}, { immediate: true })

function templatePath(templateKey) { return props.configPath ? `${props.configPath}.templates.${templateKey}` : `templates.${templateKey}` }
function templateItemPath(templateKey, itemPath) { return `${templatePath(templateKey)}.${itemPath}` }
function templateText(templateKey, attr, fallback) { return resolveConfigText(templatePath(templateKey), attr, fallback) }
function templateItemText(templateKey, itemPath, attr, fallback) { return resolveConfigText(templateItemPath(templateKey, itemPath), attr, fallback) }
function templateLabel(key) { return key ? templateText(key, 'name', props.templates?.[key]?.name || key) : translatedText('unknownTemplate') }
function clone(value) { return value === undefined ? undefined : JSON.parse(JSON.stringify(value)) }
function resolveTemplateKey(entry) {
  if (entry?.__template_key && props.templates?.[entry.__template_key]) return entry.__template_key
  const keys = Object.keys(props.templates || {})
  return keys.length === 1 ? keys[0] : ''
}
function buildDefaults(itemsMeta = {}) {
  const result = {}
  for (const [key, meta] of Object.entries(itemsMeta)) {
    if (!meta?.type) continue
    const fallback = Object.prototype.hasOwnProperty.call(meta, 'default') ? meta.default : defaultValueMap[meta.type]
    result[key] = meta.type === 'object' ? buildDefaults(meta.items || {}) : clone(fallback)
  }
  return result
}
function applyDefaults(target, itemsMeta = {}) {
  if (!target || typeof target !== 'object') return target
  for (const [key, meta] of Object.entries(itemsMeta)) {
    if (!meta?.type) continue
    if (meta.type === 'object') {
      if (!target[key] || typeof target[key] !== 'object' || Array.isArray(target[key])) target[key] = {}
      applyDefaults(target[key], meta.items || {})
      continue
    }
    if (!(key in target)) {
      const fallback = Object.prototype.hasOwnProperty.call(meta, 'default') ? meta.default : defaultValueMap[meta.type]
      target[key] = clone(fallback)
    }
  }
  return target
}
function openCreateDialog(templateKey) {
  const template = props.templates?.[templateKey]
  if (!template) return
  editingIndex.value = null
  draftEntry.value = { __template_key: templateKey, ...buildDefaults(template.items || {}) }
  editorOpen.value = true
}
function openEditDialog() {
  const index = selectedEntryIndex.value
  if (index === null || !props.modelValue?.[index]) return
  editingIndex.value = index
  draftEntry.value = clone(props.modelValue[index])
  const templateKey = resolveTemplateKey(draftEntry.value)
  if (templateKey) draftEntry.value.__template_key = templateKey
  const template = props.templates?.[templateKey]
  if (template?.items) applyDefaults(draftEntry.value, template.items)
  editorOpen.value = true
}
function closeEditor() { editorOpen.value = false; editingIndex.value = null; draftEntry.value = {} }
function saveEditor() {
  if (!activeTemplate.value) return
  const next = [...(props.modelValue || [])]
  if (editingIndex.value === null) { next.push(clone(draftEntry.value)); selectedEntryIndex.value = next.length - 1 } else next[editingIndex.value] = clone(draftEntry.value)
  emit('update:modelValue', next)
  closeEditor()
}
function removeSelectedEntry() {
  const index = selectedEntryIndex.value
  if (index === null) return
  const next = [...(props.modelValue || [])]
  next.splice(index, 1)
  selectedEntryIndex.value = next.length ? Math.min(index, next.length - 1) : null
  emit('update:modelValue', next)
}
function getItemMetaBySelector(itemsMeta = {}, selector = '') {
  let currentItems = itemsMeta; let currentMeta = null
  const keys = selector.split('.').filter(Boolean)
  for (let index = 0; index < keys.length; index += 1) {
    currentMeta = currentItems?.[keys[index]]
    if (!currentMeta) return null
    if (index < keys.length - 1) { if (currentMeta.type !== 'object') return null; currentItems = currentMeta.items || {} }
  }
  return currentMeta
}
function getValueBySelector(obj, selector) { return selector.split('.').reduce((current, key) => current && typeof current === 'object' ? current[key] : undefined, obj) }
function entrySummary(entry, index) {
  const templateKey = resolveTemplateKey(entry)
  const template = props.templates?.[templateKey]
  if (!template) return `${index + 1}. ${templateLabel(entry?.__template_key)}`
  const displayItem = template.display_item
  const displayMeta = typeof displayItem === 'string' ? getItemMetaBySelector(template.items, displayItem) : null
  const displayValueRaw = displayMeta?.type === 'string' ? getValueBySelector(entry, displayItem) : ''
  const displayValue = typeof displayValueRaw === 'string' ? displayValueRaw.trim() : ''
  if (displayValue) return `${index + 1}. ${templateLabel(templateKey)} - ${displayValue}`
  const values = Object.entries(template.items || {}).filter(([, meta]) => meta?.type === 'string').map(([key]) => entry?.[key]).filter(value => typeof value === 'string' && value.trim()).slice(0, 3)
  return values.length ? `${index + 1}. ${templateLabel(templateKey)} - ${values.join(' / ')}` : `${index + 1}. ${templateLabel(templateKey)}`
}
function shouldShowItem(itemMeta, entry) { return !itemMeta?.condition || Object.entries(itemMeta.condition).every(([key, expected]) => getValueBySelector(entry, key) === expected) }
function hasVisibleItemsAfter(entries, currentIndex, entry) { return entries.slice(currentIndex + 1).some(([, meta]) => !meta?.invisible && shouldShowItem(meta, entry)) }
</script>

<style scoped>
.template-list-editor { width: 100%; }
.template-entry-body { max-width: 880px; margin: 0 auto; }
.config-section { margin-bottom: 12px; }
.config-title, .property-name { color: var(--v-theme-primaryText); font-weight: 600; }
.config-hint, .property-hint { color: var(--v-theme-secondaryText); font-size: 0.75rem; margin-top: 2px; }
.config-row { align-items: center; margin: 0; padding: 12px 4px; }
.property-info { padding-top: 8px; }
.config-input { padding-top: 4px; padding-bottom: 4px; }
.property-key { font-size: 0.85em; font-weight: 400; opacity: 0.7; }
.nested-container { background-color: rgba(0, 0, 0, 0.02); border: 1px solid rgba(0, 0, 0, 0.1); border-radius: 6px; margin: 12px 0; padding: 16px; }
</style>
