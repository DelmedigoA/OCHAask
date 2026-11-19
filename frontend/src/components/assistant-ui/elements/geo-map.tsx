"use client";

import "leaflet/dist/leaflet.css";

import type { Map as LeafletMap, Marker as LeafletMarker } from "leaflet";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";
import { field, mono, paper } from "./surfaces";

export interface GeoMapPlace {
  id: string;
  lat: number;
  lng: number;
  label: string;
  description?: string | undefined;
}

export interface GeoMapRoute {
  id: string;
  points: readonly (readonly [lat: number, lng: number])[];
  label?: string | undefined;
}

export interface GeoMapProps extends Omit<
  ComponentProps<"div">,
  "children" | "onSelect"
> {
  places: readonly GeoMapPlace[];
  routes?: readonly GeoMapRoute[] | undefined;
  selectedId?: string | undefined;
  onSelect?: ((id: string) => void) | undefined;
  theme?: "light" | "dark" | "auto" | undefined;
  tileUrl?: string | undefined;
  attribution?: string | undefined;
  height?: number | undefined;
}

type LeafletModule = typeof import("leaflet");
type Coordinate = [lat: number, lng: number];

const DEFAULT_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";

const DEFAULT_ATTRIBUTION = '<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap</a>';

function isCoordinate(lat: number, lng: number) {
  return (
    Number.isFinite(lat) &&
    Number.isFinite(lng) &&
    lat >= -90 &&
    lat <= 90 &&
    lng >= -180 &&
    lng <= 180
  );
}

function markerIcon(leaflet: LeafletModule, selected: boolean) {
  const size = selected ? 16 : 10;
  return leaflet.divIcon({
    className: "geo-map-marker",
    html: selected
      ? '<span class="block size-4 rounded-full bg-blue-500 ring-2 ring-background"></span>'
      : '<span class="block size-2.5 rounded-full bg-foreground/55 ring-2 ring-background"></span>',
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

const TILE_INK = {
  light: "[&_.leaflet-tile-pane]:contrast-90 [&_.leaflet-tile-pane]:grayscale",
  dark: "[&_.leaflet-tile-pane]:brightness-90 [&_.leaflet-tile-pane]:contrast-90 [&_.leaflet-tile-pane]:grayscale [&_.leaflet-tile-pane]:hue-rotate-180 [&_.leaflet-tile-pane]:invert",
  auto: "[&_.leaflet-tile-pane]:contrast-90 [&_.leaflet-tile-pane]:grayscale dark:[&_.leaflet-tile-pane]:brightness-90 dark:[&_.leaflet-tile-pane]:hue-rotate-180 dark:[&_.leaflet-tile-pane]:invert",
} as const;

export function GeoMap({
  places,
  routes,
  selectedId,
  onSelect,
  theme = "auto",
  tileUrl,
  attribution,
  height = 260,
  className,
  ...props
}: GeoMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const leafletRef = useRef<LeafletModule | null>(null);
  const markersRef = useRef(new Map<string, LeafletMarker>());
  const [mapReady, setMapReady] = useState(false);
  const mapContent = useMemo(() => {
    const validPlaces = places.filter((place) =>
      isCoordinate(place.lat, place.lng),
    );
    const validRoutes =
      routes
        ?.filter((route) => route.points.length > 1 &&
          route.points.every(([lat, lng]) => isCoordinate(lat, lng)))
        .map((route) => ({ ...route, points: route.points.map(([lat, lng]) => [lat, lng] as Coordinate) })) ?? [];

    return {
      signature: JSON.stringify([
        places.map((place) => [
          place.id,
          place.lat,
          place.lng,
          place.label,
          place.description,
        ]),
        routes?.map((route) => [route.id, route.points]) ?? [],
      ]),
      validPlaces,
      validRoutes,
    };
  }, [places, routes]);
  const { signature: contentSignature, validPlaces } = mapContent;
  const mapContentRef = useRef(mapContent);
  const resolvedTileUrl = tileUrl ?? DEFAULT_TILE_URL;
  const resolvedAttribution = attribution ?? DEFAULT_ATTRIBUTION;
  const [uncontrolledSelectedId, setUncontrolledSelectedId] = useState<
    string | undefined
  >(() => validPlaces[0]?.id);
  const activeId =
    selectedId ??
    (validPlaces.some((place) => place.id === uncontrolledSelectedId)
      ? uncontrolledSelectedId
      : validPlaces[0]?.id);
  const activeIdRef = useRef(activeId);

  const selectPlace = useCallback(
    (place: GeoMapPlace) => {
      if (selectedId === undefined) setUncontrolledSelectedId(place.id);
      onSelect?.(place.id);
      mapRef.current?.panTo([place.lat, place.lng]);
    },
    [onSelect, selectedId],
  );
  const selectPlaceRef = useRef(selectPlace);

  useEffect(() => {
    mapContentRef.current = mapContent;
    activeIdRef.current = activeId;
    selectPlaceRef.current = selectPlace;
  }, [activeId, mapContent, selectPlace]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;

    let cancelled = false;
    let map: LeafletMap | null = null;
    setMapReady(false);
    void (async () => {
      const leaflet = await import("leaflet");
      if (cancelled) return;

      leafletRef.current = leaflet;
      map = leaflet.map(container, { keyboard: true });
      map.attributionControl.setPrefix(false);
      mapRef.current = map;

      leaflet
        .tileLayer(resolvedTileUrl, {
          attribution: resolvedAttribution,
        })
        .addTo(map);
      setMapReady(true);
    })();

    return () => {
      cancelled = true;
      if (mapRef.current === map) mapRef.current = null;
      map?.remove();
    };
  }, [resolvedAttribution, resolvedTileUrl]);

  useEffect(() => {
    const leaflet = leafletRef.current;
    const map = mapRef.current;
    const container = containerRef.current;
    if (!leaflet || !map || !container || !mapReady) return;

    const { validPlaces, validRoutes } = mapContentRef.current;
    const markers = markersRef.current;
    const layer = leaflet.layerGroup().addTo(map);
    const bounds = leaflet.latLngBounds([
      ...validPlaces.map((place) => [place.lat, place.lng] as Coordinate),
      ...validRoutes.flatMap((route) => route.points),
    ]);
    let lastWidth = 0;
    let lastHeight = 0;
    const fit = () => {
      const { clientWidth: width, clientHeight: height } = container;
      if (!width || !height || (width === lastWidth && height === lastHeight)) return;
      lastWidth = width;
      lastHeight = height;
      map.invalidateSize({ pan: false });
      if (bounds.isValid()) {
        map.fitBounds(bounds, { padding: [24, 24], maxZoom: 13, animate: false });
      } else {
        map.setView([0, 0], 2);
      }
    };
    fit();
    const resize = new ResizeObserver(fit);
    resize.observe(container);

    for (const route of validRoutes) {
      leaflet
        .polyline(route.points, {
          className: "stroke-foreground/70",
          weight: 2,
        })
        .addTo(layer);
    }

    for (const place of validPlaces) {
      const marker = leaflet
        .marker([place.lat, place.lng], {
          icon: markerIcon(leaflet, place.id === activeIdRef.current),
          title: place.label,
        })
        .addTo(layer)
        .on("click", () => selectPlaceRef.current(place));
      markers.set(place.id, marker);
    }

    return () => {
      resize.disconnect();
      markers.clear();
      layer.remove();
    };
  }, [contentSignature, mapReady]);

  useEffect(() => {
    const leaflet = leafletRef.current;
    if (!leaflet) return;

    for (const place of mapContentRef.current.validPlaces) {
      markersRef.current
        .get(place.id)
        ?.setIcon(markerIcon(leaflet, place.id === activeId));
    }
  }, [activeId, contentSignature]);

  return (
    <div
      data-slot="geo-map"
      className={cn(
        paper,
        "flex w-full min-w-0 max-w-sm flex-col overflow-hidden rounded-2xl p-2",
        className,
      )}
      {...props}
    >
      <div
        ref={containerRef}
        role="region"
        aria-label={mapContent.validRoutes.length ? "Map of places and routes" : `Map of ${validPlaces.length} places`}
        aria-busy={!mapReady || undefined}
        style={{ height }}
        data-theme={theme}
        className={cn(
          field,
          "relative overflow-hidden rounded-xl",
          "[&_.leaflet-bar]:border-foreground/10! [&_.leaflet-bar_a]:border-foreground/10! [&_.leaflet-bar_a]:bg-background! [&_.leaflet-bar_a]:text-foreground/80! [&_.leaflet-bar_a:hover]:bg-foreground/[0.06]! [&_.leaflet-bar]:shadow-none!",
          "[&_.leaflet-control-attribution]:bg-background/80! [&_.leaflet-control-attribution]:text-muted-foreground! [&_.leaflet-control-attribution_a]:text-foreground/70!",
          tileUrl === undefined && TILE_INK[theme],
        )}
      >
        {!mapReady ? (
          <span className="text-muted-foreground absolute inset-0 flex items-center justify-center text-xs">
            Loading map
          </span>
        ) : null}
      </div>

      <ol className="mt-2 flex flex-col gap-0.5">
        {validPlaces.map((place) => {
          const selected = place.id === activeId;
          return (
            <li key={place.id}>
              <button
                type="button"
                aria-current={selected || undefined}
                onClick={() => selectPlace(place)}
                className={cn(
                  "hover:bg-foreground/[0.035] focus-visible:ring-foreground/20 flex w-full min-w-0 flex-wrap items-start gap-x-3 gap-y-1 rounded-lg px-2.5 py-2 text-start transition-colors outline-none focus-visible:ring-1",
                  selected && "bg-foreground/[0.06]",
                )}
              >
                <span className="min-w-0 grow basis-36">
                  <span className="text-foreground/90 block text-[13.5px] leading-5 break-words">
                    {place.label}
                  </span>
                  {place.description ? (
                    <span className="text-muted-foreground block text-xs leading-4 break-words">
                      {place.description}
                    </span>
                  ) : null}
                </span>
                <span
                  data-slot="geo-map-coordinates"
                  className={cn(
                    mono,
                    "text-muted-foreground max-w-full shrink-0 pt-0.5 tabular-nums break-words",
                  )}
                >
                  {place.lat.toFixed(4)}, {place.lng.toFixed(4)}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
