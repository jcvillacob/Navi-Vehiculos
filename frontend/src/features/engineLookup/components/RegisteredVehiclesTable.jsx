export default function RegisteredVehiclesTable({ vehicles, onEdit, onDelete }) {
  return (
    <section className="card table-card">
      <header>
        <h3>Vehiculos registrados</h3>
      </header>

      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Placa</th>
              <th>Nombre Motor</th>
              <th>Fecha de registro</th>
              <th>Tipo de motor</th>
              <th>Acciones</th>
            </tr>
          </thead>
          <tbody>
            {vehicles.length === 0 ? (
              <tr>
                <td colSpan="5" className="empty-row">
                  No hay vehiculos registrados por ahora.
                </td>
              </tr>
            ) : (
              vehicles.map((vehicle) => (
                <tr key={vehicle.id}>
                  <td>{vehicle.plate}</td>
                  <td>{vehicle.engineName || "-"}</td>
                  <td>{vehicle.registeredAt}</td>
                  <td>{vehicle.engineType}</td>
                  <td>
                    <div className="inline-actions">
                      <button
                        type="button"
                        className="button-secondary button-inline"
                        onClick={() => onEdit(vehicle)}
                      >
                        Editar
                      </button>
                      <button
                        type="button"
                        className="button-danger button-inline"
                        onClick={() => onDelete(vehicle.id)}
                      >
                        Eliminar
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}