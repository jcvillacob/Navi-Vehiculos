import { useState } from "react";

import CreateVehicleModal from "../features/engineLookup/components/CreateVehicleModal";
import EditVehicleModal from "../features/engineLookup/components/EditVehicleModal";
import RegisteredVehiclesTable from "../features/engineLookup/components/RegisteredVehiclesTable";
import { useEngineLookup } from "../features/engineLookup/hooks/useEngineLookup";

export default function EngineLookupPage() {
  const [registerMessage, setRegisterMessage] = useState("");
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [editingVehicle, setEditingVehicle] = useState(null);

  const {
    loading,
    lookupResult,
    error,
    registeredVehicles,
    canContinueToRegister,
    searchVehicle,
    addTemporaryVehicleFromLookup,
    updateVehicle,
    removeVehicle,
    resetLookup
  } = useEngineLookup();

  const handleOpenCreate = () => {
    setRegisterMessage("");
    setIsCreateOpen(true);
  };

  const handleCloseCreate = () => {
    setIsCreateOpen(false);
    resetLookup();
  };

  const handleRegisterTemporary = () => {
    addTemporaryVehicleFromLookup();
    setRegisterMessage("Vehiculo agregado temporalmente. Sin persistencia todavia.");
    handleCloseCreate();
  };

  return (
    <section className="panel">
      <header className="page-header">
        <h2>Consulta de motor vehicular</h2>
        <p>Gestion de vehiculos con consulta de motor y registro temporal.</p>
      </header>

      <section className="card toolbar-card">
        <div className="toolbar-row">
          <h3>Vehiculos</h3>
          <button type="button" onClick={handleOpenCreate}>
            Nuevo vehiculo
          </button>
        </div>
      </section>

      {registerMessage ? <p className="helper-text">{registerMessage}</p> : null}

      <RegisteredVehiclesTable
        vehicles={registeredVehicles}
        onEdit={(vehicle) => setEditingVehicle(vehicle)}
        onDelete={(vehicleId) => removeVehicle(vehicleId)}
      />

      <CreateVehicleModal
        open={isCreateOpen}
        loading={loading}
        error={error}
        lookupResult={lookupResult}
        canContinueToRegister={canContinueToRegister}
        onClose={handleCloseCreate}
        onSearch={searchVehicle}
        onRegister={handleRegisterTemporary}
      />

      <EditVehicleModal
        open={Boolean(editingVehicle)}
        vehicle={editingVehicle}
        onClose={() => setEditingVehicle(null)}
        onSave={(vehicleId, data) => {
          updateVehicle(vehicleId, data);
          setEditingVehicle(null);
        }}
      />
    </section>
  );
}