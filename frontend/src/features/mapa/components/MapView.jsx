import { useEffect, useRef, useState } from "react";
import "leaflet/dist/leaflet.css";

import { useLeaflet } from "../hooks/useLeaflet";

const TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTR =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';

// Radio por defecto (m) para las geocercas de taller. Las geocercas reales
// vienen de Geotab; hasta entonces dibujamos un circulo pequeno que
// representa el edificio del taller.
const DEFAULT_ZONE_RADIUS_M = 200;

// Paleta ciclica para distinguir zonas en el mapa.
const ZONE_COLORS = ["#ee2e2f", "#185979", "#d4a017", "#1f8f5f", "#7a5af8", "#354550"];

function formatDuration(minutes) {
  if (minutes == null) return "—";
  const m = Math.max(0, Math.floor(minutes));
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rem = m - h * 60;
  return rem ? `${h}h ${rem}m` : `${h}h`;
}

function formatLocalTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString("es-CO", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function MapView({ zones, vehicles, selectedPlate, onSelectPlate }) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const groupsRef = useRef({ zones: null, vehicles: null });
  const hasFitRef = useRef(false);
  const { L, ready, loading, error } = useLeaflet();
  const [isZoomed, setIsZoomed] = useState(false);

  useEffect(() => {
    if (!ready || !L || !containerRef.current || mapRef.current) return;
    const map = L.map(containerRef.current, {
      zoomControl: true,
      attributionControl: true,
    }).setView([4.5, -75], 6);
    L.tileLayer(TILE_URL, { attribution: TILE_ATTR, maxZoom: 19 }).addTo(map);
    groupsRef.current.zones = L.layerGroup().addTo(map);
    groupsRef.current.vehicles = L.layerGroup().addTo(map);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      groupsRef.current = { zones: null, vehicles: null };
      hasFitRef.current = false;
    };
  }, [ready, L]);

  useEffect(() => {
    if (!ready || !L || !mapRef.current) return;
    const map = mapRef.current;
    const zGroup = groupsRef.current.zones;
    const vGroup = groupsRef.current.vehicles;
    zGroup.clearLayers();
    vGroup.clearLayers();

    const zoneById = new Map();

    zones.forEach((z, index) => {
      const color = ZONE_COLORS[index % ZONE_COLORS.length];
      zoneById.set(z.id, { ...z, color });

      L.circle([z.lat, z.lng], {
        radius: DEFAULT_ZONE_RADIUS_M,
        color,
        weight: 2,
        fillColor: color,
        fillOpacity: 0.12,
      }).addTo(zGroup);

      const labelIcon = L.divIcon({
        className: "mapa-geofence-label",
        html: `<span style="color:${color}">${z.name}</span>`,
        iconSize: [120, 18],
        iconAnchor: [60, 9],
      });
      L.marker([z.lat, z.lng], { icon: labelIcon, interactive: false }).addTo(zGroup);
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
      const zone = zoneById.get(v.zone_id);
      const zoneName = v.zone_name || zone?.name || "—";
      const popupHtml =
        `<strong>${v.plate}</strong>` +
        `<br/>${zoneName}` +
        `<br/>${formatDuration(v.minutes_inside)} dentro` +
        (v.motor ? `<br/>Motor: ${v.motor}` : "") +
        (v.client_name ? `<br/>Cliente: ${v.client_name}` : "") +
        (v.category ? `<br/>Categoria: ${v.category}` : "") +
        (v.enter_ts_local ? `<br/>Ingreso: ${formatLocalTime(v.enter_ts_local)}` : "");
      marker.bindPopup(popupHtml);
      marker.on("click", () => onSelectPlate?.(v.plate));
      if (isSel) marker.openPopup();
    });

    if (!hasFitRef.current && zones.length) {
      const bounds = L.latLngBounds(zones.map((z) => [z.lat, z.lng]));
      map.fitBounds(bounds, { padding: [50, 50] });
      hasFitRef.current = true;
    }

    if (selectedPlate) {
      const sel = vehicles.find((v) => v.plate === selectedPlate);
      if (sel) {
        map.flyTo([sel.lat, sel.lng], 16, { animate: true });
        setIsZoomed(true);
      }
    }
  }, [ready, L, zones, vehicles, selectedPlate, onSelectPlate]);

  const handleResetView = () => {
    const map = mapRef.current;
    if (!map || !L) return;
    if (zones.length) {
      const bounds = L.latLngBounds(zones.map((z) => [z.lat, z.lng]));
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
