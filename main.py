import tkinter as tk
import math
import numpy as np
import sounddevice as sd
import aubio
import tkinter.messagebox


# --- 設定クラス（パラメータを集中管理） ---
class Settings:
    BLOCKSIZE = 1024
    SILENCE_THRESHOLD = -40
    KEY_HIGHLIGHT_COLOR = "red"
    MIDI_START = 21
    MIDI_END = 108
    WHITE_KEY_WIDTH = 20
    BLACK_KEY_WIDTH = 14
    WHITE_KEY_HEIGHT = 120
    BLACK_KEY_HEIGHT = 80
    TUNER_WIDTH = 300
    TUNER_HEIGHT = 180
    SMOOTHING_ALPHA = 0.2
    PITCH_DIFF_THRESHOLD = 0.01
    AUDIO_DEVICE_INDEX = 1  # MOTU M4 In 1-2
    SAMPLERATE = 44100
    BUFSIZE = 1024
    TUNER_CENT_LIMIT = 50

settings = Settings()

# --- MIDI / 周波数変換ユーティリティ ---
NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F','F#', 'G', 'G#', 'A', 'A#', 'B']
def is_black(note): return "#" in note
def midi_to_note_name(midi): return NOTE_NAMES[midi % 12] + str(midi // 12 - 1)
def freq2midi(freq): return 69 + 12 * math.log2(freq / 440.0) if freq > 0 else None
def midi_to_freq(midi): return 440.0 * (2 ** ((midi - 69) / 12))

# --- タコメータウィンドウ ---
class TunerWindow:
    def __init__(self):
        self.window = tk.Toplevel()
        self.window.title("Tuner")
        self.window.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.window, width=settings.TUNER_WIDTH, height=settings.TUNER_HEIGHT, bg="black")
        self.canvas.pack()
        self.cx, self.cy, self.r = 150, 150, 100
        self.canvas.create_arc(50, 50, 250, 250, start=180, extent=180, outline="gray", width=2, style='arc')
        for i in range(-50, 51, 10):
            angle = 180 + (i + 50) * 180 / 100
            x1 = self.cx + self.r * math.cos(math.radians(angle))
            y1 = self.cy + self.r * math.sin(math.radians(angle))
            x2 = self.cx + (self.r - 10) * math.cos(math.radians(angle))
            y2 = self.cy + (self.r - 10) * math.sin(math.radians(angle))
            self.canvas.create_line(x1, y1, x2, y2, fill="gray")
        self.needle = self.canvas.create_line(self.cx, self.cy, self.cx, self.cy - self.r, fill="lime", width=3)
        self.text = self.canvas.create_text(150, 20, text="", fill="white", font=("Arial", 12))

    def update(self, diff):
        diff = max(min(diff, 0.5), -0.5)
        cent = diff * 100
        angle = 180 + ((cent + 50) / 100) * 180
        x = self.cx + self.r * math.cos(math.radians(angle))
        y = self.cy + self.r * math.sin(math.radians(angle))
        self.canvas.coords(self.needle, self.cx, self.cy, x, y)
        # 針の色を設定
        if abs(cent) < 10:
            color = "lime"
            status = "✓ In Tune"
        elif cent < -10:
            color = "blue"
            status = f"{int(cent)} cents flat"
        else:  # cent > 5
            color = "red"
            status = f"{int(cent)} cents sharp"
        self.canvas.itemconfig(self.needle, fill=color)
        self.canvas.itemconfig(self.text, text=status)

# --- 鍵盤ウィンドウ ---
class PianoWindow:
    def __init__(self):
        self.window = tk.Toplevel()
        self.window.title("Keyboard")
        self.window.overrideredirect(False)
        self.window.geometry("1200x200+100+100")
        self.canvas_frame = tk.Frame(self.window, borderwidth=0, highlightthickness=0)
        self.canvas_frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(self.canvas_frame, bg="gray", borderwidth=0, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.on_resize)
        self.white_keys = {}
        self.black_keys = {}
        self.midi_to_x = {}
        self.pitch_text = None
        self.draw_keys()
        self.window.protocol("WM_DELETE_WINDOW", self.confirm_quit)
        self.has_titlebar = True  # タイトルバーの状態を保持
        self.window.bind("<Control-t>", self.toggle_titlebar)
        self.is_topmost = True
        self.window.attributes("-topmost", self.is_topmost)
        self.window.bind("<Control-Shift-T>", self.toggle_topmost)

    def confirm_quit(self):
        if tk.messagebox.askokcancel("終了確認", "アプリケーションを終了しますか？"):
            self.window.destroy()
            root.destroy()

    def toggle_titlebar(self, event=None):
        # 切り替え前のクライアント領域の左上位置を保存
        old_root_x = self.canvas.winfo_rootx()
        old_root_y = self.canvas.winfo_rooty()
        old_w = self.window.winfo_width()
        old_h = self.window.winfo_height()

        self.has_titlebar = not self.has_titlebar

        # 一度非表示にしてから切り替えると安定しやすい
        self.window.withdraw()
        self.window.overrideredirect(not self.has_titlebar)
        self.window.deiconify()
        self.window.update_idletasks()

        # 切り替え後のクライアント領域位置との差分を補正
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
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        total_white_keys = sum(1 for midi in range(settings.MIDI_START, settings.MIDI_END + 1)
                               if not is_black(NOTE_NAMES[midi % 12]))
        white_w = width / total_white_keys
        black_w = white_w * settings.BLACK_KEY_WIDTH / settings.WHITE_KEY_WIDTH
        white_h = height * 0.8
        black_h = white_h * 0.67

        x = 0
        for midi in range(settings.MIDI_START, settings.MIDI_END + 1):
            name = NOTE_NAMES[midi % 12]
            if not is_black(name):
                rect = self.canvas.create_rectangle(x, 0, x + white_w, white_h, fill="white", outline="black")
                self.white_keys[midi] = rect
                self.midi_to_x[midi] = x
                x += white_w

        for midi in range(settings.MIDI_START, settings.MIDI_END + 1):
            if is_black(NOTE_NAMES[midi % 12]):
                prev = midi - 1
                while prev >= settings.MIDI_START and is_black(NOTE_NAMES[prev % 12]):
                    prev -= 1
                if prev in self.midi_to_x:
                    px = self.midi_to_x[prev]
                    bx = px + white_w - (black_w // 2)
                    rect = self.canvas.create_rectangle(bx, 0, bx + black_w, black_h, fill="black", outline="black")
                    self.black_keys[midi] = rect
                    self.midi_to_x[midi] = bx

        self.pitch_text = self.canvas.create_text(width // 2, height - 20, text="", fill="white", font=("Arial", 14))

    def on_resize(self, event):
        self.draw_keys()

    def highlight(self, midi, pitch):
        for k, r in self.white_keys.items():
            if k != midi:
                self.canvas.itemconfig(r, fill="white")
        for k, r in self.black_keys.items():
            if k != midi:
                self.canvas.itemconfig(r, fill="black")
        if midi in self.white_keys:
            self.canvas.itemconfig(self.white_keys[midi], fill=settings.KEY_HIGHLIGHT_COLOR)
        elif midi in self.black_keys:
            self.canvas.itemconfig(self.black_keys[midi], fill=settings.KEY_HIGHLIGHT_COLOR)
        target_freq = midi_to_freq(midi)
        self.canvas.itemconfig(self.pitch_text, text=f"{midi_to_note_name(midi)}   {pitch:.1f} Hz (Target: {target_freq:.1f} Hz)   MIDI: {midi}")

    def clear(self):
        if not self.canvas.winfo_exists():
            return
        for r in list(self.white_keys.values()):
            self.canvas.itemconfig(r, fill="white")
        for r in list(self.black_keys.values()):
            self.canvas.itemconfig(r, fill="black")
        self.canvas.itemconfig(self.pitch_text, text="")


# --- ピッチ検出 ---
class PitchDetector:
    def __init__(self, piano_gui, tuner_gui):
        self.piano_gui = piano_gui
        self.tuner_gui = tuner_gui
        self.last_note = None
        self.last_midi_note = None
        self.last_midi_note = None
        self.smoothed_diff = 0.0
        self.pitch_o = aubio.pitch("default", settings.BLOCKSIZE * 2, settings.BLOCKSIZE, settings.SAMPLERATE)
        self.pitch_o.set_unit("Hz")
        self.pitch_o.set_silence(settings.SILENCE_THRESHOLD)
        # Reduce blocksize to increase responsiveness
        self.stream = sd.InputStream(
            channels=1, callback=self.callback,
            samplerate=settings.SAMPLERATE, blocksize=settings.BLOCKSIZE,
            device=settings.AUDIO_DEVICE_INDEX
        )
        self.stream.start()

    def callback(self, indata, frames, time, status):
        samples = np.float32(indata[:, 0])
        pitch = self.pitch_o(samples)[0]
        if pitch > 0:
            midi_val = freq2midi(pitch)
            if midi_val:
                midi_note = int(round(midi_val))
                diff = midi_val - midi_note
                self.smoothed_diff = (1 - settings.SMOOTHING_ALPHA) * self.smoothed_diff + settings.SMOOTHING_ALPHA * diff

                if settings.MIDI_START <= midi_note <= settings.MIDI_END:
                    if midi_note != self.last_midi_note or self.last_note is None:
                        self.piano_gui.highlight(midi_note, pitch)
                        self.last_midi_note = midi_note
                    if abs(diff) > settings.PITCH_DIFF_THRESHOLD:
                        self.tuner_gui.update(self.smoothed_diff)
        else:
            self.last_note = None
            self.piano_gui.clear()

# --- メイン実行 ---
if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    piano = PianoWindow()
    tuner = TunerWindow()
    detector = PitchDetector(piano, tuner)
    tk.mainloop()

