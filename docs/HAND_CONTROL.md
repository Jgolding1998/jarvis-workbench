# Point with your hand

Hand control is optional and experimental. `watch me` starts the local hand camera; `active` starts the separate presence guard. The two modes share one camera and cannot run together. Starting one never silently stops or disarms the other.

## Set up once

Use Windows and Python 3.11 or newer. Double-click **Install-Optional-Tools.cmd**, choose **2. Camera + hand gestures** or **4. All optional tools**, and confirm the downloads. It creates or reuses this project's `.venv`, installs the reviewed packages, and downloads the verified hand model. No camera or pointer starts during installation.

This uses MediaPipe 1.2.0 and contrib OpenCV 4.13.0.92. Do not also install the separate `opencv-python` distribution in that environment; both supply `cv2`.

The installer saves the [official hand model](https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task) as `models/hand_landmarker.task`. Its SHA-256 must be:

```text
fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1
```

The installer and adapter both check that hash. Read the model publisher's terms. Model files stay outside Git. For manual installation, use `.\.venv\Scripts\python.exe -m pip install -r requirements-hand.txt` and download the model yourself.

## Try observation first

```powershell
.\.venv\Scripts\python.exe -m jarvis.server --allow-hand-control --hand-model models/hand_landmarker.task
```

Open the printed local address, leave real pointer movement off, and type `watch me` or `watchme`. The camera processes hands locally. Frames and landmarks stay in memory. There is no cloud connection, face identification, or recording.

Show one open hand before trying a pinch. Your index fingertip points. Touch your middle fingertip to your thumb for a left click, or your ring fingertip to your thumb for a right click. Holding a pinch does not repeat clicks.

To move the actual pointer, stop the hand session, enable its separate dashboard pointer permission, then start again. The launch flag alone never enables movement. This edition controls the primary display and reserves its corners as a manual escape area.

Optional offline voice recognizes `watch me` only after its separate dashboard arming. Voice cannot arm movement or disarm the presence guard.

## Stop reliably

- Bring both hands into view separated, then bring their wrists together. This visual clap stops hand control. It does not listen for an audio clap.
- Press Esc anywhere, use the dashboard hand stop, type `stop mouse`, or move your physical mouse into a display corner.
- A tracking, camera, or pointer error stops control and attempts to release mouse buttons.

A stop keeps the camera reserved while its device closes. If a driver cannot close, restart Jarvis before using another camera mode. An armed presence policy keeps its own permissions when hand control stops.

## What was verified

Automated tests use fake landmarks, frames, pointer actions, and Windows APIs. They exercise left/right pinch routing, smoothing, bounds, click cooldown, visual clap, concurrent stop, camera ownership, failure cleanup, and independent Esc/corner stops. The real adapter loaded the verified model with the tested MediaPipe/OpenCV versions. It processed blank frames and an [official public MediaPipe test image](https://storage.googleapis.com/mediapipe-assets/right_hands.jpg), detecting two hands with 21 landmarks each and passing those landmarks to a fake pointer.

Physical hand tracking accuracy, camera behavior, actual cursor movement, and Windows input injection still need an explicit manual test on your hardware. Voice hold, scroll, and double-click commands from the original private assistant are not included.

[MediaPipe documentation](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker/python) describes the model interface. [Microsoft documents SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput), including restrictions on input to elevated applications.
