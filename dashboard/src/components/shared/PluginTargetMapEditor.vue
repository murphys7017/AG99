<template>
  <div class="target-map-summary">
    <div class="target-map-chips">
      <span v-if="summaryEntries.length === 0" class="text-medium-emphasis">
        {{ tm('runtimeTargetEditor.empty') }}
      </span>
      <v-chip
        v-for="([scope, target]) in summaryEntries.slice(0, maxSummaryItems)"
        :key="scope"
        size="small"
        label
        color="primary"
        variant="tonal"
      >
        {{ scope }}: {{ targetLabel(target) }}
      </v-chip>
      <v-chip v-if="summaryEntries.length > maxSummaryItems" size="small" label>
        +{{ summaryEntries.length - maxSummaryItems }}
      </v-chip>
    </div>
    <v-btn size="small" color="primary" variant="tonal" @click="openDialog">
      {{ tm('runtimeTargetEditor.configure') }}
    </v-btn>
  </div>

  <v-dialog v-model="dialog" max-width="820px">
    <v-card>
      <v-card-title class="text-h3 py-4" style="font-weight: normal;">
        {{ dialogTitle }}
      </v-card-title>
      <v-card-text class="pa-4">
        <v-alert type="info" variant="tonal" density="compact" class="mb-4">
          {{ helpText }}
        </v-alert>
        <v-alert v-if="loadFailed" type="warning" variant="tonal" density="compact" class="mb-4">
          {{ tm('runtimeTargetEditor.loadFailed') }}
        </v-alert>
        <v-progress-linear v-if="loading" indeterminate color="primary" class="mb-4" />

        <div v-if="draftEntries.length === 0" class="text-medium-emphasis py-4 text-center">
          {{ tm('runtimeTargetEditor.empty') }}
        </div>

        <v-row
          v-for="(entry, index) in draftEntries"
          :key="entry.id"
          align="start"
          class="target-map-row"
        >
          <v-col cols="12" md="7">
            <v-combobox
              v-model="entry.scope"
              :items="scopeOptions"
              item-title="title"
              item-value="value"
              :return-object="false"
              :label="scopeLabel"
              :error-messages="entryError(index)"
              density="compact"
              variant="outlined"
              clearable
            />
          </v-col>
          <v-col cols="10" md="4">
            <v-select
              v-model="entry.target"
              :items="targetOptions"
              item-title="title"
              item-value="value"
              :label="tm('runtimeTargetEditor.targetLabel')"
              density="compact"
              variant="outlined"
            />
          </v-col>
          <v-col cols="2" md="1" class="d-flex justify-end">
            <v-btn
              icon="mdi-delete-outline"
              color="error"
              variant="text"
              size="small"
              :aria-label="tm('runtimeTargetEditor.remove')"
              @click="removeEntry(index)"
            />
          </v-col>
        </v-row>

        <v-btn prepend-icon="mdi-plus" variant="tonal" color="primary" @click="addEntry">
          {{ tm('runtimeTargetEditor.add') }}
        </v-btn>
      </v-card-text>
      <v-card-actions class="pa-4">
        <v-spacer />
        <v-btn variant="text" @click="dialog = false">
          {{ tm('runtimeTargetEditor.cancel') }}
        </v-btn>
        <v-btn color="primary" :disabled="hasErrors" @click="saveEntries">
          {{ tm('runtimeTargetEditor.save') }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
import axios from 'axios'
import { computed, ref } from 'vue'
import { useModuleI18n } from '@/i18n/composables'

const props = defineProps({
  modelValue: {
    type: Object,
    default: () => ({})
  },
  mode: {
    type: String,
    default: 'plugin'
  },
  maxSummaryItems: {
    type: Number,
    default: 3
  }
})

const emit = defineEmits(['update:modelValue'])
const { tm } = useModuleI18n('core.shared')
const dialog = ref(false)
const loading = ref(false)
const loadFailed = ref(false)
const draftEntries = ref([])
const scopeOptions = ref([])
let nextEntryId = 0

const validTargets = new Set(['core', 'personal_expression'])
const normalizedModelValue = computed(() => {
  if (!props.modelValue || Array.isArray(props.modelValue) || typeof props.modelValue !== 'object') {
    return {}
  }
  return props.modelValue
})
const summaryEntries = computed(() => Object.entries(normalizedModelValue.value))
const targetOptions = computed(() => [
  { title: tm('runtimeTargetEditor.targetCore'), value: 'core' },
  { title: tm('runtimeTargetEditor.targetPersona'), value: 'personal_expression' }
])
const dialogTitle = computed(() => tm(
  props.mode === 'tool'
    ? 'runtimeTargetEditor.toolDialogTitle'
    : 'runtimeTargetEditor.pluginDialogTitle'
))
const helpText = computed(() => tm(
  props.mode === 'tool'
    ? 'runtimeTargetEditor.toolHelp'
    : 'runtimeTargetEditor.pluginHelp'
))
const scopeLabel = computed(() => tm(
  props.mode === 'tool'
    ? 'runtimeTargetEditor.toolScopeLabel'
    : 'runtimeTargetEditor.pluginScopeLabel'
))
const hasErrors = computed(() => draftEntries.value.some((_, index) => Boolean(entryError(index))))

function targetLabel(target) {
  if (target === 'personal_expression') {
    return tm('runtimeTargetEditor.targetPersona')
  }
  if (target === 'core') {
    return tm('runtimeTargetEditor.targetCore')
  }
  return String(target || '')
}

function newEntry(scope = '', target = null) {
  nextEntryId += 1
  return {
    id: nextEntryId,
    scope,
    target: target || (props.mode === 'tool' ? 'personal_expression' : 'core')
  }
}

function entryError(index) {
  const scope = String(draftEntries.value[index]?.scope || '').trim()
  const target = draftEntries.value[index]?.target
  if (!scope) {
    return tm('runtimeTargetEditor.scopeRequired')
  }
  const duplicateCount = draftEntries.value.filter(
    entry => String(entry.scope || '').trim() === scope
  ).length
  if (duplicateCount > 1) {
    return tm('runtimeTargetEditor.scopeDuplicate')
  }
  if (!validTargets.has(target)) {
    return tm('runtimeTargetEditor.targetInvalid')
  }
  return ''
}

async function openDialog() {
  draftEntries.value = Object.entries(normalizedModelValue.value).map(
    ([scope, target]) => newEntry(scope, target)
  )
  dialog.value = true
  await loadScopeOptions()
}

async function loadScopeOptions() {
  loading.value = true
  loadFailed.value = false
  const requests = [axios.get('/api/plugin/get')]
  if (props.mode === 'tool') {
    requests.push(axios.get('/api/tools/list'))
  }

  const results = await Promise.allSettled(requests)
  const options = new Map()
  const pluginResult = results[0]
  if (pluginResult.status === 'fulfilled' && pluginResult.value.data?.status === 'ok') {
    for (const plugin of pluginResult.value.data.data || []) {
      if (!plugin?.name) continue
      const displayName = plugin.display_name || plugin.name
      options.set(plugin.name, {
        title: displayName === plugin.name ? plugin.name : `${displayName} (${plugin.name})`,
        value: plugin.name
      })
    }
  } else {
    loadFailed.value = true
  }

  const toolResult = results[1]
  if (props.mode === 'tool' && toolResult) {
    if (toolResult.status === 'fulfilled' && toolResult.value.data?.status === 'ok') {
      for (const tool of toolResult.value.data.data || []) {
        if (tool?.origin !== 'plugin' || !tool.origin_name || !tool.name) continue
        const value = `${tool.origin_name}.${tool.name}`
        options.set(value, {
          title: `${tool.origin_name} / ${tool.name}`,
          value
        })
      }
    } else {
      loadFailed.value = true
    }
  }

  scopeOptions.value = [...options.values()].sort((a, b) => a.title.localeCompare(b.title))
  loading.value = false
}

function addEntry() {
  draftEntries.value.push(newEntry())
}

function removeEntry(index) {
  draftEntries.value.splice(index, 1)
}

function saveEntries() {
  if (hasErrors.value) return
  const value = {}
  for (const entry of draftEntries.value) {
    value[String(entry.scope).trim()] = entry.target
  }
  emit('update:modelValue', value)
  dialog.value = false
}
</script>

<style scoped>
.target-map-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.target-map-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.target-map-row {
  margin-bottom: 2px;
}
</style>
