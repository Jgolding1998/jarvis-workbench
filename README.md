# Jarvis Workbench

A small local workstation assistant with a guided setup, explicit action permissions, and a testable presence policy. It runs on your own computer, costs $0, and needs no account or API key.

[Guided setup and gesture practice](https://jarvis-workbench.jrgoldin.chatgpt.site) / [Download the Python source package](https://github.com/Jgolding1998/jarvis-workbench/releases/latest/download/Get-Jarvis-Workbench.zip)

![Safe demo dashboard](docs/dashboard.png)

## Get started

You need [Python 3.11 or newer](https://www.python.org/downloads/). The download is a source package that runs with Python, not a standalone app.

**Windows:** unzip the download, then double-click **Start-Jarvis.cmd**. If Python is missing, the window shows the official download link and changes nothing.

**macOS or Linux:** open a terminal in the unzipped folder and run:

```
python3 -m jarvis.launcher
```

The launcher starts Jarvis in safe demo mode and opens the setup wizard in your browser. The wizard asks:

1. Whether you want to set up Jarvis at all. **No thanks** opens a screen where you can close the page or choose **Stop Jarvis** to end the local session.
2. What you will use it for: Developer, Office work, Study, Creator, Desk privacy, or Custom.
3. The most you would spend each month, from $0 to $500. This is a ceiling, not a target.
4. Which AI tool you already use, if any.
5. How you want to use your voice.
6. Your operating system, camera, microphone, and memory.

You then get a plan: which features will work on your computer and why others will not, the exact tasks left, which ones you do and which one Jarvis does, the launch command, and cost guidance. Choose **Apply and save preferences** to write `runtime/setup-config.json`. Saving or importing a plan never turns on the camera, microphone, mouse, or Windows actions.

You can also download the plan as JSON and import it later. The same wizard works as a static web page with no local server; in that mode it only builds and downloads plans.

On a phone, use the guide and practice area, then save the plan JSON. Open the computer setup link on your computer to download Jarvis, and import your plan in its local wizard. The ZIP contains Python desktop software; it does not install an iPhone or Android app.

## Add optional tools on Windows

Double-click **Install-Optional-Tools.cmd**, choose a menu item, and confirm the downloads:

1. **Camera** for the presence guard.
2. **Camera + hand gestures** for `watch me`. This also downloads and verifies the official hand model.
3. **Offline voice** for local recognition. A Vosk voice model is a separate manual download.
4. **All optional tools** for all three.

The tool uses this project's `.venv` folder. It installs packages and models without opening a camera, microphone, or mouse control. Then reopen **Start-Jarvis.cmd**, which uses that environment automatically. The launcher still opens a safe demo; your saved plan shows the separate command for live features. On macOS or Linux, use `python3 -m jarvis.install_tools` instead.

For the voice model and live launch examples, follow the [setup guide](docs/BUILD_YOUR_OWN.md).

## What works today

| Feature | Status | Needs | Cost |
| --- | --- | --- | --- |
| Local dashboard and typed commands | Implemented | Python | $0 |
| Safe simulator | Implemented | Python | $0 |
| Presence guard (people count) | Implemented | Camera, OpenCV | $0 |
| Privacy lock | Implemented | Windows, camera | $0 |
| Boss Alert synthetic workbook (playful demo) | Implemented | Windows, camera, spreadsheet app | $0 |
| Offline voice activation (Vosk) | Implemented | Windows, microphone, voice packages, a downloaded model | $0 |
| Hand control practice (virtual cursor, clap to stop) | Implemented | Python | $0 |
| Local hand tracking with optional real cursor | Implemented, experimental | Windows, camera, hand packages and model | $0 |
| Setup wizard read-aloud | Implemented, optional | A browser voice that runs on your device | $0 |
| ChatGPT, Claude, OpenAI API, Anthropic API, Ollama | Guidance only, no connector | Your own account if you use them | Set by the provider |
| ElevenLabs or your computer's built-in voice tools | Guidance only, no connector | Your own account or system settings | Set by the provider |

Jarvis has no language model and never asks for an API key. The wizard is a deterministic planner: the same answers always give the same plan.

## Planning costs

Every implemented feature costs $0 per month. Your monthly amount in the wizard is a planning ceiling: a higher number never adds features or recommends a purchase. If you already pay for an AI subscription or API, it is billed by that provider and Jarvis does not use it. Some subscription plans include API credits or other benefits; check the provider's official page for your eligibility. If you use a paid API on its own, set a spending limit in the provider's console. Prices change, so the wizard links to official pricing pages instead of quoting them.

## Use the dashboard

After setup, start the safe demo at any time:

```
python -m jarvis.server --demo
```

Open **http://127.0.0.1:8765**. The demo simulates desktop actions and does not create files.

1. Type `active` and choose **Run command**.
2. Choose **Privacy lock** and apply the permission.
3. Choose **Step away**. After more than 30 seconds, the activity list records a simulated lock.
4. Choose **At my desk** to reset the absence timer.
5. Choose **Another person**. After two continuous seconds, the privacy action is simulated.
6. Use **Stop and disarm** to stop evaluation and clear both action permissions.

For **Boss Alert**, select that action instead of privacy lock. Two people in view for two seconds produces a simulated workbook event. This is a playful synthetic-data demonstration, not identification of a boss or an attempt to hide activity.

### Hand control

The **Hand control** panel has a practice pad. Start practice, move inside the pad to steer a virtual cursor, try left and right pinch, and use **Clap to stop**. Practice never moves the real cursor or opens the camera.

Typed `watch me` or `watchme` starts the local MediaPipe hand camera; `stop mouse` stops it. Follow the [hand control guide](docs/HAND_CONTROL.md) to install the optional packages and verified model, then launch with `--allow-hand-control`. Real pointer movement stays off until you enable its separate dashboard permission and start a hand session. Voice `watch me` also needs offline voice and its own dashboard opt-in.

Clap, Esc, `stop mouse`, or **Stop hand control** stops hand control only. Hand stop never disarms presence permissions; an active presence guard keeps running during hand practice. The camera has one owner at a time: hand control and the presence guard cannot both use it, and starting one while the other runs gives a clear message instead of silently stopping either.

## Build your own

The [setup guide](docs/BUILD_YOUR_OWN.md) covers a fresh environment, local voice models, observation-only camera calibration, and the separate flags that permit real actions. The [architecture](docs/ARCHITECTURE.md) explains state transitions and the security boundary.

## Test

```
python -m unittest discover -s tests -v
```

The suite covers the presence policy timing, invalid inputs, workbook contents, simulated action isolation, local session authorization, DNS rebinding, and cross-origin requests. It also covers the setup wizard: answer validation, prototype-key and unknown-field rejection, budget limits, deterministic plans, identical results from the Python and browser planners (when Node.js is installed), atomic config saving, the local HTTP routes, the launcher's end-to-end apply and exit, the missing-Python message, hand control permissions, camera ownership, clap and Esc stops, and fake cursor output. Tests use fake observations and fake Windows calls; they never open a camera or microphone, move the cursor, or lock the machine.

Camera accuracy, microphone recognition, a physical Windows lock, opening Excel, and live hand tracking need manual checks on your own hardware. A successful Windows API result means the request started, not proof that it completed.

## Limits worth understanding

OpenCV's bundled HOG detector expects visible people, usually with substantial body framing. A seated head-and-shoulders webcam view can be missed. Establish a reliable single-person observation before trusting the timer. This edition does not recognize individuals.

This is an experimental supplementary guard. Keep Windows auto-lock enabled. It does not unlock Windows, replace authentication, certify camera reliability, or run unattended at startup.

If the camera cannot open or its reader fails, restart the process after resolving the device problem. An armed guard treats a first frame delayed more than five seconds as a camera fault and requests a lock.

## References

The implementation uses documented interfaces from [OpenCV](https://docs.opencv.org/4.x/d5/d33/structcv_1_1HOGDescriptor.html), [Vosk](https://alphacephei.com/vosk/install), and Microsoft's [LockWorkStation](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-lockworkstation) and [GetAsyncKeyState](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getasynckeystate) APIs. Their software and models have their own licenses. No third-party assistant source, model weights, or account configuration is bundled. This project is released under the [MIT License](LICENSE).
