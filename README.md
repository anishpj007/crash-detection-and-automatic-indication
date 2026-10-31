# Automatic Crash Detection System

A full-stack web application that detects road accidents from CCTV /
dashcam video footage using computer vision, logs incidents to a
database, raises alerts, and presents everything on a live dashboard
with an incident map.

Built for a final-year MCA project. The live-camera path uses a
pretrained trained temporal accident detector + ByteTrack so that a phone screen being
filmed by the laptop webcam is treated as a video display, not as random
foreground blobs.

---

## 1. Architecture

```
┌─────────────────┐        REST/JSON (JWT auth)        ┌───────────────────┐
│   Frontend       │ ──────────────────────────────────▶│  Flask Backend     │
│  (HTML/CSS/JS)   │◀────────────────────────────────── │  (backend/app.py)  │
│  dashboard.html  │                                     └─────────┬──────────┘
└─────────────────┘                                               │
                                                                    │ calls
                                                          ┌─────────▼──────────┐
                                                          │  detection.py       │
                                                          │  (YOLO + tracking)  │
                                                          └─────────┬──────────┘
                                                                    │ writes
                                                          ┌─────────▼──────────┐
                                                          │  SQLite database    │
                                                          │ crash_detection.db  │
                                                          └────────────────────┘
```

**Detection pipeline (`backend/detection.py`):**

### Live camera
1. **YOLO26n vehicle detection** — detects `car`, `motorcycle`, `bus`, and `truck` rather than generic motion blobs.
2. **ByteTrack** — keeps a persistent ID for each detected vehicle across consecutive frames.
3. **Trajectory analysis** — stores recent position/speed history for each vehicle.
4. **Approach evidence** — two vehicles must be persistent and show sustained closing motion.
5. **Contact evidence** — the tracked vehicles must overlap or come within a geometry-based collision gap.
6. **Post-contact confirmation** — the system waits for a speed drop, direction change, or post-contact separation before creating an incident. A single moving blob is not enough.
7. **Snapshot + severity scoring** — saves the collision frame with the two vehicle boxes and confidence.

### Uploaded videos
The existing lightweight OpenCV heuristic remains available for backwards compatibility with the included synthetic demo clip.

## 2. Tech stack

| Layer          | Technology                                   |
|-----------------|-----------------------------------------------|
| Detection       | Python, OpenCV, NumPy, Ultralytics YOLO      |
| Backend / API   | Flask, PyJWT (auth), SQLite (`sqlite3`)       |
| Frontend        | HTML, CSS, vanilla JavaScript, Leaflet.js map |
| Database        | SQLite (`crash_detection.db`, auto-created)   |

No React/npm build step. The first live-camera start downloads the
pretrained YOLO26n weights if they are not already present on the machine.
Ultralytics documents this automatic model download behavior.

---

## 3. Project structure

```
crash-detection-system/
├── backend/
│   ├── app.py                  # Flask app: routes, auth, job orchestration
│   ├── detection.py            # YOLO vehicle tracking + crash-detection engine
│   ├── database.py             # SQLite data-access layer
│   ├── schema.sql              # Database schema
│   ├── generate_demo_video.py  # Makes a synthetic test clip with a staged crash
│   ├── requirements.txt
│   ├── uploads/                # Uploaded videos land here
│   └── snapshots/              # Annotated crash-frame snapshots land here
├── frontend/
│   ├── index.html              # Login / register page
│   ├── dashboard.html          # Main dashboard (overview, upload, live camera, incidents, map)
│   ├── css/style.css
│   └── js/
│       ├── api.js              # REST API client
│       └── dashboard.js        # Dashboard UI logic
└── README.md
```

---

## 4. Setup & running

```bash
cd crash-detection-system/backend
pip install -r requirements.txt
python app.py
```

Then open **http://127.0.0.1:5000** in your browser.

On first run, the server creates the `admin` account and prints a unique
initial password in the server console. Save it when it appears. Later runs
reuse the account stored in the local database.

### Try it immediately with a synthetic demo clip

No dataset handy yet? Generate one:

```bash
python generate_demo_video.py
```

This writes `demo_crash.mp4` (two vehicles that collide head-on around
the 4-second mark). Upload it from the **Upload & Analyze** tab and
click through to **Incidents** to see the detection, snapshot,
severity, and confidence score.


### Live accident detection through the laptop camera

The dashboard includes a **Live Camera** view for the exact demo setup you described: play an accident clip on your phone, place the phone in front of the laptop webcam, and click **Start Live Detection**.

The browser captures the webcam at about 10 FPS and sends 1280-pixel-wide JPEG frames to Flask. The backend runs YOLO vehicle detection + ByteTrack on each consecutive frame, then checks vehicle trajectories before and after contact. This avoids the previous false-positive problem where background subtraction interpreted the phone screen/person/room as a generic moving blob.

The first time you start Live Camera, YOLO26n weights may be downloaded automatically. Make sure the computer has internet access for that first run; later runs can use the cached model. You can also point `YOLO_MODEL` at a local `.pt` file.

### SMS alerts for live detection

Live camera, screen-share, and live-file incidents can submit an SMS through Twilio. SMS is off by default. To enable it:

1. Copy `backend/.env.example` to `backend/.env`.
2. Set `SMS_ENABLED=true`, keep `SMS_PROVIDER=twilio`, and set `TWILIO_TRIAL_MODE=true` for a free trial account. Fill in the Twilio Account SID, Auth Token, sender number, and destination phone number. Use E.164 format (for example, `+15551234567`).
3. Restart the Flask app. The Live Accident Detection panel shows whether SMS is configured and masks the destination number.

Keep `backend/.env` private; do not put Twilio credentials in frontend files or commit the file. On a trial account, the app sends Twilio's predefined `sms_internal_alerts` template, so the text is generic and does not contain incident details. Set `TWILIO_TRIAL_MODE=false` only after upgrading if you want the app's custom crash details in each SMS. The app submits SMS asynchronously after it records a live incident, so detection does not wait on the SMS provider. A `queued` status means Twilio accepted the API request, not that the recipient handset confirmed delivery. Uploaded-video analysis does not send SMS, which avoids texting old test incidents.

Twilio trial accounts require the destination number to be verified first. SMS delivery rules depend on the destination country. For India, Twilio documents separate international and domestic routes; domestic delivery requires DLT registration and an approved Sender ID. Check Twilio's current [Messages API](https://www.twilio.com/docs/messaging/api/message-resource) and [India SMS guidelines](https://www.twilio.com/en-us/guidelines/in/sms) before enabling delivery.

### Preparing an insurance claim summary

Open an incident from the Incidents view and choose **Prepare Insurance Claim**. The form gathers claimant, policy, vehicle, and accident details, then creates a printable summary with the app's incident snapshot and detection metadata. Use the browser's Print command to save it as PDF. The report is only a draft and is not sent to an insurer; confirm the event and use the insurer's own current form and document checklist. Claim requirements vary by insurer and loss type. See [IRDAI's motor insurance guidance](https://irdai.gov.in/web/policy-holder/motor-insurance).

#### Optional Android phone SMS gateway

Twilio remains the default provider. To try the open-source [SMS Gateway for Android](https://github.com/capcom6/android-sms-gateway), install it on an Android phone, connect the phone and Flask computer to the same Wi-Fi, enable its **Local Server**, and copy the local `/message` URL plus the displayed basic-auth username/password into `ANDROID_SMS_GATEWAY_URL`, `ANDROID_SMS_GATEWAY_USERNAME`, and `ANDROID_SMS_GATEWAY_PASSWORD` in `backend/.env`. Change `SMS_PROVIDER=android_gateway` and restart Flask to route alerts through the phone's SIM. Set `SMS_PROVIDER=twilio` to switch back. The phone must stay powered on with the app running; carrier SMS-plan charges or limits still apply.

For the demo, start detection a couple of seconds before the collision. Keep the phone reasonably steady and large in the webcam view. The accident video should contain visible cars, motorcycles, buses, or trucks for the pretrained COCO detector to recognize them.

### Using real footage

Any short MP4/AVI/MOV/MKV clip of a fixed-camera traffic scene works.
For best results with the live YOLO pipeline: keep the phone reasonably
steady, make the accident footage large enough for the camera to resolve
vehicles, and avoid severe glare/flicker on the phone screen. For proper accident
datasets to test against and cite in your report, look at:

- **CADP** — Car Accident Detection and Prediction dataset
- **DAD** — Dashcam Accident Dataset (Chan et al.)
- Kaggle "Accident Detection From CCTV Footage"

---

## 5. Extending this for your report / viva

This project is scoped to run anywhere with zero setup friction, but
it's built so each stage can be swapped for a deeper technique --
useful material for your "Future Scope" chapter:

| Current (this build)                  | Upgrade path                                   |
|----------------------------------------|-------------------------------------------------|
| YOLO26n vehicle detector (live)        | Fine-tuned vehicle/accident detector on project data |
| ByteTrack vehicle tracker              | BoT-SORT / DeepSORT with stronger re-ID          |
| Rule-based overlap + deceleration      | LSTM / CNN-LSTM classifier over trajectory features, trained on CADP/DAD |
| Alerting       | Twilio SMS for live detections; local log for uploaded-video analysis | Delivery callbacks / additional alert providers |
| SQLite                                 | MySQL/PostgreSQL for multi-camera deployments   |
| Single-process video analysis          | Message queue (Celery/Redis) for multiple camera streams processed in parallel |

The database schema and API already separate "detection" from
"alerting" and "storage," so none of these swaps require touching the
frontend.

---

## 6. API reference (for your documentation appendix)

| Method | Endpoint                          | Auth | Purpose                        |
|--------|-------------------------------------|------|---------------------------------|
| POST   | `/api/register`                    | No   | Create an account               |
| POST   | `/api/login`                       | No   | Get a JWT token                 |
| POST   | `/api/upload`                      | Yes  | Upload a video file             |
| POST   | `/api/process/<video_id>`          | Yes  | Start crash detection on a video (background thread) |
| GET    | `/api/process/<video_id>/status`   | Yes  | Poll processing progress        |
| GET    | `/api/incidents`                   | Yes  | List incidents (`?severity=`, `?status=` filters) |
| GET    | `/api/incidents/<id>`              | Yes  | Incident detail                 |
| PUT    | `/api/incidents/<id>/status`       | Yes  | Update incident status          |
| GET    | `/api/stats`                       | Yes  | Dashboard summary stats         |
| GET    | `/api/videos`                      | Yes  | List uploaded videos            |
| POST   | `/api/live/start`                  | Yes  | Start a live-camera detection session |
| POST   | `/api/live/frame`                  | Yes  | Process one webcam JPEG frame       |
| POST   | `/api/live/stop`                   | Yes  | Stop a live-camera session           |

All authenticated routes expect `Authorization: Bearer <token>`.

---

## 7. Known limitations (worth stating honestly in your report)

- The live path is more selective than the previous blob-based version, but it can still miss an accident if the vehicles are tiny on the phone screen, heavily occluded, or the collision lasts only a few frames.
- The pretrained detector is general-purpose rather than accident-specific. It knows vehicle categories; the collision decision is still a rule-based trajectory model. A project-specific accident classifier can improve recall further.
- Live CPU speed depends on the laptop. If YOLO processing is slower than the browser frame rate, the server will naturally process fewer frames because requests are serialized.
- The included synthetic `demo_crash.mp4` was designed for the original classical-CV pipeline and is not a good benchmark for the pretrained vehicle detector. Test the live mode with real dashcam footage that visibly contains vehicles.
- The Flask development server is for development only; use Gunicorn/uWSGI behind Nginx for production.

## Live accident model

The Live Camera path now uses the MIT-licensed `Enos-123/trained-temporal-accident-svm` checkpoint rather than inferring crashes only from vehicle geometry. The published model is fine-tuned for `accident` and `vehicle` classes and reports 82.6% mAP@0.5 and 85.5% recall for the accident class on its own evaluation set. The first Live Camera start downloads the 114 MB `weights/epoch61.pt` file from Hugging Face.

The live path also tries to isolate the phone screen automatically. The dashboard shows the current accident score, vehicle count, and whether a phone ROI was detected.


## Live-start troubleshooting

The live accident model uses two classes: `accident` and `vehicle`. The live route now returns a clear HTTP 503 message when the model/runtime is unavailable instead of a generic HTTP 500.

On Windows, install the dependencies from `backend\requirements.txt`, keep internet access enabled for the first model download, and start with `python app.py`.
