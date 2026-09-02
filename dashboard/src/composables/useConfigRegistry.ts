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

interface ConfigSectionRegistryEntry extends ConfigGroupRegistryEntry {
  group: string;
  section: string;
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
  // ext_group contains built-in system behaviors, not installable plugins.
  ext_group: { workspace: 'operations', scope: 'system', order: 15 },
  plugin_group: { workspace: 'capabilities', scope: 'system', order: 20 },
  memory_group: { workspace: 'knowledge', scope: 'profile', order: 10 },
  interaction_middleware_group: { workspace: 'operations', scope: 'system', order: 10 },
  system_group: { workspace: 'operations', scope: 'system', order: 20 },
  misc_config_group: { workspace: 'operations', scope: 'system', order: 30 },
};

const CONFIG_SECTION_REGISTRY: Record<string, ConfigSectionRegistryEntry> = {
  'ai_group.agent_runner': { group: 'ai_group', section: 'agent_runner', workspace: 'intelligence', scope: 'profile', order: 10 },
  'ai_group.ai': { group: 'ai_group', section: 'ai', workspace: 'intelligence', scope: 'profile', order: 20 },
  'ai_group.persona': { group: 'ai_group', section: 'persona', workspace: 'persona', scope: 'persona', order: 10 },
  'ai_group.knowledgebase': { group: 'ai_group', section: 'knowledgebase', workspace: 'knowledge', scope: 'profile', order: 10 },
  'ai_group.websearch': { group: 'ai_group', section: 'websearch', workspace: 'capabilities', scope: 'profile', order: 10 },
  'ai_group.file_extract': { group: 'ai_group', section: 'file_extract', workspace: 'knowledge', scope: 'profile', order: 20 },
  'ai_group.agent_computer_use': { group: 'ai_group', section: 'agent_computer_use', workspace: 'capabilities', scope: 'system', order: 20 },
  'ai_group.proactive_capability': { group: 'ai_group', section: 'proactive_capability', workspace: 'automation', scope: 'persona', order: 10 },
  'ai_group.truncate_and_compress': { group: 'ai_group', section: 'truncate_and_compress', workspace: 'intelligence', scope: 'profile', order: 30 },
  'ai_group.others': { group: 'ai_group', section: 'others', workspace: 'intelligence', scope: 'profile', order: 90 },
  'ext_group.segmented_reply': { group: 'ext_group', section: 'segmented_reply', workspace: 'operations', scope: 'system', order: 15 },
  'ext_group.ltm': { group: 'ext_group', section: 'ltm', workspace: 'knowledge', scope: 'profile', order: 30 },
  'interaction_middleware_group.general': { group: 'interaction_middleware_group', section: 'general', workspace: 'operations', scope: 'system', order: 10 },
  'interaction_middleware_group.expression': { group: 'interaction_middleware_group', section: 'expression', workspace: 'persona', scope: 'persona', order: 20 },
  'interaction_middleware_group.router': { group: 'interaction_middleware_group', section: 'router', workspace: 'intelligence', scope: 'profile', order: 40 },
  'interaction_middleware_group.planner': { group: 'interaction_middleware_group', section: 'planner', workspace: 'intelligence', scope: 'profile', order: 50 },
  'interaction_middleware_group.personal_policy': { group: 'interaction_middleware_group', section: 'personal_policy', workspace: 'persona', scope: 'persona', order: 30 },
  'interaction_middleware_group.personal_runtime_policy': { group: 'interaction_middleware_group', section: 'personal_runtime_policy', workspace: 'automation', scope: 'persona', order: 20 },
  'interaction_middleware_group.stream': { group: 'interaction_middleware_group', section: 'stream', workspace: 'operations', scope: 'system', order: 20 },
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

function getSectionRegistryEntry(
  groupKey: string,
  sectionKey: string,
  configType = 'normal',
): ConfigGroupRegistryEntry {
  if (configType === 'extension' && EXTENSION_ONLY_GROUPS.has(groupKey)) {
    return {
      workspace: 'operations',
      scope: 'system',
      order: getGroupRegistryEntry(groupKey).order,
    };
  }
  return CONFIG_SECTION_REGISTRY[`${groupKey}.${sectionKey}`] || getGroupRegistryEntry(groupKey);
}

function normalizeGroupValue(
  groupKey: string,
  groupValue: any,
  registry: ConfigGroupRegistryEntry,
  index: number,
  sectionKey?: string,
) {
  const baseValue = isPlainObject(groupValue) ? { ...groupValue } : {};
  const groupOrder = typeof baseValue.order === 'number' ? baseValue.order : registry.order + index;

  return {
    ...baseValue,
    key: groupKey,
    workspace: registry.workspace,
    scope: registry.scope,
    order: groupOrder,
    legacyGroup: sectionKey ? groupKey.split('__')[0] : groupKey,
    ...(sectionKey ? { legacySection: sectionKey } : {}),
  };
}

function splitGroupIntoWorkspaceSections(groupKey: string, groupValue: any, configType = 'normal') {
  const baseValue = isPlainObject(groupValue) ? groupValue : {};
  const metadata = isPlainObject(baseValue.metadata) ? baseValue.metadata : {};
  const sectionEntries = Object.entries(metadata);
  const hasSectionMappings = sectionEntries.some(([sectionKey]) => (
    Boolean(CONFIG_SECTION_REGISTRY[`${groupKey}.${sectionKey}`])
  ));

  if (!hasSectionMappings) {
    return [{
      key: groupKey,
      value: groupValue,
      registry: getSectionRegistryEntry(groupKey, '', configType),
      sectionKey: undefined,
    }];
  }

  return sectionEntries.map(([sectionKey, sectionValue]) => {
    const registry = getSectionRegistryEntry(groupKey, sectionKey, configType);
    const sectionName = isPlainObject(sectionValue) && sectionValue.description
      ? sectionValue.description
      : baseValue.name;

    return {
      key: `${groupKey}__${sectionKey}`,
      value: {
        ...baseValue,
        name: sectionName,
        metadata: { [sectionKey]: sectionValue },
      },
      registry,
      sectionKey,
    };
  });
}

export function normalizeConfigMetadata(metadata: Record<string, any> = {}, configType = 'normal') {
  return Object.entries(metadata || {})
    .filter(([groupKey]) => shouldKeepGroup(groupKey, configType))
    .flatMap(([groupKey, groupValue], groupIndex) => (
      splitGroupIntoWorkspaceSections(groupKey, groupValue, configType).map((entry, sectionIndex) => ({
        ...entry,
        index: groupIndex * 100 + sectionIndex,
      }))
    ))
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
      acc[item.key] = normalizeGroupValue(
        item.key,
        item.value,
        item.registry,
        normalizedIndex,
        item.sectionKey,
      );
      return acc;
    }, {} as Record<string, any>);
}

export function getConfigGroupWorkspace(groupKey: string): ConfigWorkspace {
  return getGroupRegistryEntry(groupKey).workspace;
}

export function getConfigGroupScope(groupKey: string): ConfigScope {
  return getGroupRegistryEntry(groupKey).scope;
}
