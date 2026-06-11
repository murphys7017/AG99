import { computed, onMounted, reactive, shallowRef, watch } from "vue";
import axios from "axios";
import type { menu } from "@/layouts/full/vertical-sidebar/sidebarItem";

const DEFAULT_ICON = "mdi-puzzle";
const GROUP_ICON = "mdi-puzzle-outline";
const GROUP_I18N_KEY = "core.navigation.pluginWebui";
const SAFE_MDI_ICON_RE = /^mdi-[a-z0-9-]+$/i;

interface PluginEntry {
  name: string;
  display_name?: string | null;
  activated: boolean;
  pages: string[];
  icon?: string | null;
}

export const pluginSidebarState = reactive<{
  plugins: PluginEntry[];
}>({
  plugins: [],
});

function normalizeMdiIcon(icon?: string | null): string {
  const candidate = (icon || "").trim();
  return SAFE_MDI_ICON_RE.test(candidate) ? candidate : DEFAULT_ICON;
}

function buildPluginItems(plugins: PluginEntry[]): menu | null {
  const children = plugins
    .filter((plugin) => (
      plugin.activated &&
      Array.isArray(plugin.pages) &&
      plugin.pages.length > 0 &&
      typeof plugin.name === "string" &&
      plugin.name.length > 0
    ))
    .map((plugin) => {
      const displayName = plugin.display_name || plugin.name || "Unknown Plugin";
      const firstPage = plugin.pages[0];
      const icon = normalizeMdiIcon(plugin.icon);

      return {
        title: displayName,
        icon,
        to: `/plugin-page/${encodeURIComponent(plugin.name)}/${encodeURIComponent(firstPage)}`,
        isRawTitle: true,
      };
    });

  if (children.length === 0) {
    return null;
  }

  return {
    title: GROUP_I18N_KEY,
    icon: GROUP_ICON,
    children,
  };
}

let initialFetched = false;

async function initPluginState() {
  if (initialFetched) return;
  initialFetched = true;
  try {
    const res = await axios.get("/api/plugin/get");
    if (res.data?.status === "ok") {
      pluginSidebarState.plugins = res.data.data ?? [];
    }
  } catch {
    // The extension page refreshes this shared state when it is opened.
  }
}

export function usePluginSidebarItems() {
  const pluginGroup = computed(() => buildPluginItems(pluginSidebarState.plugins));
  const pluginItems = shallowRef<menu | null>(null);

  function refreshItems() {
    pluginItems.value = pluginGroup.value;
  }

  onMounted(async () => {
    await initPluginState();
    refreshItems();
  });

  watch(pluginGroup, () => {
    refreshItems();
  });

  return { pluginItems };
}
