"use client";

import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";

interface MapViewProps {
  center?: [number, number];
  zoom?: number;
}

export default function MapView({ center = [116.4, 39.9], zoom = 10 }: MapViewProps) {
  const mapContainer = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);

  useEffect(() => {
    if (!mapContainer.current || map.current) return;

    map.current = new maplibregl.Map({
      container: mapContainer.current,
      style: {
        version: 8,
        sources: {
          "osm-tile": {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            attribution: "© OpenStreetMap contributors"
          }
        },
        layers: [
          {
            id: "osm-layer",
            source: "osm-tile",
            type: "raster",
            minzoom: 0,
            maxzoom: 19
          }
        ]
      },
      center,
      zoom,
      
    });

    map.current.addControl(new maplibregl.NavigationControl(), "top-right");
    map.current.addControl(new maplibregl.ScaleControl(), "bottom-left");

    return () => {
      map.current?.remove();
      map.current = null;
    };
  }, []);

  return (
    <div ref={mapContainer} style={{ width: "100%", height: "100%" }} />
  );
}