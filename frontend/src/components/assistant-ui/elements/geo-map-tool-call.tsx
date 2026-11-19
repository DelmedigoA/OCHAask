"use client";

import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import { useMemo } from "react";
import { GeoMap, type GeoMapPlace, type GeoMapRoute } from "./geo-map";
import { ToolFallback } from "./tool-fallback.aui";

type GeoMapResult = {
  places: GeoMapPlace[];
  routes: GeoMapRoute[];
};

const tileUrl = import.meta.env.VITE_OCHASK_MAP_TILE_URL?.trim() || undefined;
const tileCredit = import.meta.env.VITE_OCHASK_MAP_ATTRIBUTION?.trim();
const attribution = tileCredit && !["© OpenStreetMap", "© OpenStreetMap contributors"].includes(tileCredit)
  ? `${tileCredit} | <a href="https://www.openstreetmap.org/copyright">© OpenStreetMap</a>`
  : undefined;

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : null;
}

function parsePlace(value: unknown): GeoMapPlace | null {
  const place = asRecord(value);
  if (
    place === null ||
    typeof place.id !== "string" ||
    typeof place.label !== "string" ||
    typeof place.lat !== "number" ||
    !Number.isFinite(place.lat) ||
    place.lat < -90 ||
    place.lat > 90 ||
    typeof place.lng !== "number" ||
    !Number.isFinite(place.lng) ||
    place.lng < -180 ||
    place.lng > 180 ||
    (place.description != null && typeof place.description !== "string")
  ) {
    return null;
  }

  return {
    id: place.id,
    label: place.label,
    lat: place.lat,
    lng: place.lng,
    ...(typeof place.description === "string"
      ? { description: place.description }
      : {}),
  };
}

function isRoute(value: unknown): value is GeoMapRoute {
  const route = asRecord(value);
  return (
    route !== null &&
    typeof route.id === "string" &&
    (route.label === undefined || typeof route.label === "string") &&
    Array.isArray(route.points) &&
    route.points.length >= 2 &&
    route.points.every(
      (point) =>
        Array.isArray(point) &&
        point.length === 2 &&
        typeof point[0] === "number" &&
        Number.isFinite(point[0]) &&
        point[0] >= -90 &&
        point[0] <= 90 &&
        typeof point[1] === "number" &&
        Number.isFinite(point[1]) &&
        point[1] >= -180 &&
        point[1] <= 180,
    )
  );
}

function parseGeoMapResult(value: unknown): GeoMapResult | null {
  let parsed = value;
  if (typeof parsed === "string") {
    try {
      parsed = JSON.parse(parsed) as unknown;
    } catch {
      return null;
    }
  }

  const result = asRecord(parsed);
  if (!result || !Array.isArray(result.places)) return null;

  const places = result.places.flatMap((place) => {
    const parsedPlace = parsePlace(place);
    return parsedPlace ? [parsedPlace] : [];
  });
  const routes = Array.isArray(result.routes)
    ? result.routes.filter(isRoute)
    : [];

  return { places, routes };
}

export const GeoMapToolCall: ToolCallMessagePartComponent<unknown, unknown> = (
  props,
) => {
  const result = useMemo(() => parseGeoMapResult(props.result), [props.result]);
  if (
    props.status.type === "requires-action" ||
    props.approval != null ||
    props.interrupt != null
  ) {
    return <ToolFallback {...props} />;
  }

  if (props.status.type === "running") return null;
  if (props.status.type === "incomplete" || props.isError) {
    return <ToolFallback {...props} />;
  }

  if (!result) return <ToolFallback {...props} />;
  if (result.places.length === 0 && result.routes.length === 0) {
    return <p className="text-muted-foreground py-2 text-sm">No locations or routes to display.</p>;
  }

  return (
    <GeoMap
      data-slot="geo-map-tool-result"
      places={result.places}
      routes={result.routes}
      height={320}
      tileUrl={tileUrl}
      attribution={attribution}
      className="min-w-0 max-w-full"
    />
  );
};
