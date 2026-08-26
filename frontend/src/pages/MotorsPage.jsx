import { useEffect, useMemo, useState } from "react";

import Can from "../components/Can";
import ToastStack from "../components/ToastStack";
import { useToasts } from "../components/useToasts";
import MotorAttachmentModal from "../features/engineLookup/components/MotorAttachmentModal";
import MotorRpmBandsModal from "../features/engineLookup/components/MotorRpmBandsModal";
import RegisterMotorModal from "../features/engineLookup/components/RegisterMotorModal";
import { useMotorsCatalog } from "../features/engineLookup/hooks/useMotorsCatalog";

const API_BASE = import.meta.env.VITE_API_URL ?? "";

// Bandas del eje de RPM (backend: rule_bands.RPM_RANGE_BANDS).
const RPM_BAND_LABELS = {
  rango_bajo: "Bajo",
  rango_economico: "Economico",
  rango_balanceado: "Balanceado",
  rango_potencia: "Potencia",
  rango_potencia_ineficiente: "Ineficiente",
  exceso_rpm: "Exceso"
};

function AttachmentIcon({ contentType }) {
  const isPdf = contentType === "application/pdf";

  return (
    <span className="attachment-icon" aria-hidden="true">
      {isPdf ? (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6" />
          <path d="M8 13h8" />
          <path d="M8 17h5" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="5" width="18" height="14" rx="2" />
          <circle cx="9" cy="10" r="1.2" />
          <path d="m21 15-4.5-4.5L8 19" />
        </svg>
      )}
    </span>
  );
}

// Los inputs de RPM viajan como entero o null (null = "sin capturar" en el
// contrato con Portal Clientes; nunca 0).
function parseRpmInput(value) {
  const trimmed = String(value ?? "").trim();
  if (!trimmed) return null;
  const parsed = Number.parseInt(trimmed, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function formatLastSeen(value) {
  if (!value) {
    return "Sin consultas";
  }
  return new Date(value).toLocaleString("es-CO", {
    dateStyle: "medium",
    timeStyle: "short"
  });
}

/* ── Edit Motor Modal ──────────────────────────────────────────────── */
function EditMotorModal({ motor, loading, onClose, onSubmit, onDelete }) {
  const [engineName, setEngineName] = useState(motor.engine_name);
  const [technicalNumber, setTechnicalNumber] = useState(motor.technical_number);
  const [governedSpeed, setGovernedSpeed] = useState(
    motor.governed_speed_rpm == null ? "" : String(motor.governed_speed_rpm)
  );
  const [maxOverspeed, setMaxOverspeed] = useState(
    motor.max_overspeed_rpm == null ? "" : String(motor.max_overspeed_rpm)
  );
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const governedValue = parseRpmInput(governedSpeed);
  const overspeedValue = parseRpmInput(maxOverspeed);
  const speedsError =
    governedValue != null && overspeedValue != null && overspeedValue < governedValue
      ? "La sobrevelocidad maxima no puede ser menor que la velocidad gobernada."
      : "";

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (speedsError) return;
    const payload = {
      engine_name: engineName.trim(),
      governed_speed_rpm: governedValue,
      max_overspeed_rpm: overspeedValue
    };
    const trimmedTechnical = technicalNumber.trim();
    if (trimmedTechnical && trimmedTechnical !== motor.technical_number) {
      payload.technical_number = trimmedTechnical;
    }
    await onSubmit(payload);
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="card modal-card" role="dialog" aria-modal="true" aria-label="Editar motor">
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Editar</span>
            <h3>{motor.technical_number}</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>
            &#10005;
          </button>
        </header>

        {showDeleteConfirm ? (
          <div className="delete-confirm-section">
            {motor.vehicle_count > 0 ? (
              <div className="notice-banner notice-error">
                Este motor tiene <strong>{motor.vehicle_count} vehiculo{motor.vehicle_count !== 1 ? "s" : ""}</strong> asociado{motor.vehicle_count !== 1 ? "s" : ""}. Al eliminarlo, los vehiculos quedaran sin motor catalogado.
              </div>
            ) : null}
            <p className="support-copy">
              Esta accion no se puede deshacer. Se eliminaran tambien los adjuntos y grupos de reglas asociados.
            </p>
            <div className="actions-row modal-actions">
              <button
                type="button"
                className="button-danger"
                disabled={loading}
                onClick={() => onDelete(motor.id)}
              >
                {loading ? "Eliminando..." : "Si, eliminar motor"}
              </button>
              <button
                type="button"
                className="button-secondary"
                onClick={() => setShowDeleteConfirm(false)}
              >
                Cancelar
              </button>
            </div>
          </div>
        ) : (
          <form className="register-form" onSubmit={handleSubmit}>
            <div className="form-field">
              <label htmlFor="edit-motor-name">Nombre del motor</label>
              <input
                id="edit-motor-name"
                value={engineName}
                onChange={(event) => setEngineName(event.target.value)}
                required
                autoFocus
              />
            </div>

            <div className="form-field">
              <label htmlFor="edit-motor-technical">Technical Engine Configuration #</label>
              <input
                id="edit-motor-technical"
                value={technicalNumber}
                onChange={(event) => setTechnicalNumber(event.target.value)}
                required
              />
            </div>

            <div className="form-field">
              <label htmlFor="edit-motor-governed-speed">
                Velocidad nominal gobernada sin carga (RPM)
              </label>
              <input
                id="edit-motor-governed-speed"
                type="number"
                min="1"
                step="1"
                inputMode="numeric"
                value={governedSpeed}
                onChange={(event) => setGovernedSpeed(event.target.value)}
                placeholder="Ej: 2100"
              />
            </div>

            <div className="form-field">
              <label htmlFor="edit-motor-max-overspeed">
                Capacidad maxima de sobrevelocidad (RPM)
              </label>
              <input
                id="edit-motor-max-overspeed"
                type="number"
                min="1"
                step="1"
                inputMode="numeric"
                value={maxOverspeed}
                onChange={(event) => setMaxOverspeed(event.target.value)}
                placeholder="Ej: 2250"
              />
              <p className="support-copy">
                Datos de la hoja tecnica del motor. Se exportan a Portal Clientes; dejalos
                vacios si aun no se conocen.
              </p>
            </div>

            {speedsError ? (
              <div className="notice-banner notice-error">{speedsError}</div>
            ) : null}

            <div className="actions-row modal-actions">
              <button
                type="submit"
                disabled={
                  loading || !engineName.trim() || !technicalNumber.trim() || Boolean(speedsError)
                }
              >
                {loading ? "Guardando..." : "Guardar cambios"}
              </button>
              <button type="button" className="button-secondary" onClick={onClose}>
                Cancelar
              </button>
              <button
                type="button"
                className="button-danger-outline"
                onClick={() => setShowDeleteConfirm(true)}
              >
                Eliminar
              </button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
}

/* ── Main Page ─────────────────────────────────────────────────────── */
export default function MotorsPage() {
  const [isRegisterOpen, setIsRegisterOpen] = useState(false);
  const [selectedMotorForUpload, setSelectedMotorForUpload] = useState(null);
  const [editingMotor, setEditingMotor] = useState(null);
  const [rpmBandsMotor, setRpmBandsMotor] = useState(null);
  const [search, setSearch] = useState("");
  const { toasts, pushToast } = useToasts();

  const {
    loading,
    motors,
    error,
    registerMotor,
    editMotor,
    removeMotor,
    uploadAttachment,
    updateAttachment,
    deleteAttachment,
    saveRpmBands
  } = useMotorsCatalog();

  useEffect(() => {
    if (error) pushToast("error", error);
  }, [error, pushToast]);

  const totals = useMemo(() => {
    return motors.reduce(
      (acc, motor) => {
        acc.motors += 1;
        acc.vehicles += motor.vehicle_count || 0;
        acc.attachments += motor.attachments?.length || 0;
        return acc;
      },
      { motors: 0, vehicles: 0, attachments: 0 }
    );
  }, [motors]);

  const filteredMotors = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return motors;
    return motors.filter(
      (motor) =>
        motor.engine_name.toLowerCase().includes(query) ||
        motor.technical_number.toLowerCase().includes(query) ||
        (motor.available_cpls || []).some((cpl) => cpl.toLowerCase().includes(query))
    );
  }, [motors, search]);

  const activeMotorForAttachments = useMemo(() => {
    if (!selectedMotorForUpload) {
      return null;
    }
    return motors.find((motor) => motor.id === selectedMotorForUpload.id) || selectedMotorForUpload;
  }, [motors, selectedMotorForUpload]);

  const handleSubmit = async (payload) => {
    try {
      await registerMotor(payload);
      pushToast("success", "Motor registrado en el catalogo.");
      setIsRegisterOpen(false);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible registrar el motor");
    }
  };

  const handleEditMotor = async (payload) => {
    try {
      await editMotor(editingMotor.id, payload);
      setEditingMotor(null);
      pushToast("success", "Motor actualizado.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible actualizar el motor");
    }
  };

  const handleDeleteMotor = async (motorId) => {
    try {
      const result = await removeMotor(motorId);
      setEditingMotor(null);
      const msg = result.vehicles_unlinked > 0
        ? `Motor eliminado. ${result.vehicles_unlinked} vehiculo${result.vehicles_unlinked !== 1 ? "s" : ""} desvinculado${result.vehicles_unlinked !== 1 ? "s" : ""}.`
        : "Motor eliminado.";
      pushToast("success", msg);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible eliminar el motor");
    }
  };

  const handleSaveRpmBands = async (bands) => {
    try {
      await saveRpmBands(rpmBandsMotor.id, bands);
      setRpmBandsMotor(null);
      pushToast(
        "success",
        bands.length ? "Rangos de RPM guardados." : "Rangos de RPM borrados."
      );
    } catch (err) {
      pushToast(
        "error",
        err instanceof Error ? err.message : "No fue posible guardar los rangos de RPM"
      );
    }
  };

  const handleCreateAttachment = async (motorId, payload) => {
    try {
      await uploadAttachment(motorId, payload);
      pushToast("success", "Adjunto cargado.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible subir el adjunto");
    }
  };

  const handleUpdateAttachment = async (attachmentId, payload) => {
    try {
      await updateAttachment(attachmentId, payload);
      pushToast("success", "Adjunto actualizado.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible actualizar el adjunto");
    }
  };

  const handleDeleteAttachment = async (attachmentId) => {
    try {
      await deleteAttachment(attachmentId);
      pushToast("success", "Adjunto eliminado.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible eliminar el adjunto");
    }
  };

  return (
    <section className="panel">
      <header className="page-header page-header-row">
        <div>
          <span className="eyebrow">Catalogo tecnico</span>
          <h2>Motores</h2>
          <p>
            Familias de motor registradas con cobertura real sobre la flota consultada.
          </p>
        </div>

        <Can permission="motors.create">
          <button type="button" onClick={() => setIsRegisterOpen(true)}>
            + Motor
          </button>
        </Can>
      </header>

      <section className="motor-overview-grid">
        <article className="card metric-card">
          <span className="eyebrow">Motores</span>
          <strong>{totals.motors}</strong>
          <p>Familias tecnicas activas</p>
        </article>

        <article className="card metric-card feature-card-accent">
          <span className="eyebrow">Cobertura</span>
          <strong>{totals.vehicles}</strong>
          <p>Vehiculos unicos asociados</p>
        </article>

        <article className="card metric-card">
          <span className="eyebrow">Adjuntos</span>
          <strong>{totals.attachments}</strong>
          <p>Archivos cargados en el catalogo</p>
        </article>
      </section>

      <div className="motors-search-bar">
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Buscar por nombre, TEC# o CPL..."
          className="motors-search-input"
        />
        {search ? (
          <button
            type="button"
            className="button-secondary button-sm"
            onClick={() => setSearch("")}
          >
            Limpiar
          </button>
        ) : null}
      </div>

      <ToastStack toasts={toasts} />

      <section className="motor-cards-grid">
        {loading && motors.length === 0 ? <p className="notice-banner notice-soft">Cargando motores...</p> : null}

        {!loading && motors.length === 0 ? (
          <article className="card empty-state-card">
            <span className="eyebrow">Sin registros</span>
            <h3>El catalogo aun esta vacio.</h3>
            <p>
              Registra el primer motor con su Technical Engine Configuration # para empezar a
              agrupar vehiculos automaticamente.
            </p>
          </article>
        ) : null}

        {!loading && motors.length > 0 && filteredMotors.length === 0 ? (
          <p className="support-copy">Sin resultados para "{search}".</p>
        ) : null}

        {filteredMotors.map((motor) => (
          <article className="card motor-card" key={motor.id}>
            <div className="motor-card-top">
              <span className="motor-count">{motor.vehicle_count} vehiculos</span>
              <span className="status status-ok">activo</span>
            </div>

            <div className="motor-card-heading">
              <h3>{motor.engine_name}</h3>
              <div className="motor-card-heading-row">
                <p className="motor-technical-number">{motor.technical_number}</p>
                <div className="motor-card-heading-actions">
                  <Can permission="motors.edit">
                    <button
                      type="button"
                      className="icon-button"
                      onClick={() => setEditingMotor(motor)}
                      title="Editar motor"
                    >
                      &#9998;
                    </button>
                  </Can>
                </div>
              </div>
            </div>

            {(motor.available_cpls || []).length > 0 ? (
              <div className="motor-cpls">
                {motor.available_cpls.map((cpl) => (
                  <span className="cpl-chip" key={cpl}>CPL {cpl}</span>
                ))}
              </div>
            ) : null}

            <div className="motor-card-meta">
              <div>
                <span>Ultima deteccion</span>
                <strong>{formatLastSeen(motor.last_seen_at)}</strong>
              </div>
              <div>
                <span>Creado</span>
                <strong>{formatLastSeen(motor.created_at)}</strong>
              </div>
              <div>
                <span>Velocidad gobernada</span>
                <strong>
                  {motor.governed_speed_rpm == null
                    ? "—"
                    : `${motor.governed_speed_rpm} RPM`}
                </strong>
              </div>
              <div>
                <span>Sobrevelocidad max.</span>
                <strong>
                  {motor.max_overspeed_rpm == null ? "—" : `${motor.max_overspeed_rpm} RPM`}
                </strong>
              </div>
            </div>

            <div className="motor-card-rpm">
              <div className="motor-rpm-header">
                <span>Rangos de RPM</span>
                <Can permission="motors.edit">
                  <button
                    type="button"
                    className="button-secondary button-sm"
                    onClick={() => setRpmBandsMotor(motor)}
                  >
                    {(motor.rpm_bands || []).length > 0 ? "Editar" : "Configurar"}
                  </button>
                </Can>
              </div>

              {(motor.rpm_bands || []).length > 0 ? (
                <div className="rpm-band-chips">
                  {motor.rpm_bands.map((band) => (
                    <span className="rpm-band-chip" key={band.band} title={RPM_BAND_LABELS[band.band]}>
                      {RPM_BAND_LABELS[band.band]} {band.rpm_min}
                      {band.rpm_max == null ? "+" : `-${band.rpm_max}`}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="support-copy">
                  Sin configurar. Los clientes en modo "Rangos por RPM" no calculan bandas
                  para este motor.
                </p>
              )}
            </div>

            <div className="motor-card-attachments">
              <div className="motor-attachments-header">
                <span>Adjuntos ({motor.attachments?.length || 0})</span>
                <Can permission="motors.attachments">
                  <button
                    type="button"
                    className="icon-button"
                    onClick={() => setSelectedMotorForUpload(motor)}
                    title="Gestionar adjuntos"
                  >
                    &#8943;
                  </button>
                </Can>
              </div>

              {motor.attachments?.length ? (
                <div className="attachment-list">
                  {motor.attachments.map((attachment) => (
                    <a
                      key={attachment.id}
                      className="attachment-chip"
                      href={`${API_BASE}${attachment.download_url}`}
                      target="_blank"
                      rel="noreferrer"
                      title={attachment.original_filename}
                      aria-label={`Abrir ${attachment.original_filename}`}
                    >
                      <AttachmentIcon contentType={attachment.content_type} />
                      <span className="attachment-chip-cpl">{attachment.cpl || "—"}</span>
                    </a>
                  ))}
                </div>
              ) : (
                <p className="support-copy">Sin adjuntos.</p>
              )}
            </div>
          </article>
        ))}
      </section>

      <RegisterMotorModal
        open={isRegisterOpen}
        loading={loading}
        title="Registrar nuevo motor"
        onClose={() => setIsRegisterOpen(false)}
        onSubmit={handleSubmit}
      />

      <MotorAttachmentModal
        open={Boolean(selectedMotorForUpload)}
        loading={loading}
        motor={activeMotorForAttachments}
        onClose={() => setSelectedMotorForUpload(null)}
        onCreate={handleCreateAttachment}
        onUpdate={handleUpdateAttachment}
        onDelete={handleDeleteAttachment}
      />

      {rpmBandsMotor ? (
        <MotorRpmBandsModal
          motor={motors.find((m) => m.id === rpmBandsMotor.id) || rpmBandsMotor}
          loading={loading}
          onClose={() => setRpmBandsMotor(null)}
          onSubmit={handleSaveRpmBands}
        />
      ) : null}

      {editingMotor ? (
        <EditMotorModal
          motor={editingMotor}
          loading={loading}
          onClose={() => setEditingMotor(null)}
          onSubmit={handleEditMotor}
          onDelete={handleDeleteMotor}
        />
      ) : null}
    </section>
  );
}
