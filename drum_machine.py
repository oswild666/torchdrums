import sys
import numpy as np
import sounddevice as sd
from PySide6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
                               QSlider, QLabel, QPushButton, QGroupBox, QFrame, QSpinBox,
                               QGraphicsView, QGraphicsScene, QGraphicsPathItem, QComboBox,
                               QScrollArea)
from PySide6.QtCore import QThread, Signal, Qt, QPointF
from PySide6.QtGui import QFontDatabase, QPen, QPainterPath, QColor
import threading
from scipy import signal
import math

# --- Constants ---
SAMPLE_RATE = 44100
BUFFER_SIZE = 512
BPM = 120
STEPS_PER_BEAT = 4
MAX_STEPS = 32

# --- Synthesis Core ---

class Instrument:
    def __init__(self, sample_rate, name="Instrument"):
        self.sample_rate = sample_rate
        self.name = name
        self.params = {}
        self.param_defs = {}
        self.mod_data = {}

    def initialize_mod_data(self):
        for p_name in self.param_defs:
            self.mod_data[p_name] = {
                'curve': np.full(MAX_STEPS, 0.5),
                'depth': 0.0,
                'polarity': 1, # 1: unipolar, 2: bipolar
                'sync': 'Global' # 'Global' or 'Track'
            }

    def play(self, temp_params=None):
        original_params = self.params
        if temp_params:
            self.params = temp_params

        sound = self._play_internal()

        self.params = original_params
        return sound

    def _play_internal(self):
        raise NotImplementedError

    def set_param(self, name, value):
        self.params[name] = value

# --- Filters ---
def butter_bandpass(lowcut, highcut, fs, order=5):
    nyq = 0.5 * fs; low = lowcut / nyq; high = highcut / nyq
    b, a = signal.butter(order, [low, high], btype='band'); return b, a

def butter_highpass(cutoff, fs, order=5):
    nyq = 0.5 * fs; normal_cutoff = cutoff / nyq
    b, a = signal.butter(order, normal_cutoff, btype='high', analog=False); return b, a

def resonant_lpf_coeffs(f0, Q, fs):
    if f0 <= 1 or Q <= 0: return [1, 0, 0], [1, 0, 0]
    w0 = 2 * math.pi * f0 / fs; alpha = math.sin(w0) / (2 * Q)
    b0 = (1 - math.cos(w0)) / 2; b1 = 1 - math.cos(w0); b2 = (1 - math.cos(w0)) / 2
    a0 = 1 + alpha; a1 = -2 * math.cos(w0); a2 = 1 - alpha
    return [b0/a0, b1/a0, b2/a0], [1, a1/a0, a2/a0]

def apply_filter(data, b, a):
    return signal.lfilter(b, a, data)

# --- Instruments ---

class KickFm(Instrument):
    def __init__(self, sample_rate):
        super().__init__(sample_rate, "Kick (FM)")
        self.params = {'start_freq': 150.0, 'end_freq': 40.0, 'decay': 0.25, 'mod_index': 2.0}
        self.param_defs = {
            'start_freq': {'min': 20, 'max': 500, 'init': 150},
            'end_freq': {'min': 20, 'max': 200, 'init': 40},
            'decay': {'min': 10, 'max': 1000, 'init': 250, 'scale': 1000.0},
            'mod_index': {'min': 0, 'max': 100, 'init': 20, 'scale': 10.0},
        }
        self.initialize_mod_data()

    def _play_internal(self):
        decay_s = self.params['decay']
        if decay_s <= 0: return np.array([], dtype=np.float32)
        length_samples = int(decay_s * self.sample_rate)
        t = np.linspace(0, decay_s, length_samples, endpoint=False)
        amp_env = np.exp(-5 * t / decay_s)
        pitch_env = np.linspace(self.params['start_freq'], self.params['end_freq'], length_samples)
        modulator_freq = pitch_env * self.params['mod_index']
        modulator = np.sin(2 * np.pi * modulator_freq * t)
        carrier_phase = 2 * np.pi * pitch_env * t + modulator
        carrier = np.sin(carrier_phase)
        return (carrier * amp_env).astype(np.float32)

class KickReso(Instrument):
    def __init__(self, sample_rate):
        super().__init__(sample_rate, "Kick (Reso)")
        self.params = {'decay': 0.2, 'start_freq': 120, 'end_freq': 40, 'Q': 5.0}
        self.param_defs = {
            'decay': {'min': 10, 'max': 1000, 'init': 200, 'scale': 1000.0},
            'start_freq': {'min': 20, 'max': 300, 'init': 120},
            'end_freq': {'min': 20, 'max': 100, 'init': 40},
            'Q': {'min': 1, 'max': 100, 'init': 50, 'scale': 10.0},
        }
        self.initialize_mod_data()

    def _play_internal(self):
        decay_s = self.params['decay']
        length_samples = int(decay_s * self.sample_rate)
        impulse = np.zeros(length_samples); impulse[0] = 1.0
        freq_env = np.linspace(self.params['start_freq'], self.params['end_freq'], length_samples)
        output = np.zeros(length_samples)
        z = np.zeros(2)
        q = self.params['Q']
        for i in range(length_samples):
            b, a = resonant_lpf_coeffs(freq_env[i], q, self.sample_rate)
            output[i], z = signal.lfilter(b, a, [impulse[i]], zi=z)
        env = np.exp(-8 * np.linspace(0, 1, length_samples))
        return (output * env).astype(np.float32)

class Snare(Instrument):
    def __init__(self, sample_rate):
        super().__init__(sample_rate, "Snare")
        self.params = {'decay': 0.2, 'tone': 0.5, 'noise_level': 0.8, 'body_freq': 200}
        self.param_defs = {
            'decay': {'min': 10, 'max': 500, 'init': 200, 'scale': 1000.0},
            'tone': {'min': 0, 'max': 100, 'init': 50, 'scale': 100.0},
            'noise_level': {'min': 0, 'max': 100, 'init': 80, 'scale': 100.0},
            'body_freq': {'min': 100, 'max': 500, 'init': 200},
        }
        self.initialize_mod_data()

    def _play_internal(self):
        decay_s = self.params['decay']
        length_samples = int(decay_s * self.sample_rate)
        t = np.linspace(0, decay_s, length_samples, endpoint=False)
        noise = np.random.randn(length_samples)
        noise_cutoff = 2000 + self.params['tone'] * 8000
        b, a = butter_bandpass(noise_cutoff - 1000, noise_cutoff, self.sample_rate, order=2)
        filtered_noise = apply_filter(noise, b, a)
        noise_env = np.exp(-15 * t / decay_s)
        noise_part = filtered_noise * noise_env * self.params['noise_level']
        body_part = np.sin(2 * np.pi * self.params['body_freq'] * t)
        body_env = np.exp(-10 * t / decay_s)
        body_part *= body_env * (1.0 - self.params['noise_level'])
        snare = (noise_part + body_part) * 0.8
        return snare.astype(np.float32)

class Hat(Instrument):
    def __init__(self, sample_rate, name, decay, choke_group=None):
        super().__init__(sample_rate, name)
        self.choke_group = choke_group
        self.params = {'decay': decay, 'fm_amount': 2.5, 'high_pass': 7000}
        self.param_defs = {
            'decay': {'min': 5, 'max': 1000, 'init': decay*1000, 'scale': 1000.0},
            'fm_amount': {'min': 0, 'max': 100, 'init': 25, 'scale': 10.0},
            'high_pass': {'min': 2000, 'max': 15000, 'init': 7000},
        }
        self.initialize_mod_data()

    def _play_internal(self):
        decay_s = self.params['decay']
        if decay_s <= 0: return np.array([], dtype=np.float32)
        length_samples = int(decay_s * self.sample_rate)
        t = np.linspace(0, decay_s, length_samples, endpoint=False)
        freqs = [210, 330, 470, 510, 680, 920]
        fm_mod = np.sin(2 * np.pi * 110 * t) * self.params['fm_amount']
        signal_mix = sum(signal.square(2 * np.pi * (f + fm_mod) * t) for f in freqs) / len(freqs)
        b, a = butter_highpass(self.params['high_pass'], self.sample_rate, order=2)
        filtered_signal = apply_filter(signal_mix, b, a)
        env = np.exp(-25 * t / decay_s)
        return (filtered_signal * env).astype(np.float32)

class Bell(Instrument):
    def __init__(self, sample_rate):
        super().__init__(sample_rate, "Bell")
        self.params = {'decay': 1.5, 'freq': 440.0, 'ratio1': 1.414, 'ratio2': 2.718, 'mod1': 1.0, 'mod2': 1.5}
        self.param_defs = {
            'decay': {'min': 100, 'max': 3000, 'init': 1500, 'scale': 1000.0},
            'freq': {'min': 100, 'max': 2000, 'init': 440},
            'ratio1': {'min': 1, 'max': 50, 'init': 14, 'scale': 10.0},
            'ratio2': {'min': 1, 'max': 50, 'init': 27, 'scale': 10.0},
            'mod1': {'min': 0, 'max': 100, 'init': 10, 'scale': 10.0},
            'mod2': {'min': 0, 'max': 100, 'init': 15, 'scale': 10.0},
        }
        self.initialize_mod_data()

    def _play_internal(self):
        decay_s = self.params['decay']
        length_samples = int(decay_s * self.sample_rate)
        t = np.linspace(0, decay_s, length_samples, endpoint=False)
        mod2 = np.sin(2 * np.pi * self.params['freq'] * self.params['ratio2'] * t) * self.params['mod2']
        mod1 = np.sin(2 * np.pi * self.params['freq'] * self.params['ratio1'] * t + mod2) * self.params['mod1']
        carrier = np.sin(2 * np.pi * self.params['freq'] * t + mod1)
        env = np.exp(-4 * t / decay_s)
        return (carrier * env).astype(np.float32)

class Klang(Instrument):
    def __init__(self, sample_rate):
        super().__init__(sample_rate, "Klang")
        self.params = {
            'decay': 0.8, 'freq1': 120, 'freq2': 180, 'ratio1': 3.1, 'ratio2': 4.2,
            'mod1': 2.0, 'mod2': 2.5, 'filter_freq': 1000, 'filter_q': 2.0
        }
        self.param_defs = {
            'decay': {'min': 50, 'max': 2000, 'init': 800, 'scale': 1000.0},
            'freq1': {'min': 50, 'max': 1000, 'init': 120},
            'freq2': {'min': 50, 'max': 1000, 'init': 180},
            'ratio1': {'min': 1, 'max': 80, 'init': 31, 'scale': 10.0},
            'ratio2': {'min': 1, 'max': 80, 'init': 42, 'scale': 10.0},
            'mod1': {'min': 0, 'max': 100, 'init': 20, 'scale': 10.0},
            'mod2': {'min': 0, 'max': 100, 'init': 25, 'scale': 10.0},
            'filter_freq': {'min': 200, 'max': 10000, 'init': 1000},
            'filter_q': {'min': 1, 'max': 100, 'init': 20, 'scale': 10.0},
        }
        self.initialize_mod_data()

    def _play_internal(self):
        decay_s = self.params['decay']
        length_samples = int(decay_s * self.sample_rate)
        t = np.linspace(0, decay_s, length_samples, endpoint=False)
        mod1 = np.sin(2 * np.pi * self.params['freq1'] * self.params['ratio1'] * t) * self.params['mod1']
        carrier1 = np.sin(2 * np.pi * self.params['freq1'] * t + mod1)
        mod2 = np.sin(2 * np.pi * self.params['freq2'] * self.params['ratio2'] * t) * self.params['mod2']
        carrier2 = np.sin(2 * np.pi * self.params['freq2'] * t + mod2)
        raw_signal = (carrier1 + carrier2) / 2.0
        b, a = resonant_lpf_coeffs(self.params['filter_freq'], self.params['filter_q'], self.sample_rate)
        filtered_signal = apply_filter(raw_signal, b, a)
        env = np.exp(-6 * t / decay_s)
        return (filtered_signal * env).astype(np.float32)

class Synth:
    def __init__(self, sample_rate):
        self.sample_rate = sample_rate
        self.instruments = {
            'kick_fm': KickFm(sample_rate),
            'kick_reso': KickReso(sample_rate),
            'snare': Snare(sample_rate),
            'closed_hat': Hat(sample_rate, "Closed Hat", 0.04, choke_group='hat'),
            'open_hat': Hat(sample_rate, "Open Hat", 0.5, choke_group='hat'),
            'bell': Bell(sample_rate),
            'klang': Klang(sample_rate),
        }
        self.voices = []
        self.lock = threading.Lock()

    def trigger(self, instrument_name, global_step, track_length):
        with self.lock:
            if instrument_name in self.instruments:
                instrument = self.instruments[instrument_name]
                if hasattr(instrument, 'choke_group') and instrument.choke_group:
                    self.voices = [v for v in self.voices if not (
                        'choke_group' in v and v['choke_group'] == instrument.choke_group
                    )]
                temp_params = instrument.params.copy()
                for p_name, p_def in instrument.param_defs.items():
                    mod_info = instrument.mod_data[p_name]
                    if mod_info['depth'] == 0.0: continue
                    mod_len = track_length if mod_info['sync'] == 'Track' else MAX_STEPS
                    mod_step = global_step % mod_len
                    curve_val = mod_info['curve'][mod_step]
                    if mod_info['polarity'] == 2: curve_val = (curve_val * 2) - 1.0
                    base_val = instrument.params[p_name]
                    min_val = p_def['min'] / p_def.get('scale', 1.0)
                    max_val = p_def['max'] / p_def.get('scale', 1.0)
                    mod_amount = (max_val - min_val) * mod_info['depth'] * curve_val
                    temp_params[p_name] = np.clip(base_val + mod_amount, min_val, max_val)
                sound_data = instrument.play(temp_params=temp_params)
                voice = {'data': sound_data, 'position': 0, 'instrument': instrument}
                if hasattr(instrument, 'choke_group'):
                    voice['choke_group'] = instrument.choke_group
                self.voices.append(voice)

    def render(self, num_samples):
        output_buffer = np.zeros(num_samples)
        with self.lock:
            remaining_voices = []
            for voice in self.voices:
                pos, data = voice['position'], voice['data']
                to_render = min(num_samples, len(data) - pos)
                if to_render > 0:
                    output_buffer[:to_render] += data[pos:pos+to_render]
                if pos + to_render < len(data):
                    voice['position'] += to_render
                    remaining_voices.append(voice)
            self.voices = remaining_voices
        return output_buffer

class AudioThread(QThread):
    error = Signal(str)
    step_changed = Signal(int)
    def __init__(self, synth, parent=None):
        super().__init__(parent)
        self.synth = synth
        self.stream = None
        self.global_step = -1
        self.step_duration_samples = int((60 / BPM) * SAMPLE_RATE / STEPS_PER_BEAT)
        self.samples_since_last_step = self.step_duration_samples
    def audio_callback(self, outdata, frames, time, status):
        if status: print(status, file=sys.stderr)
        buffer = self.synth.render(frames)
        outdata[:] = np.column_stack((buffer, buffer))
        self.samples_since_last_step += frames
        while self.samples_since_last_step >= self.step_duration_samples:
            self.samples_since_last_step -= self.step_duration_samples
            self.global_step = (self.global_step + 1) % MAX_STEPS
            self.step_changed.emit(self.global_step)
    def run(self):
        try:
            self.stream = sd.OutputStream(samplerate=SAMPLE_RATE, blocksize=BUFFER_SIZE, channels=2, callback=self.audio_callback, dtype='float32')
            with self.stream: self.exec()
        except Exception as e: self.error.emit(f"Audio Error: {e}")
    def stop(self):
        if self.stream: self.stream.stop(); self.stream.close()
        self.quit(); self.wait()

class ModulationCurveWidget(QGraphicsView):
    curve_changed = Signal()
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setBackgroundBrush(QColor("#2a2a2a"))
        self.path = QPainterPath()
        self.path_item = QGraphicsPathItem()
        self.path_item.setPen(QPen(QColor("cyan"), 2))
        self.scene().addItem(self.path_item)
        self.curve_data = np.full(MAX_STEPS, 0.5)
        self.last_mouse_pos = None
        self.update_path()
    def set_curve_data(self, data):
        self.curve_data = np.copy(data); self.update_path()
    def update_path(self):
        if not self.curve_data.any(): return
        self.path = QPainterPath()
        w = self.sceneRect().width(); h = self.sceneRect().height()
        step_w = w / len(self.curve_data)
        self.path.moveTo(0, h - self.curve_data[0] * h)
        for i, val in enumerate(self.curve_data[1:]):
            self.path.lineTo((i + 1) * step_w, h - val * h)
        self.path_item.setPath(self.path)
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setSceneRect(0, 0, self.width() - 2, self.height() - 2)
        self.update_path()
    def mousePressEvent(self, event):
        self.last_mouse_pos = event.pos(); self.draw_at(event.pos())
    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self.draw_at(event.pos()); self.last_mouse_pos = event.pos()
    def draw_at(self, pos):
        w = self.sceneRect().width(); h = self.sceneRect().height()
        step_w = w / len(self.curve_data)
        index = int(pos.x() / step_w)
        if 0 <= index < len(self.curve_data):
            if self.last_mouse_pos:
                last_index = int(self.last_mouse_pos.x() / step_w)
                if abs(index - last_index) > 1:
                    start_idx, end_idx = min(last_index, index), max(last_index, index)
                    start_y, end_y = self.curve_data[start_idx], self.curve_data[end_idx]
                    interp_y = np.interp(range(start_idx, end_idx + 1), [start_idx, end_idx], [start_y, end_y])
                    self.curve_data[start_idx:end_idx+1] = interp_y
            new_val = np.clip(1.0 - (pos.y() / h), 0.0, 1.0)
            self.curve_data[index] = new_val
        self.update_path(); self.curve_changed.emit()

class ModulationEditor(QGroupBox):
    modulation_changed = Signal()
    def __init__(self, parent=None):
        super().__init__("Modulation Editor", parent)
        self.target_instrument = None; self.target_param = None
        self.layout = QHBoxLayout()
        self.curve_view = ModulationCurveWidget()
        self.layout.addWidget(self.curve_view, 5)
        controls_layout = QVBoxLayout()
        self.add_control_widgets(controls_layout)
        self.layout.addLayout(controls_layout, 1)
        self.setLayout(self.layout)
        self.hide()
    def add_control_widgets(self, layout):
        self.depth_slider = QSlider(Qt.Vertical); self.depth_slider.setRange(0, 100)
        self.depth_slider.valueChanged.connect(self.update_mod_data); layout.addWidget(QLabel("Depth"))
        layout.addWidget(self.depth_slider, alignment=Qt.AlignCenter)
        self.polarity_combo = QComboBox(); self.polarity_combo.addItems(["Unipolar", "Bipolar"])
        self.polarity_combo.currentIndexChanged.connect(self.update_mod_data); layout.addWidget(QLabel("Polarity"))
        layout.addWidget(self.polarity_combo)
        self.sync_combo = QComboBox(); self.sync_combo.addItems(["Global", "Track"])
        self.sync_combo.currentIndexChanged.connect(self.update_mod_data); layout.addWidget(QLabel("Sync"))
        layout.addWidget(self.sync_combo)
        clear_btn = QPushButton("Clear"); clear_btn.clicked.connect(self.clear_curve); layout.addWidget(clear_btn)
        random_btn = QPushButton("Random"); random_btn.clicked.connect(self.randomize_curve); layout.addWidget(random_btn)
        layout.addStretch()
    def set_target(self, instrument, param_name):
        self.target_instrument = instrument; self.target_param = param_name
        self.setTitle(f"Modulating: {instrument.name} - {param_name.replace('_', ' ').title()}")
        mod_info = self.target_instrument.mod_data[self.target_param]
        self.depth_slider.setValue(int(mod_info['depth'] * 100))
        self.polarity_combo.setCurrentIndex(mod_info['polarity'] - 1)
        self.sync_combo.setCurrentText(mod_info['sync'])
        self.curve_view.set_curve_data(mod_info['curve'])
        self.curve_view.curve_changed.connect(self.update_mod_data)
    def update_mod_data(self):
        if not self.target_instrument: return
        mod_info = self.target_instrument.mod_data[self.target_param]
        mod_info['depth'] = self.depth_slider.value() / 100.0
        mod_info['polarity'] = self.polarity_combo.currentIndex() + 1
        mod_info['sync'] = self.sync_combo.currentText()
        mod_info['curve'] = self.curve_view.curve_data
        self.modulation_changed.emit()
    def clear_curve(self):
        self.curve_view.set_curve_data(np.full(MAX_STEPS, 0.5)); self.update_mod_data()
    def randomize_curve(self):
        self.curve_view.set_curve_data(np.random.rand(MAX_STEPS)); self.update_mod_data()

class ModdableSlider(QWidget):
    modulation_requested = Signal(object, str)
    def __init__(self, instrument, param_name, param_def):
        super().__init__()
        self.instrument = instrument; self.param_name = param_name; self.param_def = param_def
        layout = QHBoxLayout(); layout.setContentsMargins(0,0,0,0)
        self.label = QLabel(param_name.replace('_', ' ').title()); self.label.setFixedWidth(100)
        self.slider = QSlider(Qt.Horizontal); self.slider.setRange(param_def['min'], param_def['max'])
        scale = param_def.get('scale', 1.0)
        self.slider.setValue(int(instrument.params[param_name] * scale))
        self.slider.valueChanged.connect(self.slider_changed)
        self.mod_badge = QLabel("MOD"); self.mod_badge.setFixedWidth(30)
        self.mod_badge.setStyleSheet("color: #007ACC; font-weight: bold;"); self.mod_badge.hide()
        layout.addWidget(self.label); layout.addWidget(self.slider); layout.addWidget(self.mod_badge)
        self.setLayout(layout); self.update_mod_badge()
    def slider_changed(self, value):
        scale = self.param_def.get('scale', 1.0)
        self.instrument.set_param(self.param_name, value / scale)
    def mousePressEvent(self, event):
        self.modulation_requested.emit(self.instrument, self.param_name); super().mousePressEvent(event)
    def update_mod_badge(self):
        mod_depth = self.instrument.mod_data[self.param_name]['depth']
        self.mod_badge.setVisible(mod_depth > 0)

class SequencerTrack(QWidget):
    def __init__(self, name, steps=MAX_STEPS):
        super().__init__(); self.track_name = name; self.num_steps = steps; self.track_length = steps
        self.steps = []
        layout = QHBoxLayout(); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(4)
        name_label = QLabel(name); name_label.setFixedWidth(80); layout.addWidget(name_label)
        for i in range(self.num_steps):
            step_button = QPushButton(); step_button.setCheckable(True); step_button.setFixedSize(25, 25)
            layout.addWidget(step_button); self.steps.append(step_button)
        self.length_spinbox = QSpinBox(); self.length_spinbox.setRange(1, MAX_STEPS)
        self.length_spinbox.setValue(MAX_STEPS); self.length_spinbox.setFixedWidth(50)
        self.length_spinbox.valueChanged.connect(self.set_length); layout.addWidget(self.length_spinbox)
        self.setLayout(layout); self.update_visuals()
    def set_length(self, length):
        self.track_length = length; self.update_visuals()
    def update_visuals(self):
        for i, btn in enumerate(self.steps):
            is_active = i < self.track_length; btn.setEnabled(is_active)
            is_beat = (i // STEPS_PER_BEAT) % 2 == 0
            base_color = "#444" if is_beat else "#333"
            if not is_active: base_color = "#2a2a2a"
            style = f"QPushButton {{ background-color: {base_color}; border: 1px solid #555; }}" \
                    f"QPushButton:checked {{ background-color: #D2691E; }}" \
                    f"QPushButton:disabled {{ background-color: #2a2a2a; border: 1px solid #444; }}"
            btn.setStyleSheet(style)
    def is_step_active(self, step):
        if step < self.track_length: return self.steps[step].isChecked()
        return False
    def update_playback_position(self, global_step):
        self.update_visuals()
        if self.track_length > 0:
            local_step = global_step % self.track_length
            if local_step < len(self.steps):
                btn = self.steps[local_step]
                current_style = btn.styleSheet()
                new_style = current_style.replace("}", " border: 2px solid cyan; }")
                btn.setStyleSheet(new_style)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("808 Polyrhythmic Drum Machine")
        self.setGeometry(100, 100, 1200, 950)
        self.synth = Synth(SAMPLE_RATE); self.tracks = {}
        central_widget = QWidget(); self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)
        self.create_sequencer_ui()
        separator1 = QFrame(); separator1.setFrameShape(QFrame.HLine); separator1.setFrameShadow(QFrame.Sunken)
        self.main_layout.addWidget(separator1)
        scroll_area = QScrollArea(); scroll_area.setWidgetResizable(True); scroll_area.setFixedHeight(250)
        scroll_widget = QWidget(); self.instrument_controls_layout = QHBoxLayout(scroll_widget)
        scroll_area.setWidget(scroll_widget); self.main_layout.addWidget(scroll_area)
        self.instrument_controls_widgets = {}
        self.create_instrument_controls()
        separator2 = QFrame(); separator2.setFrameShape(QFrame.HLine); separator2.setFrameShadow(QFrame.Sunken)
        self.main_layout.addWidget(separator2)
        mod_controls_layout = QHBoxLayout()
        self.mod_mode_button = QPushButton("+ Modulation Mode"); self.mod_mode_button.setCheckable(True)
        self.mod_mode_button.toggled.connect(self.on_mod_mode_toggled)
        mod_controls_layout.addWidget(self.mod_mode_button); mod_controls_layout.addStretch()
        self.main_layout.addLayout(mod_controls_layout)
        self.mod_editor = ModulationEditor(self)
        self.mod_editor.modulation_changed.connect(self.update_mod_badges)
        self.main_layout.addWidget(self.mod_editor)
        self.audio_thread = AudioThread(self.synth, self)
        self.audio_thread.step_changed.connect(self.on_step_changed)
        self.audio_thread.error.connect(self.on_audio_error)
        self.audio_thread.start()
    def create_sequencer_ui(self):
        sequencer_group = QGroupBox("Sequencer")
        sequencer_layout = QVBoxLayout()
        for name, instrument in self.synth.instruments.items():
            track = SequencerTrack(instrument.name); self.tracks[name] = track
            sequencer_layout.addWidget(track)
        sequencer_group.setLayout(sequencer_layout); self.main_layout.addWidget(sequencer_group)
    def on_step_changed(self, global_step):
        for name, track in self.tracks.items():
            track.update_playback_position(global_step)
            local_step = global_step % track.track_length
            if track.is_step_active(local_step):
                self.synth.trigger(name, global_step, track.track_length)
    def create_instrument_controls(self):
        for name, instrument in self.synth.instruments.items():
            group_box = QGroupBox(instrument.name)
            layout = QVBoxLayout()
            for param_name, p_def in instrument.param_defs.items():
                control_widget = ModdableSlider(instrument, param_name, p_def)
                control_widget.modulation_requested.connect(self.on_modulation_requested)
                self.instrument_controls_widgets[(instrument.name, param_name)] = control_widget
                layout.addWidget(control_widget)
            group_box.setLayout(layout)
            self.instrument_controls_layout.addWidget(group_box)
    def update_mod_badges(self):
        for control in self.instrument_controls_widgets.values():
            control.update_mod_badge()
    def on_modulation_requested(self, instrument, param_name):
        if self.mod_mode_button.isChecked():
            self.mod_editor.set_target(instrument, param_name); self.mod_editor.show()
    def on_mod_mode_toggled(self, checked):
        if checked:
            self.mod_mode_button.setStyleSheet("background-color: #007ACC; color: white; font-weight: bold;")
            self.mod_editor.hide()
        else:
            self.mod_mode_button.setStyleSheet(""); self.mod_editor.hide()
    def on_audio_error(self, error_message):
        print(error_message, file=sys.stderr); QApplication.quit()
    def closeEvent(self, event):
        self.audio_thread.stop(); event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    font_db = QFontDatabase()
    if "Chicago" in font_db.families(): app.setFont(QFont("Chicago", 12))
    app.setStyleSheet("""
        QWidget { background-color: #1E1E1E; color: #F0F0F0; font-family: Chicago; }
        QMainWindow { background-color: #1E1E1E; }
        QGroupBox {
            border: 1px solid #4A4A4A;
            border-radius: 5px;
            margin-top: 1ex;
            font-weight: bold;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top center;
            padding: 0 5px;
            color: #993333; /* Dark Red */
        }

        SequencerTrack QPushButton:checked {
            background-color: #B85B14;
            border: 1px solid #D2691E;
        }

        QSlider::groove:horizontal {
            border: 1px solid #333; height: 4px; background: #3A3A3A;
            margin: 2px 0; border-radius: 2px;
        }
        QSlider::handle:horizontal {
            background: #B0B0B0; border: 1px solid #C0C0C0;
            width: 16px; margin: -6px 0; border-radius: 8px;
        }

        QScrollArea { border: none; }
        QScrollBar:horizontal {
            border-radius: 7px;
            background: #2A2A2A;
            height: 15px; margin: 0px 0px 0 0px;
        }
        QScrollBar::handle:horizontal {
            background: #007ACC; /* Blue accent */
            min-width: 20px;
            border-radius: 7px;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            border: none; background: none;
        }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
            background: none;
        }

        QPushButton {
            background-color: #4A4A4A; border: 1px solid #666;
            padding: 5px; border-radius: 3px;
        }
        QPushButton:hover { background-color: #5A5A5A; }
        QPushButton:pressed { background-color: #3A3A3A; }

        QPushButton:checkable:checked {
             background-color: #007ACC;
             color: white;
             border-color: #5F9CDD;
        }
        QSpinBox {
            background-color: #3A3A3A;
            border: 1px solid #993333; /* Dark Red Accent */
            padding: 2px; border-radius: 3px;
        }
        QComboBox {
             background-color: #3A3A3A;
             border: 1px solid #666;
             padding: 3px; border-radius: 3px;
        }
        QComboBox::drop-down { border: none; }
    """)
    main_win = MainWindow()
    main_win.show()
    sys.exit(app.exec())
