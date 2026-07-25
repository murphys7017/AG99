<template>
  <v-autocomplete
    :model-value="modelValue"
    @update:model-value="emitValue"
    :items="sessionItems"
    :loading="loading"
    :label="tm('sessionSelector.label')"
    item-title="title"
    item-value="value"
    density="compact"
    variant="outlined"
    class="config-field"
    clearable
    hide-details
    no-filter
    :multiple="multiple"
    :chips="multiple"
    @update:search="search = $event || ''"
  >
    <template #item="{ props, item }">
      <v-list-item v-bind="props" :subtitle="item.raw.subtitle">
        <template #prepend>
          <v-icon icon="mdi-message-outline" />
        </template>
      </v-list-item>
    </template>
    <template #no-data>
      <v-list-item :title="tm('sessionSelector.noSessions')" />
    </template>
    <template #append-inner>
      <v-btn
        icon="mdi-refresh"
        size="x-small"
        variant="text"
        :title="tm('sessionSelector.refresh')"
        @click.stop="loadSessions"
      />
    </template>
  </v-autocomplete>
</template>

<script setup>
import axios from 'axios'
import { computed, onMounted, ref } from 'vue'
import { useModuleI18n } from '@/i18n/composables'

const props = defineProps({
  modelValue: {
    type: [String, Array],
    default: ''
  },
  multiple: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['update:modelValue'])
const { tm } = useModuleI18n('core/shared')
const loading = ref(false)
const search = ref('')
const sessions = ref([])

function emitValue(value) {
  emit(
    'update:modelValue',
    props.multiple ? (Array.isArray(value) ? value : []) : (value || '')
  )
}

const sessionItems = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  return sessions.value
    .map((session) => {
      const displayName = session.display_name || session.auto_name || session.umo
      return {
        title: displayName,
        subtitle: `${session.platform || '-'} · ${session.message_type || '-'} · ${session.session_id || '-'}`,
        value: session.umo
      }
    })
    .filter((item) => {
      if (!keyword) return true
      return `${item.title} ${item.subtitle} ${item.value}`.toLowerCase().includes(keyword)
    })
})

async function loadSessions() {
  loading.value = true
  try {
    const response = await axios.get('/api/session/active-umos', {
      params: { proactive_only: true }
    })
    sessions.value = response.data?.status === 'ok'
      ? response.data.data?.umo_infos || []
      : []
  } catch {
    sessions.value = []
  } finally {
    loading.value = false
  }
}

onMounted(loadSessions)
</script>
