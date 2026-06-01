export interface User {
  id: number;
  username: string;
  email: string;
  role: "user" | "admin";
  created_at?: string;
}

export interface AuthResponse {
  token: string;
  user: User;
}

export interface Vehicle {
  id: string;
  lat: number;
  lng: number;
  speed: number;
  type: string;
  color?: string;
}

export interface TrafficLight {
  id: string;
  lat: number;
  lng: number;
  state: "green" | "yellow" | "red";
  junction_name: string;
}

export interface TrafficIndexData {
  index: number;
  level: string;
  color: string;
  roads: TrafficIndexRoad[];
  timestamp: string;
  source?: string;
  source_label?: string;
  research_note?: string;
  provenance_summary?: Record<string, number>;
  scope?: {
    monitored_road_count?: number;
    ai_junction_count?: number;
    pathumwan_ai_junction_count?: number;
    ratchathewi_feeder_junction_count?: number;
    note?: string;
  };
  freshness_seconds?: number;
  // True when at least one road actually has live data; false means the
  // backend has nothing to report yet (e.g. SUMO not running, no detections).
  // Pages should render "ไม่มีข้อมูล" instead of `0` when this is false.
  data_available?: boolean;
}

export interface TrafficIndexRoad {
  road: string;
  index: number;
  speed: number;
  free_flow_speed: number;
  vehicle_count?: number;
  detected_vehicle_count?: number;
  source?: string;
  source_label?: string;
  metric_source?: string;
  is_fallback?: boolean;
  timestamp?: string;
}

export interface RoadDensity {
  road: string;
  road_id?: string;
  density: number;
  speed: number;
  vehicle_count: number;
  travel_time: string;
  index: number;
  level: string;
  free_flow_speed?: number;
  detected_vehicle_count?: number;
  timestamp?: string;
  source?: string;
  source_label?: string;
  metric_source?: string;
  is_fallback?: boolean;
  research_note?: string;
  provenance_summary?: Record<string, number>;
  scope?: {
    monitored_road_count?: number;
    ai_junction_count?: number;
    pathumwan_ai_junction_count?: number;
    ratchathewi_feeder_junction_count?: number;
    note?: string;
  };
  freshness_seconds?: number;
  has_data?: boolean;
}

export interface RoadGeometry {
  road_id: string;
  road: string;
  segments: [number, number][][];
  bbox?: {
    min_lat: number;
    min_lng: number;
    max_lat: number;
    max_lng: number;
  } | null;
}

export interface Camera {
  id: number;
  camera_id: string;
  name: string;
  display_name?: string;
  location_hint?: string;
  road: string;
  road_id?: string;
  lat: number;
  lng: number;
  junction?: string;
  junction_id?: string;
  sumo_tls_id?: string;
  stream_status?: string;
  freshness_seconds?: number;
  research_target?: boolean;
  research_order?: number;
}

export interface CameraCounts {
  car: number;
  motorcycle: number;
  bus: number;
  truck: number;
  total: number;
}

export interface CameraCountsSnapshot {
  camera_id: string;
  counts: CameraCounts;
  timestamp: string;
  source?: string;
  stream_status?: string;
}

export interface CameraVehicleSnapshot {
  vehicles: {
    id: string;
    class: string;
    speed: number;
    lat: number;
    lng: number;
    distance: number;
  }[];
  counts: CameraCounts;
  timestamp: string;
  source?: string;
}

export interface CameraRuntimeCalibration {
  camera_id: string;
  image_width: number;
  image_height: number;
  homography_matrix: number[][];
  anchor_lat: number;
  anchor_lng: number;
  bearing_deg: number;
  pixels_per_meter: number;
  updated_at?: string | null;
}

export interface CameraRuntimeZone {
  camera_id: string;
  zone_id: string;
  junction_id: string;
  approach_id: string;
  road_id: string;
  zone_type: string;
  polygon_points: number[][];
  line_points: number[][];
  enabled: boolean;
}

export interface CameraRuntimeStatus {
  camera_id: string;
  camera_name: string;
  road_id: string;
  junction_id: string;
  status: string;
  calibration_ready: boolean;
  calibration?: CameraRuntimeCalibration | null;
  zone_count: number;
  enabled_zone_count: number;
  zone_types: string[];
  tracking_ready: boolean;
  latest_metric_at?: string | null;
}

export interface CameraRuntimeBundle {
  camera: {
    camera_id: string;
    name: string;
    road_id: string;
    junction_id: string;
    lat: number;
    lng: number;
    status: string;
  };
  calibration?: CameraRuntimeCalibration | null;
  zones: CameraRuntimeZone[];
  status: {
    calibration_ready: boolean;
    zone_count: number;
    enabled_zone_count: number;
    tracking_ready: boolean;
    latest_metric_at?: string | null;
  };
}

export interface HourlyIndex {
  hour: number;
  time: string;
  index: number;
}

export interface WeeklyIndex {
  date: string;
  max_index: number;
  avg_index: number;
}

export interface YearlyStat {
  date: string;
  time: string;
  max_index: number;
  peak_index?: number;
  peak_time?: string;
  avg_index?: number;
}

export interface DailyCount {
  date: string;
  total_vehicles: number;
}

export interface TopRoad {
  road: string;
  avg_max_index: number;
}

export interface SimStatus {
  step: number;
  vehicle_count: number;
  active: boolean;
}

export interface AIDecision {
  junction_id: string;
  phase: number;
  score?: number;
  cars?: number;
  cameras?: number;
  timestamp: number | string;
  algorithm?: string;
  method?: string;
  current_phase?: number;
  queue_length?: number;
  waiting_time?: number;
  avg_speed_kmh?: number;
  applied?: boolean;
  error?: string;
}

export interface AIAlgorithmOption {
  id: string;
  label: string;
  model_available: boolean;
}

export interface AIDecisionHistoryEntry {
  id: number;
  junction_id: string;
  timestamp?: string | null;
  reward: number;
  model_version: string;
  input_data: Record<string, unknown>;
  output: Record<string, unknown>;
}

export interface SystemLogEntry {
  timestamp: string;
  message: string;
  level: string;
}

export interface AIStatus {
  mode: "ai" | "manual";
  active: boolean;
  decisions: number;
  algorithm?: string;
  ai_mode?: boolean;
  simulation_active?: boolean;
  runtime_ready?: boolean;
  step?: number;
  camera_count?: number;
  research_junction_count?: number;
  pathumwan_research_junction_count?: number;
  ratchathewi_feeder_junction_count?: number;
  backends?: {
    system_mode?: string;
    signal_backend?: string;
    camera_backend?: string;
    ai_backend?: string;
    signal_mode?: string;
  };
  last_decisions?: AIDecision[];
}

export interface SignalPhase {
  index: number;
  state: string;
  duration: number;
  minDur: number;
  maxDur: number;
  type: "green" | "yellow" | "red";
}

export interface Junction {
  id: string;
  name: string;
  display_name_th?: string;
  camera_id: string;
  lat: number;
  lng: number;
  current_state: string;
  current_phase: number;
  time_to_switch: number;
  phases: SignalPhase[];
}

export interface HourlyVehicleCount {
  hour: string;
  road_id: string;
  road_name: string;
  car: number;
  motorcycle: number;
  bus: number;
  truck: number;
  total: number;
}
