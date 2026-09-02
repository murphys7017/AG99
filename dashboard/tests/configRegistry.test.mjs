import test from 'node:test';
import assert from 'node:assert/strict';

const { normalizeConfigMetadata } = await import('../src/composables/useConfigRegistry.ts');

const metadata = {
  ai_group: {
    name: 'ai_group.name',
    metadata: {
      agent_runner: {
        description: 'ai_group.agent_runner.description',
        type: 'object',
        items: {},
      },
      persona: {
        description: 'ai_group.persona.description',
        type: 'object',
        items: {},
      },
      knowledgebase: {
        description: 'ai_group.knowledgebase.description',
        type: 'object',
        items: {},
      },
      custom_extension: {
        description: 'ai_group.custom_extension.description',
        type: 'object',
        items: {},
      },
    },
  },
  platform_group: {
    name: 'platform_group.name',
    metadata: {
      general: {
        description: 'platform_group.general.description',
        type: 'object',
        items: {},
      },
    },
  },
  ext_group: {
    name: 'ext_group.name',
    metadata: {
      segmented_reply: {
        description: 'ext_group.segmented_reply.description',
        type: 'object',
        items: {},
      },
      ltm: {
        description: 'ext_group.ltm.description',
        type: 'object',
        items: {},
      },
    },
  },
  interaction_middleware_group: {
    name: 'interaction_middleware_group.name',
    metadata: {
      expression: {
        description: 'interaction_middleware_group.expression.description',
        type: 'object',
        items: {},
      },
      router: {
        description: 'interaction_middleware_group.router.description',
        type: 'object',
        items: {},
      },
    },
  },
};

test('normalizeConfigMetadata projects mapped ai sections into their workspaces', () => {
  const normalized = normalizeConfigMetadata(metadata, 'normal');

  assert.deepEqual(Object.keys(normalized), [
    'ai_group__persona',
    'ai_group__agent_runner',
    'ai_group__custom_extension',
    'platform_group',
    'ai_group__knowledgebase',
  ]);
  assert.deepEqual(normalized.ai_group__persona, {
    key: 'ai_group__persona',
    name: 'ai_group.persona.description',
    metadata: { persona: metadata.ai_group.metadata.persona },
    workspace: 'persona',
    scope: 'persona',
    order: 10,
    legacyGroup: 'ai_group',
    legacySection: 'persona',
  });
  assert.equal(normalized.ai_group__custom_extension.workspace, 'intelligence');
  assert.equal(normalized.ai_group__custom_extension.legacySection, 'custom_extension');
  assert.deepEqual(normalized.platform_group.metadata, metadata.platform_group.metadata);
  assert.equal(normalized.ext_group__ltm, undefined);
});

test('normalizeConfigMetadata keeps only extension groups for extension configuration', () => {
  const normalized = normalizeConfigMetadata(metadata, 'extension');

  assert.deepEqual(Object.keys(normalized), [
    'interaction_middleware_group__expression',
    'interaction_middleware_group__router',
    'ext_group__segmented_reply',
    'ext_group__ltm',
  ]);
  assert.equal(normalized.ext_group__ltm.workspace, 'operations');
  assert.equal(normalized.ext_group__segmented_reply.workspace, 'operations');
});

test('normalizeConfigMetadata keeps all extension sections in the system workspace', () => {
  const normalized = normalizeConfigMetadata({
    interaction_middleware_group: metadata.interaction_middleware_group,
    memory_group: {
      name: 'memory_group.name',
      metadata: {
        general: { description: 'memory_group.general.description' },
      },
    },
  }, 'extension');

  assert.equal(normalized.interaction_middleware_group__expression.workspace, 'operations');
  assert.equal(normalized.interaction_middleware_group__router.workspace, 'operations');
  assert.equal(normalized.memory_group.workspace, 'operations');
});
