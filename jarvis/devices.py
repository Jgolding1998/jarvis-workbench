from __future__ import annotations

import json
import queue
import threading
import time


FAILED_READS_BEFORE_FAULT = 12


def _presence_session(service):
    return service.presence_session()


def run_camera(service, stop: threading.Event, device=0):
    camera = lease = faulted_session = None
    failures = 0
    try:
        import cv2
        detector = cv2.HOGDescriptor()
        detector.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        while not stop.is_set():
            if camera is not None and not service.is_active():
                camera.release()
                camera = None
                service.presence_camera_closed(lease)
                lease = None
            if faulted_session is not None:
                if _presence_session(service) == faulted_session:
                    stop.wait(0.1)
                    continue
                faulted_session = None
            if camera is None:
                lease = service.acquire_presence_camera()
                if lease is None:
                    stop.wait(0.1)
                    continue
                try:
                    camera = cv2.VideoCapture(device)
                    if not camera.isOpened():
                        raise RuntimeError("Camera unavailable")
                except Exception:
                    if camera is not None:
                        camera.release()
                        camera = None
                    # Report while the lease still blocks a restart, so the fault cannot land on a fresh session.
                    service.camera_fault()
                    service.presence_camera_closed(lease)
                    lease = None
                    stop.wait(0.5)
                    continue
                failures = 0
            if not service.is_active():
                continue
            try:
                ok, frame = camera.read()
                if ok:
                    height, width = frame.shape[:2]
                    if not 1 <= width <= 8192 or not 1 <= height <= 8192:
                        raise ValueError("Invalid camera frame")
                    frame = cv2.resize(frame, (640, max(128, min(1280, int(height * 640 / width)))))
                    boxes, _ = detector.detectMultiScale(frame, winStride=(8, 8), padding=(8, 8), scale=1.05)
            except Exception:
                faulted_session = _presence_session(service)
                camera.release()
                camera = None
                service.camera_fault()
                service.presence_camera_closed(lease)
                lease = None
                stop.wait(0.15)
                continue
            if not ok:
                failures += 1
                if failures >= FAILED_READS_BEFORE_FAULT:
                    faulted_session = _presence_session(service)
                    camera.release()
                    camera = None
                    service.camera_fault()
                    service.presence_camera_closed(lease)
                    lease = None
                stop.wait(0.15)
                continue
            failures = 0
            service.observe(min(20, len(boxes)))
            del frame
            stop.wait(0.15)
    except Exception:
        service.camera_fault()
    finally:
        if camera is not None:
            try:
                camera.release()
            except Exception:
                return
        if lease is not None:
            service.presence_camera_closed(lease)


SAMPLE_RATE = 16000
BLOCK_SAMPLES = 1600
STALL_SECONDS = 2.0


def run_voice(service, stop: threading.Event, model_path, retry_seconds=2.0):
    try:
        import sounddevice
        import vosk
        vosk.SetLogLevel(-1)
        model = vosk.Model(str(model_path))
    except Exception:
        service.voice_status("Voice unavailable; use typed commands")
        return
    phrases = ["active", "watch me", "[unk]"] if service.hand.allowed else ["active", "[unk]"]
    while not stop.is_set():
        try:
            _listen(service, stop, sounddevice, vosk.KaldiRecognizer(model, SAMPLE_RATE, json.dumps(phrases)))
        except Exception:
            pass
        if not stop.is_set():
            service.voice_status("Microphone stopped; retrying. Typed commands still work")
            stop.wait(retry_seconds)


def _listen(service, stop, sounddevice, recognizer):
    audio = queue.Queue(maxsize=20)

    def receive(data, frames, timing, status):
        try:
            audio.put_nowait(bytes(data))
        except queue.Full:
            pass

    with sounddevice.RawInputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK_SAMPLES, dtype="int16", channels=1, callback=receive):
        service.voice_status("Listening locally")
        heard = time.monotonic()
        while not stop.is_set():
            try:
                block = audio.get(timeout=0.25)
            except queue.Empty:
                if time.monotonic() - heard > STALL_SECONDS:
                    return
                continue
            heard = time.monotonic()
            if recognizer.AcceptWaveform(block):
                text = json.loads(recognizer.Result()).get("text", "").strip()
                try:
                    with service.lock:
                        if stop.is_set():
                            return
                        if text == "active":
                            service.command(text, source="voice")
                        elif text == "watch me" and service.hand.allowed:
                            service.command(text, source="voice")
                except ValueError:
                    service.voice_status("Heard " + text + ", but it is not allowed right now")
                except Exception:
                    service.voice_status("Heard " + text + ", but the command failed")
