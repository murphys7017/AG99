import assert from 'node:assert/strict';
import test from 'node:test';
import {
  isCommandEffectivelyEnabled,
  isPluginInactive
} from '../src/utils/commandState.mjs';

const command = (overrides = {}) => ({
  handler_full_name: 'demo.handler',
  handler_name: 'handler',
  plugin: 'demo',
  plugin_display_name: null,
  module_path: 'data.plugins.demo.main',
  description: '',
  type: 'command',
  parent_signature: '',
  parent_group_handler: '',
  original_command: 'demo',
  current_fragment: 'demo',
  effective_command: 'demo',
  aliases: [],
  permission: 'everyone',
  enabled: true,
  plugin_activated: true,
  is_group: false,
  has_conflict: false,
  reserved: false,
  sub_commands: [],
  ...overrides
});

test('a disabled plugin makes an otherwise enabled command effectively disabled', () => {
  assert.equal(isPluginInactive(command({ plugin_activated: false })), true);
  assert.equal(isCommandEffectivelyEnabled(command({ plugin_activated: false })), false);
  assert.equal(isCommandEffectivelyEnabled(command({ enabled: false })), false);
  assert.equal(isCommandEffectivelyEnabled(command()), true);
});

test('missing plugin activation keeps commands compatible with an older server response', () => {
  const legacyCommand = command({ plugin_activated: undefined });
  assert.equal(isPluginInactive(legacyCommand), false);
  assert.equal(isCommandEffectivelyEnabled(legacyCommand), true);
});
