import test from 'node:test';
import assert from 'node:assert/strict';

const { default: mainRoutes } = await import('../src/router/MainRoutes.ts');

const workspaceRoute = mainRoutes.children.find(
  (route) => route.name === 'WorkspaceLanding',
);

test('workspace routes redirect to their primary task pages', () => {
  const redirect = workspaceRoute?.redirect;
  assert.equal(typeof redirect, 'function');

  const expected = {
    persona: '/persona',
    intelligence: '/providers',
    channels: '/platforms',
    knowledge: '/knowledge-base',
    capabilities: '/extension#installed',
    automation: '/cron',
    operations: '/dashboard/default',
  };

  for (const [workspaceKey, target] of Object.entries(expected)) {
    assert.equal(redirect({ params: { workspaceKey } }), target);
  }
});
