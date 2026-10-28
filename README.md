# Jarvis Workbench

A small Windows workstation assistant with a local dashboard, explicit action permissions, and a testable presence policy.

![Safe demo dashboard](docs/dashboard.png)

This is a clean portfolio edition rebuilt from a desktop assistant concept. It contains the dashboard and presence guard described here. It does not package the original assistant's accounts, private integrations, memory, browser profiles, or business tools.

## Run the safe demo

Install [Python 3.11 or newer](https://www.python.org/downloads/), clone this repository, and open a terminal in its directory:

```powershell
python -m jarvis.server --demo
```

Open **http://127.0.0.1:8765**. No dependencies, account, API key, camera, or microphone are needed. The demo simulates desktop actions and does not create files.

1. Type `active` and choose **Run command**.
2. Choose **Privacy lock** and apply the permission.
3. Choose **Step away**. After more than 30 seconds, the activity list records a simulated lock.
4. Choose **At my desk** to reset the absence timer.
5. Choose **Another person**. After two continuous seconds, the privacy action is simulated.
6. Use **Stop and disarm** to stop evaluation and clear both action permissions.

For the optional **Boss Alert** demo, select that action instead of privacy lock. Two people in view for two seconds produces a simulated workbook event. This is a playful synthetic-data demonstration, not identification of a boss or an attempt to hide activity.

## What is implemented

| Component | Behavior |
| --- | --- |
| Presence policy | Monotonic clock, explicit activation, absence timeout, extra-person debounce, stale-camera fault |
| Dashboard | Local status, typed commands, separate action arming, simulator, bounded in-memory activity |
| Windows actions | Direct `LockWorkStation` call; macro-free synthetic `.xlsx` opened with the registered desktop application |
| Camera adapter | Optional local OpenCV HOG person detector; frames are not written or uploaded |
| Voice adapter | Optional offline Vosk activation using the single command `active` |
| HTTP boundary | Loopback binding, exact Host and Origin checks, per-process session token, bounded JSON, restrictive CSP |

No language model is part of the runtime. Anyone extending the project with AI must configure their own model or provider. An AI integration is a future extension, not a hidden dependency or a claimed feature.

## Build your own

The [setup guide](docs/BUILD_YOUR_OWN.md) covers a fresh environment, local voice models, observation-only camera calibration, and the separate flags that permit real actions. The [architecture](docs/ARCHITECTURE.md) explains the state transitions and threat boundary.

## Test

```powershell
python -m unittest discover -s tests -v
```

The suite covers the exact 30-second boundary, re-entry, failed camera input, extra-person debounce, startup and stop behavior, invalid inputs, macro-free workbook contents, simulated action isolation, local session authorization, DNS rebinding, and cross-origin requests. It uses fake observations and never locks the test machine.

Camera accuracy, microphone recognition, a physical Windows lock, and opening Excel require manual checks on the target hardware. They were not exercised by the automated suite. A successful Windows API result means the lock request started, not proof the desktop is locked.

## Limits worth understanding

OpenCV's bundled HOG detector expects visible people, usually with substantial body framing. A seated head-and-shoulders webcam view can be missed. Establish a reliable single-person observation before trusting the timer, then test observation-only behavior in your actual lighting. This edition does not recognize individuals or detect that a particular person left while someone else stayed.

This is an experimental supplementary guard. Keep Windows auto-lock enabled. It does not unlock Windows, replace authentication, certify camera reliability, or run unattended at startup.

If the camera cannot open or its reader fails, restart the process after resolving the device problem. Stop and active do not reopen a failed camera thread. An armed guard treats a first frame delayed more than five seconds as a camera fault and requests a lock.

## References

The implementation uses documented interfaces from [OpenCV](https://docs.opencv.org/4.x/d5/d33/structcv_1_1HOGDescriptor.html), [Vosk](https://alphacephei.com/vosk/install), and [Microsoft's LockWorkStation API](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-lockworkstation). Their software and models have their own licenses. No third-party assistant source, model weights, or account configuration is bundled.
