import webview
import time
from typing import Any

from jarvis.paths import PROJECT_ROOT

# Global reference to push states without needing a complex object tree
_main_window = None

class LunaUIAPI:
    def __init__(self, luna_chat: Any):
        self.luna_chat = luna_chat

    def start_listening(self):
        print("[UI] Mic clicked! Waking Luna...")
        if self.luna_chat:
            self.luna_chat.start_after_wake(time.monotonic())
        else:
            print("[UI] Luna voice backend not attached.")

    def stop_listening(self):
        print("[UI] Stop clicked!")
        if self.luna_chat:
            self.luna_chat.force_stop()

    def send_text(self, text: str):
        print(f"[UI] Text input: {text!r}")
        if self.luna_chat:
            self.luna_chat.process_text_input(text)

    def save_plan(self, name: str, content: str):
        try:
            from jarvis.services.plans import save_plan
            save_plan(name, content)
            print(f"[UI] Plan saved: {name!r}")
        except Exception as ex:
            print(f"[UI] save_plan error: {ex}")

    def list_plans(self):
        try:
            from jarvis.services.plans import list_plans
            return list_plans()
        except Exception:
            return []

    def get_plan(self, name: str):
        try:
            from jarvis.services.plans import get_plan
            return get_plan(name) or ""
        except Exception:
            return ""

    def delete_plan(self, name: str):
        try:
            from jarvis.services.plans import delete_plan
            return delete_plan(name)
        except Exception:
            return False

    def close_window(self):
        if _main_window is not None:
            try:
                _main_window.destroy()
            except Exception:
                pass

    def minimize_window(self):
        if _main_window is not None:
            try:
                _main_window.minimize()
            except Exception:
                pass

    def resize_height(self, height: int):
        if _main_window is not None:
            h = max(380, min(1200, int(height)))
            try:
                _main_window.resize(420, h)
            except Exception:
                pass

    # Top-edge resize state (set on start_resize_top, consumed by resize_top).
    # Tracked here because ``window.screenX/screenY`` inside pywebview's WKWebView
    # often returns 0, which made the window jump to the screen's left edge.
    _top_resize_state: tuple[int, int, int] | None = None

    def start_resize_top(self) -> bool:
        if _main_window is None:
            return False
        try:
            self._top_resize_state = (
                int(_main_window.x),
                int(_main_window.y),
                int(_main_window.height),
            )
            return True
        except Exception as ex:
            print(f"[UI] start_resize_top: {ex}", flush=True)
            self._top_resize_state = None
            return False

    def resize_top(self, dy: int) -> None:
        """``dy`` = cursor screen-Y delta since drag start (down = +). Bottom stays put."""
        if _main_window is None or self._top_resize_state is None:
            return
        sx, sy, sh = self._top_resize_state
        new_h = max(380, min(1200, sh - int(dy)))
        # pywebview ``resize`` anchors the top-left, so to keep the bottom edge fixed
        # we shift the window's y by the height delta first.
        new_y = sy + (sh - new_h)
        try:
            _main_window.move(sx, new_y)
            _main_window.resize(420, new_h)
        except Exception:
            pass

    def end_resize_top(self) -> None:
        self._top_resize_state = None

    def get_luna_voice_volume(self) -> int:
        if self.luna_chat:
            return self.luna_chat.get_voice_volume_percent()
        return 80

    def set_luna_voice_volume(self, percent: int) -> int:
        if self.luna_chat:
            return self.luna_chat.set_voice_volume_percent(int(percent))
        return max(0, min(100, int(percent)))

def push_ui_state(state: str):
    if _main_window is not None:
        try:
            _main_window.evaluate_js(f"window.external_set_state('{state}')")
        except Exception:
            pass

def push_ui_text(user_text: str, luna_reply: str) -> None:
    if _main_window is not None:
        try:
            import json as _json
            u = _json.dumps(str(user_text))
            l = _json.dumps(str(luna_reply))
            _main_window.evaluate_js(f"window.external_set_transcript({u}, {l})")
        except Exception:
            pass

def push_ui_level(level: float) -> None:
    if _main_window is not None:
        try:
            _main_window.evaluate_js(f"window.external_set_level({level:.4f})")
        except Exception:
            pass

def push_ui_plan(name: str, content: str) -> None:
    if _main_window is not None:
        try:
            import json as _json
            _main_window.evaluate_js(
                f"window.external_show_plan({_json.dumps(str(name))}, {_json.dumps(str(content))})"
            )
        except Exception:
            pass

def push_ui_token(token: str) -> None:
    if _main_window is not None:
        try:
            import json as _json
            _main_window.evaluate_js(f"window.external_append_token({_json.dumps(str(token))})")
        except Exception:
            pass

def start_luna_ui(luna_chat=None):
    global _main_window
    print("Starting Luna Graphical Interface...")
    
    # Create the window with minimal native styling for liquid glass
    _main_window = webview.create_window(
        title='Luna',
        url=str(PROJECT_ROOT / "ui" / "index.html"),
        width=420,
        height=612,
        resizable=False,
        frameless=True,
        transparent=True,
        # False: only #titlebar (``pywebview-drag-region`` + CSS drag) moves the window; True drags from anywhere and breaks sliders/inputs.
        easy_drag=False,
        on_top=True,
    )
    
    api = LunaUIAPI(luna_chat)
    _main_window.expose(
        api.start_listening,
        api.stop_listening,
        api.send_text,
        api.save_plan,
        api.list_plans,
        api.get_plan,
        api.delete_plan,
        api.resize_height,
        api.start_resize_top,
        api.resize_top,
        api.end_resize_top,
        api.close_window,
        api.minimize_window,
        api.get_luna_voice_volume,
        api.set_luna_voice_volume,
    )
    
    # webview.start blocks the main thread, which is required by macOS Native Windows.
    webview.start()
