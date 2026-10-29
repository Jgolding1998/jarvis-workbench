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

## One raised hand, one lowered hand

Raise the hand you want to point with. You may keep your other hand down; when it is clearly lower, Jarvis ignores it, so it cannot move the pointer, click, or start a clap. With no other hand in view, either hand can point.

The tracker sees hand landmarks only. It does not see your body or your waist, so "lowered" is judged from the camera image: the lower palm must be at least two palm lengths below the raised palm and in the bottom 40 percent of the frame. It stays ignored until the gap shrinks below one and a half palm lengths. A palm length is the wrist to the middle knuckle. When two hands are visible but neither is clearly lowered, the pointer idles and the clap stop works as before.

If the raised hand leaves the view while the lowered hand remains, the pointer idles. The lowered hand does not take over, even after a frame with no hands. Raise a hand toward where the pointing hand was to continue. After any handover, lost tracking, or idle period, open your hand before the next pinch can click. If the two hands appear to swap places in one frame, the pointer idles for that frame and any pending pinch is cancelled.

How this behaves depends on framing. At a typical laptop distance your waist is usually below the camera's view, so a hand resting there is simply not seen. A hand held at chest level near the bottom of the frame can count as lowered. If you sit far back, both hands may look raised, and the pointer will idle until one is lowered or out of view. These thresholds are `lowered_gap`, `lowered_release`, and `lowered_line` in `GestureSettings`; the dashboard does not expose them.

To move the actual pointer, stop the hand session, enable its separate dashboard pointer permission, then start again. The launch flag alone never enables movement. This edition controls the primary display and reserves its corners as a manual escape area.

Optional offline voice recognizes `watch me` only after its separate dashboard arming. Voice cannot arm movement or disarm the presence guard.

## Stop reliably

- Raise both hands into view separated, then bring their wrists together. This visual clap stops hand control. A clearly lowered hand cannot start a clap. It does not listen for an audio clap.
- Press Esc anywhere, use the dashboard hand stop, type `stop mouse`, or move your physical mouse into a display corner.
- A tracking, camera, or pointer error stops control and attempts to release mouse buttons.

A stop keeps the camera reserved while its device closes. If a driver cannot close, restart Jarvis before using another camera mode. An armed presence policy keeps its own permissions when hand control stops.

## What was verified

Automated tests use fake landmarks, frames, pointer actions, and Windows APIs. They exercise left/right pinch routing, smoothing, bounds, click cooldown, visual clap, a lowered second hand in either detection order, losing the raised hand, concurrent stop, camera ownership, failure cleanup, and independent Esc/corner stops. The real adapter loaded the verified model with the tested MediaPipe/OpenCV versions. It processed blank frames and an [official public MediaPipe test image](https://storage.googleapis.com/mediapipe-assets/right_hands.jpg), detecting two hands with 21 landmarks each and passing those landmarks to a fake pointer.

Physical hand tracking accuracy, camera behavior, actual cursor movement, and Windows input injection still need an explicit manual test on your hardware. Voice hold, scroll, and double-click commands from the original private assistant are not included.

[MediaPipe documentation](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker/python) describes the model interface. [Microsoft documents SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput), including restrictions on input to elevated applications.
