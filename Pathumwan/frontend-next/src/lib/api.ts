import axios from "axios";
import type {
  AuthResponse,
  User,
  Vehicle,
  TrafficLight,
  TrafficIndexData,
  RoadDensity,
  RoadGeometry,
  Camera,
  CameraCountsSnapshot,
  CameraVehicleSnapshot,
  CameraRuntimeBundle,
  CameraRuntimeStatus,
  HourlyIndex,
  WeeklyIndex,
  YearlyStat,
  TopRoad,
  DailyCount,
  AIAlgorithmOption,
  AIDecisionHistoryEntry,
  AIStatus,
  Junction,
  HourlyVehicleCount,
  SystemLogEntry,
} from "./types";

function resolveApiBase(): string {
  if (typeof window !== "undefined") {
    return process.env.NEXT_PUBLIC_API_URL || "/api";
  }

  const backendInternalUrl = process.env.BACKEND_INTERNAL_URL?.replace(/\/$/, "");

  return (
    process.env.INTERNAL_API_BASE ||
    (backendInternalUrl ? `${backendInternalUrl}/api` : undefined) ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://127.0.0.1:5000/api"
  );
}

const API_BASE = resolveApiBase();

function resolveMediaApiBase(): string {
  const explicit =
    process.env.NEXT_PUBLIC_STREAM_API_URL ||
    process.env.NEXT_PUBLIC_MEDIA_API_URL;
  if (explicit) {
    return explicit.replace(/\/$/, "");
  }

  if (typeof window !== "undefined") {
    const publicApi = process.env.NEXT_PUBLIC_API_URL;
    if (publicApi && !publicApi.startsWith("/")) {
      return publicApi.replace(/\/$/, "");
    }

    const { protocol, hostname, port } = window.location;
    const isHttpDev = protocol === "http:" && port && port !== "5000";
    if (isHttpDev) {
      return `${protocol}//${hostname}:5000/api`;
    }
    return "/api";
  }

  return API_BASE;
}

const MEDIA_API_BASE = resolveMediaApiBase();
const SAME_ORIGIN_MEDIA_API_BASE = "/api";

function cameraMediaUrl(base: string, cameraId: string, path: string): string {
  return `${base}/cameras/${encodeURIComponent(cameraId)}${path}`;
}

const api = axios.create({
  baseURL: API_BASE,
  headers: { "Content-Type": "application/json" },
  timeout: 8000,
});

type CacheEntry<T> = {
  value?: T;
  expiresAt: number;
  inFlight?: Promise<T>;
};

const responseCache = new Map<string, CacheEntry<unknown>>();

async function getCached<T>(cacheKey: string, ttlMs: number, loader: () => Promise<T>): Promise<T> {
  const now = Date.now();
  const existing = responseCache.get(cacheKey) as CacheEntry<T> | undefined;
  if (existing?.value !== undefined && existing.expiresAt > now) {
    return existing.value;
  }
  if (existing?.inFlight) {
    return existing.inFlight;
  }

  const inFlight = loader().then((value) => {
    responseCache.set(cacheKey, { value, expiresAt: Date.now() + ttlMs });
    return value;
  }).catch((error) => {
    responseCache.delete(cacheKey);
    throw error;
  });

  responseCache.set(cacheKey, {
    value: existing?.value,
    expiresAt: existing?.expiresAt ?? 0,
    inFlight,
  });

  return inFlight;
}

api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("token");
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  }
  return config;
});

api.interceptors.response.use(
  (res) => res,
  (error) => {
    if (error.response?.status === 401 && typeof window !== "undefined") {
      localStorage.removeItem("token");
      window.location.href = "/login";
    }
    return Promise.reject(error);
  }
);

// Auth
export const login = (identifier: string, password: string) =>
  api.post<AuthResponse>("/auth/login", { email: identifier, password });

export const register = (username: string, email: string, password: string) =>
  api.post<AuthResponse>("/auth/register", { username, email, password });

export const resetPassword = (identifier: string, newPassword: string) =>
  api.post("/auth/reset-password", { email: identifier, new_password: newPassword });

export async function getMe() {
  const res = await api.get<{ success?: boolean; user?: User } | User>("/auth/me");
  const payload = res.data;
  const user = (typeof payload === "object" && payload !== null && "user" in payload)
    ? payload.user
    : payload;
  return {
    ...res,
    data: user as User,
  };
}

export const updateProfile = (data: { username?: string; email?: string; password?: string }) =>
  api.put("/auth/profile", data);

export const deactivateAccount = () => api.post("/auth/deactivate");

// Traffic — unwrap from {status, vehicles/...}
export async function getVehicles(): Promise<Vehicle[]> {
  return getCached("vehicles", 1200, async () => {
    const res = await api.get("/vehicles");
    return res.data.vehicles || [];
  });
}

export async function getTrafficLights(): Promise<TrafficLight[]> {
  return getCached("traffic-lights", 3000, async () => {
    const res = await api.get("/traffic-lights");
    return res.data.lights || [];
  });
}

export async function getTrafficIndex(): Promise<TrafficIndexData> {
  return getCached("traffic-index", 2500, async () => {
    const res = await api.get("/traffic-index");
    // Preserve null when the backend reports no data so the UI can show
    // "ไม่มีข้อมูล" instead of a misleading `0`. Older callers that read
    // `index` as a number still get a number when data_available is true.
    const dataAvailable = res.data.data_available ?? true;
    return {
      index: dataAvailable ? (res.data.index ?? 0) : 0,
      level: res.data.level ?? "คล่องตัว",
      color: res.data.color ?? "#22c55e",
      roads: res.data.roads ?? [],
      timestamp: res.data.timestamp ?? "",
      source: res.data.source ?? "unknown",
      source_label: res.data.source_label ?? undefined,
      research_note: res.data.research_note ?? undefined,
      provenance_summary: res.data.provenance_summary ?? undefined,
      scope: res.data.scope ?? undefined,
      data_available: dataAvailable,
    };
  });
}

export async function getDensity(): Promise<RoadDensity[]> {
  return getCached("road-density", 2500, async () => {
    const res = await api.get("/road-density");
    return res.data.roads || [];
  });
}

export async function getRoadGeometries(): Promise<RoadGeometry[]> {
  return getCached("road-geometries", 300000, async () => {
    const res = await api.get("/roads/geometry");
    return res.data.roads || [];
  });
}

// Cameras — unwrap from {cameras: [...]}
export async function getCameras(): Promise<Camera[]> {
  return getCached("cameras", 8000, async () => {
    const res = await api.get("/cameras");
    return res.data.cameras || [];
  });
}

export function getCameraFrameUrl(cameraId: string): string {
  return cameraMediaUrl(MEDIA_API_BASE, cameraId, "/frame");
}

export function getCameraDetectFrameUrl(cameraId: string): string {
  return cameraMediaUrl(MEDIA_API_BASE, cameraId, "/detect");
}

export function getCameraSameOriginFrameUrl(cameraId: string): string {
  return cameraMediaUrl(SAME_ORIGIN_MEDIA_API_BASE, cameraId, "/frame");
}

export function getCameraSameOriginDetectFrameUrl(cameraId: string): string {
  return cameraMediaUrl(SAME_ORIGIN_MEDIA_API_BASE, cameraId, "/detect");
}

export function getCameraStreamUrl(cameraId: string): string {
  return cameraMediaUrl(MEDIA_API_BASE, cameraId, "/stream");
}

export function getCameraDetectStreamUrl(cameraId: string): string {
  return cameraMediaUrl(MEDIA_API_BASE, cameraId, "/detect/stream");
}

export function getCameraAnalyticsStreamUrl(cameraId: string, detect = true): string {
  return `${cameraMediaUrl(MEDIA_API_BASE, cameraId, "/analytics/stream")}?detect=${detect ? "true" : "false"}`;
}

export function getCameraSameOriginStreamUrl(cameraId: string): string {
  return cameraMediaUrl(SAME_ORIGIN_MEDIA_API_BASE, cameraId, "/stream");
}

export function getCameraSameOriginDetectStreamUrl(cameraId: string): string {
  return cameraMediaUrl(SAME_ORIGIN_MEDIA_API_BASE, cameraId, "/detect/stream");
}

export function getCameraSameOriginAnalyticsStreamUrl(cameraId: string, detect = true): string {
  return `${cameraMediaUrl(SAME_ORIGIN_MEDIA_API_BASE, cameraId, "/analytics/stream")}?detect=${detect ? "true" : "false"}`;
}

export async function getCameraRuntimeStatuses(): Promise<CameraRuntimeStatus[]> {
  const res = await api.get("/admin/camera-runtime");
  return res.data.cameras || [];
}

export async function getCameraRuntimeBundle(cameraId: string): Promise<CameraRuntimeBundle> {
  const res = await api.get(`/admin/camera-runtime/${cameraId}`);
  return {
    camera: res.data.camera,
    calibration: res.data.calibration || null,
    zones: res.data.zones || [],
    status: res.data.status,
  };
}

export async function getCameraCounts(cameraId: string): Promise<CameraCountsSnapshot> {
  return getCached(`camera-counts:${cameraId}`, 1200, async () => {
    const res = await api.get(`/cameras/${encodeURIComponent(cameraId)}/counts`);
    return {
      camera_id: res.data.camera_id || cameraId,
      counts: res.data.counts || { car: 0, motorcycle: 0, bus: 0, truck: 0, total: 0 },
      timestamp: res.data.timestamp || "",
      source: res.data.source || "unknown",
      stream_status: res.data.stream_status || "offline",
    };
  });
}

export async function getCameraVehicles(cameraId: string, radius?: number): Promise<CameraVehicleSnapshot> {
  const params = radius ? `?radius=${radius}` : "";
  return getCached(`camera-vehicles:${cameraId}:${radius ?? "default"}`, 1200, async () => {
    const res = await api.get(`/cameras/${encodeURIComponent(cameraId)}/vehicles${params}`);
    return {
      vehicles: res.data.vehicles || [],
      counts: res.data.counts || { car: 0, motorcycle: 0, bus: 0, truck: 0, total: 0 },
      timestamp: res.data.timestamp || "",
      source: res.data.source || "unknown",
    };
  });
}

// Stats — unwrap from {data: [...]}
export async function getIndexToday(): Promise<HourlyIndex[]> {
  const res = await api.get("/stats/index-today");
  return res.data.data || [];
}

export async function getIndexWeekly(): Promise<WeeklyIndex[]> {
  const res = await api.get("/stats/index-weekly");
  return res.data.data || [];
}

export async function getYearlyStats(year: number): Promise<YearlyStat[]> {
  const res = await api.get(`/stats/yearly/${year}`);
  return res.data.data || [];
}

export async function getTopRoads(year: number): Promise<TopRoad[]> {
  const res = await api.get(`/stats/top-roads/${year}`);
  return res.data.data || [];
}

export async function getDailyCount(year: number): Promise<DailyCount[]> {
  const res = await api.get(`/stats/daily-count/${year}`);
  return res.data.data || [];
}

export async function getAvailableYears(): Promise<number[]> {
  const res = await api.get("/stats/available-years");
  return res.data.years || [];
}

// Admin
export const setSignalMode = (mode: "ai" | "manual") =>
  api.post("/admin/signal/mode", { mode });

export type SignalDirection = "all" | "ns" | "ew" | "n" | "e" | "s" | "w";

export const setManualSignal = (
  junction_id: string,
  state: string,
  direction: SignalDirection = "all"
) => api.post("/admin/signal/manual", { junction_id, state, direction });

export async function getAIStatus(): Promise<AIStatus> {
  const res = await api.get("/admin/ai-status");
  return res.data;
}

export async function getAIAlgorithms(): Promise<{ current: string; algorithms: AIAlgorithmOption[] }> {
  const res = await api.get("/admin/ai/algorithms");
  return {
    current: String(res.data.current || ""),
    algorithms: res.data.algorithms || [],
  };
}

export const setAIAlgorithm = (algorithm: string) =>
  api.post("/admin/ai/algorithm", { algorithm });

export async function getAIDecisionHistory(limit = 30): Promise<AIDecisionHistoryEntry[]> {
  const res = await api.get(`/admin/ai/decisions?limit=${limit}`);
  return res.data.decisions || [];
}

export async function getAdminLogs(): Promise<SystemLogEntry[]> {
  const res = await api.get("/admin/logs");
  return res.data || [];
}

// Signal control
export async function getJunctions(): Promise<Junction[]> {
  const res = await api.get("/admin/signal/junctions");
  return res.data.junctions || [];
}

export const setSignalPhase = (junction_id: string, phases: { index: number; duration: number }[]) =>
  api.post("/admin/signal/phase", { junction_id, phases });

// Hourly vehicle counts
export async function getHourlyVehicleCounts(date?: string): Promise<HourlyVehicleCount[]> {
  const params = date ? `?date=${date}` : "";
  const res = await api.get(`/stats/hourly-counts${params}`);
  return res.data.data || [];
}

export async function getRoadVehicleHistory(road_id: string, days?: number): Promise<HourlyVehicleCount[]> {
  const params = days ? `?days=${days}` : "";
  const res = await api.get(`/stats/road-history/${road_id}${params}`);
  return res.data.data || [];
}

export default api;
