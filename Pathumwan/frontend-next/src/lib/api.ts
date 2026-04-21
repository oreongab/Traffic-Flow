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
  AIStatus,
  Junction,
  HourlyVehicleCount,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:5000/api";

const api = axios.create({
  baseURL: API_BASE,
  headers: { "Content-Type": "application/json" },
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

export const getMe = () => api.get<User>("/auth/me");

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
    return {
      index: res.data.index ?? 0,
      level: res.data.level ?? "คล่องตัว",
      color: res.data.color ?? "#22c55e",
      roads: res.data.roads ?? [],
      timestamp: res.data.timestamp ?? "",
      source: res.data.source ?? "unknown",
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
    const res = await api.get("/cameras/");
    return res.data.cameras || [];
  });
}

export function getCameraFrameUrl(cameraId: string): string {
  return `${API_BASE}/cameras/${cameraId}/frame`;
}

export function getCameraStreamUrl(cameraId: string): string {
  return `${API_BASE}/cameras/${cameraId}/stream`;
}

export function getCameraDetectStreamUrl(cameraId: string): string {
  return `${API_BASE}/cameras/${cameraId}/detect/stream`;
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
    const res = await api.get(`/cameras/${cameraId}/counts`);
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
    const res = await api.get(`/cameras/${cameraId}/vehicles${params}`);
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

export const setManualSignal = (junction_id: string, state: string) =>
  api.post("/admin/signal/manual", { junction_id, state });

export async function getAIStatus(): Promise<AIStatus> {
  const res = await api.get("/admin/ai-status");
  return res.data;
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
