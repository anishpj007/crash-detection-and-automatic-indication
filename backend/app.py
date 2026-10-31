"""
app.py
Flask REST API for the Automatic Crash Detection System.

Run with:  python app.py
Serves the frontend (static files) AND the JSON API from one process,
so there's nothing extra to configure (no CORS issues, no separate server).
"""

import os
import json
import threading
import datetime
import base64
import secrets
import subprocess
import sys
import jwt
import cv2
import numpy as np
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, g
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

import database as db
from detection import CrashDetector, SNAPSHOT_DIR
from sms_alerts import SmsDeliveryError, get_sms_status, send_sms

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
TRAINING_DIR = os.path.join(BASE_DIR, "training_uploads")
FRONTEND_DIR = os.path.join(os.path.dirname(BASE_DIR), "frontend")
ALLOWED_EXTENSIONS = {"mp4", "avi", "mov", "mkv"}

SECRET_KEY = os.environ.get("CRASH_APP_SECRET") or secrets.token_hex(32)

# The demonstration project assigns every detected incident to this fixed site.
# Coordinates point to the SRMIST Kattankulathur campus area.
INCIDENT_LOCATION_NAME = "SRM Institute of Science and Technology, Kattankulathur, Tamil Nadu"
INCIDENT_LOCATION_LATITUDE = 12.824943
INCIDENT_LOCATION_LONGITUDE = 80.045181
INCIDENT_MAP_URL = (
    "https://www.google.com/maps/search/?api=1&query="
    f"{INCIDENT_LOCATION_LATITUDE}%2C{INCIDENT_LOCATION_LONGITUDE}"
)


def _incident_location_fields():
    return {
        "location_name": INCIDENT_LOCATION_NAME,
        "latitude": INCIDENT_LOCATION_LATITUDE,
        "longitude": INCIDENT_LOCATION_LONGITUDE,
        "map_url": INCIDENT_MAP_URL,
    }


def _with_incident_location(row):
    result = dict(row)
    result.update(_incident_location_fields())
    return result

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB upload cap

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(TRAINING_DIR, exist_ok=True)

# In-memory job status tracker: {video_id: {"status": ..., "progress": 0-100}}
JOBS = {}

# Live browser-camera sessions.
LIVE_SESSIONS = {}
LIVE_SESSIONS_LOCK = threading.Lock()

# Training job state (one at a time)
TRAIN_JOB = {"status": "idle", "run_id": None, "log": []}
TRAIN_LOCK = threading.Lock()


def _deliver_live_sms(alert_id, message):
    """Submit a queued SMS outside the live-frame request and record outcome."""
    try:
        send_sms(message)
    except SmsDeliveryError as exc:
        status = f"failed: {exc}"
        db.execute(
            "UPDATE alerts SET sent_status = ? WHERE id = ?", (status, alert_id)
        )
        app.logger.warning("SMS alert %s failed (%s)", alert_id, exc.error_code)
    except Exception as exc:
        db.execute(
            "UPDATE alerts SET sent_status = 'failed: unexpected error' WHERE id = ?",
            (alert_id,),
        )
        app.logger.exception("Unexpected SMS error for alert %s", alert_id)
    else:
        # Twilio has accepted the message for delivery; final handset delivery
        # receipts require a separately configured provider status callback.
        db.execute(
            "UPDATE alerts SET sent_status = 'queued' WHERE id = ?", (alert_id,)
        )


def _queue_live_sms(incident_id, severity, confidence, location_name):
    """Create an alert row and asynchronously notify the configured phone."""
    try:
        status = get_sms_status()
        if status["ready"]:
            sent_status = "queued"
        elif status["state"] == "disabled":
            sent_status = "disabled"
        else:
            sent_status = "not_configured"

        alert_id = db.execute(
            "INSERT INTO alerts (incident_id, channel, recipient, sent_status) VALUES (?, ?, ?, ?)",
            (incident_id, "sms", os.environ.get("SMS_TO_NUMBER"), sent_status),
        )
        if sent_status != "queued":
            return sent_status

        safe_location = INCIDENT_LOCATION_NAME[:60]
        detected_at = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        message = (
            f"CRASH ALERT #{incident_id}: {severity} accident detected at "
            f"{safe_location}. Confidence {round(float(confidence) * 100)}%. "
            f"Time: {detected_at}. Map: {INCIDENT_MAP_URL}"
        )
        threading.Thread(
            target=_deliver_live_sms,
            args=(alert_id, message),
            daemon=True,
        ).start()
        return sent_status
    except Exception:
        app.logger.exception("Could not queue SMS alert for incident %s", incident_id)
        return "failed"


# ---------------- Auth helpers ----------------

def make_token(user):
    payload = {
        "user_id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=12),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def token_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header"}), 401
        token = auth_header.split(" ", 1)[1]
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
            g.user = payload
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401
        return f(*args, **kwargs)
    return wrapper


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ---------------- Auth routes ----------------

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "username and password are required"}), 400

    existing = db.fetch_one("SELECT id FROM users WHERE username = ?", (username,))
    if existing:
        return jsonify({"error": "username already exists"}), 409

    user_id = db.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        (username, generate_password_hash(password), "operator"),
    )
    return jsonify({"message": "registered", "user_id": user_id}), 201


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    user = db.fetch_one("SELECT * FROM users WHERE username = ?", (username,))
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "invalid credentials"}), 401

    token = make_token(user)
    return jsonify({
        "token": token,
        "user": {"id": user["id"], "username": user["username"], "role": user["role"]},
    })


# ---------------- Live camera processing ----------------

@app.route("/api/live/start", methods=["POST"])
@token_required
def live_start():
    data = request.get_json(silent=True) or {}
    location_name = INCIDENT_LOCATION_NAME
    latitude = INCIDENT_LOCATION_LATITUDE
    longitude = INCIDENT_LOCATION_LONGITUDE

    session_id = secrets.token_urlsafe(18)
    original_name = f"LIVE CAMERA — {session_id[:8]}"

    try:
        detector = CrashDetector(None, 0, live_mode=True)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503

    video_id = db.execute(
        """INSERT INTO videos (filename, original_name, location_name, latitude, longitude, uploaded_by, status)
           VALUES (?, ?, ?, ?, ?, ?, 'processing')""",
        ("__live_camera__", original_name, location_name, latitude, longitude, g.user["user_id"]),
    )

    detector.video_id = video_id
    session = {
        "video_id": video_id,
        "detector": detector,
        "location_name": location_name,
        "latitude": latitude,
        "longitude": longitude,
        "started_at": datetime.datetime.utcnow().isoformat() + "Z",
        "fps": float(data.get("fps", 12) or 12),
        "lock": threading.Lock(),
        "frames": 0,
        "incidents": 0,
    }
    with LIVE_SESSIONS_LOCK:
        LIVE_SESSIONS[session_id] = session

    return jsonify({"session_id": session_id, "video_id": video_id, "fps": session["fps"]}), 201


@app.route("/api/live/frame", methods=["POST"])
@token_required
def live_frame():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    image_data = data.get("image")
    if not session_id or not image_data:
        return jsonify({"error": "session_id and image are required"}), 400

    with LIVE_SESSIONS_LOCK:
        session = LIVE_SESSIONS.get(session_id)
    if not session:
        return jsonify({"error": "live session not found"}), 404

    try:
        encoded = image_data.split(",", 1)[1] if "," in image_data else image_data
        raw = base64.b64decode(encoded, validate=True)
        frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("invalid JPEG frame")
    except Exception as exc:
        return jsonify({"error": f"invalid frame: {exc}"}), 400

    sms_alert_status = None
    sms_incident = None
    with session["lock"]:
        incident = session["detector"].process_frame(frame, fps=session["fps"])
        session["frames"] += 1

        if incident:
            incident_id = db.execute(
                """INSERT INTO incidents
                   (video_id, frame_number, timestamp_in_video, severity, confidence,
                    snapshot_path, location_name, latitude, longitude, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')""",
                (
                    session["video_id"], incident["frame_number"], incident["timestamp_in_video"],
                    incident["severity"], incident["confidence"], incident["snapshot_path"],
                    session["location_name"], session["latitude"], session["longitude"],
                ),
            )
            session["incidents"] += 1
            incident = {**incident, "id": incident_id, **_incident_location_fields()}
            sms_incident = incident

    if sms_incident:
        sms_alert_status = _queue_live_sms(
            sms_incident["id"],
            sms_incident["severity"],
            sms_incident["confidence"],
            session["location_name"],
        )
        incident = {**sms_incident, "sms_alert_status": sms_alert_status}

    return jsonify({
        "frame_number": session["frames"],
        "incident": incident,
        "sms_alert_status": sms_alert_status,
        "incidents_found": session["incidents"],
        "debug": getattr(session["detector"], "live_last_debug", {}),
    })


@app.route("/api/notifications/sms/status", methods=["GET"])
@token_required
def sms_notification_status():
    status = get_sms_status()
    latest_attempt = db.fetch_one(
        "SELECT sent_status, sent_at FROM alerts WHERE channel = 'sms' ORDER BY id DESC LIMIT 1"
    )
    status["last_attempt"] = latest_attempt
    return jsonify(status)


@app.route("/api/live/stop", methods=["POST"])
@token_required
def live_stop():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    with LIVE_SESSIONS_LOCK:
        session = LIVE_SESSIONS.pop(session_id, None)
    if not session:
        return jsonify({"error": "live session not found"}), 404

    db.execute("UPDATE videos SET status = 'processed' WHERE id = ?", (session["video_id"],))
    return jsonify({
        "message": "live session stopped",
        "video_id": session["video_id"],
        "frames": session["frames"],
        "incidents_found": session["incidents"],
    })


# ---------------- Video upload + processing ----------------

@app.route("/api/upload", methods=["POST"])
@token_required
def upload_video():
    if "video" not in request.files:
        return jsonify({"error": "no video file in request"}), 400
    file = request.files["video"]
    if file.filename == "" or not allowed_file(file.filename):
        return jsonify({"error": "invalid or missing file (allowed: mp4, avi, mov, mkv)"}), 400

    location_name = INCIDENT_LOCATION_NAME
    latitude = INCIDENT_LOCATION_LATITUDE
    longitude = INCIDENT_LOCATION_LONGITUDE

    safe_name = secure_filename(file.filename)
    stored_name = f"{int(datetime.datetime.utcnow().timestamp())}_{safe_name}"
    save_path = os.path.join(UPLOAD_DIR, stored_name)
    file.save(save_path)

    video_id = db.execute(
        """INSERT INTO videos (filename, original_name, location_name, latitude, longitude, uploaded_by, status)
           VALUES (?, ?, ?, ?, ?, ?, 'uploaded')""",
        (stored_name, safe_name, location_name, latitude, longitude, g.user["user_id"]),
    )
    return jsonify({"message": "uploaded", "video_id": video_id}), 201


def _run_detection_job(video_id, video_path, location_name, latitude, longitude):
    location_name = INCIDENT_LOCATION_NAME
    latitude = INCIDENT_LOCATION_LATITUDE
    longitude = INCIDENT_LOCATION_LONGITUDE
    JOBS[video_id] = {"status": "processing", "progress": 0}
    db.execute("UPDATE videos SET status = 'processing' WHERE id = ?", (video_id,))

    def progress_cb(current, total):
        JOBS[video_id]["progress"] = int((current / total) * 100) if total else 0

    try:
        detector = CrashDetector(video_path, video_id)
        incidents = detector.process(progress_callback=progress_cb)

        for inc in incidents:
            incident_id = db.execute(
                """INSERT INTO incidents
                   (video_id, frame_number, timestamp_in_video, severity, confidence,
                    snapshot_path, location_name, latitude, longitude, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')""",
                (video_id, inc["frame_number"], inc["timestamp_in_video"], inc["severity"],
                 inc["confidence"], inc["snapshot_path"], location_name, latitude, longitude),
            )
            db.execute(
                "INSERT INTO alerts (incident_id, channel, recipient, sent_status) VALUES (?, ?, ?, ?)",
                (incident_id, "log", "control-room", "simulated"),
            )

        db.execute("UPDATE videos SET status = 'processed' WHERE id = ?", (video_id,))
        JOBS[video_id] = {"status": "processed", "progress": 100, "incidents_found": len(incidents)}
    except Exception as e:
        db.execute("UPDATE videos SET status = 'failed' WHERE id = ?", (video_id,))
        JOBS[video_id] = {"status": "failed", "progress": 0, "error": str(e)}


@app.route("/api/process/<int:video_id>", methods=["POST"])
@token_required
def process_video(video_id):
    video = db.fetch_one("SELECT * FROM videos WHERE id = ?", (video_id,))
    if not video:
        return jsonify({"error": "video not found"}), 404

    video_path = os.path.join(UPLOAD_DIR, video["filename"])
    thread = threading.Thread(
        target=_run_detection_job,
        args=(video_id, video_path, video["location_name"], video["latitude"], video["longitude"]),
        daemon=True,
    )
    thread.start()
    return jsonify({"message": "processing started", "video_id": video_id})


@app.route("/api/process/<int:video_id>/status", methods=["GET"])
@token_required
def process_status(video_id):
    job = JOBS.get(video_id)
    if not job:
        video = db.fetch_one("SELECT status FROM videos WHERE id = ?", (video_id,))
        if not video:
            return jsonify({"error": "video not found"}), 404
        return jsonify({"status": video["status"], "progress": 100 if video["status"] == "processed" else 0})
    return jsonify(job)


# ---------------- Incidents API ----------------

@app.route("/api/incidents", methods=["GET"])
@token_required
def list_incidents():
    severity = request.args.get("severity")
    status = request.args.get("status")

    query = """SELECT incidents.*, videos.original_name AS video_name
               FROM incidents JOIN videos ON incidents.video_id = videos.id
               WHERE 1=1"""
    params = []
    if severity:
        query += " AND incidents.severity = ?"
        params.append(severity)
    if status:
        query += " AND incidents.status = ?"
        params.append(status)
    query += " ORDER BY incidents.detected_at DESC"

    rows = db.fetch_all(query, tuple(params))
    return jsonify([_with_incident_location(row) for row in rows])


@app.route("/api/incidents/<int:incident_id>", methods=["GET"])
@token_required
def get_incident(incident_id):
    row = db.fetch_one(
        """SELECT incidents.*, videos.original_name AS video_name
           FROM incidents JOIN videos ON incidents.video_id = videos.id
           WHERE incidents.id = ?""",
        (incident_id,),
    )
    if not row:
        return jsonify({"error": "incident not found"}), 404
    return jsonify(_with_incident_location(row))


@app.route("/api/incidents/<int:incident_id>", methods=["DELETE"])
@token_required
def delete_incident(incident_id):
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT snapshot_path FROM incidents WHERE id = ?", (incident_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "incident not found"}), 404

        snapshot_path = row["snapshot_path"]
        conn.execute("DELETE FROM alerts WHERE incident_id = ?", (incident_id,))
        conn.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))
        snapshot_still_used = False
        if snapshot_path:
            snapshot_still_used = conn.execute(
                "SELECT 1 FROM incidents WHERE snapshot_path = ? LIMIT 1", (snapshot_path,)
            ).fetchone() is not None
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Snapshots are generated as plain filenames; keep deletion inside SNAPSHOT_DIR.
    if snapshot_path and not snapshot_still_used and os.path.basename(snapshot_path) == snapshot_path:
        try:
            os.remove(os.path.join(SNAPSHOT_DIR, snapshot_path))
        except FileNotFoundError:
            pass
    return jsonify({"message": "incident deleted"})


@app.route("/api/incidents/<int:incident_id>/status", methods=["PUT"])
@token_required
def update_incident_status(incident_id):
    data = request.get_json(force=True) or {}
    new_status = data.get("status")
    if new_status not in ("new", "acknowledged", "resolved", "false_alarm"):
        return jsonify({"error": "invalid status"}), 400
    db.execute("UPDATE incidents SET status = ? WHERE id = ?", (new_status, incident_id))
    return jsonify({"message": "updated"})


@app.route("/api/stats", methods=["GET"])
@token_required
def stats():
    total = db.fetch_one("SELECT COUNT(*) AS c FROM incidents")["c"]
    by_severity = db.fetch_all("SELECT severity, COUNT(*) AS c FROM incidents GROUP BY severity")
    by_status = db.fetch_all("SELECT status, COUNT(*) AS c FROM incidents GROUP BY status")
    videos_processed = db.fetch_one("SELECT COUNT(*) AS c FROM videos WHERE status = 'processed'")["c"]
    return jsonify({
        "total_incidents": total,
        "by_severity": by_severity,
        "by_status": by_status,
        "videos_processed": videos_processed,
    })


@app.route("/api/videos", methods=["GET"])
@token_required
def list_videos():
    rows = db.fetch_all("SELECT * FROM videos ORDER BY uploaded_at DESC")
    return jsonify(rows)


# ═══════════════════════════════ Training API ═══════════════════════════════

@app.route("/api/training/videos", methods=["GET"])
@token_required
def list_training_videos():
    rows = db.fetch_all("SELECT * FROM training_videos ORDER BY added_at DESC")
    return jsonify(rows)


@app.route("/api/training/upload", methods=["POST"])
@token_required
def upload_training_video():
    """Upload a video for training. Returns the training_video id and duration/fps."""
    if "video" not in request.files:
        return jsonify({"error": "no video file in request"}), 400
    file = request.files["video"]
    if not file.filename or not allowed_file(file.filename):
        return jsonify({"error": "invalid file (allowed: mp4, avi, mov, mkv)"}), 400

    label = request.form.get("label", "unlabeled")  # crash / normal / mixed / unlabeled

    safe_name = secure_filename(file.filename)
    stored_name = f"train_{int(datetime.datetime.utcnow().timestamp())}_{safe_name}"
    save_path = os.path.join(TRAINING_DIR, stored_name)
    file.save(save_path)

    # Read video metadata
    cap = cv2.VideoCapture(save_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    duration = frame_count / fps if fps > 0 else 0
    cap.release()

    tv_id = db.execute(
        """INSERT INTO training_videos
           (filename, original_name, duration, fps, crash_intervals, normal_intervals, label, added_by)
           VALUES (?, ?, ?, ?, '[]', '[]', ?, ?)""",
        (stored_name, safe_name, round(duration, 2), round(fps, 2), label, g.user["user_id"]),
    )

    return jsonify({
        "id": tv_id,
        "filename": stored_name,
        "original_name": safe_name,
        "duration": round(duration, 2),
        "fps": round(fps, 2),
        "label": label,
    }), 201


@app.route("/api/training/videos/<int:tv_id>/label", methods=["PUT"])
@token_required
def label_training_video(tv_id):
    """
    Save crash_intervals and normal_intervals for a training video.
    Body: { "crash_intervals": [[start, end], ...], "normal_intervals": [[start, end], ...], "label": "mixed" }
    """
    data = request.get_json(force=True) or {}
    crash_ivs = data.get("crash_intervals", [])
    normal_ivs = data.get("normal_intervals", [])
    label = data.get("label", "mixed")

    tv = db.fetch_one("SELECT * FROM training_videos WHERE id = ?", (tv_id,))
    if not tv:
        return jsonify({"error": "training video not found"}), 404

    db.execute(
        "UPDATE training_videos SET crash_intervals=?, normal_intervals=?, label=? WHERE id=?",
        (json.dumps(crash_intervals_validated(crash_ivs)),
         json.dumps(crash_intervals_validated(normal_ivs)),
         label, tv_id),
    )
    return jsonify({"message": "labels saved", "id": tv_id})


def crash_intervals_validated(intervals):
    out = []
    for iv in intervals:
        if isinstance(iv, (list, tuple)) and len(iv) == 2:
            s, e = float(iv[0]), float(iv[1])
            if e > s >= 0:
                out.append([round(s, 2), round(e, 2)])
    return out


@app.route("/api/training/videos/<int:tv_id>", methods=["DELETE"])
@token_required
def delete_training_video(tv_id):
    tv = db.fetch_one("SELECT * FROM training_videos WHERE id = ?", (tv_id,))
    if not tv:
        return jsonify({"error": "not found"}), 404
    path = os.path.join(TRAINING_DIR, tv["filename"])
    try:
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass
    db.execute("DELETE FROM training_videos WHERE id = ?", (tv_id,))
    return jsonify({"message": "deleted"})


def _run_training_job(run_id):
    global TRAIN_JOB
    log_lines = []

    def log(msg):
        log_lines.append(msg)
        TRAIN_JOB["log"] = log_lines[:]
        db.execute("UPDATE training_runs SET log=? WHERE id=?", ("\n".join(log_lines), run_id))

    try:
        db.execute("UPDATE training_runs SET status='running' WHERE id=?", (run_id,))
        log("Training started...")

        # Build config from DB
        rows = db.fetch_all("SELECT * FROM training_videos")
        if not rows:
            raise RuntimeError("No training videos found. Upload videos first.")

        videos_cfg = []
        for row in rows:
            crash_ivs = json.loads(row["crash_intervals"] or "[]")
            normal_ivs = json.loads(row["normal_intervals"] or "[]")

            if not crash_ivs and not normal_ivs:
                # Auto-label: if label==crash treat whole video as crash, else normal
                dur = row["duration"]
                if row["label"] == "crash":
                    crash_ivs = [[0, dur]]
                elif row["label"] == "normal":
                    normal_ivs = [[0, dur]]
                else:
                    log(f"  SKIP (no intervals labeled): {row['original_name']}")
                    continue

            videos_cfg.append({
                "path": os.path.join(TRAINING_DIR, row["filename"]),
                "description": row["original_name"],
                "crash_intervals": crash_ivs,
                "normal_intervals": normal_ivs,
            })

        if not videos_cfg:
            raise RuntimeError("No labeled training videos. Please label crash intervals on at least one video.")

        config = {
            "videos": videos_cfg,
            "model": {"C": 4.0, "gamma": "scale", "random_state": 42,
                      "crash_sample_step": 0.25, "normal_sample_step": 0.5},
            "output": os.path.join(BASE_DIR, "models", "accident_temporal_svm.joblib"),
        }
        config_path = os.path.join(BASE_DIR, "models", "training_config_run.json")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        log(f"Config written with {len(videos_cfg)} video(s).")
        for v in videos_cfg:
            log(f"  - {v['description']}: crash={v['crash_intervals']} normal={len(v['normal_intervals'])} segs")

        log("Running train_accident_model.py...")
        python = sys.executable
        result = subprocess.run(
            [python, os.path.join(BASE_DIR, "train_accident_model.py"),
             "--config", config_path,
             "--out", config["output"]],
            capture_output=True, text=True, cwd=BASE_DIR
        )

        if result.stdout:
            for line in result.stdout.strip().splitlines():
                log(line)
        if result.stderr:
            for line in result.stderr.strip().splitlines():
                if "FutureWarning" not in line and "InconsistentVersion" not in line:
                    log("[WARN] " + line)

        if result.returncode != 0:
            raise RuntimeError(f"Training script failed (exit {result.returncode})")

        # Parse summary from results file
        results_path = os.path.join(BASE_DIR, "..", "training_results.json")
        samples_total = samples_pos = samples_neg = 0
        if os.path.isfile(results_path):
            with open(results_path) as f:
                rdata = json.load(f)
            samples_total = rdata.get("training_samples", 0)
            samples_pos = rdata.get("positive_samples", 0)
            samples_neg = rdata.get("negative_samples", 0)

        db.execute(
            """UPDATE training_runs
               SET status='done', samples_total=?, samples_positive=?, samples_negative=?,
                   finished_at=datetime('now'), log=?
               WHERE id=?""",
            (samples_total, samples_pos, samples_neg, "\n".join(log_lines), run_id),
        )
        log(f"Done. Model updated. {samples_pos} crash + {samples_neg} normal samples.")

        # Reload the detection module's model
        try:
            import importlib
            import detection
            importlib.reload(detection)
        except Exception:
            pass

        with TRAIN_LOCK:
            TRAIN_JOB["status"] = "done"
            TRAIN_JOB["log"] = log_lines

    except Exception as exc:
        err = str(exc)
        log(f"ERROR: {err}")
        db.execute(
            "UPDATE training_runs SET status='failed', finished_at=datetime('now'), log=? WHERE id=?",
            ("\n".join(log_lines), run_id),
        )
        with TRAIN_LOCK:
            TRAIN_JOB["status"] = "failed"
            TRAIN_JOB["log"] = log_lines


@app.route("/api/training/start", methods=["POST"])
@token_required
def start_training():
    with TRAIN_LOCK:
        if TRAIN_JOB["status"] == "running":
            return jsonify({"error": "A training job is already running."}), 409
        run_id = db.execute(
            "INSERT INTO training_runs (status) VALUES ('pending')"
        )
        TRAIN_JOB["status"] = "running"
        TRAIN_JOB["run_id"] = run_id
        TRAIN_JOB["log"] = []

    thread = threading.Thread(target=_run_training_job, args=(run_id,), daemon=True)
    thread.start()
    return jsonify({"message": "training started", "run_id": run_id}), 201


@app.route("/api/training/status", methods=["GET"])
@token_required
def training_status():
    with TRAIN_LOCK:
        return jsonify({
            "status": TRAIN_JOB["status"],
            "run_id": TRAIN_JOB["run_id"],
            "log": TRAIN_JOB["log"],
        })


@app.route("/api/training/runs", methods=["GET"])
@token_required
def list_training_runs():
    rows = db.fetch_all("SELECT id, status, samples_total, samples_positive, samples_negative, started_at, finished_at FROM training_runs ORDER BY id DESC LIMIT 20")
    return jsonify(rows)


# ---------------- File serving ----------------

@app.route("/snapshots/<path:filename>")
def serve_snapshot(filename):
    return send_from_directory(SNAPSHOT_DIR, filename)


@app.route("/")
def serve_index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:path>")
def serve_frontend(path):
    full_path = os.path.join(FRONTEND_DIR, path)
    if os.path.isfile(full_path):
        return send_from_directory(FRONTEND_DIR, path)
    return send_from_directory(FRONTEND_DIR, "index.html")


if __name__ == "__main__":
    db.init_db()
    db.seed_default_admin()
    print("=" * 60)
    print(" Automatic Crash Detection System")
    print(" Server running at: http://127.0.0.1:5000")
    print(" On first run, save the generated admin password shown above.")
    print("=" * 60)
    app.run(debug=False, host="0.0.0.0", port=5000)
