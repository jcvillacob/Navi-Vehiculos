import { useEffect, useRef, useState } from "react";
import "leaflet/dist/leaflet.css";

import { useLeaflet } from "../hooks/useLeaflet";

const TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';

export default function MapView({ geofences, vehicles, selectedPlate, onSelectPlate }) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const groupsRef = useRef({ geofences: null, vehicles: null });
  const hasFitRef = useRef(false);
  const { L, ready, loading, error } = useLeaflet();
  const [isZoomed, setIsZoomed] = useState(false);

  // Init: crea el mapa una sola vez cuando Leaflet esta listo.
  useEffect(() => {
    if (!ready || !L || !containerRef.current || mapRef.current) return;
    const map = L.map(containerRef.current, { zoomControl: true, attributionControl: true }).setView(
      [4.5, -75],
      6
    );
    L.tileLayer(TILE_URL, { attribution: TILE_ATTR, maxZoom: 19 }).addTo(map);
    groupsRef.current.geofences = L.layerGroup().addTo(map);
    groupsRef.current.vehicles = L.layerGroup().addTo(map);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      groupsRef.current = { geofences: null, vehicles: null };
      hasFitRef.current = false;
    };
  }, [ready, L]);

  // Render de capas: geocercas + markers de vehiculos. Se reejecuta cuando
  // cambian los datos o la seleccion (para resaltar el marker activo).
  useEffect(() => {
    if (!ready || !L || !mapRef.current) return;
    const map = mapRef.current;
    const gfGroup = groupsRef.current.geofences;
    const vGroup = groupsRef.current.vehicles;
    gfGroup.clearLayers();
    vGroup.clearLayers();

    geofences.forEach((g) => {
      L.circle([g.lat, g.lng], {
        radius: g.radiusM,
        color: g.color,
        weight: 2,
        fillColor: g.color,
        fillOpacity: 0.12,
      }).addTo(gfGroup);

      const labelIcon = L.divIcon({
        className: "mapa-geofence-label",
        html: `<span style="color:${g.color}">${g.name}</span>`,
        iconSize: [80, 18],
        iconAnchor: [40, 9],
      });
      L.marker([g.lat, g.lng], { icon: labelIcon, interactive: false }).addTo(gfGroup);
    });

    vehicles.forEach((v) => {
      const isSel = v.plate === selectedPlate;
      const icon = L.divIcon({
        className: "mapa-veh-icon",
        html: `<div class="mapa-veh-marker${isSel ? " is-selected" : ""}">${v.plate}</div>`,
        iconSize: [60, 22],
        iconAnchor: [30, 11],
      });
      const marker = L.marker([v.lat, v.lng], { icon }).addTo(vGroup);
      marker.bindPopup(
        `<strong>${v.plate}</strong><br/>${v.geofenceName}<br/>${formatHours(v.hoursInside)} dentro` +
          (v.motor ? `<br/>Motor: ${v.motor}` : "") +
          (v.cliente ? `<br/>Cliente: ${v.cliente}` : "")
      );
      marker.on("click", () => onSelectPlate?.(v.plate));
      if (isSel) marker.openPopup();
    });

    if (!hasFitRef.current && geofences.length) {
      const bounds = L.latLngBounds(geofences.map((g) => [g.lat, g.lng]));
      map.fitBounds(bounds, { padding: [50, 50] });
      hasFitRef.current = true;
    }

    if (selectedPlate) {
      const sel = vehicles.find((v) => v.plate === selectedPlate);
      if (sel) {
        map.flyTo([sel.lat, sel.lng], 13, { animate: true });
        setIsZoomed(true);
      }
    }
  }, [ready, L, geofences, vehicles, selectedPlate, onSelectPlate]);

  const handleResetView = () => {
    const map = mapRef.current;
    if (!map || !L) return;
    if (geofences.length) {
      const bounds = L.latLngBounds(geofences.map((g) => [g.lat, g.lng]));
      map.flyToBounds(bounds, { padding: [50, 50] });
    } else {
      map.flyTo([4.5, -75], 6);
    }
    onSelectPlate?.(null);
    setIsZoomed(false);
  };

  if (error) {
    return (
      <div className="mapa-status">
        <p>{error}</p>
      </div>
    );
  }
  if (loading) {
    return (
      <div className="mapa-status">
        <p>Cargando mapa…</p>
      </div>
    );
  }
  return (
    <div className="mapa-view-wrapper">
      <div className="mapa-view" ref={containerRef} />
      {(isZoomed || selectedPlate) && (
        <button
          type="button"
          className="mapa-reset-btn"
          onClick={handleResetView}
          aria-label="Ver Colombia completa"
          title="Ver Colombia completa"
        >
          Ver Colombia
        </button>
      )}
    </div>
  );
}

function formatHours(h) {
  if (h == null) return "—";
  const hours = Math.floor(h);
  const mins = Math.round((h - hours) * 60);
  return mins ? `${hours}h ${mins}m` : `${hours}h`;
}
