-- PostgreSQL migration: normalize reference/master data and add real foreign keys.
-- Safe order: create parent tables -> backfill canonical IDs -> seed parent rows -> add constraints.

BEGIN;

CREATE TABLE IF NOT EXISTS roads (
    road_id VARCHAR(100) PRIMARY KEY,
    road_name VARCHAR(255) NOT NULL,
    free_flow_speed_kmh DOUBLE PRECISION DEFAULT 50.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS junctions (
    junction_id VARCHAR(100) PRIMARY KEY,
    junction_name VARCHAR(255) DEFAULT '',
    sumo_tls_id VARCHAR(100) UNIQUE,
    lat DOUBLE PRECISION,
    lng DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS approaches (
    id BIGSERIAL PRIMARY KEY,
    junction_id VARCHAR(100) NOT NULL,
    approach_id VARCHAR(100) NOT NULL,
    road_id VARCHAR(100),
    camera_id VARCHAR(100),
    approach_name VARCHAR(255) DEFAULT '',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE cameras ADD COLUMN IF NOT EXISTS road_id VARCHAR(100);
ALTER TABLE cameras ADD COLUMN IF NOT EXISTS junction_id VARCHAR(100);

CREATE INDEX IF NOT EXISTS ix_cameras_road_id ON cameras (road_id);
CREATE INDEX IF NOT EXISTS ix_cameras_junction_id ON cameras (junction_id);
CREATE INDEX IF NOT EXISTS ix_junctions_sumo_tls_id ON junctions (sumo_tls_id);
CREATE INDEX IF NOT EXISTS ix_approaches_junction_id ON approaches (junction_id);
CREATE INDEX IF NOT EXISTS ix_approaches_road_id ON approaches (road_id);
CREATE INDEX IF NOT EXISTS ix_approaches_camera_id ON approaches (camera_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_approaches_junction_approach'
    ) THEN
        ALTER TABLE approaches
            ADD CONSTRAINT uq_approaches_junction_approach UNIQUE (junction_id, approach_id);
    END IF;
END $$;

WITH canonical_roads (road_id, road_name, free_flow_speed_kmh) AS (
    VALUES
        ('RAMA1', 'ถนนพระราม 1', 50.0),
        ('RAMA4', 'ถนนพระราม 4', 50.0),
        ('PHAYATHAI', 'ถนนพญาไท', 50.0),
        ('RATCHADAMRI', 'ถนนราชดำริ', 50.0),
        ('PLOENCHIT', 'ถนนเพลินจิต', 50.0),
        ('BANTHATTHONG', 'ถนนบรรทัดทอง', 50.0),
        ('CHARUMUEANG', 'ถนนจารุเมือง', 50.0),
        ('WITTHAYU', 'ถนนวิทยุ', 50.0),
        ('HENRIDUNANT', 'ถนนอังรีดูนังต์', 50.0),
        ('SARASIN', 'ถนนสารสิน', 50.0)
)
INSERT INTO roads (road_id, road_name, free_flow_speed_kmh)
SELECT road_id, road_name, free_flow_speed_kmh
FROM canonical_roads
ON CONFLICT (road_id) DO UPDATE
SET road_name = EXCLUDED.road_name,
    free_flow_speed_kmh = EXCLUDED.free_flow_speed_kmh,
    updated_at = NOW();

CREATE TEMP TABLE tmp_road_aliases (
    alias TEXT PRIMARY KEY,
    road_id VARCHAR(100) NOT NULL
) ON COMMIT DROP;

INSERT INTO tmp_road_aliases (alias, road_id)
VALUES
    ('RAMA1', 'RAMA1'),
    ('ถนนพระราม 1', 'RAMA1'),
    ('ถนนพระรามที่ 1', 'RAMA1'),
    ('RAMA4', 'RAMA4'),
    ('ถนนพระราม 4', 'RAMA4'),
    ('ถนนพระรามที่ 4', 'RAMA4'),
    ('PHAYATHAI', 'PHAYATHAI'),
    ('ถนนพญาไท', 'PHAYATHAI'),
    ('RATCHADAMRI', 'RATCHADAMRI'),
    ('ถนนราชดำริ', 'RATCHADAMRI'),
    ('PLOENCHIT', 'PLOENCHIT'),
    ('ถนนเพลินจิต', 'PLOENCHIT'),
    ('BANTHATTHONG', 'BANTHATTHONG'),
    ('ถนนบรรทัดทอง', 'BANTHATTHONG'),
    ('CHARUMUEANG', 'CHARUMUEANG'),
    ('ถนนจารุเมือง', 'CHARUMUEANG'),
    ('WITTHAYU', 'WITTHAYU'),
    ('ถนนวิทยุ', 'WITTHAYU'),
    ('HENRIDUNANT', 'HENRIDUNANT'),
    ('ถนนอังรีดูนังต์', 'HENRIDUNANT'),
    ('SARASIN', 'SARASIN'),
    ('ถนนสารสิน', 'SARASIN')
ON CONFLICT (alias) DO NOTHING;

INSERT INTO cameras (camera_id, name, lat, lng, status, created_at)
SELECT src.camera_id, src.camera_id, 0.0, 0.0, 'active', NOW()
FROM (
    SELECT camera_id FROM camera_streams
    UNION
    SELECT camera_id FROM camera_calibrations
    UNION
    SELECT camera_id FROM camera_zones
    UNION
    SELECT camera_id FROM traffic_detections
    UNION
    SELECT camera_id FROM live_vehicle_tracks
    UNION
    SELECT camera_id FROM live_approach_metrics
) AS src
WHERE src.camera_id IS NOT NULL
  AND BTRIM(src.camera_id) <> ''
  AND NOT EXISTS (
      SELECT 1 FROM cameras c WHERE c.camera_id = src.camera_id
  );

UPDATE cameras
SET road_id = NULLIF(BTRIM(road_id), ''),
    junction_id = NULLIF(BTRIM(junction_id), '');

UPDATE cameras c
SET road_id = COALESCE(
        (SELECT t.road_id FROM tmp_road_aliases t WHERE t.alias = BTRIM(COALESCE(NULLIF(c.road_id, ''), c.road)) LIMIT 1),
        NULLIF(BTRIM(COALESCE(c.road_id, c.road)), '')
    ),
    junction_id = COALESCE(NULLIF(BTRIM(c.junction_id), ''), NULLIF(BTRIM(c.sumo_tls_id), ''), NULLIF(BTRIM(c.junction), ''));

UPDATE camera_zones z
SET junction_id = COALESCE(NULLIF(BTRIM(z.junction_id), ''), c.junction_id),
    road_id = COALESCE(
        (SELECT t.road_id FROM tmp_road_aliases t WHERE t.alias = BTRIM(COALESCE(NULLIF(z.road_id, ''), c.road_id, c.road)) LIMIT 1),
        NULLIF(BTRIM(COALESCE(z.road_id, c.road_id, c.road)), '')
    ),
    approach_id = NULLIF(BTRIM(z.approach_id), '')
FROM cameras c
WHERE c.camera_id = z.camera_id;

UPDATE live_approach_metrics
SET junction_id = COALESCE(NULLIF(BTRIM(junction_id), ''), NULLIF(BTRIM(camera_id), '')),
    approach_id = COALESCE(NULLIF(BTRIM(approach_id), ''), NULLIF(BTRIM(camera_id), '')),
    road_id = NULLIF(BTRIM(road_id), '');

UPDATE live_approach_metrics m
SET road_id = COALESCE(
        (SELECT t.road_id FROM tmp_road_aliases t WHERE t.alias = BTRIM(COALESCE(NULLIF(m.road_id, ''), c.road_id, c.road)) LIMIT 1),
        NULLIF(BTRIM(COALESCE(m.road_id, c.road_id, c.road)), '')
    )
FROM cameras c
WHERE c.camera_id = m.camera_id;

UPDATE road_density
SET road_id = COALESCE(
        (SELECT t.road_id FROM tmp_road_aliases t WHERE t.alias = BTRIM(COALESCE(NULLIF(road_id, ''), road_name)) LIMIT 1),
        NULLIF(BTRIM(COALESCE(road_id, road_name)), '')
    );

UPDATE hourly_vehicle_counts
SET road_id = COALESCE(
        (SELECT t.road_id FROM tmp_road_aliases t WHERE t.alias = BTRIM(COALESCE(NULLIF(road_id, ''), road_name)) LIMIT 1),
        NULLIF(BTRIM(COALESCE(road_id, road_name)), '')
    );

INSERT INTO roads (road_id, road_name, free_flow_speed_kmh)
SELECT DISTINCT src.road_id, src.road_name, 50.0
FROM (
    SELECT c.road_id, COALESCE(NULLIF(BTRIM(c.road), ''), c.road_id) AS road_name FROM cameras c
    UNION ALL
    SELECT z.road_id, z.road_id FROM camera_zones z
    UNION ALL
    SELECT m.road_id, m.road_id FROM live_approach_metrics m
    UNION ALL
    SELECT rd.road_id, COALESCE(NULLIF(BTRIM(rd.road_name), ''), rd.road_id) FROM road_density rd
    UNION ALL
    SELECT h.road_id, COALESCE(NULLIF(BTRIM(h.road_name), ''), h.road_id) FROM hourly_vehicle_counts h
) AS src
WHERE src.road_id IS NOT NULL
  AND BTRIM(src.road_id) <> ''
ON CONFLICT (road_id) DO UPDATE
SET road_name = COALESCE(NULLIF(EXCLUDED.road_name, ''), roads.road_name),
    updated_at = NOW();

INSERT INTO junctions (junction_id, junction_name, sumo_tls_id, lat, lng)
SELECT DISTINCT
    c.junction_id,
    COALESCE(NULLIF(BTRIM(c.name), ''), NULLIF(BTRIM(c.junction), ''), c.junction_id),
    NULLIF(BTRIM(c.sumo_tls_id), ''),
    c.lat,
    c.lng
FROM cameras c
WHERE c.junction_id IS NOT NULL
  AND BTRIM(c.junction_id) <> ''
ON CONFLICT (junction_id) DO UPDATE
SET junction_name = COALESCE(NULLIF(EXCLUDED.junction_name, ''), junctions.junction_name),
    sumo_tls_id = COALESCE(EXCLUDED.sumo_tls_id, junctions.sumo_tls_id),
    lat = COALESCE(EXCLUDED.lat, junctions.lat),
    lng = COALESCE(EXCLUDED.lng, junctions.lng),
    updated_at = NOW();

INSERT INTO junctions (junction_id, junction_name)
SELECT DISTINCT src.junction_id, src.junction_id
FROM (
    SELECT NULLIF(BTRIM(junction_id), '') AS junction_id FROM signal_timings
    UNION
    SELECT NULLIF(BTRIM(junction_id), '') AS junction_id FROM signal_states
    UNION
    SELECT NULLIF(BTRIM(junction_id), '') AS junction_id FROM signal_controllers
    UNION
    SELECT NULLIF(BTRIM(junction_id), '') AS junction_id FROM ai_decisions
    UNION
    SELECT NULLIF(BTRIM(junction_id), '') AS junction_id FROM live_approach_metrics
    UNION
    SELECT NULLIF(BTRIM(junction_id), '') AS junction_id FROM camera_zones
) AS src
WHERE src.junction_id IS NOT NULL
ON CONFLICT (junction_id) DO NOTHING;

INSERT INTO approaches (junction_id, approach_id, road_id, camera_id, approach_name, enabled)
SELECT DISTINCT src.junction_id, src.approach_id, src.road_id, src.camera_id, src.approach_id, TRUE
FROM (
    SELECT junction_id, approach_id, road_id, camera_id FROM camera_zones
    WHERE junction_id IS NOT NULL AND approach_id IS NOT NULL
    UNION
    SELECT junction_id, approach_id, road_id, camera_id FROM live_approach_metrics
    WHERE junction_id IS NOT NULL AND approach_id IS NOT NULL
) AS src
ON CONFLICT (junction_id, approach_id) DO UPDATE
SET road_id = COALESCE(EXCLUDED.road_id, approaches.road_id),
    camera_id = COALESCE(EXCLUDED.camera_id, approaches.camera_id),
    enabled = TRUE,
    updated_at = NOW();

DELETE FROM camera_zones z1
USING camera_zones z2
WHERE z1.ctid < z2.ctid
  AND z1.camera_id = z2.camera_id
  AND z1.zone_id = z2.zone_id;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_camera_zones_camera_zone') THEN
        ALTER TABLE camera_zones
            ADD CONSTRAINT uq_camera_zones_camera_zone UNIQUE (camera_id, zone_id);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_cameras_road_id') THEN
        ALTER TABLE cameras ADD CONSTRAINT fk_cameras_road_id FOREIGN KEY (road_id) REFERENCES roads (road_id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_cameras_junction_id') THEN
        ALTER TABLE cameras ADD CONSTRAINT fk_cameras_junction_id FOREIGN KEY (junction_id) REFERENCES junctions (junction_id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'camera_streams_camera_id_fkey') THEN
        ALTER TABLE camera_streams ADD CONSTRAINT camera_streams_camera_id_fkey FOREIGN KEY (camera_id) REFERENCES cameras (camera_id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'camera_calibrations_camera_id_fkey') THEN
        ALTER TABLE camera_calibrations ADD CONSTRAINT camera_calibrations_camera_id_fkey FOREIGN KEY (camera_id) REFERENCES cameras (camera_id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'camera_zones_camera_id_fkey') THEN
        ALTER TABLE camera_zones ADD CONSTRAINT camera_zones_camera_id_fkey FOREIGN KEY (camera_id) REFERENCES cameras (camera_id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'traffic_detections_camera_id_fkey') THEN
        ALTER TABLE traffic_detections ADD CONSTRAINT traffic_detections_camera_id_fkey FOREIGN KEY (camera_id) REFERENCES cameras (camera_id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'live_vehicle_tracks_camera_id_fkey') THEN
        ALTER TABLE live_vehicle_tracks ADD CONSTRAINT live_vehicle_tracks_camera_id_fkey FOREIGN KEY (camera_id) REFERENCES cameras (camera_id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'live_approach_metrics_camera_id_fkey') THEN
        ALTER TABLE live_approach_metrics ADD CONSTRAINT live_approach_metrics_camera_id_fkey FOREIGN KEY (camera_id) REFERENCES cameras (camera_id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'camera_zones_junction_id_fkey') THEN
        ALTER TABLE camera_zones ADD CONSTRAINT camera_zones_junction_id_fkey FOREIGN KEY (junction_id) REFERENCES junctions (junction_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'camera_zones_road_id_fkey') THEN
        ALTER TABLE camera_zones ADD CONSTRAINT camera_zones_road_id_fkey FOREIGN KEY (road_id) REFERENCES roads (road_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_camera_zones_approach') THEN
        ALTER TABLE camera_zones ADD CONSTRAINT fk_camera_zones_approach FOREIGN KEY (junction_id, approach_id) REFERENCES approaches (junction_id, approach_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'road_density_road_id_fkey') THEN
        ALTER TABLE road_density ADD CONSTRAINT road_density_road_id_fkey FOREIGN KEY (road_id) REFERENCES roads (road_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'hourly_vehicle_counts_road_id_fkey') THEN
        ALTER TABLE hourly_vehicle_counts ADD CONSTRAINT hourly_vehicle_counts_road_id_fkey FOREIGN KEY (road_id) REFERENCES roads (road_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'signal_timings_junction_id_fkey') THEN
        ALTER TABLE signal_timings ADD CONSTRAINT signal_timings_junction_id_fkey FOREIGN KEY (junction_id) REFERENCES junctions (junction_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'signal_states_junction_id_fkey') THEN
        ALTER TABLE signal_states ADD CONSTRAINT signal_states_junction_id_fkey FOREIGN KEY (junction_id) REFERENCES junctions (junction_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'signal_controllers_junction_id_fkey') THEN
        ALTER TABLE signal_controllers ADD CONSTRAINT signal_controllers_junction_id_fkey FOREIGN KEY (junction_id) REFERENCES junctions (junction_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ai_decisions_junction_id_fkey') THEN
        ALTER TABLE ai_decisions ADD CONSTRAINT ai_decisions_junction_id_fkey FOREIGN KEY (junction_id) REFERENCES junctions (junction_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'live_approach_metrics_junction_id_fkey') THEN
        ALTER TABLE live_approach_metrics ADD CONSTRAINT live_approach_metrics_junction_id_fkey FOREIGN KEY (junction_id) REFERENCES junctions (junction_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'live_approach_metrics_road_id_fkey') THEN
        ALTER TABLE live_approach_metrics ADD CONSTRAINT live_approach_metrics_road_id_fkey FOREIGN KEY (road_id) REFERENCES roads (road_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_live_approach_metrics_approach') THEN
        ALTER TABLE live_approach_metrics ADD CONSTRAINT fk_live_approach_metrics_approach FOREIGN KEY (junction_id, approach_id) REFERENCES approaches (junction_id, approach_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'approaches_junction_id_fkey') THEN
        ALTER TABLE approaches ADD CONSTRAINT approaches_junction_id_fkey FOREIGN KEY (junction_id) REFERENCES junctions (junction_id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'approaches_road_id_fkey') THEN
        ALTER TABLE approaches ADD CONSTRAINT approaches_road_id_fkey FOREIGN KEY (road_id) REFERENCES roads (road_id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'approaches_camera_id_fkey') THEN
        ALTER TABLE approaches ADD CONSTRAINT approaches_camera_id_fkey FOREIGN KEY (camera_id) REFERENCES cameras (camera_id) ON DELETE SET NULL;
    END IF;
END $$;

COMMIT;