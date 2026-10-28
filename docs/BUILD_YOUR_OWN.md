# Build your own dashboard

## 1. Start from a clean environment

Install 64-bit Python 3.11 or newer. The dependency-free demo and automated suite were exercised with Python 3.12. Optional hardware packages depend on available wheels for your Python version and architecture.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m jarvis.server --demo
```

Open **http://127.0.0.1:8765**. Keep that exact address: the local server checks its Host header. If the port is busy, add `--port 8877` and use the address printed by the application.

Use the simulator before connecting hardware. Typing `active` and applying an action permission are separate steps. The first step enables evaluation; the second authorizes a specific allowlisted desktop effect. Neither a command nor a model can run arbitrary programs.

## 2. Add a local camera, with actions disabled

```powershell
python -m pip install -r requirements-camera.txt
python -m jarvis.server --camera
```

Type `active`. The camera opens only while the guard is active. It releases the camera after the guard stops. Frame processing stays in the Python process and only a person-count estimate reaches the dashboard.

Use `--camera-index 1` if your chosen webcam is the second device. HOG is a simple bundled detector, not a modern face or identity model. Arrange the camera so people are visible with substantial body framing. Test a single person, an empty view, two people, partial occlusion, and a disconnected camera. A tight seated webcam crop may not work reliably.

The absence timer becomes meaningful only after at least one person has been observed. If the interface remains at **Waiting to establish your presence**, improve the camera framing before arming anything. The policy cannot know whether the person in view is you.

No frames are saved. Do not point the camera at people who have not agreed to the demonstration.

## 3. Add offline voice activation

Install the optional voice packages:

```powershell
python -m pip install -r requirements-voice.txt
```

Download a language model directly from the [Vosk model catalog](https://alphacephei.com/vosk/models), read its license, verify the download with any checksum supplied by its publisher, and unpack it into a local directory such as `models/voice-en`. Model files are intentionally ignored by Git and are not distributed here.

```powershell
python -m jarvis.server --camera --voice-model .\models\voice-en
```

The microphone now listens locally for **active**. Voice can start observation; voice cannot arm actions or disarm the guard. Use the dashboard's **Stop and disarm** button to stop it. Microphone and camera access are explicit launch choices, and both are disabled in the default demo.

Nearby speech or audio playback can cause false activation. No speaker authentication is claimed. An activation after a stop starts observation with desktop actions still disarmed.

## 4. Permit a real Windows lock

Save your work and confirm Windows auto-lock is enabled before your own manual hardware test. This command permits the UI to arm locking; the command alone does not arm it:

```powershell
python -m jarvis.server --camera --allow-lock
```

In the dashboard, select **Privacy lock** and apply the permission, then activate. Optional voice works with the same command if you add `--voice-model`.

The policy requests a Windows lock when an established person is absent for **more than 30 seconds**, two or more people remain visible for **at least two seconds**, or camera observations are missing for **more than five seconds**. A camera fault is shown as a fault, not as an empty room.

Windows must allow the process to interact with the current desktop. The API request is asynchronous, so manually verify that the computer locks. This project does not query the Windows session to confirm the lock. An action failure appears in the local activity list. Use the operating system's own automatic lock for dependable enforcement.

## 5. Try the optional synthetic workbook action

```powershell
python -m jarvis.server --camera --allow-spreadsheet
```

Select **Boss Alert** and apply the permission. A sustained additional-person count creates `runtime/Boss Alert.xlsx` with three rows of demonstration data, then opens it with the Windows file association. Excel or a compatible registered spreadsheet application is required. It contains no macros, formulas, external links, personal information, or business data.

The workbook action and privacy lock are mutually exclusive in the dashboard. The workbook mode does not protect an unattended workstation; choose privacy lock for that purpose. A repeated crowd observation will not reopen the workbook until the view has contained fewer than two people continuously for five seconds.

## 6. Extend the assistant without widening its authority

`Presence` only emits named events. `Actions` implements the allowlist. `Service` serializes state changes. A future local AI assistant can suggest a supported typed command, but should not bypass arming, execute shell text, or receive camera frames by default. Keep credentials in your own secret store and add tests for every new action boundary.

The build here is complete for the listed features. Weather, email, browser automation, general chat, language model reasoning, and the original assistant's private tools are not included.
