#!/usr/bin/env python3
"""
Start the runtime, list detected devices, and keep the webview alive with opencv at the same time.

PYTHONPATH=host/main/src python3 test/test_cv.py
$env:PYTHONPATH="host/main/src"; python test/test_cv.py - WINDOWS
"""

from __future__ import annotations
import time
from openrdk import CommsRuntime
import cv2
import threading

def main():
    stop_event = threading.Event()

    webview_thread = threading.Thread(target=webview, args=(stop_event,), daemon=False)
    camera_thread = threading.Thread(target=camera, args=(stop_event,), daemon=False)

    webview_thread.start()
    camera_thread.start()

    try:
        webview_thread.join()
        camera_thread.join()
    except KeyboardInterrupt:
        print("\nshutdown requested")
        stop_event.set()
        webview_thread.join()
        camera_thread.join()

def camera(stop_event):
    cap = cv2.VideoCapture(0)

    try:
        while not stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                break

            cv2.imshow("Camera", frame)

            if cv2.waitKey(1) & 0xFF == 13:
                stop_event.set()
                break

    finally:
        cap.release()
        cv2.destroyAllWindows()

def webview(stop_event):
    openrdk = None
    try:
        openrdk = CommsRuntime(
        auto_start=True,
        enable_webview=True,
        enable_webview_updates=True,
    )
        openrdk.post("webview_complete")
        time.sleep(3)
        openrdk.list_devices(verbose="full")

        input("\npress Enter to quit\n")

    except KeyboardInterrupt:
        print("\nshutdown requested")
    finally:
         stop_event.set()
         if openrdk is not None:
             openrdk.stop()



if __name__ == "__main__":
    main()
    
