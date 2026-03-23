import json
import math
import queue
import sys
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aubio
import numpy as np
import sounddevice as sd
from tkinter import messagebox


NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
APP_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = APP_DIR / "config.json"


@dataclass
class AppSettings:
    blocksize: int = 1024
    silence_threshold: float = -40.0
    key_highlight_color: str = "red"
    midi_start: int = 21
    midi_end: int = 108
    white_key_width_ratio: float = 20.0
    black_key_width_ratio: float = 14.0
    tuner_width: int = 300
    tuner_height: int = 180
    smoothing_alpha: float = 0.2
    pitch_diff_threshold: float = 0.01
    audio_device_index: Optional[int] = None
    samplerate: int = 44100
    tuner_cent_limit: int = 50
    piano_window_geometry: str = "1200x200+100+100"
    piano_bg_color: str = "gray"
    tuner_bg_color: str = "black"
    white_key_fill: str = "white"
    black_key_fill: str = "black"
    white_key_outline: str = "black"
    black_key_outline: str = "black"
    tuner_arc_color: str = "gray"
    tuner_needle_in_tune_color: str = "lime"
    tuner_needle_flat_color: str = "blue"
    tuner_needle_sharp_color: str = "red"
    tuner_text_color: str = "white"
    piano_text_color: str = "white"
    piano_font: tuple = ("Arial", 14)
    tuner_font: tuple = ("Arial", 12)
    piano_title: str = "Keyboard"
    tuner_title: str = "Tuner"
    piano_topmost: bool = True
    tuner_topmost: bool = True
    ui_poll_interval_ms: int = 20

    @classmethod
    def load(cls, path: Path) -> "AppSettings":
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)

        data = cls().__dict__.copy()
        data.update(raw)
        return cls(**data)


class AudioError(RuntimeError):
    pass


def is_black(note: str) -> bool:
    return "#" in note


def midi_to_note_name(midi: int) -> str:
    return NOTE_NAMES[midi % 12] + str(midi // 12 - 1)


def freq_to_midi(freq: float) -> Optional[float]:
    return 69 + 12 * math.log2(freq / 440.0) if freq > 0 else None


def midi_to_freq(midi: int) -> float:
    return 440.0 * (2 ** ((midi - 69) / 12))


class TunerWindow:
    def __init__(self, root: tk.Tk, settings: AppSettings):
        self.settings = settings
        self.window = tk.Toplevel(root)
        self.window.title(settings.tuner_title)
        self.window.attributes("-topmost", settings.tuner_topmost)
        self.canvas = tk.Canvas(
            self.window,
            width=settings.tuner_width,
            height=settings.tuner_height,
            bg=settings.tuner_bg_color,
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self.cx = settings.tuner_width // 2
        self.cy = settings.tuner_height - 30
        self.r = min(settings.tuner_width // 2 - 20, settings.tuner_height - 60)

        self._draw_base()

    def _draw_base(self) -> None:
        left = self.cx - self.r
        top = self.cy - self.r
        right = self.cx + self.r
        bottom = self.cy + self.r
        self.canvas.create_arc(
            left,
            top,
            right,
            bottom,
            start=180,
            extent=180,
            outline=self.settings.tuner_arc_color,
            width=2,
            style="arc",
        )

        for i in range(-self.settings.tuner_cent_limit, self.settings.tuner_cent_limit + 1, 10):
            angle = 180 + (i + self.settings.tuner_cent_limit) * 180 / (2 * self.settings.tuner_cent_limit)
            x1 = self.cx + self.r * math.cos(math.radians(angle))
            y1 = self.cy + self.r * math.sin(math.radians(angle))
            x2 = self.cx + (self.r - 10) * math.cos(math.radians(angle))
            y2 = self.cy + (self.r - 10) * math.sin(math.radians(angle))
            self.canvas.create_line(x1, y1, x2, y2, fill=self.settings.tuner_arc_color)

        self.needle = self.canvas.create_line(
            self.cx,
            self.cy,
            self.cx,
            self.cy - self.r,
            fill=self.settings.tuner_needle_in_tune_color,
            width=3,
        )
        self.text = self.canvas.create_text(
            self.cx,
            20,
            text="Waiting for input...",
            fill=self.settings.tuner_text_color,
            font=self.settings.tuner_font,
        )

    def update(self, diff: float) -> None:
        diff = max(min(diff, 0.5), -0.5)
        cent = diff * 100
        angle = 180 + ((cent + self.settings.tuner_cent_limit) / (2 * self.settings.tuner_cent_limit)) * 180
        x = self.cx + self.r * math.cos(math.radians(angle))
        y = self.cy + self.r * math.sin(math.radians(angle))
        self.canvas.coords(self.needle, self.cx, self.cy, x, y)

        if abs(cent) < 10:
            color = self.settings.tuner_needle_in_tune_color
            status = "✓ In Tune"
        elif cent < -10:
            color = self.settings.tuner_needle_flat_color
            status = f"{int(cent)} cents flat"
        else:
            color = self.settings.tuner_needle_sharp_color
            status = f"{int(cent)} cents sharp"

        self.canvas.itemconfig(self.needle, fill=color)
        self.canvas.itemconfig(self.text, text=status)


class PianoWindow:
    def __init__(self, root: tk.Tk, settings: AppSettings, on_close):
        self.settings = settings
        self.on_close = on_close
        self.window = tk.Toplevel(root)
        self.window.title(settings.piano_title)
        self.window.overrideredirect(False)
        self.window.geometry(settings.piano_window_geometry)
        self.window.protocol("WM_DELETE_WINDOW", self.confirm_quit)

        self.canvas_frame = tk.Frame(self.window, borderwidth=0, highlightthickness=0)
        self.canvas_frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(
            self.canvas_frame,
            bg=settings.piano_bg_color,
            borderwidth=0,
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.on_resize)

        self.white_keys = {}
        self.black_keys = {}
        self.midi_to_x = {}
        self.pitch_text = None
        self.has_titlebar = True
        self.is_topmost = settings.piano_topmost
        self.window.attributes("-topmost", self.is_topmost)
        self.window.bind("<Control-t>", self.toggle_titlebar)
        self.window.bind("<Control-Shift-T>", self.toggle_topmost)

        self.draw_keys()

    def confirm_quit(self):
        if messagebox.askokcancel("終了確認", "アプリケーションを終了しますか？"):
            self.on_close()

    def toggle_titlebar(self, event=None):
        old_root_x = self.canvas.winfo_rootx()
        old_root_y = self.canvas.winfo_rooty()
        old_w = self.window.winfo_width()
        old_h = self.window.winfo_height()

        self.has_titlebar = not self.has_titlebar

        self.window.withdraw()
        self.window.overrideredirect(not self.has_titlebar)
        self.window.deiconify()
        self.window.update_idletasks()

        new_root_x = self.canvas.winfo_rootx()
        new_root_y = self.canvas.winfo_rooty()

        dx = old_root_x - new_root_x
        dy = old_root_y - new_root_y

        x = self.window.winfo_x() + dx
        y = self.window.winfo_y() + dy
        self.window.geometry(f"{old_w}x{old_h}+{x}+{y}")

    def toggle_topmost(self, event=None):
        self.is_topmost = not self.is_topmost
        self.window.attributes("-topmost", self.is_topmost)

    def draw_keys(self):
        self.canvas.delete("all")
        self.white_keys.clear()
        self.black_keys.clear()
        self.midi_to_x.clear()

        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)

        total_white_keys = sum(
            1
            for midi in range(self.settings.midi_start, self.settings.midi_end + 1)
            if not is_black(NOTE_NAMES[midi % 12])
        )
        white_w = width / total_white_keys
        black_w = white_w * self.settings.black_key_width_ratio / self.settings.white_key_width_ratio
        white_h = height * 0.8
        black_h = white_h * 0.67

        x = 0.0
        for midi in range(self.settings.midi_start, self.settings.midi_end + 1):
            name = NOTE_NAMES[midi % 12]
            if not is_black(name):
                rect = self.canvas.create_rectangle(
                    x,
                    0,
                    x + white_w,
                    white_h,
                    fill=self.settings.white_key_fill,
                    outline=self.settings.white_key_outline,
                )
                self.white_keys[midi] = rect
                self.midi_to_x[midi] = x
                x += white_w

        for midi in range(self.settings.midi_start, self.settings.midi_end + 1):
            if is_black(NOTE_NAMES[midi % 12]):
                prev = midi - 1
                while prev >= self.settings.midi_start and is_black(NOTE_NAMES[prev % 12]):
                    prev -= 1
                if prev in self.midi_to_x:
                    px = self.midi_to_x[prev]
                    bx = px + white_w - (black_w / 2)
                    rect = self.canvas.create_rectangle(
                        bx,
                        0,
                        bx + black_w,
                        black_h,
                        fill=self.settings.black_key_fill,
                        outline=self.settings.black_key_outline,
                    )
                    self.black_keys[midi] = rect
                    self.midi_to_x[midi] = bx

        self.pitch_text = self.canvas.create_text(
            width // 2,
            height - 20,
            text="",
            fill=self.settings.piano_text_color,
            font=self.settings.piano_font,
        )

    def on_resize(self, event):
        self.draw_keys()

    def highlight(self, midi: int, pitch: float):
        for k, rect_id in self.white_keys.items():
            if k != midi:
                self.canvas.itemconfig(rect_id, fill=self.settings.white_key_fill)
        for k, rect_id in self.black_keys.items():
            if k != midi:
                self.canvas.itemconfig(rect_id, fill=self.settings.black_key_fill)

        if midi in self.white_keys:
            self.canvas.itemconfig(self.white_keys[midi], fill=self.settings.key_highlight_color)
        elif midi in self.black_keys:
            self.canvas.itemconfig(self.black_keys[midi], fill=self.settings.key_highlight_color)

        target_freq = midi_to_freq(midi)
        self.canvas.itemconfig(
            self.pitch_text,
            text=f"{midi_to_note_name(midi)}   {pitch:.1f} Hz (Target: {target_freq:.1f} Hz)   MIDI: {midi}",
        )

    def clear(self):
        if not self.canvas.winfo_exists():
            return
        for rect_id in self.white_keys.values():
            self.canvas.itemconfig(rect_id, fill=self.settings.white_key_fill)
        for rect_id in self.black_keys.values():
            self.canvas.itemconfig(rect_id, fill=self.settings.black_key_fill)
        self.canvas.itemconfig(self.pitch_text, text="")


class PitchDetector:
    def __init__(self, settings: AppSettings, ui_queue: queue.Queue):
        self.settings = settings
        self.ui_queue = ui_queue
        self.last_midi_note = None
        self.smoothed_diff = 0.0
        self.running = False

        self.pitch_o = aubio.pitch(
            "default",
            settings.blocksize * 2,
            settings.blocksize,
            settings.samplerate,
        )
        self.pitch_o.set_unit("Hz")
        self.pitch_o.set_silence(settings.silence_threshold)

        self.stream = None

    def start(self):
        try:
            self.stream = sd.InputStream(
                channels=1,
                callback=self.callback,
                samplerate=self.settings.samplerate,
                blocksize=self.settings.blocksize,
                device=self.settings.audio_device_index,
                dtype="float32",
            )
            self.stream.start()
            self.running = True
        except Exception as e:
            raise AudioError(
                "音声入力ストリームを開始できませんでした。"
                f"\nAUDIO_DEVICE_INDEX={self.settings.audio_device_index}"
                f"\n詳細: {e}"
            ) from e

    def stop(self):
        self.running = False
        if self.stream is not None:
            try:
                self.stream.stop()
            except Exception:
                pass
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def callback(self, indata, frames, time_info, status):
        if status:
            self.ui_queue.put(("status", str(status)))

        samples = np.asarray(indata[:, 0], dtype=np.float32)
        pitch = float(self.pitch_o(samples)[0])

        if pitch <= 0:
            self.last_midi_note = None
            self.ui_queue.put(("clear", None))
            return

        midi_val = freq_to_midi(pitch)
        if midi_val is None:
            self.ui_queue.put(("clear", None))
            return

        midi_note = int(round(midi_val))
        diff = midi_val - midi_note
        self.smoothed_diff = (
            (1 - self.settings.smoothing_alpha) * self.smoothed_diff
            + self.settings.smoothing_alpha * diff
        )

        if self.settings.midi_start <= midi_note <= self.settings.midi_end:
            self.ui_queue.put(("highlight", (midi_note, pitch)))
            if abs(diff) > self.settings.pitch_diff_threshold:
                self.ui_queue.put(("tuner", self.smoothed_diff))
            self.last_midi_note = midi_note
        else:
            self.ui_queue.put(("clear", None))


class PianoTunerApp:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.root = tk.Tk()
        self.root.withdraw()
        self.ui_queue: queue.Queue = queue.Queue()
        self.closed = False

        self.piano = PianoWindow(self.root, settings, self.close)
        self.tuner = TunerWindow(self.root, settings)
        self.detector = PitchDetector(settings, self.ui_queue)

    def start(self):
        self.root.after(self.settings.ui_poll_interval_ms, self.process_ui_queue)
        self.detector.start()
        self.root.mainloop()

    def process_ui_queue(self):
        try:
            while True:
                action, payload = self.ui_queue.get_nowait()
                if action == "highlight":
                    midi_note, pitch = payload
                    self.piano.highlight(midi_note, pitch)
                elif action == "tuner":
                    self.tuner.update(payload)
                elif action == "clear":
                    self.piano.clear()
                elif action == "status":
                    # 必要ならここでログ表示やステータスバー更新に使える
                    pass
        except queue.Empty:
            pass

        if not self.closed:
            self.root.after(self.settings.ui_poll_interval_ms, self.process_ui_queue)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.detector.stop()

        for win in (self.piano.window, self.tuner.window, self.root):
            try:
                win.destroy()
            except Exception:
                pass


def print_audio_devices() -> None:
    try:
        print(sd.query_devices())
    except Exception as e:
        print(f"Failed to query audio devices: {e}", file=sys.stderr)


def main():
    try:
        settings = AppSettings.load(DEFAULT_CONFIG_PATH)
    except Exception as e:
        messagebox.showerror("設定ファイルエラー", f"設定ファイルの読み込みに失敗しました。\n{e}")
        raise

    if "--list-devices" in sys.argv:
        print_audio_devices()
        return

    app = PianoTunerApp(settings)
    try:
        app.start()
    except AudioError as e:
        messagebox.showerror("音声入力エラー", str(e))
        app.close()
    except Exception:
        app.close()
        raise


if __name__ == "__main__":
    main()
