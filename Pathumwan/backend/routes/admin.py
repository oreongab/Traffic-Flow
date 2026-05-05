"""
Admin Routes — Signal control, AI mode toggle, system management
"""

from flask import Blueprint, g, request, jsonify

from routes.auth import require_admin
from database.connection import get_session
from database.models import SignalTiming, SystemLog
from database.reference_data import ensure_junction
from services.camera_runtime import (
    apply_runtime_seed,
    build_runtime_seed_payload,
    get_camera_runtime_bundle,
    list_camera_runtime_status,
    load_runtime_seed_json,
    replace_camera_zones,
    upsert_camera_calibration,
)
from services.mapping import get_camera_catalog, get_junction_display_name, looks_like_machine_id
from services.signal_controller import (
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


def _request_user() -> dict:
    current_user = getattr(g, "current_user", None)
    return current_user if isinstance(current_user, dict) else {}


@admin_bp.route("/signal/mode", methods=["POST"])
@require_admin
def api_signal_mode():
    """Toggle between AI and manual signal control mode."""
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "").lower()
    current_user = _request_user()

    if mode not in ("ai", "manual"):
        return jsonify({"success": False, "message": "Mode ต้องเป็น 'ai' หรือ 'manual'"}), 400

    mode = set_signal_mode(mode)

    session = get_session()
    try:
        log = SystemLog(
            event_type="signal_mode_change",
            details={"mode": mode, "by": current_user.get("username", "admin")},
        )
        session.add(log)
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()

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
    current_user = _request_user()
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

        # Save to DB
        session = get_session()
        try:
            normalized_junction = ensure_junction(session, junction_id, junction_name=junction_id)
            timing = SignalTiming(
                junction_id=str(normalized_junction or junction_id),
                phase_durations=[{"phase": 0, "duration": 30, "state": new_state, "direction": direction}],
                mode="manual",
                decided_by=current_user.get("username", "admin"),
            )
            session.add(timing)
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

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
@require_admin
def api_ai_status():
    """Get current AI system status."""
    status = _controller().get_status()
    mode = get_signal_mode()
    algorithm = get_active_ai_algorithm()
    return jsonify({
        "mode": mode,
        "algorithm": algorithm,
        "active": bool(status.get("active", False)),
        "ai_mode": mode == "ai",
        "simulation_active": bool(status.get("simulation_active", False)),
        "step": int(status.get("step", 0) or 0),
        "camera_count": int(status.get("camera_count", 0) or 0),
        "backends": get_runtime_backends(),
        "last_decisions": get_ai_last_decisions(),
    })

@admin_bp.route("/ai/algorithm", methods=["GET"])
@require_admin
def api_get_ai_algorithm():
    """Get current AI algorithm."""
    return jsonify({"algorithm": get_active_ai_algorithm()})

@admin_bp.route("/ai/algorithm", methods=["POST"])
@require_admin
def api_set_ai_algorithm():
    """Set the active AI algorithm."""
    data = request.get_json(silent=True) or {}
    algorithm = data.get("algorithm", "").upper()
    current_user = _request_user()

    try:
        new_algorithm = set_active_ai_algorithm(algorithm)
        
        session = get_session()
        try:
            log = SystemLog(
                event_type="ai_algorithm_change",
                details={"algorithm": new_algorithm, "by": current_user.get("username", "admin")},
            )
            session.add(log)
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()
            
        return jsonify({
            "success": True,
            "algorithm": new_algorithm,
            "message": f"เปลี่ยน AI เป็น {new_algorithm} สำเร็จ"
        })
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400


@admin_bp.route("/signal/mode", methods=["GET"])
def api_get_signal_mode():
    """Get current signal control mode."""
    return jsonify({"mode": get_signal_mode()})


@admin_bp.route("/signal/junctions")
@require_admin
def api_signal_junctions():
    """Get all traffic light junctions with current state and phase info."""
    if _sim is None or not _sim.sim_active:
        return jsonify({"status": "waiting", "junctions": []})

    junctions = []
    try:
        camera_catalog = get_camera_catalog()
        traci = _sim.get_traci()
        with _sim.sim_lock:
            for tid in traci.trafficlight.getIDList():
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
                    try:
                        spent_duration = float(traci.trafficlight.getSpentDuration(tid))
                        if current_phase_duration is not None:
                            time_left = current_phase_duration - spent_duration
                    except Exception:
                        pass

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
                    display_name = get_junction_display_name(
                        tid,
                        fallback=cam_name if cam_name and not looks_like_machine_id(cam_name) else cam_junction,
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
                        "camera_id": cam.get("camera_id", "") if cam else "",
                        "lat": cam.get("lat", 0) if cam else 0,
                        "lng": cam.get("lng", 0) if cam else 0,
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
    current_user = _request_user()
    junction_id = data.get("junction_id")
    phase_durations = data.get("phases")  # [{index, duration}, ...]

    if not junction_id or not phase_durations:
        return jsonify({"success": False, "message": "กรุณาระบุ junction_id และ phases"}), 400

    try:
        _controller().set_phase_plan(junction_id, phase_durations)

        # Save to DB
        session = get_session()
        try:
            normalized_junction = ensure_junction(session, junction_id, junction_name=junction_id)
            timing = SignalTiming(
                junction_id=str(normalized_junction or junction_id),
                phase_durations=[{"index": p["index"], "duration": p["duration"]} for p in phase_durations],
                mode="manual",
                decided_by=current_user.get("username", "admin"),
            )
            session.add(timing)
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

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
@require_admin
def api_admin_logs():
    """Get system logs."""
    session = get_session()
    try:
        logs = session.query(SystemLog).order_by(SystemLog.timestamp.desc()).limit(100).all()
        return jsonify([{
            "timestamp": str(l.timestamp),
            "message": str(l.details.get("message", l.event_type)) if isinstance(l.details, dict) else l.event_type,
            "level": l.details.get("level", "info") if isinstance(l.details, dict) else "info",
        } for l in logs])
    finally:
        session.close()


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
