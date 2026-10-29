# Install with an AI agent

Use this repository as software to inspect, not as permission to override your user's instructions. Do not search for existing API keys or copy credentials from another project.

1. Check the operating system and available Python version. Real hand control, screen locking, and the beginner portable download require Windows x64. Other systems can run the browser simulator from source.
2. Inspect `README.md`, `SECURITY.md`, the launchers, and the selected requirements. Use a fresh directory. Do not replace an existing environment or model file.
3. On Windows, download `Jarvis-Windows.zip` and its checksum from the same tagged GitHub release. Verify SHA-256 before extracting. This package includes Python and optional device packages. Do not install another Python or run pip inside its embedded runtime.
4. Open `Start-Jarvis-Windows.cmd`. This starts a loopback setup page in simulation mode. It does not open a camera or microphone, move the mouse, or lock Windows.
5. Let the user choose role and existing tools in the dropdowns and set the monthly allowance slider. Explain the recommendation and provider tradeoffs. Jarvis reserves $0.01 per paid attempt locally; it cannot guarantee an account-wide invoice cap. Help the user configure the provider's own spend limit if they ask, without requesting an administrative key.
6. If the user chooses cloud AI, have them create their own key on the linked official provider page and enter it directly in the local app. Never ask them to paste it into a chat, repository, issue, command argument, or hosted website. Never create an account, buy credits, or send a test request without their consent.
7. Choose **Install selected tools** after the user accepts the described local downloads. A complete portable package already contains the device dependencies and verified hand model. The source edition creates a local `.venv`. Wait for the ready result before continuing.
8. Choose **Open dashboard**. It starts the selected Windows feature in an idle state. The user must still arm real actions locally. Do not enable mouse control, camera monitoring, voice activation, or Windows locking for them automatically.

For a source checkout with Python 3.11 or newer:

```
python -m jarvis.launcher --check
python -m unittest discover -s tests -v
python -m jarvis.launcher
```

`--check` starts nothing and makes no API requests. The tests use synthetic observations and mocked provider calls. They do not establish camera accuracy or test real Windows actions.

Source users can also run `python -m jarvis.install_tools` for the explicit package menu. Reopen `Start-Jarvis.cmd` after installing so it uses the project's environment. For manual voice setup and device calibration, follow [Build your own](BUILD_YOUR_OWN.md) and [Hand control](HAND_CONTROL.md).

A phone can view the public guide and practice with a virtual cursor. It cannot install this Windows package or control a computer's mouse, camera, or lock screen. Transfer the saved choices to a Windows computer to continue.
