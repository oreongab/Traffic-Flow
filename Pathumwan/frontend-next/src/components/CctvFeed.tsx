"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import {
  getCameraCounts,
  getCameraDetectStreamUrl,
  getCameraStreamUrl,
} from "@/lib/api";
import type { CameraCounts, CameraCountsSnapshot } from "@/lib/types";

const CctvMiniMap = dynamic(() => import("@/components/CctvMiniMap"), {
  ssr: false,
});

interface CctvFeedProps {
  cameraId: string;
  cameraName: string;
  subtitle?: string;
  detectMode?: boolean;
  cameraLat?: number;
  cameraLng?: number;
}

const EMPTY_COUNTS: CameraCounts = {
  car: 0,
  motorcycle: 0,
  bus: 0,
  truck: 0,
  total: 0,
};

export default function CctvFeed({
  cameraId,
  cameraName,
  subtitle,
  detectMode = true,
  cameraLat,
  cameraLng,
}: CctvFeedProps) {
  const [counts, setCounts] = useState<CameraCounts>(EMPTY_COUNTS);
  const [streamError, setStreamError] = useState(false);
  const [retryKey, setRetryKey] = useState(0);

  const baseStreamUrl = detectMode
    ? getCameraDetectStreamUrl(cameraId)
    : getCameraStreamUrl(cameraId);
  // retryKey busts the browser cache when the user clicks retry.
  const streamUrl = retryKey > 0 ? `${baseStreamUrl}?r=${retryKey}` : baseStreamUrl;

  useEffect(() => {
    setStreamError(false);
    setRetryKey(0);
  }, [cameraId, detectMode]);

  useEffect(() => {
    let active = true;

    async function fetchCounts() {
      try {
        const result = await getCameraCounts(cameraId);
        if (active) {
          setCounts(result?.counts ?? EMPTY_COUNTS);
        }
      } catch {
        if (active) {
          setCounts(EMPTY_COUNTS);
        }
      }
    }

    fetchCounts();
    const interval = setInterval(fetchCounts, 3000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [cameraId]);

  return (
    <div className="relative h-full w-full overflow-hidden bg-[#0b1220]">
      {/* Video stream — full view, no overlapping map */}
      <img
        key={streamUrl}
        src={streamUrl}
        alt={cameraName}
        className="h-full w-full object-contain"
        onError={() => setStreamError(true)}
        onLoad={() => setStreamError(false)}
      />

      {streamError && (
        <div className="absolute inset-0 z-[4] flex flex-col items-center justify-center gap-2 bg-slate-950/85 text-center px-4">
          <div className="text-3xl">📷</div>
          <div className="text-sm font-semibold text-white">
            ไม่พบสตรีมของกล้องนี้
          </div>
          <div className="text-[11px] text-white/70 max-w-[260px]">
            กล้อง {cameraName} ยังไม่พร้อมใช้งาน — อาจเป็นเพราะกล้อง offline, SUMO ยังไม่เริ่มสตรีม, หรือยังไม่ได้ calibrate
          </div>
          <button
            type="button"
            onClick={() => {
              setStreamError(false);
              setRetryKey((k) => k + 1);
            }}
            className="mt-1 rounded-md bg-cyan-600 px-3 py-1 text-[11px] font-medium text-white hover:bg-cyan-500"
          >
            ลองใหม่
          </button>
        </div>
      )}

      {/* Top overlay — camera name + subtitle only */}
      <div className="absolute inset-x-0 top-0 bg-gradient-to-b from-black/70 to-transparent px-4 py-3 text-white z-[2]">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{cameraName}</div>
          {subtitle && (
            <div className="truncate text-[11px] text-white/75">{subtitle}</div>
          )}
          {Number.isFinite(cameraLat) && Number.isFinite(cameraLng) && (
            <div className="truncate text-[10px] text-cyan-200/85">
              พิกัดกล้อง {Number(cameraLat).toFixed(6)}, {Number(cameraLng).toFixed(6)}
            </div>
          )}
        </div>
      </div>

      {Number.isFinite(cameraLat) && Number.isFinite(cameraLng) && (
        <div className="absolute right-3 top-14 z-[3] h-[96px] w-[136px] overflow-hidden rounded-xl border border-cyan-300/35 bg-slate-950/85 shadow-[0_12px_30px_rgba(2,8,23,0.45)] backdrop-blur">
          <div className="absolute inset-x-0 top-0 z-[510] bg-gradient-to-b from-slate-950/95 to-transparent px-2 py-1 text-[9px] font-medium uppercase tracking-[0.16em] text-cyan-200">
            Camera Position
          </div>
          <CctvMiniMap
            cameraId={cameraId}
            cameraName={cameraName}
            lat={Number(cameraLat)}
            lng={Number(cameraLng)}
            pollInterval={3000}
            detectMode={detectMode}
            zoom={19}
            interactive={false}
            showHud={false}
          />
          <div className="absolute inset-x-0 bottom-0 z-[510] bg-gradient-to-t from-slate-950/95 to-transparent px-2 pb-1 pt-3 text-[9px] text-white/75">
            ตำแหน่งจริงของกล้องและรถรอบจุดติดตั้ง
          </div>
        </div>
      )}

      {/* Bottom overlay — single thin strip so the stream is never cropped */}
      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent px-3 pb-1.5 pt-2 text-white z-[2]">
        <div className="flex items-center justify-between gap-3 text-[11px] text-white/90">
          <div className="flex items-baseline gap-2">
            <span className="text-[10px] uppercase tracking-wide text-white/60">รถในมุมกล้อง</span>
            <span className="text-base font-bold leading-none">{counts.total}</span>
          </div>
          <div className="flex flex-wrap justify-end gap-2 text-[11px] text-white/80">
            <span>🚗 {counts.car}</span>
            <span>🏍️ {counts.motorcycle}</span>
            <span>🚌 {counts.bus}</span>
            <span>🚛 {counts.truck}</span>
          </div>
        </div>
      </div>
    </div>
  );
}