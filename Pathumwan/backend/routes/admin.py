"""
Admin Routes — Signal control, AI mode toggle, system management
"""

import os
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify

from routes.auth import require_admin, require_auth
from database.connection import get_session
from database.models import AIDecision
from services.camera_runtime import (
    apply_runtime_seed,
    build_runtime_seed_payload,
    get_camera_runtime_bundle,
    list_camera_runtime_status,
    load_runtime_seed_json,
    replace_camera_zones,
    upsert_camera_calibration,
)
from services.mapping import (
    get_camera_catalog,
    get_junction_display_name,
    get_research_targets,
    looks_like_machine_id,
)
from services.live_state import get_latest_junction_state
from services.signal_controller import (
    record_manual_override,
    get_ai_last_decisions,
    get_runtime_backends,
    get_signal_controller,
    get_signal_mode,
    set_signal_mode,
    get_active_ai_algorithm,
    set_active_ai_algorithm,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")

_sim = None


def init_admin_routes(simulation_module):
    global _sim
    _sim = simulation_module


def _controller():
    return get_signal_controller(_sim)

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_runtime_logs(limit: int = 100) -> list[dict[str, str]]:
    session = get_session()
    try:
        logs: list[dict[str, str]] = []
        backends = get_runtime_backends()
        current_mode = get_signal_mode()
        current_algorithm = get_active_ai_algorithm()
        controller_status = _controller().get_status()
        runtime_ready = bool(
            controller_status.get("simulation_active", False)
            or get_latest_junction_state()
            or get_camera_catalog()
        )

        logs.extend(
            [
                {
                    "timestamp": _now_iso(),
                    "message": f"โหมดควบคุมปัจจุบัน: {'AI' if current_mode == 'ai' else 'Manual'}",
                    "level": "info",
                },
                {
                    "timestamp": _now_iso(),
                    "message": f"AI algorithm ปัจจุบัน: {current_algorithm}",
                    "level": "info",
                },
                {
                    "timestamp": _now_iso(),
                    "message": (
                        f"Runtime {'พร้อม' if runtime_ready else 'รอข้อมูล'}"
                        f" • system={backends.get('system_mode', 'sim')}"
                        f" • camera={backends.get('camera_backend', 'sumo')}"
                        f" • signal={backends.get('signal_backend', 'sim')}"
                    ),
                    "level": "info" if runtime_ready else "warning",
                },
            ]
        )

        rows = (
            session.query(AIDecision)
            .order_by(AIDecision.timestamp.desc())
            .limit(max(1, min(limit, 40)))
            .all()
        )
        for row in rows:
            output = row.output or {}
            phase = output.get("phase", output.get("target_phase", 0))
            applied = bool(output.get("applied", False))
            junction_label = (
                get_junction_display_name(row.junction_id, fallback=row.junction_id)
                or str(row.junction_id or "")
            )
            algorithm = str(
                row.model_version
                or output.get("algorithm")
                or output.get("method")
                or "AI"
            )
            logs.append(
                {
                    "timestamp": row.timestamp.isoformat() if row.timestamp else _now_iso(),
                    "message": (
                        f"{algorithm} เลือก phase {phase} ให้ {junction_label}"
                        f"{' และถูกใช้งานแล้ว' if applied else ''}"
                    ),
                    "level": "info" if applied else "warning",
                }
            )

        logs.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        return logs[:limit]
    finally:
        session.close()


def _research_target_lookup() -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    targets = get_research_targets()
    by_junction = {
        str(target.get("junction_id") or ""): target
        for target in targets
        if str(target.get("junction_id") or "")
    }
    return targets, by_junction


@admin_bp.route("/signal/mode", methods=["POST"])
@require_admin
def api_signal_mode():
    """Toggle between AI and manual signal control mode."""
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "").lower()

    if mode not in ("ai", "manual"):
        return jsonify({"success": False, "message": "Mode ต้องเป็น 'ai' หรือ 'manual'"}), 400

    mode = set_signal_mode(mode)

    return jsonify({
        "success": True,
        "mode": mode,
        "message": f"เปลี่ยนเป็นโหมด {'AI อัตโนมัติ' if mode == 'ai' else 'ควบคุมเอง'} สำเร็จ",
    })


@admin_bp.route("/signal/manual", methods=["POST"])
@require_admin
def api_signal_manual():
    """Manually control a traffic light.

    Optional `direction` param ("all" | "ns" | "ew" | "n" | "e" | "s" | "w")
    selects which approaches to flip. Defaults to "all" for backwards compat.

    When the underlying simulation is not running we return 200 with
    `applied: false, reason: "sim_inactive"` so the /control page can show a
    toast instead of a generic error.
    """
    if get_signal_mode() == "ai":
        return jsonify({"success": False, "applied": False, "reason": "ai_mode", "message": "กรุณาปิดโหมด AI ก่อนควบคุมเอง"}), 400

    data = request.get_json(silent=True) or {}
    junction_id = data.get("junction_id")
    color = data.get("state", data.get("color", "")).lower()
    direction = (data.get("direction") or "all").lower().strip()

    if not junction_id or color not in ("red", "yellow", "green"):
        return jsonify({
            "success": False,
            "applied": False,
            "reason": "bad_request",
            "message": "กรุณาระบุ junction_id และ color (red/yellow/green)",
        }), 400
    if direction not in ("all", "ns", "ew", "n", "e", "s", "w"):
        return jsonify({
            "success": False,
            "applied": False,
            "reason": "bad_direction",
            "message": "direction ต้องเป็น all/ns/ew/n/e/s/w",
        }), 400

    try:
        new_state = _controller().set_manual_color(junction_id, color, direction=direction)
        record_manual_override(junction_id, color=color, direction=direction)

        return jsonify({
            "success": True,
            "applied": True,
            "message": f"เปลี่ยนไฟ {junction_id} ทิศ {direction} เป็น {color} สำเร็จ",
            "junction_id": junction_id,
            "state": color,
            "direction": direction,
        })
    except NotImplementedError as e:
        return jsonify({"success": False, "applied": False, "reason": "not_implemented", "message": str(e)})
    except RuntimeError as e:
        # Sim not active is an expected, recoverable state — 200 + reason so
        # the UI shows a friendly toast instead of a generic 503 error.
        return jsonify({"success": False, "applied": False, "reason": "sim_inactive", "message": str(e)})
    except Exception as e:
        return jsonify({"success": False, "applied": False, "reason": "internal_error", "message": f"เกิดข้อผิดพลาด: {str(e)}"}), 500


@admin_bp.route("/ai-status")
@require_auth
def api_ai_status():
    """Get current AI system status."""
    status = _controller().get_status()
    mode = get_signal_mode()
    algorithm = get_active_ai_algorithm()
    research_targets, _ = _research_target_lookup()
    pathumwan_scope_count = sum(
        1 for target in research_targets
        if str(target.get("scope") or "pathumwan").strip() != "ratchathewi_feeder"
    )
    feeder_scope_count = sum(
        1 for target in research_targets
        if str(target.get("scope") or "").strip() == "ratchathewi_feeder"
    )
    runtime_ready = bool(
        status.get("simulation_active", False)
        or get_latest_junction_state()
        or get_camera_catalog()
    )
    return jsonify({
        "mode": mode,
        "algorithm": algorithm,
        "active": bool(status.get("active", False)),
        "ai_mode": mode == "ai",
        "simulation_active": bool(status.get("simulation_active", False)),
        "runtime_ready": runtime_ready,
        "step": int(status.get("step", 0) or 0),
        "camera_count": int(status.get("camera_count", 0) or 0),
        "research_junction_count": len(research_targets),
        "pathumwan_research_junction_count": pathumwan_scope_count,
        "ratchathewi_feeder_junction_count": feeder_scope_count,
        "backends": get_runtime_backends(),
        "last_decisions": get_ai_last_decisions(),
    })

@admin_bp.route("/ai/algorithm", methods=["GET"])
@require_auth
def api_get_ai_algorithm():
    """Get current AI algorithm."""
    return jsonify({"algorithm": get_active_ai_algorithm()})

@admin_bp.route("/ai/algorithm", methods=["POST"])
@require_admin
def api_set_ai_algorithm():
    """Set the active AI algorithm."""
    data = request.get_json(silent=True) or {}
    algorithm = data.get("algorithm", "").upper()

    try:
        new_algorithm = set_active_ai_algorithm(algorithm)

        return jsonify({
            "success": True,
            "algorithm": new_algorithm,
            "message": f"เปลี่ยน AI เป็น {new_algorithm} สำเร็จ"
        })
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400


@admin_bp.route("/ai/algorithms", methods=["GET"])
@require_auth
def api_list_ai_algorithms():
    """List research AI algorithms exposed in the control UI."""
    model_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ai", "models")
    algorithms = []
    for algorithm in ("PPO", "DQN", "A2C"):
        model_path = os.path.join(model_dir, f"{algorithm.lower()}_traffic.zip")
        algorithms.append({
            "id": algorithm,
            "label": algorithm,
            "model_available": os.path.exists(model_path),
        })
    return jsonify({
        "current": get_active_ai_algorithm(),
        "algorithms": algorithms,
    })


@admin_bp.route("/ai/decisions", methods=["GET"])
@require_auth
def api_ai_decisions():
    """Return recent AI decision history persisted in the database."""
    limit = max(1, min(int(request.args.get("limit", 50) or 50), 200))

    session = get_session()
    try:
        rows = (
            session.query(AIDecision)
            .order_by(AIDecision.timestamp.desc())
            .limit(limit)
            .all()
        )
        return jsonify({
            "success": True,
            "decisions": [
                {
                    "id": row.id,
                    "junction_id": str(row.junction_id or ""),
                    "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                    "reward": float(row.reward or 0.0),
                    "model_version": str(row.model_version or ""),
                    "input_data": row.input_data or {},
                    "output": row.output or {},
                }
                for row in rows
            ],
        })
    finally:
        session.close()


@admin_bp.route("/signal/mode", methods=["GET"])
def api_get_signal_mode():
    """Get current signal control mode."""
    return jsonify({"mode": get_signal_mode()})


@admin_bp.route("/signal/junctions")
@require_auth
def api_signal_junctions():
    """Get all traffic light junctions with current state and phase info."""
    research_targets, research_by_junction = _research_target_lookup()
    if _sim is None or not _sim.sim_active:
        junction_state = {
            str(item.get("junction_id") or ""): item
            for item in get_latest_junction_state()
            if str(item.get("junction_id") or "")
        }
        camera_catalog = get_camera_catalog()
        fallback_junctions = []
        if research_targets:
            for target in research_targets:
                junction_id = str(target.get("junction_id") or "")
                if not junction_id:
                    continue
                camera_id = str(target.get("camera_id") or "")
                camera = camera_catalog.get(camera_id, {})
                live_state = junction_state.get(junction_id, {})
                display_name = str(target.get("label_th") or target.get("camera_label_th") or junction_id)
                fallback_junctions.append(
                    {
                        "id": junction_id,
                        "name": display_name,
                        "display_name_th": display_name,
                        "camera_id": camera_id,
                        "lat": float(camera.get("lat") or target.get("lat") or 0.0),
                        "lng": float(camera.get("lng") or target.get("lng") or 0.0),
                        "current_state": "",
                        "current_phase": int(live_state.get("current_phase") or 0),
                        "time_to_switch": max(0.0, round(float(live_state.get("next_switch_eta") or 0.0), 1)),
                        "phases": [],
                    }
                )
        else:
            seen: set[str] = set()
            for camera in camera_catalog.values():
                junction_id = str(camera.get("junction_id") or "")
                if not junction_id or junction_id in seen:
                    continue
                seen.add(junction_id)

                camera_name = str(camera.get("name") or "")
                display_name = get_junction_display_name(junction_id, fallback=camera_name)
                if not display_name:
                    display_name = camera_name if camera_name and not looks_like_machine_id(camera_name) else junction_id

                live_state = junction_state.get(junction_id, {})
                fallback_junctions.append(
                    {
                        "id": junction_id,
                        "name": display_name,
                        "display_name_th": display_name,
                        "camera_id": str(camera.get("camera_id") or ""),
                        "lat": float(camera.get("lat") or 0.0),
                        "lng": float(camera.get("lng") or 0.0),
                        "current_state": "",
                        "current_phase": int(live_state.get("current_phase") or 0),
                        "time_to_switch": max(0.0, round(float(live_state.get("next_switch_eta") or 0.0), 1)),
                        "phases": [],
                    }
                )

        return jsonify({
            "status": "ok" if fallback_junctions else "waiting",
            "junctions": fallback_junctions,
        })

    junctions = []
    try:
        camera_catalog = get_camera_catalog()
        traci = _sim.get_traci()
        with _sim.sim_lock:
            research_by_effective_tls: dict[str, dict[str, object]] = {}
            if research_targets:
                candidate_tls_ids = []
                for target in research_targets:
                    target_junction_id = str(target.get("junction_id") or "")
                    target_camera_id = str(target.get("camera_id") or "")
                    if not target_junction_id and not target_camera_id:
                        continue
                    cam_for_target = next(
                        (
                            c for c in _sim.camera_points
                            if str(c.get("camera_id") or "") == target_camera_id
                            or str(c.get("sumo_tls_id") or "") == target_junction_id
                        ),
                        None,
                    )
                    effective_tls_id = str(
                        (cam_for_target or {}).get("sumo_tls_id")
                        or target_junction_id
                    )
                    if not effective_tls_id:
                        continue
                    candidate_tls_ids.append(effective_tls_id)
                    research_by_effective_tls[effective_tls_id] = target
            else:
                candidate_tls_ids = list(traci.trafficlight.getIDList())

            for tid in candidate_tls_ids:
                try:
                    state = traci.trafficlight.getRedYellowGreenState(tid)
                    phase_idx = traci.trafficlight.getPhase(tid)
                    raw_time_left = traci.trafficlight.getNextSwitch(tid) - traci.simulation.getTime()
                    programs = traci.trafficlight.getAllProgramLogics(tid)

                    phases = []
                    current_phase_duration = None
                    if programs:
                        for i, p in enumerate(programs[0].phases):
                            g = p.state.lower().count("g")
                            total = max(1, len(p.state))
                            dom = "green" if g / total > 0.3 else ("yellow" if "y" in p.state.lower() else "red")
                            phases.append({
                                "index": i,
                                "state": p.state,
                                "duration": p.duration,
                                "minDur": p.minDur,
                                "maxDur": p.maxDur,
                                "type": dom,
                            })
                        if 0 <= phase_idx < len(programs[0].phases):
                            current_phase_duration = float(programs[0].phases[phase_idx].duration)

                    time_left = raw_time_left

                    if time_left < 0:
                        time_left = raw_time_left
                    if current_phase_duration is not None and time_left > current_phase_duration + 5:
                        time_left = current_phase_duration
                    time_left = max(0.0, min(float(time_left), 300.0))

                    cam = next(
                        (c for c in _sim.camera_points if str(c.get("sumo_tls_id", "")) == str(tid)),
                        None,
                    )
                    if cam is None:
                        cam = next((c for c in _sim.camera_points if c.get("junction") == tid), None)
                    if cam is None:
                        cam = next(
                            (
                                meta for meta in camera_catalog.values()
                                if str(meta.get("junction_id") or "") == str(tid)
                            ),
                            None,
                        )
                    cam_name = cam.get("name", "") if cam else ""
                    cam_junction = cam.get("junction_id") or cam.get("junction") if cam else ""
                    research_target = research_by_effective_tls.get(str(tid)) or research_by_junction.get(str(tid))
                    display_name = (
                        str(research_target.get("label_th") or research_target.get("camera_label_th") or "")
                        if research_target
                        else get_junction_display_name(
                            tid,
                            fallback=cam_name if cam_name and not looks_like_machine_id(cam_name) else cam_junction,
                        )
                    )
                    # Hide junctions we have no Thai name for — keeps UI readable.
                    if not display_name:
                        if cam_name and not looks_like_machine_id(cam_name):
                            display_name = cam_name
                        else:
                            continue

                    junctions.append({
                        "id": tid,
                        "name": display_name,
                        "display_name_th": display_name,
                        "camera_id": (
                            str(research_target.get("camera_id") or "")
                            if research_target and research_target.get("camera_id")
                            else cam.get("camera_id", "") if cam else ""
                        ),
                        "lat": (
                            float(cam.get("lat", 0))
                            if cam and cam.get("lat") is not None
                            else float(research_target.get("lat", 0) or 0) if research_target else 0
                        ),
                        "lng": (
                            float(cam.get("lng", 0))
                            if cam and cam.get("lng") is not None
                            else float(research_target.get("lng", 0) or 0) if research_target else 0
                        ),
                        "current_state": state,
                        "current_phase": phase_idx,
                        "time_to_switch": max(0, round(time_left, 1)),
                        "phases": phases,
                    })
                except Exception:
                    continue
    except Exception:
        pass

    return jsonify({"status": "ok", "junctions": junctions})


@admin_bp.route("/signal/phase", methods=["POST"])
@require_admin
def api_signal_set_phase():
    """Set phase durations for a traffic light junction."""
    if get_signal_mode() == "ai":
        return jsonify({"success": False, "message": "กรุณาปิดโหมด AI ก่อนตั้งค่า"}), 400

    data = request.get_json(silent=True) or {}
    junction_id = data.get("junction_id")
    phase_durations = data.get("phases")  # [{index, duration}, ...]

    if not junction_id or not phase_durations:
        return jsonify({"success": False, "message": "กรุณาระบุ junction_id และ phases"}), 400

    try:
        _controller().set_phase_plan(junction_id, phase_durations)
        record_manual_override(junction_id, phase_durations=phase_durations)

        return jsonify({
            "success": True,
            "message": f"ตั้งค่าเฟสไฟจราจร {junction_id} สำเร็จ",
        })
    except NotImplementedError as e:
        return jsonify({"success": False, "applied": False, "reason": "not_implemented", "message": str(e)})
    except RuntimeError as e:
        # Sim not active is an expected, recoverable state — surface as 200 + reason
        # so the /control page can render a "ยังไม่พร้อม" toast instead of 503.
        return jsonify({"success": False, "applied": False, "reason": "sim_inactive", "message": str(e)})
    except LookupError as e:
        return jsonify({"success": False, "applied": False, "reason": "not_found", "message": str(e)}), 404
    except Exception as e:
        return jsonify({"success": False, "applied": False, "reason": "internal_error", "message": f"เกิดข้อผิดพลาด: {str(e)}"}), 500


@admin_bp.route("/users")
@require_admin
def api_admin_users():
    """Get all users."""
    from database.models import User as UserModel
    session = get_session()
    try:
        users = session.query(UserModel).all()
        return jsonify([{
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "role": u.role,
            "created_at": str(u.created_at) if u.created_at is not None else None,
        } for u in users])
    finally:
        session.close()


@admin_bp.route("/logs")
@require_auth
def api_admin_logs():
    """Get synthesized runtime activity logs without depending on audit tables."""
    return jsonify(_build_runtime_logs())


@admin_bp.route("/camera-runtime")
@require_admin
def api_camera_runtime_index():
    """List camera runtime readiness for calibration/zone tuning."""
    return jsonify({"success": True, "cameras": list_camera_runtime_status()})


@admin_bp.route("/camera-runtime/<camera_id>")
@require_admin
def api_camera_runtime_detail(camera_id: str):
    """Get one camera's calibration and zone bundle."""
    try:
        return jsonify({"success": True, **get_camera_runtime_bundle(camera_id)})
    except LookupError as e:
        return jsonify({"success": False, "message": str(e)}), 404


@admin_bp.route("/camera-runtime/export")
@require_admin
def api_camera_runtime_export():
    """Export current runtime config for all cameras as JSON."""
    return jsonify({"success": True, **build_runtime_seed_payload()})


@admin_bp.route("/camera-runtime/<camera_id>/calibration", methods=["PUT"])
@require_admin
def api_camera_runtime_upsert_calibration(camera_id: str):
    """Upsert calibration for one camera."""
    payload = request.get_json(silent=True) or {}
    try:
        calibration = upsert_camera_calibration(camera_id, payload)
        return jsonify({"success": True, "camera_id": camera_id, "calibration": calibration})
    except LookupError as e:
        return jsonify({"success": False, "message": str(e)}), 404
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400


@admin_bp.route("/camera-runtime/<camera_id>/zones", methods=["PUT"])
@require_admin
def api_camera_runtime_replace_zones(camera_id: str):
    """Replace all zones for one camera in a single request."""
    payload = request.get_json(silent=True) or {}
    zones = payload.get("zones")
    if not isinstance(zones, list):
        return jsonify({"success": False, "message": "Payload ต้องมี zones เป็น list"}), 400
    try:
        saved_zones = replace_camera_zones(camera_id, zones)
        return jsonify({"success": True, "camera_id": camera_id, "zones": saved_zones})
    except LookupError as e:
        return jsonify({"success": False, "message": str(e)}), 404
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400


@admin_bp.route("/camera-runtime/import", methods=["POST"])
@require_admin
def api_camera_runtime_import_seed():
    """Import runtime config seed from a JSON file path on the server."""
    payload = request.get_json(silent=True) or {}
    file_path = str(payload.get("file_path") or "").strip()
    replace_zones = bool(payload.get("replace_zones", False))
    if not file_path:
        return jsonify({"success": False, "message": "กรุณาระบุ file_path"}), 400
    try:
        summary = apply_runtime_seed(load_runtime_seed_json(file_path), replace_zones=replace_zones)
        return jsonify({"success": True, "file_path": file_path, "summary": summary})
    except FileNotFoundError:
        return jsonify({"success": False, "message": "ไม่พบไฟล์ seed JSON"}), 404
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400


def is_ai_mode():
    """Check if AI mode is active (used by other modules)."""
    return get_signal_mode() == "ai"
