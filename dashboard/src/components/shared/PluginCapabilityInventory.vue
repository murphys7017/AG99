<template>
  <div class="capability-inventory">
    <div class="d-flex align-center mb-2">
      <div>
        <div class="text-subtitle-1 font-weight-medium">
          {{ tm('runtimeTargetEditor.capabilityInventory.title') }}
        </div>
        <div class="text-caption text-medium-emphasis">
          {{ tm('runtimeTargetEditor.capabilityInventory.subtitle') }}
        </div>
      </div>
      <v-spacer />
      <v-btn
        size="small"
        variant="text"
        icon="mdi-refresh"
        :loading="loading"
        :aria-label="tm('runtimeTargetEditor.capabilityInventory.title')"
        @click="load"
      />
    </div>

    <v-alert
      v-if="loadFailed"
      type="warning"
      variant="tonal"
      density="compact"
      class="mb-3"
    >
      {{ tm('runtimeTargetEditor.capabilityInventory.loadFailed') }}
    </v-alert>

    <v-alert type="info" variant="tonal" density="compact" class="mb-3">
      {{ tm('runtimeTargetEditor.capabilityInventory.editableHint') }}
    </v-alert>

    <v-progress-linear v-if="loading" indeterminate color="primary" class="mb-3" />

    <div
      v-if="!loading && interactionCapabilities.length === 0"
      class="text-medium-emphasis py-3"
    >
      {{ tm('runtimeTargetEditor.capabilityInventory.empty') }}
    </div>

    <v-table v-if="interactionCapabilities.length > 0" density="compact">
      <thead>
        <tr>
          <th>{{ tm('runtimeTargetEditor.capabilityInventory.kind') }}</th>
          <th>{{ tm('runtimeTargetEditor.capabilityInventory.target') }}</th>
          <th>{{ tm('runtimeTargetEditor.capabilityInventory.permission') }}</th>
          <th>{{ tm('runtimeTargetEditor.capabilityInventory.hardness') }}</th>
          <th>{{ tm('runtimeTargetEditor.capabilityInventory.owner') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="cap in interactionCapabilities"
          :key="capabilityKey(cap)"
        >
          <td>
            <div>{{ cap.kind }}</div>
            <div class="text-caption text-medium-emphasis">
              {{ cap.plugin_id || cap.item_name }}
            </div>
          </td>
          <td>
            <span>{{ targetLabel(cap) }}</span>
            <v-chip
              v-if="!cap.target_editable"
              size="x-small"
              class="ml-2"
              variant="tonal"
            >
              {{ tm('runtimeTargetEditor.capabilityInventory.targetFixed') }}
            </v-chip>
          </td>
          <td>
            <v-chip
              size="small"
              :color="cap.permission_state === 'allowed' ? 'success' : 'warning'"
              variant="tonal"
            >
              {{ cap.permission_state }}
            </v-chip>
          </td>
          <td>{{ hardnessLabel(cap) }}</td>
          <td class="text-caption">{{ cap.owner_module_path }}</td>
        </tr>
      </tbody>
    </v-table>

    <template v-if="processCapabilities.length > 0">
      <div class="text-subtitle-2 font-weight-medium mt-4">
        {{ tm('runtimeTargetEditor.capabilityInventory.scopeProcess') }}
      </div>
      <div class="text-caption text-medium-emphasis mb-2">
        {{ tm('runtimeTargetEditor.capabilityInventory.scopeProcessHint') }}
      </div>
      <v-table density="compact">
        <thead>
          <tr>
            <th>{{ tm('runtimeTargetEditor.capabilityInventory.kind') }}</th>
            <th>{{ tm('runtimeTargetEditor.capabilityInventory.migration') }}</th>
            <th>{{ tm('runtimeTargetEditor.capabilityInventory.owner') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="cap in processCapabilities"
            :key="capabilityKey(cap)"
          >
            <td>{{ cap.kind }}</td>
            <td>{{ cap.migration_state }}</td>
            <td class="text-caption">{{ cap.owner_module_path }}</td>
          </tr>
        </tbody>
      </v-table>
    </template>
  </div>
</template>

<script setup>
import axios from 'axios'
import { computed, onMounted, ref } from 'vue'
import { useModuleI18n } from '@/i18n/composables'

const props = defineProps({
  /**
   * Display-only: the inventory API reports every plugin at once, so this is
   * used to highlight the current plugin rather than to filter the request.
   */
  pluginName: {
    type: String,
    default: ''
  }
})

const { tm } = useModuleI18n('core.shared')

const loading = ref(false)
const loadFailed = ref(false)
const capabilities = ref([])

const visibleCapabilities = computed(() => {
  if (!props.pluginName) {
    return capabilities.value
  }
  return capabilities.value.filter(
    cap => !cap.owner_plugin_name || cap.owner_plugin_name === props.pluginName
  )
})

const interactionCapabilities = computed(() =>
  visibleCapabilities.value.filter(cap => cap.scope === 'interaction')
)
const processCapabilities = computed(() =>
  visibleCapabilities.value.filter(cap => cap.scope === 'process')
)

function capabilityKey(cap) {
  // Handler / Hook / Tool rows have no plugin_id, so identity must include the
  // owner module and the item name; otherwise several rows of one kind collide.
  return [
    cap.owner_module_path || cap.owner_plugin_name || '',
    cap.kind,
    cap.plugin_id || '',
    cap.item_name || ''
  ].join('|')
}

function targetLabel(cap) {
  if (!cap.target) {
    return 'meta.targets'
  }
  if (cap.target_reason === 'fixed_by_contract') {
    return `${cap.target}`
  }
  if (cap.target_reason === 'configuration') {
    return `${cap.target} · ${tm('runtimeTargetEditor.capabilityInventory.reasonRuntimeTargets')}`
  }
  if (cap.target_reason === 'process_lifecycle') {
    return `${cap.target} · ${tm('runtimeTargetEditor.capabilityInventory.reasonProcess')}`
  }
  return cap.target
}

function hardnessLabel(cap) {
  return cap.hard_or_soft === 'hard'
    ? tm('runtimeTargetEditor.capabilityInventory.hard')
    : tm('runtimeTargetEditor.capabilityInventory.soft')
}

async function load() {
  loading.value = true
  loadFailed.value = false
  try {
    const response = await axios.get('/api/plugin/capabilities')
    if (response.data?.status === 'ok') {
      const plugins = response.data.data?.plugins || []
      capabilities.value = plugins.flatMap(plugin =>
        (plugin.capabilities || []).map(cap => ({
          ...cap,
          owner_plugin_name: plugin.plugin_name
        }))
      )
    } else {
      loadFailed.value = true
    }
  } catch (error) {
    loadFailed.value = true
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.capability-inventory {
  width: 100%;
}
</style>
