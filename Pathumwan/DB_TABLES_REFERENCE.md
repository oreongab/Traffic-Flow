# Database Tables Reference

This document matches `backend/database/models.py` as of 2026-04-21.

> **Database engine:** Neon PostgreSQL (serverless Postgres) in production. SQLite is used only as a local dev fallback.

## Reading Guide

- The database has **16 persisted tables**.
- Business keys used across the system are mainly `camera_id`, `road_id`, and `junction_id`.
- Several tables use JSON columns so the backend can keep flexible payloads without creating many tiny child tables.
- Alias names in Python (`CameraStream`, `CameraCalibration`, `CameraZone`) are not separate tables — they all point to the `cameras` table.
- `LiveVehicleTrack` and `LiveApproachMetric` are set to `None` in code — tracker data is kept in-memory only.
- **Retention**: append-only time-series tables (detections, density, index, signal states, logs, ai decisions) are pruned by `backend/services/daily_stats.py::purge_old_raw_data()`. See the Retention column below and [`DB_DIAGRAM.md`](DB_DIAGRAM.md) for details.

## Quick Domain Summary

| Table | What it stores | Why it exists | Main relations | Growth | Retention |
| --- | --- | --- | --- | --- | --- |
| `users` | Login identities and roles | Access control for admin/user flows | standalone | very slow | kept |
| `runtime_config` | Small key-value runtime settings | Persist lightweight system flags/config | standalone | very slow | kept |
| `roads` | Canonical monitored roads | Shared reference for all road-level metrics | parent of `cameras`, `approaches`, `road_density`, `hourly_vehicle_counts` | static | kept |
| `junctions` | Canonical controlled junctions | Shared reference for signal control and approach mapping | parent of `cameras`, `approaches`, `signal_states`, `signal_timings`, `signal_controllers`, `ai_decisions` | static | kept |
| `cameras` | Camera inventory plus stream/calibration/zone config | Single source of truth for camera metadata | child of `roads` / `junctions`, parent of `traffic_detections`, referenced by `approaches` | ~55 rows | kept |
| `approaches` | Junction approach definitions | Connect each approach to road and optional camera | child of `junctions`, `roads`, `cameras` | ~4× junctions | kept |
| `traffic_detections` | Per-camera detection snapshots | Save YOLO/detection output over time | child of `cameras` | **fastest** (55 cams × every 5s) | **7 days** |
| `road_density` | Per-road derived density snapshots | Drive road analytics and UI density views | child of `roads` | every index tick × 10 roads | 30 days |
| `traffic_index` | Area-level traffic index snapshots | Keep one row per area/timestamp with rolled-up road context | standalone JSON payload referencing roads conceptually | every index tick | 30 days |
| `hourly_vehicle_counts` | Hourly road aggregates | Support charts and historical statistics | child of `roads` | 24 × 10 / day | kept |
| `signal_states` | Observed signal runtime state | Show what a controller/junction is doing right now or recently | child of `junctions` | every signal tick × N junctions | 30 days |
| `signal_timings` | Persisted timing plans/decisions | Keep chosen phase durations over time | child of `junctions` | per decision | kept |
| `signal_controllers` | Controller connection metadata | Store how the system talks to or simulates controllers | child of `junctions` | static | kept |
| `ai_decisions` | AI input/output/reward history | Audit and inspect AI control decisions | child of `junctions` | per AI decision | 30 days |
| `historical_stats` | Daily historical summaries | Power day-level reporting and rankings | standalone summary table | 1 row / day | kept |
| `system_logs` | Backend event records | Diagnostics and operational trace | standalone append-only log | varies | 30 days |

## Detailed Table Notes

### `users`

- Stores authentication identity and authorization role.
- **Columns**: `id` (PK), `username` (unique), `email` (unique), `password_hash`, `role` (default `"user"`), `is_active` (bool), `created_at`, `updated_at`.
- **Purpose**: Support login, role gating, and admin management.
- **Relations**: None to traffic tables; intentionally isolated from runtime traffic data.

### `runtime_config`

- Stores low-volume key/value settings such as runtime toggles or last-known app settings.
- **Columns**: `id` (PK), `config_key` (unique), `config_value` (text), `updated_at`.
- **Purpose**: Keep lightweight mutable configuration without adding bespoke tables.
- **Relations**: Standalone; referenced logically by backend services, not by foreign key.
- **Examples**: `system_mode`, `camera_backend`, `signal_backend`, `ai_backend`.

### `roads`

- Canonical road registry used across density, counts, camera inventory, and approach mapping.
- **Columns**: `road_id` (PK), `road_name`, `free_flow_speed_kmh` (default 50), `created_at`, `updated_at`.
- **Purpose**: Normalize road-level analytics around a stable identifier.
- **Relations**:
	- parent for `cameras.road_id`
	- parent for `approaches.road_id`
	- parent for `road_density.road_id`
	- parent for `hourly_vehicle_counts.road_id`
- **Seeded from**: `data/pathumwan_roads.json` via `app.py → _seed_cameras_from_json()`.

### `junctions`

- Canonical junction registry, usually aligned to SUMO traffic-light IDs.
- **Columns**: `junction_id` (PK), `junction_name`, `sumo_tls_id` (unique), `lat`, `lng`, `created_at`, `updated_at`.
- **Purpose**: Normalize all signal-control and approach-level behavior around one junction key.
- **Relations**:
	- parent for `cameras.junction_id`
	- parent for `approaches.junction_id`
	- parent for `signal_states.junction_id`
	- parent for `signal_timings.junction_id`
	- parent for `signal_controllers.junction_id`
	- parent for `ai_decisions.junction_id`

### `cameras`

- **Source of truth for camera inventory.**
- This table also absorbed what used to conceptually be camera stream, calibration, and zone tables.
- **Columns**:
	- Identity/location: `id` (PK), `camera_id` (unique), `name`, `lat`, `lng`, `road` (text), `road_id` (FK → roads), `junction` (text), `junction_id` (FK → junctions), `sumo_tls_id`, `status`, `created_at`
	- Stream runtime config: `stream_url`, `stream_type` (`"sim"` | `"rtsp"` | `"http"`), `stream_enabled` (bool), `fps_target` (int), `last_frame_at` (datetime), `stream_status` (`"online"` | `"offline"` | `"error"`)
	- Calibration: `calibration_data` (JSON) — `{image_width, image_height, homography_matrix, anchor_lat, anchor_lng, bearing_deg, pixels_per_meter}`
	- Zones: `zones` (JSON list) — `[{zone_id, junction_id, approach_id, road_id, zone_type, polygon_json, line_json, enabled}]`
- **Purpose**: Avoid splitting camera metadata into many small tables while keeping one canonical row per camera.
- **Relations**:
	- child of `roads` (via `road_id`)
	- child of `junctions` (via `junction_id`)
	- parent of `traffic_detections` (via `camera_id`)
	- referenced by `approaches.camera_id`
- **Indexes**: `camera_id` (unique), `road_id`, `junction_id`, `last_frame_at`.
- **Seeded from**: `data/pathumwan_roads.json` and `camera_sync.py` (for SUMO TLS cameras).

### `approaches`

- Defines each monitored/controlled approach of a junction.
- **Columns**: `id` (PK), `junction_id` (FK → junctions, NOT NULL), `approach_id` (NOT NULL), `road_id` (FK → roads), `camera_id` (FK → cameras), `approach_name`, `enabled` (bool), `created_at`, `updated_at`.
- **Purpose**: Bridge control logic and analytics by telling the system which road/camera corresponds to which approach.
- **Relations**:
	- child of `junctions`
	- optional child of `roads`
	- optional child of `cameras`
- **Unique constraint**: `(junction_id, approach_id)`.
- **Composite index**: `(junction_id, road_id)`.

### `traffic_detections`

- Stores saved detection snapshots per camera.
- **Columns**: `id` (PK), `camera_id` (FK → cameras, NOT NULL), `timestamp` (indexed), `vehicle_counts` (JSON), `edge_id`, `confidence_avg` (float).
- **Purpose**: Persist count-oriented results from detection so the frontend and later aggregation jobs can read a stable history.
- **Relations**: child of `cameras`.
- **Notes**:
	- `vehicle_counts` is JSON because counts are grouped by class in one payload: `{car, motorcycle, bus, truck, total}`.
	- This table is not the full runtime tracker store; it is the persisted snapshot layer.
- **Composite index**: `(camera_id, timestamp DESC)`.

### `road_density`

- Stores per-road density snapshots used by density/index views.
- **Columns**: `id` (PK), `road_name`, `road_id` (FK → roads, NOT NULL), `timestamp` (indexed), `vehicle_count`, `density_level`, `avg_speed`, `travel_time`, `vc_ratio`.
- **Purpose**: Persist the road-level derived state that the UI can chart or inspect later.
- **Relations**: child of `roads`.
- **Composite index**: `(road_id, timestamp DESC)`.

### `traffic_index`

- Stores area-level traffic snapshots.
- **Columns**: `id` (PK), `timestamp` (indexed), `area` (NOT NULL, indexed), `index_value` (float, NOT NULL), `roads` (JSON).
- **Purpose**: Keep one snapshot row for an area such as "pathumwan" with a rolled-up index and per-road JSON context.
- **Relations**: No hard foreign key to roads; the `roads` JSON field embeds the road breakdown used to explain the index.
- **Design note**: This denormalized shape keeps reads simple for dashboards.
- **Composite index**: `(area, timestamp DESC)`.
- **JSON `roads` payload**: `[{road_name, road_id, index, vehicle_count, avg_speed, free_flow_speed, vc_ratio, travel_time, level, color}]`.

### `hourly_vehicle_counts`

- Stores hourly per-road rollups.
- **Columns**: `id` (PK), `date` (string like `"2026-04-19"`, indexed), `hour` (string like `"08:00"`), `road_id` (FK → roads, NOT NULL), `road_name`, `car`, `motorcycle`, `bus`, `truck`, `total`, `avg_speed`, `density_index`, `source` (`"yolo"` or `"sumo"`), `created_at`.
- **Purpose**: Support charts, summaries, and time-bucketed reporting without rescanning raw detections each time.
- **Relations**: child of `roads`.
- **Composite indexes**: `(date, road_id)`, `(date, hour)`.

### `signal_states`

- Stores observed signal/controller state snapshots.
- **Columns**: `id` (PK), `junction_id` (FK → junctions, NOT NULL), `timestamp` (indexed), `current_phase` (int), `phase_count` (int), `phase_duration` (float), `next_switch_eta` (float), `source` (`"sim"` | `"hardware"`), `raw_state` (JSON).
- **Purpose**: Expose what the signal is currently doing and retain a short history of those states.
- **Relations**: child of `junctions`.
- **Composite index**: `(junction_id, timestamp)`.

### `signal_timings`

- Stores timing plans or timing decisions per junction.
- **Columns**: `id` (PK), `junction_id` (FK → junctions, NOT NULL), `timestamp`, `phase_durations` (JSON list), `mode` (default `"ai"`), `decided_by` (default `"system"`).
- **Purpose**: Audit how long each phase was intended to run and who/what made that choice.
- **Relations**: child of `junctions`.

### `signal_controllers`

- Stores controller endpoint/configuration metadata.
- **Columns**: `id` (PK), `junction_id` (FK → junctions, NOT NULL), `controller_type` (default `"mock"`), `endpoint` (text), `auth_config` (JSON), `enabled` (bool), `phase_map` (JSON), `created_at`, `updated_at`.
- **Purpose**: Keep control integration settings separate from live state snapshots.
- **Relations**: child of `junctions`.

### `ai_decisions`

- Stores AI control decision history.
- **Columns**: `id` (PK), `junction_id` (FK → junctions, NOT NULL), `timestamp`, `input_data` (JSON), `output` (JSON), `reward` (float), `model_version` (string).
- **Purpose**: Explain what data the AI saw, what it decided, and how that decision scored.
- **Relations**: child of `junctions`.

### `historical_stats`

- Stores day-level summary statistics.
- **Columns**: `id` (PK), `date` (string, indexed), `year` (int, indexed), `total_vehicles`, `peak_index`, `peak_time`, `avg_index`, `road_rankings` (JSON list).
- **Purpose**: Serve historical reporting pages and ranked summaries without recomputing from raw tables on every request.
- **Relations**: None by foreign key; it is a rolled-up reporting table.

### `system_logs`

- Stores backend operational events.
- **Columns**: `id` (PK), `timestamp` (indexed), `event_type` (indexed), `details` (JSON).
- **Purpose**: Diagnostics, troubleshooting, and audit-style event tracking.
- **Relations**: None by foreign key; payload is flexible through the `details` JSON field.

## What Is No Longer A Real Table

These names may still appear in code or older notes, but they are **not** separate persisted tables:

| Code name | What happened |
| --- | --- |
| `CameraStream` | Python alias → points to `cameras` table |
| `CameraCalibration` | Python alias → points to `cameras` table |
| `CameraZone` | Python alias → points to `cameras` table |
| `LiveVehicleTrack` | Set to `None` — tracker data is in-memory only |
| `LiveApproachMetric` | Set to `None` — approach metrics are in-memory only |
| `live_vehicle_tracks` | Removed from persisted schema |
| `live_approach_metrics` | Removed from persisted schema |

## Runtime / Persistence Boundary

- In **sim mode**: SUMO TraCI provides live vehicle positions, speeds, and signal states. The detection loop captures periodic snapshots into `traffic_detections`. Road density and traffic index are computed from SUMO + detection data and persisted to `road_density` and `traffic_index`.
- In **real/RTSP mode**: Live queue, occupancy, speed, and tracks come from in-memory tracker state in `backend/detection/tracker_service.py`.
- **Persisted count-oriented snapshots**: `traffic_detections`, `road_density`, `traffic_index`, `hourly_vehicle_counts`.
- This split is intentional: the database stores durable history, while the runtime layer handles high-frequency live state.

---

## JSON Payload Schemas

All JSON columns are stored as Postgres `JSON`. The shapes below are enforced by convention (in application code), not by database constraints.

### `cameras.calibration_data`

Used by the top-down renderer and the real-camera projection math to convert pixel coordinates into world coordinates.

```json
{
  "image_width": 1280,
  "image_height": 720,
  "homography_matrix": [[a,b,c],[d,e,f],[g,h,1]],
  "anchor_lat": 13.7456,
  "anchor_lng": 100.5312,
  "bearing_deg": 45.0,
  "pixels_per_meter": 6.8
}
```

### `cameras.zones`

A list of per-camera analysis zones (approach lanes, stop bars, counting lines). One camera can have many zones.

```json
[
  {
    "zone_id": "pathumwan_n_approach",
    "junction_id": "pathumwan",
    "approach_id": "north",
    "road_id": "rama1",
    "zone_type": "approach",
    "polygon_points": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]],
    "line_points": [[xa,ya],[xb,yb]],
    "enabled": true
  }
]
```

`zone_type` is one of: `"approach"`, `"stopbar"`, `"count_line"`, `"exit"`.
`polygon_points` / `line_points` are image-space (pixel) coordinates.

### `traffic_detections.vehicle_counts`

Per-class vehicle counts observed during one detection tick.

```json
{
  "car": 12,
  "motorcycle": 5,
  "bus": 1,
  "truck": 2,
  "total": 20
}
```

### `traffic_index.roads`

Embedded per-road breakdown for an area-level index snapshot. One `traffic_index` row holds the rolled-up view across roads.

```json
[
  {
    "road_id": "rama1",
    "road_name": "Rama I Road",
    "index": 72.5,
    "level": "heavy",
    "color": "#ff9900",
    "vehicle_count": 45,
    "detected_vehicle_count": 38,
    "avg_speed": 18.2,
    "free_flow_speed": 50.0,
    "vc_ratio": 0.63,
    "travel_time": "3m 12s"
  }
]
```

### `signal_states.raw_state`

Raw controller-reported state, kept flexible since sim and hardware expose different fields.

```json
{
  "phase_state_string": "GrGrrrGGG",
  "program_id": "0",
  "phase_index": 2,
  "remaining_duration": 18.4,
  "next_phase": 3,
  "raw_controller_response": {}
}
```

### `signal_timings.phase_durations`

Ordered list of durations (seconds) for each phase in the controller's program.

```json
[30.0, 4.0, 25.0, 4.0, 20.0, 4.0]
```

### `signal_controllers.auth_config` / `phase_map`

```json
// auth_config
{"username": "admin", "password_hash": "...", "api_key": "..."}

// phase_map — maps logical phase name → controller-specific phase index
{"NS_GREEN": 0, "NS_YELLOW": 1, "EW_GREEN": 2, "EW_YELLOW": 3}
```

### `ai_decisions.input_data` / `output`

```json
// input_data
{
  "junction_id": "pathumwan",
  "approaches": [
    {"approach_id": "north", "queue": 12, "wait_time_avg": 28.5, "flow_rate": 8.2},
    {"approach_id": "south", "queue": 5,  "wait_time_avg": 11.0, "flow_rate": 4.1}
  ],
  "current_phase": 0,
  "since_phase_start": 14.2
}

// output
{
  "action": "extend_green",
  "target_phase": 0,
  "extension_seconds": 8.0,
  "policy_confidence": 0.87
}
```

### `historical_stats.road_rankings`

```json
[
  {"road_name": "Rama I", "max_index": 89.1},
  {"road_name": "Henri Dunant", "max_index": 76.4}
]
```

### `system_logs.details`

Free-form diagnostic payload. Common shape:

```json
{"source": "detection_loop", "error": "camera pathumwan_01 offline", "meta": {"last_frame_at": "2026-04-19T10:44:12Z"}}
```

---

## Foreign Keys & Indexes (summary)

### Foreign keys

| Child table | Child column | Parent | On delete |
| --- | --- | --- | --- |
| `cameras` | `road_id` | `roads.road_id` | SET NULL |
| `cameras` | `junction_id` | `junctions.junction_id` | SET NULL |
| `approaches` | `junction_id` | `junctions.junction_id` | CASCADE |
| `approaches` | `road_id` | `roads.road_id` | SET NULL |
| `approaches` | `camera_id` | `cameras.camera_id` | SET NULL |
| `traffic_detections` | `camera_id` | `cameras.camera_id` | CASCADE |
| `road_density` | `road_id` | `roads.road_id` | (no action) |
| `hourly_vehicle_counts` | `road_id` | `roads.road_id` | (no action) |
| `signal_states` | `junction_id` | `junctions.junction_id` | (no action) |
| `signal_timings` | `junction_id` | `junctions.junction_id` | (no action) |
| `signal_controllers` | `junction_id` | `junctions.junction_id` | (no action) |
| `ai_decisions` | `junction_id` | `junctions.junction_id` | (no action) |

### Unique constraints

- `users(username)`, `users(email)`
- `cameras(camera_id)`
- `junctions(sumo_tls_id)`
- `approaches(junction_id, approach_id)` — composite via `uq_approaches_junction_approach`
- `runtime_config(config_key)`

### Named indexes

| Name | Table | Columns |
| --- | --- | --- |
| `ix_detection_cam_ts` | `traffic_detections` | `(camera_id, timestamp DESC)` |
| `ix_ti_area_ts` | `traffic_index` | `(area, timestamp DESC)` |
| `ix_rd_road_ts` | `road_density` | `(road_id, timestamp DESC)` |
| `ix_signal_state_junction_ts` | `signal_states` | `(junction_id, timestamp)` |
| `ix_hvc_date_road` | `hourly_vehicle_counts` | `(date, road_id)` |
| `ix_hvc_date_hour` | `hourly_vehicle_counts` | `(date, hour)` |
| `ix_approach_junction_road` | `approaches` | `(junction_id, road_id)` |

Per-column `index=True` declarations in the ORM are created implicitly by SQLAlchemy/Alembic under auto-generated names.

---

## CREATE TABLE DDL (PostgreSQL)

> Authoritative DDL lives in `backend/database/models.py`. The statements below are derived from those SQLAlchemy models and reflect the schema as of 2026-04-21. Run `Base.metadata.create_all(engine)` to materialise them — or use `migrate_runtime_schema.py` for additive migrations.

```sql
-- identity & access
CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    username        VARCHAR(100) NOT NULL UNIQUE,
    email           VARCHAR(255) NOT NULL UNIQUE,
    password_hash   TEXT         NOT NULL,
    role            VARCHAR(20)  NOT NULL DEFAULT 'user',
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_users_username ON users(username);
CREATE INDEX ix_users_email    ON users(email);

-- runtime config (key/value)
CREATE TABLE runtime_config (
    id           SERIAL PRIMARY KEY,
    config_key   VARCHAR(100) NOT NULL UNIQUE,
    config_value TEXT DEFAULT '',
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_runtime_config_key ON runtime_config(config_key);

-- topology
CREATE TABLE roads (
    road_id              VARCHAR(100) PRIMARY KEY,
    road_name            VARCHAR(255) NOT NULL,
    free_flow_speed_kmh  DOUBLE PRECISION DEFAULT 50.0,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    updated_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE junctions (
    junction_id    VARCHAR(100) PRIMARY KEY,
    junction_name  VARCHAR(255) DEFAULT '',
    sumo_tls_id    VARCHAR(100) UNIQUE,
    lat            DOUBLE PRECISION,
    lng            DOUBLE PRECISION,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_junctions_sumo_tls_id ON junctions(sumo_tls_id);

-- cameras (inventory + stream + calibration + zones inline)
CREATE TABLE cameras (
    id                SERIAL PRIMARY KEY,
    camera_id         VARCHAR(100) NOT NULL UNIQUE,
    name              VARCHAR(255) NOT NULL,
    road              VARCHAR(255) DEFAULT '',
    road_id           VARCHAR(100) REFERENCES roads(road_id) ON DELETE SET NULL,
    lat               DOUBLE PRECISION NOT NULL,
    lng               DOUBLE PRECISION NOT NULL,
    junction          VARCHAR(255) DEFAULT '',
    junction_id       VARCHAR(100) REFERENCES junctions(junction_id) ON DELETE SET NULL,
    sumo_tls_id       VARCHAR(100) DEFAULT '',
    status            VARCHAR(20)  DEFAULT 'active',
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    stream_url        TEXT DEFAULT '',
    stream_type       VARCHAR(20) DEFAULT 'sim',
    stream_enabled    BOOLEAN DEFAULT TRUE,
    fps_target        INTEGER DEFAULT 8,
    last_frame_at     TIMESTAMPTZ,
    stream_status     VARCHAR(20) DEFAULT 'offline',
    calibration_data  JSON DEFAULT '{}'::json,
    zones             JSON DEFAULT '[]'::json
);
CREATE INDEX ix_cameras_camera_id     ON cameras(camera_id);
CREATE INDEX ix_cameras_road_id       ON cameras(road_id);
CREATE INDEX ix_cameras_junction_id   ON cameras(junction_id);
CREATE INDEX ix_cameras_last_frame_at ON cameras(last_frame_at);

CREATE TABLE approaches (
    id             SERIAL PRIMARY KEY,
    junction_id    VARCHAR(100) NOT NULL REFERENCES junctions(junction_id) ON DELETE CASCADE,
    approach_id    VARCHAR(100) NOT NULL,
    road_id        VARCHAR(100) REFERENCES roads(road_id) ON DELETE SET NULL,
    camera_id      VARCHAR(100) REFERENCES cameras(camera_id) ON DELETE SET NULL,
    approach_name  VARCHAR(255) DEFAULT '',
    enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_approaches_junction_approach UNIQUE (junction_id, approach_id)
);
CREATE INDEX ix_approach_junction_road ON approaches(junction_id, road_id);
CREATE INDEX ix_approaches_junction_id ON approaches(junction_id);
CREATE INDEX ix_approaches_approach_id ON approaches(approach_id);
CREATE INDEX ix_approaches_road_id     ON approaches(road_id);
CREATE INDEX ix_approaches_camera_id   ON approaches(camera_id);

-- measurements
CREATE TABLE traffic_detections (
    id              SERIAL PRIMARY KEY,
    camera_id       VARCHAR(100) NOT NULL REFERENCES cameras(camera_id) ON DELETE CASCADE,
    timestamp       TIMESTAMPTZ DEFAULT NOW(),
    vehicle_counts  JSON DEFAULT '{}'::json,
    edge_id         VARCHAR(100) DEFAULT '',
    confidence_avg  DOUBLE PRECISION DEFAULT 0.0
);
CREATE INDEX ix_detection_cam_ts          ON traffic_detections(camera_id, timestamp DESC);
CREATE INDEX ix_traffic_detections_camera ON traffic_detections(camera_id);
CREATE INDEX ix_traffic_detections_ts     ON traffic_detections(timestamp);

CREATE TABLE traffic_index (
    id           SERIAL PRIMARY KEY,
    timestamp    TIMESTAMPTZ DEFAULT NOW(),
    area         VARCHAR(100) NOT NULL,
    index_value  DOUBLE PRECISION NOT NULL,
    roads        JSON DEFAULT '[]'::json
);
CREATE INDEX ix_ti_area_ts        ON traffic_index(area, timestamp DESC);
CREATE INDEX ix_traffic_index_ts  ON traffic_index(timestamp);
CREATE INDEX ix_traffic_index_area ON traffic_index(area);

CREATE TABLE road_density (
    id             SERIAL PRIMARY KEY,
    road_name      VARCHAR(255) NOT NULL,
    road_id        VARCHAR(100) NOT NULL REFERENCES roads(road_id),
    timestamp      TIMESTAMPTZ DEFAULT NOW(),
    vehicle_count  INTEGER DEFAULT 0,
    density_level  VARCHAR(50) DEFAULT '',
    avg_speed      DOUBLE PRECISION DEFAULT 0.0,
    travel_time    DOUBLE PRECISION DEFAULT 0.0,
    vc_ratio       DOUBLE PRECISION DEFAULT 0.0
);
CREATE INDEX ix_rd_road_ts          ON road_density(road_id, timestamp DESC);
CREATE INDEX ix_road_density_road   ON road_density(road_id);
CREATE INDEX ix_road_density_ts     ON road_density(timestamp);

CREATE TABLE hourly_vehicle_counts (
    id             SERIAL PRIMARY KEY,
    date           VARCHAR(10) NOT NULL,
    hour           VARCHAR(5)  NOT NULL,
    road_id        VARCHAR(100) NOT NULL REFERENCES roads(road_id),
    road_name      VARCHAR(255) DEFAULT '',
    car            INTEGER DEFAULT 0,
    motorcycle     INTEGER DEFAULT 0,
    bus            INTEGER DEFAULT 0,
    truck          INTEGER DEFAULT 0,
    total          INTEGER DEFAULT 0,
    avg_speed      DOUBLE PRECISION DEFAULT 0.0,
    density_index  DOUBLE PRECISION DEFAULT 0.0,
    source         VARCHAR(20) DEFAULT 'yolo',
    created_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_hvc_date_road ON hourly_vehicle_counts(date, road_id);
CREATE INDEX ix_hvc_date_hour ON hourly_vehicle_counts(date, hour);
CREATE INDEX ix_hvc_date      ON hourly_vehicle_counts(date);
CREATE INDEX ix_hvc_road_id   ON hourly_vehicle_counts(road_id);

-- signal control
CREATE TABLE signal_states (
    id                SERIAL PRIMARY KEY,
    junction_id       VARCHAR(100) NOT NULL REFERENCES junctions(junction_id),
    timestamp         TIMESTAMPTZ DEFAULT NOW(),
    current_phase     INTEGER DEFAULT 0,
    phase_count       INTEGER DEFAULT 0,
    phase_duration    DOUBLE PRECISION DEFAULT 0.0,
    next_switch_eta   DOUBLE PRECISION DEFAULT 0.0,
    source            VARCHAR(20) DEFAULT 'sim',
    raw_state         JSON DEFAULT '{}'::json
);
CREATE INDEX ix_signal_state_junction_ts ON signal_states(junction_id, timestamp);
CREATE INDEX ix_signal_states_junction   ON signal_states(junction_id);
CREATE INDEX ix_signal_states_ts         ON signal_states(timestamp);

CREATE TABLE signal_timings (
    id               SERIAL PRIMARY KEY,
    junction_id      VARCHAR(100) NOT NULL REFERENCES junctions(junction_id),
    timestamp        TIMESTAMPTZ DEFAULT NOW(),
    phase_durations  JSON DEFAULT '[]'::json,
    mode             VARCHAR(20) DEFAULT 'ai',
    decided_by       VARCHAR(100) DEFAULT 'system'
);
CREATE INDEX ix_signal_timings_junction ON signal_timings(junction_id);

CREATE TABLE signal_controllers (
    id               SERIAL PRIMARY KEY,
    junction_id      VARCHAR(100) NOT NULL REFERENCES junctions(junction_id),
    controller_type  VARCHAR(30) DEFAULT 'mock',
    endpoint         TEXT DEFAULT '',
    auth_config      JSON DEFAULT '{}'::json,
    enabled          BOOLEAN NOT NULL DEFAULT TRUE,
    phase_map        JSON DEFAULT '{}'::json,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_signal_controllers_junction ON signal_controllers(junction_id);

CREATE TABLE ai_decisions (
    id             SERIAL PRIMARY KEY,
    junction_id    VARCHAR(100) NOT NULL REFERENCES junctions(junction_id),
    timestamp      TIMESTAMPTZ DEFAULT NOW(),
    input_data     JSON DEFAULT '{}'::json,
    output         JSON DEFAULT '{}'::json,
    reward         DOUBLE PRECISION DEFAULT 0.0,
    model_version  VARCHAR(50) DEFAULT ''
);
CREATE INDEX ix_ai_decisions_junction ON ai_decisions(junction_id);

-- summaries & logs
CREATE TABLE historical_stats (
    id              SERIAL PRIMARY KEY,
    date            VARCHAR(20) NOT NULL,
    year            INTEGER NOT NULL,
    total_vehicles  INTEGER DEFAULT 0,
    peak_index      DOUBLE PRECISION DEFAULT 0.0,
    peak_time       VARCHAR(10) DEFAULT '',
    avg_index       DOUBLE PRECISION DEFAULT 0.0,
    road_rankings   JSON DEFAULT '[]'::json
);
CREATE INDEX ix_historical_stats_date ON historical_stats(date);
CREATE INDEX ix_historical_stats_year ON historical_stats(year);

CREATE TABLE system_logs (
    id          SERIAL PRIMARY KEY,
    timestamp   TIMESTAMPTZ DEFAULT NOW(),
    event_type  VARCHAR(100) NOT NULL,
    details     JSON DEFAULT '{}'::json
);
CREATE INDEX ix_system_logs_ts    ON system_logs(timestamp);
CREATE INDEX ix_system_logs_event ON system_logs(event_type);
```

---

## Example Queries

Short recipes for the queries the app actually runs.

```sql
-- 1) Total vehicles today (across all roads)
SELECT COALESCE(SUM(total), 0) AS total_today
FROM hourly_vehicle_counts
WHERE date = TO_CHAR(NOW(), 'YYYY-MM-DD');

-- 2) Current traffic index for Pathumwan area
SELECT timestamp, index_value, roads
FROM traffic_index
WHERE area = 'pathumwan'
ORDER BY timestamp DESC
LIMIT 1;

-- 3) Top-10 busiest roads this year (by average peak index per day)
SELECT road_name, AVG(max_index) AS avg_max_index
FROM (
  SELECT
    (item->>'road_name') AS road_name,
    (item->>'max_index')::float AS max_index
  FROM historical_stats,
       jsonb_array_elements(road_rankings::jsonb) AS item
  WHERE year = EXTRACT(YEAR FROM NOW())::int
) t
GROUP BY road_name
ORDER BY avg_max_index DESC
LIMIT 10;

-- 4) Latest signal state per junction
SELECT DISTINCT ON (junction_id)
       junction_id, timestamp, current_phase, phase_duration, next_switch_eta, source
FROM signal_states
ORDER BY junction_id, timestamp DESC;

-- 5) Hourly chart for a specific road today
SELECT hour, total, avg_speed
FROM hourly_vehicle_counts
WHERE date = TO_CHAR(NOW(), 'YYYY-MM-DD')
  AND road_id = 'rama1'
ORDER BY hour ASC;

-- 6) Recent detections for a single camera (last 30 minutes)
SELECT timestamp, vehicle_counts, confidence_avg
FROM traffic_detections
WHERE camera_id = 'pathumwan_01'
  AND timestamp >= NOW() - INTERVAL '30 minutes'
ORDER BY timestamp DESC;

-- 7) Sanity check: is retention working?
--    traffic_detections should never have rows older than 7 days.
SELECT COUNT(*) AS stale_rows,
       MIN(timestamp) AS oldest
FROM traffic_detections
WHERE timestamp < NOW() - INTERVAL '7 days';

-- 8) Camera freshness: who hasn't pushed a frame in the last minute?
SELECT camera_id, name, stream_status, last_frame_at
FROM cameras
WHERE stream_enabled = TRUE
  AND (last_frame_at IS NULL OR last_frame_at < NOW() - INTERVAL '1 minute')
ORDER BY last_frame_at NULLS FIRST;

-- 9) Find junctions that have no approaches configured yet
SELECT j.junction_id, j.junction_name
FROM junctions j
LEFT JOIN approaches a ON a.junction_id = j.junction_id
WHERE a.id IS NULL;

-- 10) AI decisions that scored poorly in the last day (for offline review)
SELECT timestamp, junction_id, reward, output
FROM ai_decisions
WHERE timestamp >= NOW() - INTERVAL '1 day'
  AND reward < 0
ORDER BY reward ASC
LIMIT 50;
```

---

## Retention Cheat Sheet

Kept in one place so Ops can audit it quickly. If this conflicts with `backend/services/daily_stats.py::RETENTION_DAYS`, the code wins — update the docs.

| Table | TTL | Pruner |
| --- | --- | --- |
| `traffic_detections` | 7 days | `purge_old_raw_data()` |
| `traffic_index` | 30 days | `purge_old_raw_data()` |
| `road_density` | 30 days | `purge_old_raw_data()` |
| `signal_states` | 30 days | `purge_old_raw_data()` |
| `system_logs` | 30 days | `purge_old_raw_data()` |
| `ai_decisions` | 30 days | `purge_old_raw_data()` |
| everything else | kept | — |