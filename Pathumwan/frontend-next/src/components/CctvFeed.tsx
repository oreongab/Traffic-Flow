"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  getCameraAnalyticsStreamUrl,
  getCameraCounts,
  getCameraDetectFrameUrl,
  getCameraFrameUrl,
  getCameraSameOriginDetectFrameUrl,
  getCameraSameOriginFrameUrl,
  getCameraDetectStreamUrl,
  getCameraSameOriginAnalyticsStreamUrl,
  getCameraSameOriginDetectStreamUrl,
  getCameraSameOriginStreamUrl,
  getCameraStreamUrl,
} from "@/lib/api";
import type { CameraCounts } from "@/lib/types";

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
  showCounts?: boolean;
  showMiniMap?: boolean;
  streamFps?: number;
  streamMode?: "auto" | "raw" | "detect" | "analytics";
  streamTransport?: "mjpeg" | "snapshot";
  showInfoOverlay?: boolean;
  eagerStream?: boolean;
  showPoster?: boolean;
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
  showCounts = true,
  showMiniMap = true,
  streamFps,
  streamMode = "auto",
  streamTransport = "mjpeg",
  showInfoOverlay = true,
  eagerStream = false,
  showPoster = true,
}: CctvFeedProps) {
  const [counts, setCounts] = useState<CameraCounts>(EMPTY_COUNTS);
  const [retryKey, setRetryKey] = useState(0);
  const [isVisible, setIsVisible] = useState(false);
  const [documentVisible, setDocumentVisible] = useState(true);
  const [streamEnabled, setStreamEnabled] = useState(true);
  const [useSameOriginStream, setUseSameOriginStream] = useState(false);
  const [snapshotUrl, setSnapshotUrl] = useState("");
  const reconnectTimerRef = useRef<number | null>(null);
  const snapshotTimerRef = useRef<number | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const posterImgRef = useRef<HTMLImageElement | null>(null);
  const streamImgRef = useRef<HTMLImageElement | null>(null);

  function clearImageSource(img: HTMLImageElement | null) {
    if (!img) return;
    img.src = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==";
  }

  const abortStreamImages = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (snapshotTimerRef.current !== null) {
      window.clearInterval(snapshotTimerRef.current);
      snapshotTimerRef.current = null;
    }
    clearImageSource(streamImgRef.current);
    clearImageSource(posterImgRef.current);
  }, []);

  const stopStreamNow = useCallback(() => {
    abortStreamImages();
    setSnapshotUrl("");
    setStreamEnabled(false);
  }, [abortStreamImages]);

  const appendStreamParams = (url: string) => {
    if (!streamFps || streamFps <= 0) return url;
    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}fps=${Math.max(1, Math.min(12, Math.round(streamFps)))}`;
  };
  const effectiveStreamMode = streamMode === "auto"
    ? (detectMode ? "detect" : "raw")
    : streamMode;
  const primaryFrameBaseUrl = effectiveStreamMode === "raw"
    ? getCameraFrameUrl(cameraId)
    : getCameraDetectFrameUrl(cameraId);
  const fallbackFrameBaseUrl = effectiveStreamMode === "raw"
    ? getCameraSameOriginFrameUrl(cameraId)
    : getCameraSameOriginDetectFrameUrl(cameraId);
  const frameBaseUrl = useSameOriginStream ? fallbackFrameBaseUrl : primaryFrameBaseUrl;
  const posterUrl = retryKey > 0
    ? `${frameBaseUrl}${frameBaseUrl.includes("?") ? "&" : "?"}r=${retryKey}`
    : frameBaseUrl;
  const primaryStreamBaseUrl = effectiveStreamMode === "analytics"
    ? getCameraAnalyticsStreamUrl(cameraId, true)
    : effectiveStreamMode === "detect"
      ? getCameraDetectStreamUrl(cameraId)
      : getCameraStreamUrl(cameraId);
  const fallbackStreamBaseUrl = effectiveStreamMode === "analytics"
    ? getCameraSameOriginAnalyticsStreamUrl(cameraId, true)
    : effectiveStreamMode === "detect"
      ? getCameraSameOriginDetectStreamUrl(cameraId)
      : getCameraSameOriginStreamUrl(cameraId);
  const streamBaseUrl = appendStreamParams(useSameOriginStream ? fallbackStreamBaseUrl : primaryStreamBaseUrl);
  const streamUrl = retryKey > 0
    ? `${streamBaseUrl}${streamBaseUrl.includes("?") ? "&" : "?"}r=${retryKey}`
    : streamBaseUrl;
  const canUseSnapshotTransport = streamTransport === "snapshot" && effectiveStreamMode !== "analytics";
  const shouldShowMedia = streamEnabled && documentVisible && (eagerStream || isVisible);
  const shouldStream = shouldShowMedia && !canUseSnapshotTransport;
  const shouldSnapshot = shouldShowMedia && canUseSnapshotTransport;

  useEffect(() => {
    function handleStopStreams() {
      stopStreamNow();
    }

    window.addEventListener("traffixflow:stop-cctv-streams", handleStopStreams);
    window.addEventListener("pagehide", handleStopStreams);
    return () => {
      window.removeEventListener("traffixflow:stop-cctv-streams", handleStopStreams);
      window.removeEventListener("pagehide", handleStopStreams);
      abortStreamImages();
    };
  }, [abortStreamImages, stopStreamNow]);

  useEffect(() => {
    const node = containerRef.current;
    if (!node || !("IntersectionObserver" in window)) {
      const fallbackTimer = window.setTimeout(() => setIsVisible(true), 0);
      return () => window.clearTimeout(fallbackTimer);
    }

    const observer = new IntersectionObserver(
      ([entry]) => setIsVisible(Boolean(entry?.isIntersecting)),
      { rootMargin: "240px" }
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    function syncVisibility() {
      setDocumentVisible(!document.hidden);
    }

    syncVisibility();
    document.addEventListener("visibilitychange", syncVisibility);
    return () => document.removeEventListener("visibilitychange", syncVisibility);
  }, []);

  useEffect(() => {
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    const timer = window.setTimeout(() => {
      setUseSameOriginStream(false);
      setRetryKey(0);
    }, 0);
    return () => {
      window.clearTimeout(timer);
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };
  }, [cameraId, detectMode, streamFps, streamMode, streamTransport]);

  useEffect(() => {
    if (snapshotTimerRef.current !== null) {
      window.clearInterval(snapshotTimerRef.current);
      snapshotTimerRef.current = null;
    }
    if (!shouldSnapshot) {
      return;
    }

    const fps = streamFps && streamFps > 0 ? Math.max(1, Math.min(12, Math.round(streamFps))) : 3;
    const intervalMs = Math.max(250, Math.round(1000 / fps));
    const nextUrl = () => `${frameBaseUrl}${frameBaseUrl.includes("?") ? "&" : "?"}r=${Date.now()}`;
    const refreshSnapshot = () => {
      if (!document.hidden) {
        setSnapshotUrl(nextUrl());
      }
    };

    const firstRefreshTimer = window.setTimeout(refreshSnapshot, 0);
    snapshotTimerRef.current = window.setInterval(refreshSnapshot, intervalMs);

    return () => {
      window.clearTimeout(firstRefreshTimer);
      if (snapshotTimerRef.current !== null) {
        window.clearInterval(snapshotTimerRef.current);
        snapshotTimerRef.current = null;
      }
    };
  }, [frameBaseUrl, shouldSnapshot, streamFps]);

  function reconnectStream() {
    if (!streamEnabled) return;
    const primaryBase = canUseSnapshotTransport ? primaryFrameBaseUrl : primaryStreamBaseUrl;
    const fallbackBase = canUseSnapshotTransport ? fallbackFrameBaseUrl : fallbackStreamBaseUrl;
    if (!useSameOriginStream && primaryBase !== fallbackBase) {
      setUseSameOriginStream(true);
      setRetryKey((k) => k + 1);
      return;
    }
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
    }
    reconnectTimerRef.current = window.setTimeout(() => {
      if (document.hidden) return;
      setRetryKey((k) => k + 1);
    }, 3000);
  }

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

    if (!showCounts) {
      return () => {
        active = false;
      };
    }

    fetchCounts();
    const interval = setInterval(fetchCounts, 3000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [cameraId, showCounts]);

  return (
    <div ref={containerRef} className="relative h-full w-full overflow-hidden bg-[#0b1220]">
      {/* Poster frame keeps the panel from going black while MJPEG connects. */}
      {streamEnabled && showPoster && !canUseSnapshotTransport && (
        <img
          ref={posterImgRef}
          key={posterUrl}
          src={posterUrl}
          alt=""
          aria-hidden="true"
          className="absolute inset-0 h-full w-full object-contain"
          loading={eagerStream ? "eager" : "lazy"}
          decoding="async"
        />
      )}

      {/* Snapshot transport avoids keeping 6 long-lived MJPEG HTTP connections open. */}
      {shouldSnapshot && snapshotUrl && (
        <img
          ref={streamImgRef}
          src={snapshotUrl}
          alt={cameraName}
          className="absolute inset-0 h-full w-full object-contain"
          loading={eagerStream ? "eager" : "lazy"}
          decoding="async"
          fetchPriority={eagerStream ? "high" : "low"}
          onError={() => {
            reconnectStream();
          }}
        />
      )}

      {/* Video stream — mounted only when visible to avoid many live MJPEG decoders. */}
      {shouldStream && (
        <img
          ref={streamImgRef}
          key={streamUrl}
          src={streamUrl}
          alt={cameraName}
          className="absolute inset-0 h-full w-full object-contain"
          loading={eagerStream ? "eager" : "lazy"}
          decoding="async"
          fetchPriority={eagerStream ? "high" : "low"}
          onError={() => {
            reconnectStream();
          }}
          onLoad={() => {}}
        />
      )}

      {showInfoOverlay && (
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
      )}

      {showMiniMap && (
        <div className="absolute right-3 top-14 z-[3] h-[clamp(84px,22%,118px)] w-[clamp(120px,28%,168px)] overflow-hidden rounded-lg border border-cyan-300/35 bg-slate-950/85 shadow-[0_12px_30px_rgba(2,8,23,0.45)] backdrop-blur">
          {Number.isFinite(cameraLat) && Number.isFinite(cameraLng) ? (
            <CctvMiniMap
              cameraId={cameraId}
              cameraName={cameraName}
              lat={Number(cameraLat)}
              lng={Number(cameraLng)}
              pollInterval={3000}
              detectMode={detectMode}
              zoom={18}
              interactive={false}
              showHud={false}
            />
          ) : (
            <div className="flex h-full items-center justify-center px-3 text-center text-[10px] text-slate-300">
              รอพิกัดกล้อง
            </div>
          )}
        </div>
      )}

      {showCounts && (
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
      )}
    </div>
  );
}
