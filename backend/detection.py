"""
Crash detection engine.

Uses the trained temporal accident classifier (optical-flow + frame-difference
features over an RBF SVM) for BOTH live camera streams and uploaded footage.

Features:
- Dynamic time-normalized sampling: frame rate invariant (works smoothly at 8, 10, 15, 25, 30+ FPS).
- Screen / Phone ROI auto-detection: isolates phone screen / video display when pointing webcam at a screen.
- Calibrated thresholds with multi-frame confirmation to prevent false alarms while reliably catching accidents.
- Cooldown suppression so a single accident event doesn't trigger dozens of duplicate alerts.
"""

import math, os, time
from collections import deque
import cv2, numpy as np
import joblib
from temporal_features import TemporalFeatureExtractor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_DIR = os.path.join(BASE_DIR, "snapshots")
MODEL_PATH = os.environ.get("ACCIDENT_MODEL", os.path.join(BASE_DIR, "models", "accident_temporal_svm.joblib"))
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

LIVE_THRESHOLD = float(os.environ.get("LIVE_ACCIDENT_THRESHOLD", "0.65"))
LIVE_STRONG_THRESHOLD = float(os.environ.get("LIVE_STRONG_THRESHOLD", "0.80"))
LIVE_SCORE_WINDOW = int(os.environ.get("LIVE_SCORE_WINDOW", "4"))
LIVE_PREDICT_EVERY = int(os.environ.get("LIVE_PREDICT_EVERY", "2"))
LIVE_WARMUP_FRAMES = int(os.environ.get("LIVE_WARMUP_FRAMES", "10"))
LIVE_IGNORE_LARGE_FACE = os.environ.get("LIVE_IGNORE_LARGE_FACE", "1").lower() not in {"0", "false", "no"}
COOLDOWN_SECONDS = float(os.environ.get("CRASH_COOLDOWN_SECONDS", "7.0"))
_FACE_CASCADES = []
if hasattr(cv2, "data"):
    for _face_model in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
        _cascade = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, _face_model))
        if not _cascade.empty():
            _FACE_CASCADES.append(_cascade)


def detect_screen_roi(frame):
    """
    Detects if the camera frame contains an active glowing phone screen or monitor.
    If found, returns (cropped_roi, True, (x, y, w, h)).
    Otherwise returns (frame, False, None).
    """
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (7, 7), 0)
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best_box = None
    max_area = 0
    total_area = w * h
    for c in contours:
        area = cv2.contourArea(c)
        if 0.12 * total_area <= area <= 0.88 * total_area:
            bx, by, bw, bh = cv2.boundingRect(c)
            aspect = bw / float(bh)
            if 0.4 <= aspect <= 2.5 and area > max_area:
                max_area = area
                best_box = (bx, by, bw, bh)

    if best_box is not None:
        bx, by, bw, bh = best_box
        pad_x, pad_y = int(bw * 0.03), int(bh * 0.03)
        x1 = max(0, bx + pad_x)
        y1 = max(0, by + pad_y)
        x2 = min(w, bx + bw - pad_x)
        y2 = min(h, by + bh - pad_y)
        if (x2 - x1) > 50 and (y2 - y1) > 50:
            return frame[y1:y2, x1:x2], True, (x1, y1, x2 - x1, y2 - y1)

    return frame, False, None


def detect_face(frame):
    """Detect a visible face in a live frame, including either profile direction."""
    if not _FACE_CASCADES:
        return False
    h, w = frame.shape[:2]
    scale = min(1.0, 320.0 / max(w, h))
    sample = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale)))) if scale < 1.0 else frame
    gray = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
    mirrored = cv2.flip(gray, 1)
    sample_area = sample.shape[0] * sample.shape[1]
    for cascade in _FACE_CASCADES:
        for view in (gray, mirrored):
            faces = cascade.detectMultiScale(view, scaleFactor=1.1, minNeighbors=4, minSize=(24, 24))
            if any((fw * fh) / sample_area >= 0.015 for _, _, fw, fh in faces):
                return True
    return False


class CrashDetector:
    def __init__(self, video_path=None, video_id=0, live_mode=False):
        self.video_path = video_path
        self.video_id = video_id
        self.live_mode = live_mode
        self.model = None
        self._load_model()
        self.reset()

    def _load_model(self):
        if not os.path.isfile(MODEL_PATH):
            raise RuntimeError(f"Trained accident model not found: {MODEL_PATH}")
        try:
            self.model = joblib.load(MODEL_PATH)
        except Exception as exc:
            raise RuntimeError(f"Could not load trained accident model: {exc}") from exc

    def reset(self):
        self.frame_number = 0
        self.last_crash_time = -999.0
        self.frame_buffer = deque(maxlen=70) # pairs of (timestamp_seconds, gray_frame)
        self.score_history = deque(maxlen=LIVE_SCORE_WINDOW)
        self.face_history = deque(maxlen=LIVE_SCORE_WINDOW)
        self.last_debug = {
            "accident_score": 0.0,
            "accident_mean": 0.0,
            "threshold": LIVE_THRESHOLD,
            "phone_roi_detected": False,
            "face_detected": False,
            "face_guard_active": False,
            "model": "temporal-accident-svm",
        }
        self.live_last_debug = self.last_debug
        self.best_score = 0.0

    def _save_snapshot(self, frame, frame_number, score, roi_box=None):
        annotated = frame.copy()
        if roi_box is not None:
            rx, ry, rw, rh = roi_box
            cv2.rectangle(annotated, (rx, ry), (rx + rw, ry + rh), (0, 0, 255), 3)
            cv2.putText(annotated, "ACCIDENT ROI", (rx + 5, max(24, ry - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)
        cv2.rectangle(annotated, (2, 2), (annotated.shape[1] - 3, annotated.shape[0] - 3), (0, 0, 255), 4)
        cv2.putText(annotated, "CRASH DETECTED", (18, 38), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
        cv2.putText(annotated, f"Accident score: {score:.0%}", (18, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        fn = f"video{self.video_id}_frame{frame_number}_{int(time.time())}.jpg"
        path = os.path.join(SNAPSHOT_DIR, fn)
        cv2.imwrite(path, annotated)
        return fn

    def process_frame(self, frame, fps=25, timestamp=None):
        self.frame_number += 1
        fps_val = float(fps or 25)
        if timestamp is not None:
            t = float(timestamp)
        elif self.live_mode:
            t = self.frame_number / max(1.0, fps_val)
        else:
            t = self.frame_number / max(1.0, fps_val)

        # Detect phone/screen ROI if in live camera mode
        roi_detected = False
        roi_box = None
        face_detected = self.live_mode and detect_face(frame)
        eval_frame = frame
        if self.live_mode:
            cropped_roi, roi_detected, roi_box = detect_screen_roi(frame)
            if roi_detected:
                eval_frame = cropped_roi

        gray = TemporalFeatureExtractor.preprocess_gray(eval_frame)
        self.frame_buffer.append((t, gray))

        # Evict frames older than t - 2.5 seconds
        while self.frame_buffer and (self.frame_buffer[-1][0] - self.frame_buffer[0][0]) > 2.5:
            self.frame_buffer.popleft()

        # Need at least 0.90s of temporal context
        span = self.frame_buffer[-1][0] - self.frame_buffer[0][0]
        if span < 0.90 or (self.frame_number % LIVE_PREDICT_EVERY != 0):
            return None

        # Sample 10 frames with target step ~0.12s (identical to model training)
        target_times = [t - (9 - k) * 0.12 for k in range(10)]
        grays = []
        for tt in target_times:
            closest = min(self.frame_buffer, key=lambda item: abs(item[0] - tt))
            grays.append(closest[1])

        feat = TemporalFeatureExtractor.extract_from_grays(grays).reshape(1, -1)
        score = float(self.model.predict_proba(feat)[0, 1])
        self.score_history.append(score)
        self.face_history.append(face_detected)
        self.best_score = max(self.best_score, score)

        smoothed = float(np.mean(self.score_history))
        strong_hits = sum(s >= LIVE_STRONG_THRESHOLD for s in self.score_history)
        positive_hits = sum(s >= LIVE_THRESHOLD for s in self.score_history)

        self.last_debug = {
            "accident_score": round(score, 3),
            "accident_mean": round(smoothed, 3),
            "positive_hits": positive_hits,
            "strong_hits": strong_hits,
            "phone_roi_detected": roi_detected,
            "face_detected": face_detected,
            "face_guard_active": any(self.face_history),
            "threshold": LIVE_THRESHOLD,
            "buffer_span_sec": round(span, 2),
            "model": "temporal-accident-svm",
        }
        self.live_last_debug = self.last_debug

        # Warmup protection
        if self.frame_number <= LIVE_WARMUP_FRAMES:
            return None

        # Require repeated evidence so a single noisy prediction cannot trigger an alert.
        is_accident = (
            (strong_hits >= 2 and smoothed >= LIVE_THRESHOLD)
            or (positive_hits >= 3 and smoothed >= LIVE_THRESHOLD)
        )
        # Suppress when a face appeared in any recent scoring frame; this keeps one
        # missed Haar detection from allowing the same head motion to trigger an alert.
        if self.live_mode and LIVE_IGNORE_LARGE_FACE and any(self.face_history):
            is_accident = False

        if is_accident and (t - self.last_crash_time >= COOLDOWN_SECONDS):
            self.last_crash_time = t
            final_score = float(max(score, smoothed))
            severity = "High" if final_score >= 0.80 else "Medium"
            snapshot = self._save_snapshot(frame, self.frame_number, final_score, roi_box)
            self.score_history.clear()
            return {
                "frame_number": self.frame_number,
                "timestamp_in_video": round(t, 2),
                "severity": severity,
                "confidence": round(final_score, 2),
                "snapshot_path": snapshot,
                "debug": self.last_debug,
            }

        return None

    def process(self, progress_callback=None):
        """Processes an entire video file from start to finish."""
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Could not open video: {self.video_path}")
        self.reset()
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        incidents = []

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = self.frame_number / float(fps)
            inc = self.process_frame(frame, fps=fps, timestamp=t)
            if inc:
                incidents.append(inc)
            if progress_callback and total:
                progress_callback(self.frame_number, total)

        cap.release()
        return incidents
