<template>
  <v-dialog v-model="visible" max-width="520">
    <v-card>
      <v-card-title class="upgrade-recovery-title">
        <span>{{ t("core.common.upgradeRecovery.title") }}</span>
      </v-card-title>

      <v-card-text>
        <p class="mb-3">
          {{
            t("core.common.upgradeRecovery.description", {
              coreVersion,
              dashboardVersion,
            })
          }}
        </p>
        <v-alert type="warning" variant="tonal" density="comfortable" class="mb-3">
          {{ t("core.common.upgradeRecovery.hint") }}
        </v-alert>
        <v-progress-linear
          v-if="restarting"
          indeterminate
          color="primary"
          class="mb-2"
        />
        <div v-if="statusMessage" class="text-medium-emphasis">
          {{ statusMessage }}
        </div>
      </v-card-text>

      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" :disabled="restarting" @click="dismiss">
          {{ t("core.common.upgradeRecovery.laterButton") }}
        </v-btn>
        <v-btn
          color="primary"
          variant="flat"
          prepend-icon="mdi-restart"
          :loading="restarting"
          @click="restartCore"
        >
          {{ t("core.common.upgradeRecovery.restartButton") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import axios from "axios";
import { onBeforeUnmount, onMounted, ref } from "vue";

import { useI18n } from "@/i18n/composables";
import {
  UPGRADE_RECOVERY_EVENT,
  UPGRADE_RECOVERY_TOKEN_KEY,
  displayVersion,
  getUpgradeRecoveryDismissKey,
  versionsMismatch,
  type UpgradeRecoveryDetail,
} from "@/utils/upgradeRecovery";

type VersionPayload = {
  status?: string;
  message?: string;
  data?: {
    version?: string | null;
    dashboard_version?: string | null;
  };
};

type StartTimePayload = {
  data?: {
    start_time?: number | string | null;
  };
};

const { t } = useI18n();

const visible = ref(false);
const restarting = ref(false);
const statusMessage = ref("");
const coreVersion = ref("");
const dashboardVersion = ref("");
const initialStartTime = ref<number | string | null>(null);

let restartTimer: ReturnType<typeof setInterval> | null = null;

function getRecoveryToken(): string | null {
  const recoveryToken = sessionStorage.getItem(UPGRADE_RECOVERY_TOKEN_KEY);
  if (recoveryToken) {
    return recoveryToken;
  }
  return localStorage.getItem("token");
}

function getRequestHeaders() {
  const headers: Record<string, string> = {};
  const token = getRecoveryToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const locale = localStorage.getItem("astrbot-locale");
  if (locale) {
    headers["Accept-Language"] = locale;
  }
  return headers;
}

function currentDismissKey() {
  return getUpgradeRecoveryDismissKey(coreVersion.value, dashboardVersion.value);
}

function clearRestartTimer() {
  if (restartTimer !== null) {
    clearInterval(restartTimer);
    restartTimer = null;
  }
}

function dismiss() {
  sessionStorage.setItem(currentDismissKey(), "1");
  visible.value = false;
}

function showForVersions(detail: UpgradeRecoveryDetail) {
  if (!versionsMismatch(detail.version, detail.dashboard_version)) {
    return;
  }
  coreVersion.value = displayVersion(detail.version);
  dashboardVersion.value = displayVersion(detail.dashboard_version);
  if (sessionStorage.getItem(currentDismissKey())) {
    return;
  }
  statusMessage.value = "";
  visible.value = true;
}

async function fetchStartTime(): Promise<number | string | null> {
  const response = await axios.get<StartTimePayload>("/api/stat/start-time", {
    headers: getRequestHeaders(),
    timeout: 3000,
  });
  return response.data?.data?.start_time ?? null;
}

function finishRestart() {
  clearRestartTimer();
  restarting.value = false;
  statusMessage.value = t("core.common.upgradeRecovery.ready");
  sessionStorage.removeItem(UPGRADE_RECOVERY_TOKEN_KEY);
  window.location.reload();
}

function waitForRestart() {
  clearRestartTimer();
  let attempts = 0;
  restartTimer = setInterval(async () => {
    attempts += 1;
    try {
      const nextStartTime = await fetchStartTime();
      if (
        nextStartTime !== null &&
        String(nextStartTime) !== String(initialStartTime.value)
      ) {
        finishRestart();
        return;
      }
    } catch (_error) {
      // The backend may be temporarily unavailable during restart.
    }

    if (attempts >= 90) {
      clearRestartTimer();
      restarting.value = false;
      statusMessage.value = t("core.common.upgradeRecovery.failed");
    }
  }, 1000);
}

async function restartCore() {
  restarting.value = true;
  statusMessage.value = t("core.common.upgradeRecovery.restarting");
  try {
    initialStartTime.value =
      initialStartTime.value ?? (await fetchStartTime().catch(() => null));
    await axios.post(
      "/api/stat/restart-core",
      {},
      {
        headers: getRequestHeaders(),
      },
    );
    statusMessage.value = t("core.common.upgradeRecovery.waiting");
    waitForRestart();
  } catch (_error) {
    restarting.value = false;
    statusMessage.value = t("core.common.upgradeRecovery.failed");
  }
}

async function detectMismatchFromCurrentSession() {
  const token = getRecoveryToken();
  if (!token) {
    return;
  }
  try {
    const response = await axios.get<VersionPayload>("/api/stat/version", {
      headers: getRequestHeaders(),
      validateStatus: () => true,
    });
    if (response.status >= 400) {
      return;
    }
    const versionData = response.data?.data || {};
    if (!versionsMismatch(versionData.version, versionData.dashboard_version)) {
      sessionStorage.removeItem(UPGRADE_RECOVERY_TOKEN_KEY);
      return;
    }
    showForVersions(versionData);
  } catch (_error) {
    // Best-effort only; never block the app on recovery probing.
  }
}

function handleRecoveryEvent(event: Event) {
  const customEvent = event as CustomEvent<UpgradeRecoveryDetail>;
  showForVersions(customEvent.detail || {});
}

onMounted(() => {
  window.addEventListener(UPGRADE_RECOVERY_EVENT, handleRecoveryEvent);
  void detectMismatchFromCurrentSession();
});

onBeforeUnmount(() => {
  clearRestartTimer();
  window.removeEventListener(UPGRADE_RECOVERY_EVENT, handleRecoveryEvent);
});
</script>

<style scoped>
.upgrade-recovery-title {
  align-items: center;
  display: flex;
  white-space: normal;
  word-break: break-word;
}
</style>
