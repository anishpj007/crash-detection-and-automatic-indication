"""
Enhanced training script for the temporal accident classifier.

Supports a YAML/JSON annotation file that maps any number of videos to labeled
time intervals. Place videos anywhere and list them in training_config.json.

Usage
-----
# 1. Edit training_config.json to add your videos and crash intervals
# 2. Run from the backend folder:
   python train_accident_model.py
   python train_accident_model.py --config ../training_config.json --out models/accident_temporal_svm.joblib

What counts as a good crash interval?
  - The crash impact moment ± 1-2 seconds before/after visible collision
  - Include slowdowns, swerves, and post-impact debris if visible

What counts as good normal footage?
  - Regular city driving, highway footage, dashcam without incident
  - Different weather, lighting, camera angles → more robust model
"""

import os, json, time, argparse, sys
from pathlib import Path

import cv2, joblib, numpy as np
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import StratifiedKFold, cross_validate
from sklearn.metrics import classification_report, confusion_matrix

# ─────────────────────────────────── Feature settings ────────────────────────
SIZE = (160, 96)
CROP = 0.90
WINDOW_FRAMES = 10
FRAME_STEP = 3


def preprocess_gray(frame):
    h, w = frame.shape[:2]
    nw, nh = int(w * CROP), int(h * CROP)
    x, y = (w - nw) // 2, (h - nh) // 2
    frame = frame[y:y+nh, x:x+nw]
    frame = cv2.resize(frame, SIZE)
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    g = cv2.GaussianBlur(g, (5, 5), 0)
    return g.astype(np.float32) / 255.0


def feature_for_window(cap, start_frame):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(start_frame))
    grays = []
    for _ in range(WINDOW_FRAMES):
        ok, fr = cap.read()
        if not ok:
            return None
        grays.append(preprocess_gray(fr))
        for _ in range(FRAME_STEP - 1):
            ok, _ = cap.read()
            if not ok:
                return None
    diffs = []; flow_stats = []; hist_acc = np.zeros(8, np.float32)
    edge = []; br = []; sharp = []
    for g in grays:
        br.append((g.mean(), g.std()))
        edge.append(cv2.Canny((g * 255).astype(np.uint8), 80, 160).mean() / 255.0)
        sharp.append(cv2.Laplacian((g * 255).astype(np.uint8), cv2.CV_64F).var() / 10000)
    for i in range(len(grays) - 1):
        d = np.abs(grays[i+1] - grays[i])
        diffs += [d.mean(), d.std(), np.percentile(d, 90), np.percentile(d, 99)]
        flow = cv2.calcOpticalFlowFarneback(grays[i], grays[i+1], None, 0.5, 3, 15, 3, 5, 1.2, 0)
        mag, ang = cv2.cartToPolar(flow[..., 0], flow[..., 1], angleInDegrees=False)
        flow_stats += [mag.mean(), mag.std(), np.percentile(mag, 90), np.percentile(mag, 99)]
        hist, _ = np.histogram(ang, bins=8, range=(0, 2 * np.pi), weights=mag)
        s = hist.sum()
        if s > 1e-6: hist = hist / s
        hist_acc += hist
    hist_acc /= max(1, len(grays) - 1)
    return np.array(diffs + flow_stats + hist_acc.tolist() + [
        np.mean([x[0] for x in br]), np.mean([x[1] for x in br]),
        np.std([x[0] for x in br]), np.mean(edge), np.std(edge), np.max(edge),
        np.mean(sharp), np.max(sharp)
    ], np.float32)


def collect_from_video(video_path, crash_intervals, normal_intervals,
                       crash_step=0.25, normal_step=0.5, verbose=True):
    """
    Extract labeled feature windows from one video file.

    crash_intervals:  list of [start_sec, end_sec] → label=1
    normal_intervals: list of [start_sec, end_sec] → label=0
    """
    if not os.path.isfile(video_path):
        print(f"  [WARN] Video not found, skipping: {video_path}")
        return [], []

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps
    X, y = [], []

    if verbose:
        print(f"  Loading: {os.path.basename(video_path)}  ({duration:.1f}s @ {fps:.0f}fps)")

    # Positive (crash) windows
    for start, end in crash_intervals:
        start = max(0, start)
        end = min(end, duration - 1.0)
        times = np.arange(start, end, crash_step)
        for t in times:
            feat = feature_for_window(cap, int(t * fps))
            if feat is not None:
                X.append(feat)
                y.append(1)
        if verbose:
            print(f"    + crash [{start:.1f}s – {end:.1f}s] → {len(times)} windows")

    # Negative (normal) windows
    for start, end in normal_intervals:
        start = max(0, start)
        end = min(end, duration - 1.0)
        times = np.arange(start, end, normal_step)
        for t in times:
            feat = feature_for_window(cap, int(t * fps))
            if feat is not None:
                X.append(feat)
                y.append(0)
        if verbose:
            print(f"    - normal [{start:.1f}s – {end:.1f}s] → {len(times)} windows")

    cap.release()
    return X, y


def build_default_config():
    """Returns the default config that mirrors the original training setup."""
    return {
        "videos": [
            {
                "path": "../training_videos/vidssave.com Car to Car Crash Test - Nissan Tsuru Vs Nissan Versa 1080P.mp4",
                "description": "Car-to-car frontal crash test (Nissan Tsuru vs Versa)",
                "crash_intervals": [[7.5, 14.6]],
                "normal_intervals": [[0.0, 6.0], [16.0, 33.0]]
            },
            {
                "path": "../training_videos/vidssave.com Road traffic video for object recognition 144p.mp4",
                "description": "Normal road traffic (no crash)",
                "crash_intervals": [],
                "normal_intervals": [[0.0, 2046.0]]
            },
            {
                "path": "../training_videos/videoplayback.mp4",
                "description": "Normal driving footage (no crash)",
                "crash_intervals": [],
                "normal_intervals": [[0.0, 52.0]]
            }
        ],
        "model": {
            "C": 4.0,
            "gamma": "scale",
            "random_state": 42,
            "crash_sample_step": 0.25,
            "normal_sample_step": 0.6
        },
        "output": "models/accident_temporal_svm.joblib",
        "notes": [
            "Add more crash videos by appending entries to the 'videos' list.",
            "Set crash_intervals to [] for pure normal-traffic videos.",
            "Set normal_intervals to [] for crash-only clips with no clean segment.",
            "crash_sample_step: seconds between each positive training window (smaller = more samples).",
            "normal_sample_step: seconds between each negative training window."
        ]
    }


def load_config(config_path):
    if config_path and os.path.isfile(config_path):
        with open(config_path) as f:
            cfg = json.load(f)
        print(f"Loaded config: {config_path}")
        return cfg
    else:
        print("No config file found — using built-in default configuration.")
        return build_default_config()


def main():
    ap = argparse.ArgumentParser(description="Train temporal crash detection SVM")
    ap.add_argument("--config", default="../training_config.json",
                    help="Path to training_config.json. Will use defaults if missing.")
    ap.add_argument("--out", default=None,
                    help="Override output model path.")
    ap.add_argument("--cv", action="store_true",
                    help="Run 5-fold cross-validation before saving (slower).")
    ap.add_argument("--verbose", action="store_true", default=True)
    args = ap.parse_args()

    cfg = load_config(args.config)
    out_path = args.out or cfg.get("output", "models/accident_temporal_svm.joblib")
    model_cfg = cfg.get("model", {})

    X_all, y_all = [], []
    crash_step = model_cfg.get("crash_sample_step", 0.25)
    normal_step = model_cfg.get("normal_sample_step", 0.5)

    print(f"\n{'='*60}")
    print(f"  Training dataset")
    print(f"{'='*60}")

    for entry in cfg["videos"]:
        path = entry["path"]
        crash_ivs = entry.get("crash_intervals", [])
        normal_ivs = entry.get("normal_intervals", [])
        entry_crash_step = float(entry.get("crash_sample_step", crash_step))
        entry_normal_step = float(entry.get("normal_sample_step", normal_step))
        X, y = collect_from_video(
            path, crash_ivs, normal_ivs, entry_crash_step, entry_normal_step, args.verbose
        )
        X_all += X
        y_all += y

    if not X_all:
        print("\nERROR: No training samples collected. Check video paths in training_config.json.")
        sys.exit(1)

    X = np.vstack(X_all)
    y = np.asarray(y_all, dtype=np.int64)
    pos = int(y.sum())
    neg = int((y == 0).sum())

    print(f"\n{'='*60}")
    print(f"  Dataset summary")
    print(f"{'='*60}")
    print(f"  Total windows : {len(y)}")
    print(f"  Crash (pos)   : {pos}  ({100*pos/len(y):.1f}%)")
    print(f"  Normal (neg)  : {neg}  ({100*neg/len(y):.1f}%)")

    if pos < 10:
        print("\n[WARN] Very few positive crash samples (<10). Add more crash videos or tighten crash intervals.")
    if neg < pos * 3:
        print("[WARN] Normal samples are fewer than 3× crash samples. Consider adding more normal footage.")

    # ─── Train ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  Training model  C={model_cfg.get('C', 4.0)}  gamma={model_cfg.get('gamma', 'scale')}")
    print(f"{'='*60}")

    base_svm = SVC(
        C=float(model_cfg.get("C", 4.0)),
        kernel="rbf",
        class_weight="balanced",
        gamma=model_cfg.get("gamma", "scale"),
        random_state=model_cfg.get("random_state", 42),
    )
    calibrated = CalibratedClassifierCV(base_svm, cv=5, method="isotonic")
    model = Pipeline([
        ("scaler", StandardScaler()),
        ("svc", calibrated),
    ])

    if args.cv:
        print("  Running 5-fold cross-validation (this takes a moment)...")
        cv_result = cross_validate(
            Pipeline([
                ("scaler", StandardScaler()),
                ("svc", SVC(C=float(model_cfg.get("C", 4.0)), kernel="rbf",
                            class_weight="balanced", gamma=model_cfg.get("gamma", "scale"),
                            probability=True, random_state=42))
            ]),
            X, y,
            cv=StratifiedKFold(n_splits=5, shuffle=True, random_state=42),
            scoring=["accuracy", "balanced_accuracy", "f1", "precision", "recall"],
            n_jobs=-1,
        )
        print(f"  Accuracy         : {cv_result['test_accuracy'].mean():.3f} ± {cv_result['test_accuracy'].std():.3f}")
        print(f"  Balanced Acc     : {cv_result['test_balanced_accuracy'].mean():.3f} ± {cv_result['test_balanced_accuracy'].std():.3f}")
        print(f"  F1               : {cv_result['test_f1'].mean():.3f} ± {cv_result['test_f1'].std():.3f}")
        print(f"  Precision        : {cv_result['test_precision'].mean():.3f} ± {cv_result['test_precision'].std():.3f}")
        print(f"  Recall           : {cv_result['test_recall'].mean():.3f} ± {cv_result['test_recall'].std():.3f}")

    t0 = time.time()
    model.fit(X, y)
    print(f"  Training done in {time.time()-t0:.1f}s")

    # Quick self-evaluation
    y_pred = model.predict(X)
    print(f"\n  Train-set report (for sanity, not validation):\n")
    print(classification_report(y, y_pred, target_names=["Normal", "Crash"], digits=3))

    # ─── Save ────────────────────────────────────────────────────────────────
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, out, compress=3)
    print(f"  Model saved: {out}")

    # Save updated training report
    report = {
        "model_type": "temporal motion classifier (Calibrated RBF SVM)",
        "feature_dimensions": X.shape[1],
        "training_samples": len(y),
        "positive_samples": pos,
        "negative_samples": neg,
        "videos_used": [
            {
                "path": e["path"],
                "description": e.get("description", ""),
                "crash_intervals": e.get("crash_intervals", []),
                "normal_intervals": e.get("normal_intervals", []),
            }
            for e in cfg["videos"]
        ],
    }
    report_path = out.parent.parent / "training_results.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"  Report saved : {report_path}\n")


if __name__ == "__main__":
    main()
