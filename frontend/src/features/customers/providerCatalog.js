const ARTIMO_DEFAULTS = {
  customerId: "",
  groupName: "",
  apiBaseUrl: "https://api.artimo.com.co",
  authBaseUrl: "https://apifront.artimo.com.co",
  monthStartHourUtc: "5",
  monthEndHourUtc: "16"
};

export const DATABASE_PROVIDERS = [
  {
    key: "database",
    label: "Database",
    description: "Conexion generica o acceso manual.",
    usesAccessUrl: true,
    supportsMonthlyPerformance: false
  },
  {
    key: "geotab",
    label: "Geotab",
    description: "Telematica Geotab con reglas por motor.",
    usesAccessUrl: false,
    supportsMonthlyPerformance: false
  },
  {
    key: "artimo",
    label: "Artimo",
    description: "Telematica Artimo para rendimientos mensuales.",
    usesAccessUrl: false,
    supportsMonthlyPerformance: true
  }
];

export function getProviderDefinition(connectionType) {
  return DATABASE_PROVIDERS.find((provider) => provider.key === connectionType) || DATABASE_PROVIDERS[0];
}

export function getDatabaseTypeLabel(connectionType) {
  return getProviderDefinition(connectionType).label;
}

export function providerUsesAccessUrl(connectionType) {
  return getProviderDefinition(connectionType).usesAccessUrl;
}

export function getInitialProviderConfig(connectionType, providerConfig = {}) {
  if (connectionType === "artimo") {
    return {
      ...ARTIMO_DEFAULTS,
      customerId: providerConfig.customer_id || ARTIMO_DEFAULTS.customerId,
      groupName: providerConfig.group_name || ARTIMO_DEFAULTS.groupName,
      apiBaseUrl: providerConfig.api_base_url || ARTIMO_DEFAULTS.apiBaseUrl,
      authBaseUrl: providerConfig.auth_base_url || ARTIMO_DEFAULTS.authBaseUrl,
      monthStartHourUtc: String(providerConfig.month_start_hour_utc ?? ARTIMO_DEFAULTS.monthStartHourUtc),
      monthEndHourUtc: String(providerConfig.month_end_hour_utc ?? ARTIMO_DEFAULTS.monthEndHourUtc)
    };
  }
  return {};
}

export function buildProviderConfigPayload(connectionType, providerState) {
  if (connectionType === "artimo") {
    return {
      customer_id: providerState.customerId.trim(),
      group_name: providerState.groupName.trim(),
      api_base_url: providerState.apiBaseUrl.trim(),
      auth_base_url: providerState.authBaseUrl.trim(),
      month_start_hour_utc: Number(providerState.monthStartHourUtc || 5),
      month_end_hour_utc: Number(providerState.monthEndHourUtc || 16)
    };
  }
  return {};
}

export function getProviderDetailRows(connectionType, providerConfig = {}) {
  if (connectionType === "artimo") {
    return [
      { label: "Artimo customer_id", value: providerConfig.customer_id || "-" },
      { label: "Artimo group_name", value: providerConfig.group_name || "-" },
      { label: "API base", value: providerConfig.api_base_url || "-" },
      { label: "Auth base", value: providerConfig.auth_base_url || "-" }
    ];
  }
  return [];
}
