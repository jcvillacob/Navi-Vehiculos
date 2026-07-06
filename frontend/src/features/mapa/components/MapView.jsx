import { useEffect, useRef, useState } from "react";
import L from "leaflet";
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

function buildVehicleIcon(plate, isSel) {
  return L.divIcon({
    className: "mapa-veh-icon",
    html: `<div class="mapa-veh-marker${isSel ? " is-selected" : ""}">${plate}</div>`,
    iconSize: [60, 22],
    iconAnchor: [30, 11],
  });
}

function buildExitedIcon(plate) {
  return L.divIcon({
    className: "mapa-veh-icon",
    html: `<div class="mapa-veh-marker is-exited">${plate}</div>`,
    iconSize: [60, 22],
    iconAnchor: [30, 11],
  });
}

function buildExitedPopup(v) {
  return (
    `<strong>${v.plate}</strong> <span class="mapa-popup-tag">salió</span>` +
    `<br/>${v.zone_name || "—"}` +
    (v.motor ? `<br/>Motor: ${v.motor}` : "") +
    (v.client_name ? `<br/>Cliente: ${v.client_name}` : "") +
    (v.exit_ts_local ? `<br/>Salida: ${formatLocalTime(v.exit_ts_local)}` : "")
  );
}

function buildVehiclePopup(v, zoneName) {
  return (
    `<strong>${v.plate}</strong>` +
    `<br/>${zoneName}` +
    `<br/>${formatDuration(v.minutes_inside)} dentro` +
    (v.motor ? `<br/>Motor: ${v.motor}` : "") +
    (v.client_name ? `<br/>Cliente: ${v.client_name}` : "") +
    (v.category ? `<br/>Categoria: ${v.category}` : "") +
    (v.enter_ts_local ? `<br/>Ingreso: ${formatLocalTime(v.enter_ts_local)}` : "")
  );
}

export default function MapView({
  zones,
  vehicles,
  exited = [],
  selectedPlate,
  onSelectPlate,
}) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const groupsRef = useRef({ zones: null, vehicles: null, exited: null });
  const hasFitRef = useRef(false);
  const zoneLayersRef = useRef(new Map());
  const vehicleMarkersRef = useRef(new Map());
  const exitedMarkersRef = useRef(new Map());
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
    groupsRef.current.exited = L.layerGroup().addTo(map);
    groupsRef.current.vehicles = L.layerGroup().addTo(map);
    mapRef.current = map;

    const ro = new ResizeObserver(() => {
      if (mapRef.current) mapRef.current.invalidateSize();
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      map.remove();
      mapRef.current = null;
      groupsRef.current = { zones: null, vehicles: null, exited: null };
      hasFitRef.current = false;
      zoneLayersRef.current.clear();
      vehicleMarkersRef.current.clear();
      exitedMarkersRef.current.clear();
    };
  }, [ready, L]);

  useEffect(() => {
    if (!ready || !L || !mapRef.current) return;
    const map = mapRef.current;
    const zGroup = groupsRef.current.zones;
    const vGroup = groupsRef.current.vehicles;
    const eGroup = groupsRef.current.exited;
    const zoneLayers = zoneLayersRef.current;
    const vehicleMarkers = vehicleMarkersRef.current;
    const exitedMarkers = exitedMarkersRef.current;

    const zoneById = new Map();

    // ── Zones: diff-based update (create / update / remove) ──
    zones.forEach((z) => {
      zoneById.set(z.id, z);
      const existing = zoneLayers.get(z.id);
      if (existing) {
        existing.circle.setLatLng([z.lat, z.lng]);
        existing.label.setLatLng([z.lat, z.lng]);
        if (existing.name !== z.name) {
          existing.label.setIcon(L.divIcon({
            className: "mapa-geofence-label",
            html: `<span style="color:${existing.color}">${z.name}</span>`,
            iconSize: [120, 18],
            iconAnchor: [60, 9],
          }));
          existing.name = z.name;
        }
      } else {
        const color = ZONE_COLORS[zoneLayers.size % ZONE_COLORS.length];
        const circle = L.circle([z.lat, z.lng], {
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
        const label = L.marker([z.lat, z.lng], {
          icon: labelIcon,
          interactive: false,
        }).addTo(zGroup);
        zoneLayers.set(z.id, { circle, label, color, name: z.name });
      }
    });

    // Remove zones no longer present
    for (const [id, layers] of zoneLayers) {
      if (!zoneById.has(id)) {
        layers.circle.remove();
        layers.label.remove();
        zoneLayers.delete(id);
      }
    }

    // ── Vehicles: diff-based update (create / update / remove) ──
    const plateSet = new Set();

    vehicles.forEach((v) => {
      plateSet.add(v.plate);
      const isSel = v.plate === selectedPlate;
      const zone = zoneById.get(v.zone_id);
      const zoneName = v.zone_name || zone?.name || "—";
      const popupHtml = buildVehiclePopup(v, zoneName);
      const existing = vehicleMarkers.get(v.plate);

      if (existing) {
        const marker = existing.marker;
        const wasSel = existing.isSel;
        marker.setLatLng([v.lat, v.lng]);
        marker.setPopupContent(popupHtml);
        if (wasSel !== isSel) {
          marker.setIcon(buildVehicleIcon(v.plate, isSel));
          existing.isSel = isSel;
        }
        if (isSel && !wasSel) marker.openPopup();
        if (!isSel && wasSel) marker.closePopup();
      } else {
        const marker = L.marker([v.lat, v.lng], {
          icon: buildVehicleIcon(v.plate, isSel),
        }).addTo(vGroup);
        marker.bindPopup(popupHtml);
        marker.on("click", () => onSelectPlate?.(v.plate));
        if (isSel) marker.openPopup();
        vehicleMarkers.set(v.plate, { marker, isSel });
      }
    });

    // Remove vehicles no longer present
    for (const [plate, entry] of vehicleMarkers) {
      if (!plateSet.has(plate)) {
        entry.marker.remove();
        vehicleMarkers.delete(plate);
      }
    }

    // ── Exited vehicles: markers atenuados (ya salieron) ──
    const exitedSet = new Set();
    exited.forEach((v) => {
      exitedSet.add(v.plate);
      const popupHtml = buildExitedPopup(v);
      const existing = exitedMarkers.get(v.plate);
      if (existing) {
        existing.marker.setLatLng([v.lat, v.lng]);
        existing.marker.setPopupContent(popupHtml);
      } else {
        const marker = L.marker([v.lat, v.lng], {
          icon: buildExitedIcon(v.plate),
          zIndexOffset: -500,
        }).addTo(eGroup);
        marker.bindPopup(popupHtml);
        exitedMarkers.set(v.plate, { marker });
      }
    });
    for (const [plate, entry] of exitedMarkers) {
      if (!exitedSet.has(plate)) {
        entry.marker.remove();
        exitedMarkers.delete(plate);
      }
    }

    // First-time fit to all zones + exited points
    if (!hasFitRef.current && (zones.length || exited.length)) {
      const pts = [
        ...zones.map((z) => [z.lat, z.lng]),
        ...exited.map((v) => [v.lat, v.lng]),
      ];
      if (pts.length) {
        map.fitBounds(L.latLngBounds(pts), { padding: [50, 50], maxZoom: 8 });
        hasFitRef.current = true;
      }
    }

    // Fly to selected plate
    if (selectedPlate) {
      const sel = vehicles.find((v) => v.plate === selectedPlate);
      if (sel) {
        map.flyTo([sel.lat, sel.lng], 16, { animate: true });
        setIsZoomed(true);
      }
    }
  }, [ready, L, zones, vehicles, exited, selectedPlate, onSelectPlate]);

  const handleResetView = () => {
    const map = mapRef.current;
    if (!map || !L) return;
    const pts = [
      ...zones.map((z) => [z.lat, z.lng]),
      ...exited.map((v) => [v.lat, v.lng]),
    ];
    if (pts.length) {
      map.flyToBounds(L.latLngBounds(pts), { padding: [50, 50], maxZoom: 8 });
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
      <button
        type="button"
        className="mapa-reset-btn"
        onClick={handleResetView}
        aria-label="Ver Colombia completa"
        title="Ver Colombia completa"
      >
        Ver Colombia
      </button>
    </div>
  );
}
