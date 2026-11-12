"""Astronaut Voice Log (Python desktop version).

Voice-controlled recording with an estimated mood for each log.
Speech recognition runs offline on this computer with Vosk.

VOICE COMMANDS (after clicking Start Listening):
  "start log"  "stop log"  "set baseline"  "play last log"

The mood result is an estimate for a school prototype.
It is NOT a medical or psychological diagnosis.
"""

import json
import math
import os
import queue
import threading
import time
import urllib.request
import uuid
import wave
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import ttk

import numpy as np

try:
    import sounddevice as sd
    import vosk
except ImportError:
    raise SystemExit("Missing packages. Start the app with 'Start Voice Log.bat', "
                     "or run: pip install -r requirements.txt")

import mood_core as core

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
MODELS = HERE / "models"
MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
RATE = 16000
BLOCK = 4096
COMMAND_WORDS = '"start log", "stop log", "set baseline", "play last log"'
BASELINE_SENTENCE = ("Read calmly: \"Today I checked the station systems, ate breakfast, and looked out at "
                     "the Earth below. Everything is running as planned, and I am ready for the next task.\"")

COLORS = {
    "bg": "#e6ebf0", "panel": "#f7f9fb", "inset": "#dde4eb", "line": "#b6c1cc",
    "fg": "#16232f", "muted": "#566573", "primary": "#1c4f7c", "record": "#c8401a", "white": "#ffffff",
}


def gmt_text(iso_date, for_file=False):
    moment = datetime.fromisoformat(iso_date).astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%d_%H-%M-%S") if for_file else moment.strftime("%Y-%m-%d %H:%M:%S GMT")


# MARK: - Audio pipeline (microphone, speech recognition, file saving)

class AudioPipeline:
    """Audio arrives on a background thread, so shared state is guarded by a lock."""

    def __init__(self, model, events):
        self.model = model
        self.events = events
        self.lock = threading.Lock()
        self.stream = None
        self.audio = None
        self.running = False
        self.generation = 0
        self.reset = False
        self.capturing = False
        self.frames = []
        self.wav = None

    def start(self, device=None):
        recognizer = vosk.KaldiRecognizer(self.model, RATE)
        audio = queue.Queue()
        stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK,
                                device=device, callback=lambda data, *_: audio.put(data[:, 0].copy()))
        with self.lock:
            self.running = True
            self.generation += 1
            self.reset = False
        self.audio = audio
        threading.Thread(target=self._work, args=(recognizer, audio), daemon=True).start()
        try:
            stream.start()
        except Exception:
            self.stream = stream
            self.stop()
            raise
        self.stream = stream

    def stop(self):
        with self.lock:
            self.running = False
            self.generation += 1
            self.capturing = False
            self.frames = []
            if self.wav:
                self.wav.close()
                self.wav = None
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if self.audio:
            self.audio.put(None)
            self.audio = None

    def restart_recognition(self):
        """Starts a fresh transcript so earlier words are not heard again."""
        with self.lock:
            if self.running:
                self.generation += 1
                self.reset = True

    def begin_capture(self, save_to=None):
        wav = None
        if save_to:
            wav = wave.open(str(save_to), "wb")
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
        with self.lock:
            self.wav = wav
            self.frames = []
            self.capturing = True

    def end_capture(self):
        """Stops saving and closes the file. Listening continues."""
        with self.lock:
            self.capturing = False
            if self.wav:
                self.wav.close()
                self.wav = None
            captured = self.frames
            self.frames = []
        return captured

    def feed(self, recognizer, block, state):
        """Handles one block of audio. Used by the listening thread and by tests."""
        with self.lock:
            if self.reset:
                recognizer.Reset()
                self.reset = False
                state["partial"] = ""
            generation = self.generation
            capturing = self.capturing
            if capturing and self.wav:
                self.wav.writeframes(block.tobytes())

        if recognizer.AcceptWaveform(block.tobytes()):
            text = json.loads(recognizer.Result()).get("text", "")
            state["partial"] = ""
            self.events.put(("speech", generation, text, True))
        else:
            text = json.loads(recognizer.PartialResult()).get("partial", "")
            if text and text != state["partial"]:
                state["partial"] = text
                self.events.put(("speech", generation, text, False))

        if capturing:
            samples = block.astype(np.float64) / 32768
            frame = {
                "db": 20 * math.log10(max(core.rms(samples), 0.000001)),
                "pitch": core.detect_pitch(samples, RATE),
                "seconds": len(samples) / RATE,
            }
            with self.lock:
                if self.capturing:
                    self.frames.append(frame)

    def _work(self, recognizer, audio):
        state = {"partial": ""}
        while True:
            block = audio.get()
            if block is None:
                return
            with self.lock:
                if not self.running:
                    return
            try:
                self.feed(recognizer, block, state)
            except Exception as error:
                self.events.put(("error", f"Listening stopped: {error}"))
                return


# MARK: - App

class VoiceLogApp:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.model = None
        self.pipeline = None

        self.mode = "idle"
        self.status = "Getting the speech model ready..."
        self.live_text = ""
        self.committed_text = ""
        self.partial_text = ""
        self.current_file = ""
        self.calibration_token = 0
        self.playback_token = 0
        self.resume_after_playback = False

        DATA.mkdir(parents=True, exist_ok=True)
        self.logs = self._load_json(DATA / "logs.json", [])
        self.baseline = self._load_json(DATA / "baseline.json", None)

        self.devices = self._input_devices()
        self._build_ui()
        self.render()
        self.render_logs()

        threading.Thread(target=self.prepare_model, daemon=True).start()
        root.after(50, self.pump)
        root.protocol("WM_DELETE_WINDOW", self.close)

    # MARK: Files

    @staticmethod
    def _load_json(path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def save_logs(self):
        (DATA / "logs.json").write_text(json.dumps(self.logs, indent=2), encoding="utf-8")

    # MARK: Speech model

    def prepare_model(self):
        try:
            path = MODELS / MODEL_NAME
            if not (path / "conf").is_dir():
                MODELS.mkdir(parents=True, exist_ok=True)
                partial = MODELS / f"{MODEL_NAME}.zip.part"
                last = [-1]

                def progress(blocks, size, total):
                    percent = min(100, blocks * size * 100 // total) if total > 0 else 0
                    if percent != last[0]:
                        last[0] = percent
                        self.events.put(("model_status",
                                         f"First run: downloading the speech model ({percent}% of about 40 MB)"))

                urllib.request.urlretrieve(MODEL_URL, partial, progress)
                self.events.put(("model_status", "Unpacking the speech model..."))
                with zipfile.ZipFile(partial) as archive:
                    archive.extractall(MODELS)
                partial.unlink()

            self.events.put(("model_status", "Loading the speech model..."))
            vosk.SetLogLevel(-1)
            self.events.put(("model_ready", vosk.Model(str(path))))
        except Exception as error:
            self.events.put(("model_error", f"The speech model could not be loaded ({error}). "
                                            "Check your internet connection, then restart the app."))

    def pump(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "speech":
                    _, generation, text, ended = event
                    if self.pipeline and generation == self.pipeline.generation:
                        self.handle_speech(text, ended)
                elif kind == "model_status" and self.model is None:
                    self.set_status(event[1])
                elif kind == "model_ready":
                    self.model = event[1]
                    self.pipeline = AudioPipeline(self.model, self.events)
                    self.set_status("Click Start Listening")
                elif kind == "model_error":
                    self.set_status(event[1])
                elif kind == "error":
                    self.stop_everything(event[1])
        except queue.Empty:
            pass
        self.root.after(50, self.pump)

    # MARK: Listening

    def set_status(self, text):
        self.status = text
        self.render()

    def start_listening(self):
        if self.mode != "idle":
            return
        if self.pipeline is None:
            self.set_status("The speech model is still getting ready. Try again in a moment.")
            return
        try:
            self.pipeline.start(self.selected_device())
        except Exception as error:
            self.set_status(f"The microphone could not start ({error}). "
                            "Check it is plugged in, or pick another one from the list.")
            return
        self.clear_speech()
        self.mode = "listening"
        self.set_status('Listening. Say "start log".')

    def stop_listening(self):
        if self.mode != "listening":
            return
        self.pipeline.stop()
        self.clear_speech()
        self.mode = "idle"
        self.set_status("Click Start Listening")

    def stop_everything(self, message):
        if self.pipeline:
            self.pipeline.stop()
        sd.stop()
        self.clear_speech()
        self.mode = "idle"
        self.set_status(message)

    def main_button_tapped(self):
        if self.mode == "idle":
            self.start_listening()
            if self.mode == "listening":
                self.start_log()
        elif self.mode == "listening":
            self.start_log()
        elif self.mode == "recording":
            self.stop_log(self.full_text())
        elif self.mode == "calibrating":
            self.finish_baseline()
        elif self.mode == "playing":
            self.finish_playback()

    def full_text(self):
        return f"{self.committed_text} {self.partial_text}".strip()

    def clear_speech(self):
        self.committed_text = ""
        self.partial_text = ""
        self.live_text = ""

    def handle_speech(self, text, ended):
        if self.mode not in ("listening", "recording", "calibrating"):
            return
        if text:
            self.partial_text = text
        spoken = " " + " ".join(core.words(text)) + " "

        if self.mode == "listening":
            self.live_text = self.partial_text
            if any(c in spoken for c in (" start log ", " begin log ", " start blog ")):
                self.start_log()
                return
            if " set baseline " in spoken:
                self.start_baseline()
                return
            if " play last log " in spoken and self.logs:
                self.play(self.logs[0])
                return
            if ended:
                self.clear_speech()
        elif self.mode == "recording":
            self.live_text = self.full_text()
            for command in ("stop log", "end log", "stop blog"):
                if f" {command} " in spoken:
                    self.stop_log(self.text_before(command))
                    return
            if ended:
                self.committed_text = self.full_text()
                self.partial_text = ""
        else:
            self.live_text = self.full_text()
            if ended:
                self.committed_text = self.full_text()
                self.partial_text = ""
        self.render()

    def text_before(self, command):
        text = self.full_text()
        index = text.lower().rfind(command)
        return text if index < 0 else text[:index].strip()

    # MARK: Logs

    def start_log(self):
        if self.mode != "listening":
            return
        now = datetime.now(timezone.utc).isoformat()
        name = f"Log_{gmt_text(now, for_file=True)}_GMT.wav"
        try:
            self.pipeline.begin_capture(DATA / name)
        except OSError as error:
            self.set_status(f"Error: {error}")
            return
        self.current_file = name
        self.clear_speech()
        self.pipeline.restart_recognition()
        self.mode = "recording"
        self.set_status('Recording. Say "stop log" when finished.')

    def stop_log(self, spoken_text):
        if self.mode != "recording":
            return
        frames = self.pipeline.end_capture()
        features = core.make_features(frames, len(core.words(spoken_text)))
        reading = core.analyze(spoken_text, features, self.baseline)

        self.logs.insert(0, {
            "id": str(uuid.uuid4()),
            "date": datetime.now(timezone.utc).isoformat(),
            "fileName": self.current_file,
            "transcript": spoken_text,
            "reading": reading,
        })
        try:
            self.save_logs()
            message = 'Saved. Listening for "start log".'
        except OSError:
            message = "This log could not be saved to disk."

        self.clear_speech()
        self.pipeline.restart_recognition()
        self.mode = "listening"
        self.set_status(message)
        self.render_logs(select_first=True)

    def delete_selected(self):
        log = self.selected_log()
        if not log:
            return
        try:
            (DATA / log["fileName"]).unlink(missing_ok=True)
        except OSError:
            pass
        self.logs.remove(log)
        self.save_logs()
        self.render_logs()
        self.render()

    # MARK: Baseline

    def start_baseline(self):
        if self.mode != "listening":
            return
        self.pipeline.begin_capture(None)
        self.clear_speech()
        self.pipeline.restart_recognition()
        self.mode = "calibrating"
        self.set_status("Read the sentence calmly for 20 seconds.")

        self.calibration_token += 1
        token = self.calibration_token
        self.root.after(20000, lambda: token == self.calibration_token and self.finish_baseline())

    def finish_baseline(self):
        if self.mode != "calibrating":
            return
        self.calibration_token += 1

        frames = self.pipeline.end_capture()
        features = core.make_features(frames, len(core.words(self.full_text())))

        self.clear_speech()
        self.pipeline.restart_recognition()
        self.mode = "listening"

        if features["speechSeconds"] < 5 or features["pitchHz"] <= 0:
            self.set_status("Not enough clear speech. Please try the baseline again.")
            return

        self.baseline = features
        (DATA / "baseline.json").write_text(json.dumps(features, indent=2), encoding="utf-8")
        self.set_status("Baseline saved. Listening.")

    # MARK: Playback

    def play(self, log):
        if self.mode not in ("idle", "listening", "playing"):
            self.set_status("Finish the current recording first.")
            return

        # Listening pauses so the app does not hear its own playback.
        if self.mode == "listening":
            self.resume_after_playback = True
            self.pipeline.stop()
            self.clear_speech()
        elif self.mode == "idle":
            self.resume_after_playback = False

        try:
            with wave.open(str(DATA / log["fileName"]), "rb") as recording:
                rate = recording.getframerate()
                data = np.frombuffer(recording.readframes(recording.getnframes()), dtype=np.int16)
            sd.stop()
            sd.play(data, rate)
        except Exception as error:
            self.mode = "playing"
            self.finish_playback(f"Could not play: {error}")
            return

        self.mode = "playing"
        self.set_status("Playing log")
        self.playback_token += 1
        token = self.playback_token
        wait = int((len(data) / rate + 0.3) * 1000)
        self.root.after(wait, lambda: token == self.playback_token and self.finish_playback())

    def finish_playback(self, message=None):
        if self.mode != "playing":
            return
        self.playback_token += 1
        sd.stop()
        self.mode = "idle"
        self.set_status(message or "Click Start Listening")
        if self.resume_after_playback:
            self.resume_after_playback = False
            self.start_listening()

    # MARK: Microphones

    @staticmethod
    def _input_devices():
        devices = []
        try:
            default = sd.default.device[0]
            hostapi = sd.query_hostapis(sd.query_devices(default)["hostapi"])["name"] if default >= 0 else None
            for index, info in enumerate(sd.query_devices()):
                if info["max_input_channels"] < 1:
                    continue
                if hostapi and sd.query_hostapis(info["hostapi"])["name"] != hostapi:
                    continue
                label = info["name"] + (" (Windows default)" if index == default else "")
                devices.append((label, index))
        except Exception:
            pass
        return devices

    def selected_device(self):
        label = self.device_choice.get()
        for name, index in self.devices:
            if name == label:
                return index
        return None

    # MARK: Screen

    def _build_ui(self):
        c = COLORS
        root = self.root
        root.title("Astronaut Voice Log")
        root.configure(bg=c["bg"])
        root.geometry("1120x760")
        root.minsize(900, 640)
        root.columnconfigure(0, weight=1, uniform="half")
        root.columnconfigure(1, weight=1, uniform="half")
        root.rowconfigure(0, weight=1)

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Treeview", rowheight=34, font=("Segoe UI", 11), background=c["panel"],
                        fieldbackground=c["panel"], foreground=c["fg"], bordercolor=c["line"])
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10), background=c["inset"],
                        foreground=c["fg"])
        style.map("Treeview", background=[("selected", c["primary"])], foreground=[("selected", c["white"])])
        style.configure("TCombobox", padding=4)

        left = tk.Frame(root, bg=c["bg"], padx=24, pady=20)
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        wrap = 470

        tk.Label(left, text="CREW MOOD ESTIMATE", font=("Consolas", 9), fg=c["muted"], bg=c["bg"]) \
            .grid(row=0, column=0, sticky="w")
        tk.Label(left, text="ASTRONAUT VOICE LOG", font=("Bahnschrift SemiBold Condensed", 34),
                 fg=c["fg"], bg=c["bg"]).grid(row=1, column=0, sticky="w")
        self.status_label = tk.Label(left, font=("Segoe UI Semibold", 13), fg=c["fg"], bg=c["bg"],
                                     wraplength=wrap, justify="left", anchor="w")
        self.status_label.grid(row=2, column=0, sticky="we")
        tk.Label(left, text="Speech is turned into text on this computer. Nothing is sent online.",
                 font=("Segoe UI", 9), fg=c["muted"], bg=c["bg"], wraplength=wrap, justify="left") \
            .grid(row=3, column=0, sticky="w", pady=(0, 12))

        self.main_button = tk.Button(left, font=("Bahnschrift SemiBold Condensed", 34), fg=c["white"],
                                     activeforeground=c["white"], relief="flat", bd=0, height=1, pady=18,
                                     cursor="hand2", command=self.main_button_tapped)
        self.main_button.grid(row=4, column=0, sticky="we")

        controls = tk.Frame(left, bg=c["bg"])
        controls.grid(row=5, column=0, sticky="we", pady=(12, 4))
        controls.columnconfigure(1, weight=1)
        button_style = dict(font=("Segoe UI Semibold", 10), bg=c["panel"], fg=c["fg"], relief="solid", bd=1,
                            padx=12, pady=5, cursor="hand2", highlightthickness=0)
        self.listen_button = tk.Button(controls, **button_style)
        self.listen_button.grid(row=0, column=0, sticky="w")
        self.baseline_button = tk.Button(controls, command=self.start_baseline, **button_style)
        self.baseline_button.grid(row=0, column=2, sticky="e")

        microphone = tk.Frame(left, bg=c["bg"])
        microphone.grid(row=6, column=0, sticky="we", pady=(4, 4))
        microphone.columnconfigure(1, weight=1)
        tk.Label(microphone, text="Microphone", font=("Segoe UI", 10), fg=c["muted"], bg=c["bg"]) \
            .grid(row=0, column=0, sticky="w", padx=(0, 8))
        labels = [name for name, _ in self.devices]
        self.device_choice = tk.StringVar(value=next((n for n in labels if n.endswith("(Windows default)")),
                                                     labels[0] if labels else "No microphone found"))
        self.device_menu = ttk.Combobox(microphone, textvariable=self.device_choice, values=labels,
                                        state="readonly")
        self.device_menu.grid(row=0, column=1, sticky="we")

        self.hint_label = tk.Label(left, font=("Consolas", 9), fg=c["muted"], bg=c["bg"],
                                   wraplength=wrap, justify="left", anchor="w")
        self.hint_label.grid(row=7, column=0, sticky="we", pady=(6, 8))

        self.live_label = tk.Label(left, font=("Segoe UI", 12), bg=c["inset"], wraplength=wrap - 24,
                                   justify="left", anchor="nw", padx=12, pady=10, height=3)
        self.live_label.grid(row=8, column=0, sticky="we")

        self.card = tk.Frame(left, bg=c["panel"], padx=16, pady=12, highlightthickness=0)
        self.card.grid(row=9, column=0, sticky="we", pady=(16, 0))
        self.card.columnconfigure(0, weight=1)
        tk.Label(self.card, text="LATEST ESTIMATED MOOD", font=("Consolas", 9), fg=c["muted"], bg=c["panel"]) \
            .grid(row=0, column=0, sticky="w")
        self.mood_label = tk.Label(self.card, font=("Segoe UI Semibold", 20), bg=c["panel"],
                                   wraplength=wrap - 40, justify="left", anchor="w")
        self.mood_label.grid(row=1, column=0, sticky="we")
        self.confidence_label = tk.Label(self.card, font=("Segoe UI", 11), fg=c["fg"], bg=c["panel"])
        self.confidence_label.grid(row=2, column=0, sticky="w")
        self.bars = tk.Canvas(self.card, height=84, bg=c["panel"], highlightthickness=0)
        self.bars.grid(row=3, column=0, sticky="we", pady=(6, 4))
        self.support_label = tk.Label(self.card, font=("Segoe UI", 10), fg=c["fg"], bg=c["panel"],
                                      wraplength=wrap - 40, justify="left",
                                      text="If this feeling keeps coming back, talk with the flight surgeon "
                                           "or someone you trust.")
        self.support_label.grid(row=4, column=0, sticky="w")
        tk.Label(self.card, text="Estimate only. Not a medical diagnosis.", font=("Segoe UI", 9),
                 fg=c["muted"], bg=c["panel"]).grid(row=5, column=0, sticky="w")

        right = tk.Frame(root, bg=c["panel"], padx=16, pady=16, highlightbackground=c["line"],
                         highlightthickness=1)
        right.grid(row=0, column=1, sticky="nsew", padx=(0, 24), pady=20)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)

        head = tk.Frame(right, bg=c["panel"])
        head.grid(row=0, column=0, sticky="we")
        head.columnconfigure(0, weight=1)
        tk.Label(head, text="MISSION LOGS", font=("Bahnschrift SemiBold Condensed", 20), fg=c["fg"],
                 bg=c["panel"]).grid(row=0, column=0, sticky="w")
        self.count_label = tk.Label(head, font=("Consolas", 12), fg=c["fg"], bg=c["panel"])
        self.count_label.grid(row=0, column=1, sticky="e")
        tk.Frame(right, height=2, bg=c["fg"]).grid(row=1, column=0, sticky="we", pady=(4, 8))

        table = tk.Frame(right, bg=c["panel"])
        table.grid(row=2, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=("mood", "date"), show="headings", selectmode="browse")
        self.tree.heading("mood", text="Mood", anchor="w")
        self.tree.heading("date", text="Date", anchor="w")
        self.tree.column("mood", width=260, anchor="w")
        self.tree.column("date", width=190, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.render_details())

        self.empty_label = tk.Label(right, text='No logs yet. Say "start log" or click START LOG to record '
                                                'the first one.', font=("Segoe UI", 10), fg=c["muted"],
                                    bg=c["panel"], wraplength=420, justify="left")

        self.details = tk.Text(right, height=9, wrap="word", font=("Segoe UI", 10), bg=c["bg"], fg=c["fg"],
                               relief="flat", padx=10, pady=8, state="disabled")
        self.details.grid(row=4, column=0, sticky="we", pady=(10, 6))
        self.details.tag_configure("muted", foreground=c["muted"])

        actions = tk.Frame(right, bg=c["panel"])
        actions.grid(row=5, column=0, sticky="we")
        self.play_button = tk.Button(actions, text="Play", command=lambda: self.selected_log()
                                     and self.play(self.selected_log()), **button_style)
        self.play_button.pack(side="left")
        tk.Button(actions, text="Open recordings folder", command=lambda: os.startfile(DATA),
                  **button_style).pack(side="left", padx=8)
        self.delete_button = tk.Button(actions, text="Delete", command=self.delete_selected,
                                       **dict(button_style, fg="#c5302b"))
        self.delete_button.pack(side="right")

    def render(self):
        c = COLORS
        mode = self.mode
        titles = {"idle": "START LOG", "listening": "START LOG", "recording": "STOP LOG",
                  "calibrating": "FINISH BASELINE", "playing": "STOP PLAYBACK"}
        color = c["record"] if mode == "recording" else c["primary"]
        self.main_button.configure(text=titles[mode], bg=color, activebackground=color)
        self.status_label.configure(text=self.status)

        if mode == "idle":
            self.listen_button.configure(text="Start Listening", command=self.start_listening)
            self.listen_button.grid()
        elif mode == "listening":
            self.listen_button.configure(text="Stop Listening", command=self.stop_listening)
            self.listen_button.grid()
        else:
            self.listen_button.grid_remove()

        self.baseline_button.configure(text="Redo Calm Baseline" if self.baseline else "Set Calm Baseline",
                                       state="normal" if mode == "listening" else "disabled")
        self.device_menu.configure(state="readonly" if mode == "idle" else "disabled")

        if mode == "calibrating":
            self.hint_label.configure(text=BASELINE_SENTENCE, font=("Segoe UI", 13), fg=c["fg"])
        else:
            self.hint_label.configure(text=f"Say: {COMMAND_WORDS}", font=("Consolas", 9), fg=c["muted"])

        self.live_label.configure(text=self.live_text or "Words you say appear here",
                                  fg=c["fg"] if self.live_text else c["muted"])
        self.render_mood_card()

    def render_mood_card(self):
        if not self.logs:
            self.card.grid_remove()
            return
        self.card.grid()
        reading = self.logs[0]["reading"]
        label, emoji, color = core.MOODS[reading["mood"]]
        self.card.configure(highlightbackground=color, highlightthickness=3)
        self.mood_label.configure(text=f"{emoji} {label}", fg=color)
        self.confidence_label.configure(text=f"Confidence: {reading['confidence']}")
        if reading["mood"] in ("lowMood", "sad"):
            self.support_label.grid()
        else:
            self.support_label.grid_remove()

        canvas = self.bars
        canvas.delete("all")
        canvas.update_idletasks()
        width = max(canvas.winfo_width(), 300)
        for row, score in enumerate(reading["scores"][:3]):
            name, row_emoji, row_color = core.MOODS[score["mood"]]
            y = row * 28 + 4
            percent = round(score["share"] * 100)
            canvas.create_text(0, y + 9, text=f"{row_emoji} {name.split(' (')[0]}", anchor="w",
                               font=("Segoe UI", 10), fill=COLORS["fg"])
            left, right = 150, width - 50
            canvas.create_rectangle(left, y + 4, right, y + 14, fill=COLORS["inset"], width=0)
            canvas.create_rectangle(left, y + 4, left + (right - left) * score["share"], y + 14,
                                    fill=row_color, width=0)
            canvas.create_text(width, y + 9, text=f"{percent}%", anchor="e", font=("Consolas", 10),
                               fill=COLORS["fg"])

    def render_logs(self, select_first=False):
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for log in self.logs:
            label, emoji, _ = core.MOODS[log["reading"]["mood"]]
            self.tree.insert("", "end", iid=log["id"], values=(f"{emoji}  {label}", gmt_text(log["date"])))
        self.count_label.configure(text=f"{len(self.logs):03d}")

        if self.logs:
            self.empty_label.grid_remove()
        else:
            self.empty_label.grid(row=3, column=0, sticky="we", pady=8)

        if select_first and self.logs:
            self.tree.selection_set(self.logs[0]["id"])
        elif selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        self.render_details()

    def selected_log(self):
        selection = self.tree.selection()
        return next((log for log in self.logs if selection and log["id"] == selection[0]), None)

    def render_details(self):
        log = self.selected_log()
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        if log:
            reading = log["reading"]
            self.details.insert("end", (log["transcript"] or "(No words heard)") + "\n\n")
            self.details.insert("end", f"Confidence: {reading['confidence']}\n")
            for reason in reading["reasons"]:
                self.details.insert("end", f"• {reason}\n", "muted")
        else:
            self.details.insert("end", "Select a log to see what was said and why the mood was chosen.", "muted")
        self.details.configure(state="disabled")
        state = "normal" if log else "disabled"
        self.play_button.configure(state=state)
        self.delete_button.configure(state=state)

    def close(self):
        if self.pipeline:
            self.pipeline.stop()
        sd.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    VoiceLogApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
