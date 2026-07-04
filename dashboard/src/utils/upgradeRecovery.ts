export const UPGRADE_RECOVERY_EVENT = "astrbot:upgrade-recovery";
export const UPGRADE_RECOVERY_TOKEN_KEY = "astrbot-upgrade-recovery-token";

export type UpgradeRecoveryDetail = {
  version?: string | null;
  dashboard_version?: string | null;
};

export function normalizeVersion(version?: string | null): string {
  return String(version || "").trim().replace(/^v/i, "");
}

export function displayVersion(version?: string | null): string {
  const normalized = normalizeVersion(version);
  return normalized ? `v${normalized}` : "unknown";
}

export function versionsMismatch(
  coreVersion?: string | null,
  dashboardVersion?: string | null,
): boolean {
  const normalizedCore = normalizeVersion(coreVersion);
  const normalizedDashboard = normalizeVersion(dashboardVersion);
  return Boolean(
    normalizedCore &&
      normalizedDashboard &&
      normalizedCore !== normalizedDashboard,
  );
}

export function getUpgradeRecoveryDismissKey(
  coreVersion?: string | null,
  dashboardVersion?: string | null,
): string {
  return `astrbot-upgrade-recovery-dismissed:${displayVersion(coreVersion)}:${displayVersion(dashboardVersion)}`;
}
