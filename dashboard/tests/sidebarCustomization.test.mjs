import test from 'node:test';
import assert from 'node:assert/strict';

import { applySidebarCustomization } from '../src/utils/sidebarCustomization.js';

const workspaceItems = [
  { title: 'core.navigation.welcome' },
  { title: 'core.navigation.workspaces.persona' },
  { title: 'core.navigation.workspaces.intelligence' },
  { title: 'core.navigation.workspaces.channels' },
  { title: 'core.navigation.workspaces.knowledge' },
  { title: 'core.navigation.workspaces.capabilities' },
  { title: 'core.navigation.workspaces.automation' },
  { title: 'core.navigation.workspaces.operations' },
  {
    title: 'core.navigation.groups.more',
    children: [{ title: 'core.navigation.about' }],
  },
];

test('applySidebarCustomization migrates legacy menu keys before persisting them', () => {
  const previousStorage = globalThis.localStorage;
  let stored = JSON.stringify({
    mainItems: [
      'core.navigation.welcome',
      'core.navigation.platforms',
      'core.navigation.providers',
      'core.navigation.config',
      'core.navigation.extension',
      'core.navigation.knowledgeBase',
      'core.navigation.persona',
    ],
    moreItems: [
      'core.navigation.conversation',
      'core.navigation.sessionManagement',
      'core.navigation.cron',
      'core.navigation.subagent',
      'core.navigation.dashboard',
      'core.navigation.console',
      'core.navigation.trace',
    ],
  });

  globalThis.localStorage = {
    getItem: () => stored,
    setItem: (_key, value) => {
      stored = value;
    },
  };

  try {
    const items = applySidebarCustomization(workspaceItems);
    const persisted = JSON.parse(stored);

    assert.deepEqual(items.map((item) => item.title), [
      'core.navigation.welcome',
      'core.navigation.workspaces.channels',
      'core.navigation.workspaces.intelligence',
      'core.navigation.workspaces.capabilities',
      'core.navigation.workspaces.knowledge',
      'core.navigation.workspaces.persona',
      'core.navigation.groups.more',
    ]);
    assert.deepEqual(persisted, {
      mainItems: [
        'core.navigation.welcome',
        'core.navigation.workspaces.channels',
        'core.navigation.workspaces.intelligence',
        'core.navigation.workspaces.capabilities',
        'core.navigation.workspaces.knowledge',
        'core.navigation.workspaces.persona',
      ],
      moreItems: [
        'core.navigation.workspaces.operations',
        'core.navigation.workspaces.automation',
      ],
    });
  } finally {
    globalThis.localStorage = previousStorage;
  }
});
