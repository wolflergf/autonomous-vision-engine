"""Detection controller: ties capture, workflow client, and overlay together.

Uses separate threads for detection and action to maximize responsiveness:
- Detection thread: capture -> inference -> push to queue (non-blocking)
- Action thread: pop from queue -> execute action planner immediately
"""
import threading
import queue
import time
from typing import Callable, Tuple, List, Dict, Any
from loguru import logger
from capture import CaptureWorker
from model_client import LocalModelClient
from action_planner import ActionPlanner
from utils import get_window_rect


class DetectionController:
    def __init__(self, hwnd: int, overlay_update: Callable, log_fn: Callable, fps: int = None):
        """overlay_update(detections, (w,h)) will be called on each new result or tick.
        log_fn(text) will be called to output logs to UI.
        """
        self.hwnd = hwnd
        self.overlay_update = overlay_update
        self.log = log_fn
        # fps=None or fps<=0 means "no throttle" (run as fast as possible)
        self.fps = fps if fps is not None else None

        # Capture at full speed for real-time detection
        cap_target_fps = 0
        try:
            if self.fps is not None and self.fps > 0:
                cap_target_fps = max(10, int(self.fps * 2))
        except Exception:
            cap_target_fps = 0
        self.capture = CaptureWorker(hwnd=hwnd, target_fps=cap_target_fps, resize_max=None)
        # local model uses models/aion.pt
        self.client = LocalModelClient(weights_path="models/aion.pt")
        # action planner for inputs (skills auto-target, no Tab needed)
        self.action_planner = ActionPlanner(hwnd=hwnd, enabled=True)

        # Unbounded queue for detection -> action communication
        self._detection_queue: queue.Queue = queue.Queue()

        # Separate threads for detection and action
        self._detection_thread = threading.Thread(target=self._detection_loop, daemon=True)
        self._action_thread = threading.Thread(target=self._action_loop, daemon=True)
        self._running = threading.Event()

        # Shared state
        self._detections: List[Dict[str, Any]] = []
        self._frame_size: Tuple[int, int] = (0, 0)
        self._startup_complete = False

    def start(self):
        self.log("Starting capture and detection (dual-thread mode)")
        self.capture.start_capture()
        self._running.set()
        
        # Start both threads
        if not self._detection_thread.is_alive():
            self._detection_thread.start()
        if not self._action_thread.is_alive():
            self._action_thread.start()
        
        self._startup_complete = True
        self.log("✓ Automation active (detection + action threads running)")

    def stop(self):
        self.log("Stopping detection")
        self._running.clear()
        self.capture.stop_capture()
        # Signal action thread to exit by pushing sentinel
        self._detection_queue.put(None)

    def _detection_loop(self):
        """Detection thread: capture -> inference -> push to queue.
        
        Runs as fast as possible to maximize detection speed.
        Updates overlay in REAL-TIME after every inference.
        """
        _last_overlay_update = 0.0
        _overlay_update_interval = 0.016  # ~60 FPS overlay updates
        
        while self._running.is_set():
            frame = self.capture.get_latest_frame()
            if frame is None:
                # Still update overlay even without frame to keep it responsive
                now = time.time()
                if now - _last_overlay_update >= _overlay_update_interval:
                    try:
                        orig_w, orig_h = self.capture.get_window_size() or self._frame_size
                        self.overlay_update(self._detections, (orig_w, orig_h))
                        _last_overlay_update = now
                    except Exception:
                        pass
                time.sleep(0.003)  # 3ms spin-wait for maximum responsiveness
                continue
            
            h, w = frame.shape[:2]
            self._frame_size = (w, h)
            
            try:
                orig_w, orig_h = self.capture.get_window_size() or (w, h)
                frame_for_model = frame.copy()
                
                # Run inference
                t0 = time.time()
                preds = self.client.predict(frame_for_model)
                t1 = time.time()
                inference_ms = (t1 - t0) * 1000.0

                # Scale predictions to original window coordinates
                sent_h, sent_w = frame_for_model.shape[:2]
                sx = orig_w / sent_w if sent_w and orig_w else 1.0
                sy = orig_h / sent_h if sent_h and orig_h else 1.0

                conv = []
                for p in preds:
                    try:
                        x = float(p.get("x", 0)) * sx
                        y = float(p.get("y", 0)) * sy
                        pw = float(p.get("width", 0)) * sx
                        ph = float(p.get("height", 0)) * sy
                        conv.append({
                            "x": x, "y": y, "width": pw, "height": ph,
                            "class": p.get("class"), "confidence": p.get("confidence")
                        })
                    except Exception:
                        continue

                # Update shared detection state IMMEDIATELY
                self._detections = conv
                
                # Update action planner state
                try:
                    self.action_planner._last_frame_had_detections = bool(len(conv))
                    self.action_planner._last_inference_ms = inference_ms
                    # Store detections in action planner for roaming checks
                    self.action_planner._last_detections = conv
                except Exception:
                    pass
                
                # REAL-TIME overlay update - every frame!
                try:
                    self.overlay_update(conv, (orig_w, orig_h))
                    _last_overlay_update = time.time()
                except Exception:
                    pass
                
                # Push detection data to action queue (non-blocking)
                if self._startup_complete:
                    rect = get_window_rect(self.hwnd)
                    if rect:
                        left, top, rw, rh = rect
                        window_rect = (left, top, rw, rh)
                    else:
                        window_rect = (0, 0, orig_w, orig_h)
                    
                    # Push to unbounded queue
                    self._detection_queue.put((conv, window_rect, inference_ms))
                    
            except Exception as e:
                self.log(f"Detection error: {e}")
            
            # NO throttle - run as fast as possible for real-time detection
            # FPS setting only affects logging, not detection speed

    def _action_loop(self):
        """Action thread: consume detections from queue and execute immediately.
        
        Runs independently of detection to minimize input latency.
        """
        while self._running.is_set():
            try:
                # Block with timeout to allow checking _running flag
                item = self._detection_queue.get(timeout=0.1)
                
                if item is None:
                    # Sentinel value: exit thread
                    break
                
                detections, window_rect, inference_ms = item
                
                # Execute action planner immediately
                try:
                    self.action_planner.plan_and_execute(detections, window_rect)
                except Exception as e:
                    self.log(f"Action planner error: {e}")
                    
            except queue.Empty:
                # No detections in queue, continue waiting
                continue
            except Exception as e:
                self.log(f"Action loop error: {e}")
