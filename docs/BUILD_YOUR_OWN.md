# Build your own dashboard

For the beginner Windows download, use **Jarvis-Windows.zip** and **Start-Jarvis-Windows.cmd**. Python and device packages are included. Choose your role and existing tool, set the allowance slider, then choose **Open dashboard**; a complete package has nothing to install. This guide also covers the source edition and manual calibration; its `.venv` commands apply to a source checkout. For the portable edition, use its `runtime\python.exe` from the outer `Jarvis-Windows` folder instead.

## 1. Try the safe demo

Install [Python 3.11 or newer](https://www.python.org/downloads/), unzip Jarvis, and double-click **Start-Jarvis.cmd** on Windows. If Python is missing, the window explains what to install. On macOS or Linux, run `python3 -m jarvis.launcher` from the unzipped folder.

The launcher opens the setup wizard without using the camera, microphone, mouse, or Windows actions. Try the simulator before installing optional tools. A saved plan never arms anything.

## 2. Install only the tools you want

On Windows, double-click **Install-Optional-Tools.cmd**:

| Menu | Adds |
| --- | --- |
| 1. Camera | OpenCV for the presence guard |
| 2. Camera + hand gestures | MediaPipe, OpenCV, and a checksum-verified hand model |
| 3. Offline voice | Vosk and sounddevice; download the voice model separately |
| 4. All optional tools | All of the above |
| Q. Cancel | Changes nothing |

Read the package list, then enter `y` to confirm. Installation uses this project's `.venv` directory. It downloads packages from PyPI and, for hand control, the fixed official Google model. It never opens devices. Existing environments and model files are preserved when setup cannot safely continue.

Reopen **Start-Jarvis.cmd** after a source installation. It prefers `.venv` automatically and returns to setup. **Open dashboard** starts the selected feature idle. The commands below let you choose manual observation-only settings for calibration.

On macOS or Linux, use the same menu with `python3 -m jarvis.install_tools`. This creates `.venv` too; portable live commands start with `./.venv/bin/python`. Windows examples below use the project's `.venv` interpreter directly.

## 3. Calibrate the presence camera

After installing Camera, open a terminal in the Jarvis folder:

```powershell
.\.venv\Scripts\python.exe -m jarvis.server --camera
```

Open the printed address, normally **http://127.0.0.1:8765**, and type `active`. Actions remain disarmed. Use `--camera-index 1` for a second webcam, or `--port 8877` if the default port is busy.

The HOG detector needs substantial body framing. A tight seated head-and-shoulders view may not work reliably. Test one person, an empty view, a second person, occlusion, and a disconnected camera before arming any action. The absence timer starts only after a person has been observed. No identity recognition is performed and no frames are saved or uploaded.

## 4. Add offline voice

The guided voice setup supports Windows in this release. Use typed commands on other systems; the pinned Vosk package has no macOS wheel.

Choose **Offline voice** or **All optional tools** in the installer. Voice setup builds the pinned, checksum-verified `srt` dependency from its official source archive; the other dependencies use wheels. The installer accepts only that reviewed source archive.

Then download a small English model from the [Vosk model catalog](https://alphacephei.com/vosk/models), read its license, and unzip its contents into `models/voice-en`. Check that these paths exist:

```text
models/voice-en/conf/model.conf
models/voice-en/am/
```

The installer deliberately leaves voice model selection and extraction to you. Do not leave an extra versioned folder between `voice-en` and `conf`.

```powershell
.\.venv\Scripts\python.exe -m jarvis.server --camera --voice-model models/voice-en
```

The microphone listens locally for `active`. With hand control enabled, separately armed voice can also recognize `watch me`. Voice cannot arm desktop actions or disarm the presence guard. Nearby speech can cause false activation; speaker authentication is not claimed.

Audio reaches the recognizer in 100 ms blocks. A command runs once Vosk detects the pause after it, so expect a short delay after you finish speaking. If the microphone stops delivering audio for two seconds or fails to open, the dashboard shows `Microphone stopped; retrying` and Jarvis reopens it every two seconds without reloading the voice model. A newly connected microphone may not appear until Jarvis restarts. Once shutdown begins, a command recognized afterward is discarded. A voice command whose dispatch already started finishes first, and shutdown then stops presence and hand control as usual.

## 5. Permit an optional desktop action

For a Windows privacy lock:

```powershell
.\.venv\Scripts\python.exe -m jarvis.server --camera --allow-lock
```

Select **Privacy lock**, apply the permission, then activate. The policy requests a lock after an established person is absent for more than 30 seconds, two people remain visible for at least two seconds, or observations are stale for more than five seconds. Keep Windows automatic lock enabled. The API is asynchronous, so manually verify the actual lock on your hardware.

For the synthetic workbook demonstration:

```powershell
.\.venv\Scripts\python.exe -m jarvis.server --camera --allow-spreadsheet
```

Select **Boss Alert** instead of privacy lock. It writes `runtime/Boss Alert.xlsx` with demonstration data and opens the registered spreadsheet application. It contains no macros, formulas, external links, or business data. This mode does not protect an unattended workstation.

## 6. Try hand control

Choose installer option **2** or **4**, then follow the [hand control guide](HAND_CONTROL.md). Practice uses a virtual cursor. Live `watch me` uses the verified local model, with real pointer movement off until separately permitted. Point with one raised hand; a second hand held clearly lower is ignored. A visual clap with both hands raised, or Esc, stops hands only.

The camera has one owner. Stop a mode explicitly and wait for its camera to close before starting the other. If a driver cannot release the camera, restart Jarvis.

## Manual installation alternative

Advanced users can create an environment and install the reviewed files directly:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-camera.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-hand.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-voice.txt
```

Install only the files needed for your features. For hand control, the manual route also needs the model and checksum in the hand guide. Use contrib OpenCV consistently; do not combine it with the separate `opencv-python` distribution. On macOS or Linux, replace `python` with `python3` when creating the environment and use `./.venv/bin/python` afterwards.

## Extend carefully

`Presence` emits named events, `Actions` implements the allowlist, and `Service` manages local permissions. New integrations must preserve those boundaries. Weather, email, browser automation, general chat, voice hold/scroll/double-click commands, and the original assistant's private tools are not included.
