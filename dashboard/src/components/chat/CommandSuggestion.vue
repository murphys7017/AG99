<template>
  <div
    v-if="visible && commands.length"
    class="command-suggestion-panel"
    :class="{ 'is-dark': isDark }"
  >
    <div class="command-suggestion-list">
      <button
        v-for="(cmd, index) in commands"
        :key="`${cmd.handler_full_name}:${cmd.effective_command}`"
        type="button"
        class="command-suggestion-item"
        :class="{ active: index === selectedIndex }"
        @mousedown.prevent="selectCommand(index)"
        @mouseenter="$emit('updateSelectedIndex', index)"
      >
        <span class="command-main">
          <span class="command-name">{{ cmd.effective_command }}</span>
          <span v-if="cmd.plugin_display_name" class="command-plugin">
            {{ cmd.plugin_display_name }}
          </span>
        </span>
        <span v-if="cmd.description" class="command-description">
          {{ cmd.description }}
        </span>
      </button>
    </div>
    <div class="command-suggestion-hint">
      <span>↑↓ {{ tm("commandSuggestion.navigate") }}</span>
      <span>Enter {{ tm("commandSuggestion.select") }}</span>
      <span>Esc {{ tm("commandSuggestion.close") }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useModuleI18n } from "@/i18n/composables";

export interface SuggestionCommand {
  handler_full_name: string;
  effective_command: string;
  description: string;
  plugin_display_name: string | null;
  enabled: boolean;
  reserved: boolean;
}

const props = defineProps<{
  visible: boolean;
  commands: SuggestionCommand[];
  selectedIndex: number;
  isDark: boolean;
}>();

const emit = defineEmits<{
  select: [command: SuggestionCommand];
  updateSelectedIndex: [index: number];
}>();

const { tm } = useModuleI18n("features/chat");

function selectCommand(index: number) {
  const command = props.commands[index];
  if (command) {
    emit("select", command);
  }
}
</script>

<style scoped>
.command-suggestion-panel {
  position: absolute;
  left: 18px;
  right: 18px;
  bottom: calc(100% + 8px);
  z-index: 10;
  max-height: min(320px, 50vh);
  overflow: hidden;
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 8px;
  background: rgb(var(--v-theme-surface));
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.16);
  color: rgb(var(--v-theme-on-surface));
}

.command-suggestion-list {
  max-height: 260px;
  overflow-y: auto;
  padding: 6px;
}

.command-suggestion-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  min-height: 46px;
  padding: 8px 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.command-suggestion-item:hover,
.command-suggestion-item.active {
  background: rgba(var(--v-theme-primary), 0.1);
}

.command-main {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}

.command-name {
  min-width: 0;
  overflow: hidden;
  color: rgb(var(--v-theme-primary));
  font-family: "Fira Code", Consolas, monospace;
  font-size: 13px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.command-plugin {
  flex-shrink: 0;
  max-width: 120px;
  overflow: hidden;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgba(var(--v-theme-on-surface), 0.62);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.command-description {
  overflow: hidden;
  color: rgba(var(--v-theme-on-surface), 0.62);
  font-size: 12px;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.command-suggestion-hint {
  display: flex;
  gap: 12px;
  overflow-x: auto;
  padding: 6px 12px;
  border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 11px;
}

.command-suggestion-hint span {
  white-space: nowrap;
}

@media (max-width: 768px) {
  .command-suggestion-panel {
    left: 8px;
    right: 8px;
    max-height: 44vh;
  }
}
</style>
