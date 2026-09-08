export const isPluginInactive = (command) =>
  command?.plugin_activated === false

export const isCommandEffectivelyEnabled = (command) =>
  Boolean(command?.enabled) && !isPluginInactive(command)
