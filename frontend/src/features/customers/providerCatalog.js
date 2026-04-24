const ARTIMO_DEFAULTS = {
  customerId: "",
  groupName: "",
  apiBaseUrl: "https://api.artimo.com.co",
  authBaseUrl: "https://apifront.artimo.com.co",
  monthStartHourUtc: "5",
  monthEndHourUtc: "16"
};

const LOGITRACS_TRITON_DEFAULTS = {
  codigoEmpresa: "",
  passwordWeb: "",
  tritonLoginUrl: "https://triton.logitracs.com/Logitracs.Triton/api/Usuarios/Login",
  logivimBaseUrl: "https://triton.logitracs.com/LogiVIMwebTriton/public"
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
  },
  {
    key: "frotcom",
    label: "Frotcom",
    description: "Telematica Frotcom (credenciales por database; usa el enlace como visual).",
    usesAccessUrl: true,
    supportsMonthlyPerformance: true
  },
  {
    key: "logitracs_triton",
    label: "LogiTracs Triton",
    description: "Informe operacional de flota mensual.",
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
  if (connectionType === "logitracs_triton") {
    return {
      ...LOGITRACS_TRITON_DEFAULTS,
      codigoEmpresa: providerConfig.codigo_empresa || LOGITRACS_TRITON_DEFAULTS.codigoEmpresa,
      passwordWeb: providerConfig.password_web || LOGITRACS_TRITON_DEFAULTS.passwordWeb,
      tritonLoginUrl: providerConfig.triton_login_url || LOGITRACS_TRITON_DEFAULTS.tritonLoginUrl,
      logivimBaseUrl: providerConfig.logivim_base_url || LOGITRACS_TRITON_DEFAULTS.logivimBaseUrl
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
  if (connectionType === "logitracs_triton") {
    const payload = {
      codigo_empresa: (providerState.codigoEmpresa || "").trim(),
      triton_login_url: LOGITRACS_TRITON_DEFAULTS.tritonLoginUrl,
      logivim_base_url: LOGITRACS_TRITON_DEFAULTS.logivimBaseUrl
    };
    if ((providerState.passwordWeb || "").trim()) {
      payload.password_web = providerState.passwordWeb.trim();
    }
    return payload;
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
  if (connectionType === "logitracs_triton") {
    return [];
  }
  return [];
}

const PROVIDERS_WITH_MANUAL_ID = new Set(["geotab", "artimo", "frotcom", "logitracs_triton"]);

export function providerSupportsManualVehicleId(connectionType) {
  return PROVIDERS_WITH_MANUAL_ID.has(connectionType);
}
