from __future__ import annotations

import json
import queue
import threading


def run_camera(service, stop: threading.Event, device=0):
    try:
        import cv2
        camera = None
        detector = cv2.HOGDescriptor()
        detector.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        try:
            while not stop.is_set():
                if not service.is_active():
                    if camera is not None:
                        camera.release()
                        camera = None
                    stop.wait(0.2)
                    continue
                if camera is None:
                    camera = cv2.VideoCapture(device)
                    if not camera.isOpened():
                        raise RuntimeError("Camera unavailable")
                ok, frame = camera.read()
                if not ok:
                    service.camera_fault()
                    stop.wait(0.2)
                    continue
                height, width = frame.shape[:2]
                frame = cv2.resize(frame, (640, max(128, int(height * 640 / width))))
                boxes, _ = detector.detectMultiScale(frame, winStride=(8, 8), padding=(8, 8), scale=1.05)
                service.observe(min(20, len(boxes)))
                del frame
                stop.wait(0.15)
        finally:
            if camera is not None:
                camera.release()
    except Exception:
        service.camera_fault()


def run_voice(service, stop: threading.Event, model_path):
    try:
        import sounddevice
        import vosk
        vosk.SetLogLevel(-1)
        model = vosk.Model(str(model_path))
        recognizer = vosk.KaldiRecognizer(model, 16000, json.dumps(["active", "[unk]"]))
        audio = queue.Queue(maxsize=8)

        def receive(data, frames, timing, status):
            try:
                audio.put_nowait(bytes(data))
            except queue.Full:
                pass

        with sounddevice.RawInputStream(samplerate=16000, blocksize=4000, dtype="int16", channels=1, callback=receive):
            service.voice_status("Listening locally")
            while not stop.is_set():
                try:
                    block = audio.get(timeout=0.25)
                except queue.Empty:
                    continue
                if recognizer.AcceptWaveform(block):
                    text = json.loads(recognizer.Result()).get("text", "").strip()
                    if text == "active":
                        service.command(text)
    except Exception:
        service.voice_status("Voice unavailable; use typed commands")
