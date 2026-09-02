<template>
  <v-container fluid class="workspace-landing px-6 py-5">
    <v-row class="mb-4" align="center">
      <v-col cols="12">
        <div class="text-h5 font-weight-medium">{{ pageTitle }}</div>
        <div class="text-caption text-medium-emphasis">{{ pageSubtitle }}</div>
      </v-col>
    </v-row>

    <v-row>
      <v-col
        v-for="item in shortcuts"
        :key="item.to"
        cols="12"
        sm="6"
        lg="4"
      >
        <v-card variant="outlined" class="workspace-shortcut" rounded="md">
          <v-card-text class="d-flex align-center ga-3">
            <v-avatar size="36" color="primary" variant="tonal">
              <v-icon :icon="item.icon" />
            </v-avatar>
            <div class="flex-grow-1 min-w-0">
              <div class="text-subtitle-1 font-weight-medium text-truncate">
                {{ item.label }}
              </div>
              <div v-if="item.hint" class="text-caption text-medium-emphasis text-truncate">
                {{ item.hint }}
              </div>
            </div>
            <v-btn :to="item.to" icon="mdi-arrow-right" variant="text" size="small" />
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from '@/i18n/composables';
import type { ConfigWorkspace } from '@/composables/useConfigRegistry';

type WorkspaceKey = ConfigWorkspace;

type Shortcut = {
  label: string;
  hint?: string;
  icon: string;
  to: string;
};

type ShortcutDef = {
  labelKey: string;
  hintKey?: string;
  icon: string;
  to: string;
};

const props = defineProps<{
  workspaceKey: WorkspaceKey;
}>();

const { t } = useI18n();

const WORKSPACE_SHORTCUTS: Record<WorkspaceKey, ShortcutDef[]> = {
  persona: [
    { labelKey: 'core.navigation.persona', icon: 'mdi-heart', to: '/persona' },
    { labelKey: 'core.navigation.config', hintKey: 'core.navigation.configTabs.normal', icon: 'mdi-cog', to: '/config#normal' },
  ],
  intelligence: [
    { labelKey: 'core.navigation.providers', icon: 'mdi-creation', to: '/providers' },
    { labelKey: 'core.navigation.configTabs.normal', hintKey: 'core.navigation.configTabs.system', icon: 'mdi-cog', to: '/config#normal' },
  ],
  channels: [
    { labelKey: 'core.navigation.platforms', icon: 'mdi-robot', to: '/platforms' },
  ],
  knowledge: [
    { labelKey: 'core.navigation.knowledgeBase', icon: 'mdi-book-open-variant', to: '/knowledge-base' },
  ],
  capabilities: [
    { labelKey: 'core.navigation.configTabs.extension', icon: 'mdi-tune-variant', to: '/config#extension' },
    { labelKey: 'core.navigation.extensionTabs.installed', icon: 'mdi-puzzle', to: '/extension#installed' },
    { labelKey: 'core.navigation.extensionTabs.mcp', icon: 'mdi-server-network', to: '/extension#mcp' },
    { labelKey: 'core.navigation.extensionTabs.skills', icon: 'mdi-lightning-bolt', to: '/extension#skills' },
  ],
  automation: [
    { labelKey: 'core.navigation.cron', icon: 'mdi-clock-outline', to: '/cron' },
    { labelKey: 'core.navigation.subagent', icon: 'mdi-vector-link', to: '/subagent' },
    { labelKey: 'core.navigation.sessionManagement', icon: 'mdi-pencil-ruler', to: '/session-management' },
  ],
  operations: [
    { labelKey: 'core.navigation.configTabs.system', icon: 'mdi-cog-outline', to: '/config#system' },
    { labelKey: 'core.navigation.dashboard', icon: 'mdi-view-dashboard', to: '/dashboard/default' },
    { labelKey: 'core.navigation.conversation', icon: 'mdi-database', to: '/conversation' },
    { labelKey: 'core.navigation.console', icon: 'mdi-console', to: '/console' },
    { labelKey: 'core.navigation.trace', icon: 'mdi-timeline-text-outline', to: '/trace' },
    { labelKey: 'core.navigation.settings', icon: 'mdi-cog', to: '/settings' },
  ],
};

const workspaceKey = computed<WorkspaceKey>(() => props.workspaceKey);

const pageTitle = computed(() => t('core.navigation.workspaces.' + workspaceKey.value));
const pageSubtitle = computed(() => {
  if (workspaceKey.value === 'intelligence') {
    return t('core.navigation.providers');
  }
  if (workspaceKey.value === 'persona') {
    return t('core.navigation.persona');
  }
  return pageTitle.value;
});
const shortcuts = computed<Shortcut[]>(() => WORKSPACE_SHORTCUTS[workspaceKey.value].map((shortcut) => ({
  label: t(shortcut.labelKey),
  hint: shortcut.hintKey ? t(shortcut.hintKey) : undefined,
  icon: shortcut.icon,
  to: shortcut.to
})));
</script>

<style scoped>
.workspace-landing {
  max-width: 1280px;
}

.workspace-shortcut {
  min-height: 88px;
}
</style>
