export const CONFIG_WORKSPACE_ORDER = [
  'persona',
  'intelligence',
  'channels',
  'knowledge',
  'capabilities',
  'automation',
  'operations',
] as const;

export type ConfigWorkspace = typeof CONFIG_WORKSPACE_ORDER[number];

type ConfigScope = 'system' | 'platform' | 'profile' | 'persona' | 'session';

interface ConfigGroupRegistryEntry {
  workspace: ConfigWorkspace;
  scope: ConfigScope;
  order: number;
}

const EXTENSION_ONLY_GROUPS = new Set([
  'ext_group',
  'interaction_middleware_group',
  'memory_group',
]);

const CONFIG_GROUP_REGISTRY: Record<string, ConfigGroupRegistryEntry> = {
  ai_group: { workspace: 'intelligence', scope: 'profile', order: 10 },
  provider_group: { workspace: 'intelligence', scope: 'profile', order: 20 },
  platform_group: { workspace: 'channels', scope: 'platform', order: 10 },
  ext_group: { workspace: 'capabilities', scope: 'profile', order: 10 },
  plugin_group: { workspace: 'capabilities', scope: 'system', order: 20 },
  memory_group: { workspace: 'knowledge', scope: 'profile', order: 10 },
  interaction_middleware_group: { workspace: 'operations', scope: 'system', order: 10 },
  system_group: { workspace: 'operations', scope: 'system', order: 20 },
  misc_config_group: { workspace: 'operations', scope: 'system', order: 30 },
};

function isPlainObject(value: unknown): value is Record<string, any> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function getWorkspaceRank(workspace: string): number {
  const index = CONFIG_WORKSPACE_ORDER.indexOf(workspace as ConfigWorkspace);
  return index === -1 ? CONFIG_WORKSPACE_ORDER.length : index;
}

function shouldKeepGroup(groupKey: string, configType: string): boolean {
  if (configType === 'extension') {
    return EXTENSION_ONLY_GROUPS.has(groupKey);
  }
  return !EXTENSION_ONLY_GROUPS.has(groupKey);
}

function getGroupRegistryEntry(groupKey: string): ConfigGroupRegistryEntry {
  return CONFIG_GROUP_REGISTRY[groupKey] || {
    workspace: 'operations',
    scope: 'system',
    order: 999,
  };
}

function normalizeGroupValue(groupKey: string, groupValue: any, index: number) {
  const registry = getGroupRegistryEntry(groupKey);
  const baseValue = isPlainObject(groupValue) ? { ...groupValue } : {};
  const groupOrder = typeof baseValue.order === 'number' ? baseValue.order : registry.order + index;

  return {
    ...baseValue,
    key: groupKey,
    workspace: registry.workspace,
    scope: registry.scope,
    order: groupOrder,
    legacyGroup: groupKey,
  };
}

export function normalizeConfigMetadata(metadata: Record<string, any> = {}, configType = 'normal') {
  return Object.entries(metadata || {})
    .filter(([groupKey]) => shouldKeepGroup(groupKey, configType))
    .map(([groupKey, groupValue], index) => ({
      groupKey,
      groupValue,
      index,
      registry: getGroupRegistryEntry(groupKey),
    }))
    .sort((left, right) => {
      const leftWorkspaceRank = getWorkspaceRank(left.registry.workspace);
      const rightWorkspaceRank = getWorkspaceRank(right.registry.workspace);
      if (leftWorkspaceRank !== rightWorkspaceRank) {
        return leftWorkspaceRank - rightWorkspaceRank;
      }
      if (left.registry.order !== right.registry.order) {
        return left.registry.order - right.registry.order;
      }
      return left.index - right.index;
    })
    .reduce((acc, item, normalizedIndex) => {
      acc[item.groupKey] = normalizeGroupValue(item.groupKey, item.groupValue, normalizedIndex);
      return acc;
    }, {} as Record<string, any>);
}

export function getConfigGroupWorkspace(groupKey: string): ConfigWorkspace {
  return getGroupRegistryEntry(groupKey).workspace;
}

export function getConfigGroupScope(groupKey: string): ConfigScope {
  return getGroupRegistryEntry(groupKey).scope;
}
