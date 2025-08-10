import sys
import numpy as np
import sounddevice as sd
from PySide6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
                               QSlider, QLabel, QPushButton, QGroupBox, QFrame, QSpinBox,
                               QGraphicsView, QGraphicsScene, QGraphicsPathItem, QComboBox,
                               QScrollArea, QGridLayout, QLineEdit)
from PySide6.QtCore import QThread, Signal, Qt, QPointF
from PySide6.QtGui import QFontDatabase, QPen, QPainterPath, QColor, QPainter
import threading
from scipy import signal
import math

# --- Constants ---
SAMPLE_RATE = 44100
BUFFER_SIZE = 512
BPM = 120
STEPS_PER_BEAT = 4
MAX_STEPS = 32

# --- Synthesis Core (No Changes) ---
class Instrument:
    def __init__(self, sample_rate, name="Instrument"):
        self.sample_rate, self.name, self.params, self.param_defs, self.mod_data = sample_rate, name, {}, {}, {}
    def initialize_mod_data(self):
        for p_name in self.param_defs: self.mod_data[p_name] = {'curve': np.full(MAX_STEPS, 0.5), 'depth': 0.0, 'polarity': 1, 'sync': 'Global'}
    def play(self, temp_params=None):
        original_params = self.params
        if temp_params: self.params = temp_params
        sound = self._play_internal()
        self.params = original_params
        return sound
    def _play_internal(self): raise NotImplementedError
    def set_param(self, name, value): self.params[name] = value
def butter_bandpass(lowcut, highcut, fs, order=5):
    nyq = 0.5*fs; low=lowcut/nyq; high=highcut/nyq; b,a=signal.butter(order,[low,high],btype='band'); return b,a
def butter_highpass(cutoff, fs, order=5):
    nyq = 0.5*fs; normal_cutoff=cutoff/nyq; b,a=signal.butter(order,normal_cutoff,btype='high',analog=False); return b,a
def resonant_lpf_coeffs(f0, Q, fs):
    if f0<=1 or Q<=0: return [1,0,0],[1,0,0]
    w0=2*math.pi*f0/fs; alpha=math.sin(w0)/(2*Q); b0=(1-math.cos(w0))/2; b1=1-math.cos(w0); b2=(1-math.cos(w0))/2
    a0=1+alpha; a1=-2*math.cos(w0); a2=1-alpha; return [b0/a0,b1/a0,b2/a0],[1,a1/a0,a2/a0]
def apply_filter(data, b, a): return signal.lfilter(b,a,data)
class KickFm(Instrument):
    def __init__(self, s): super().__init__(s,"Kick (FM)"); self.params={'start_freq':150,'end_freq':40,'decay':0.25,'mod_index':2.0}; self.param_defs={'start_freq':{'min':20,'max':500,'init':150},'end_freq':{'min':20,'max':200,'init':40},'decay':{'min':10,'max':1000,'init':250,'scale':1000.0},'mod_index':{'min':0,'max':100,'init':20,'scale':10.0}}; self.initialize_mod_data()
    def _play_internal(self):
        d, sr, p = self.params['decay'], self.sample_rate, self.params; l=int(d*sr); t=np.linspace(0,d,l,endpoint=False); amp=np.exp(-5*t/d); pit=np.linspace(p['start_freq'],p['end_freq'],l); modf=pit*p['mod_index']; mod=np.sin(2*np.pi*modf*t); car_p=2*np.pi*pit*t+mod; car=np.sin(car_p); return (car*amp).astype(np.float32)
class KickReso(Instrument):
    def __init__(self,s): super().__init__(s,"Kick (Reso)"); self.params={'decay':0.2,'start_freq':120,'end_freq':40,'Q':5.0}; self.param_defs={'decay':{'min':10,'max':1000,'init':200,'scale':1000.0},'start_freq':{'min':20,'max':300,'init':120},'end_freq':{'min':20,'max':100,'init':40},'Q':{'min':1,'max':100,'init':50,'scale':10.0}}; self.initialize_mod_data()
    def _play_internal(self):
        d, sr, p = self.params['decay'], self.sample_rate, self.params; l=int(d*sr); imp=np.zeros(l); imp[0]=1.0; freq_env=np.linspace(p['start_freq'],p['end_freq'],l); out=np.zeros(l); z=np.zeros(2); q=p['Q']
        for i in range(l): b,a=resonant_lpf_coeffs(freq_env[i],q,sr); y,z=signal.lfilter(b,a,[imp[i]],zi=z); out[i]=y[0]
        return (out*np.exp(-8*np.linspace(0,1,l))).astype(np.float32)
class Snare(Instrument):
    def __init__(self,s): super().__init__(s,"Snare"); self.params={'decay':0.2,'tone':0.5,'noise_level':0.8,'body_freq':200}; self.param_defs={'decay':{'min':10,'max':500,'init':200,'scale':1000.0},'tone':{'min':0,'max':100,'init':50,'scale':100.0},'noise_level':{'min':0,'max':100,'init':80,'scale':100.0},'body_freq':{'min':100,'max':500,'init':200}}; self.initialize_mod_data()
    def _play_internal(self):
        d, sr, p = self.params['decay'], self.sample_rate, self.params; l=int(d*sr); t=np.linspace(0,d,l,endpoint=False); n=np.random.randn(l); nc=2000+p['tone']*8000; b,a=butter_bandpass(nc-1000,nc,sr,order=2); fn=apply_filter(n,b,a); ne=np.exp(-15*t/d); np_ = fn*ne*p['noise_level']; bp=np.sin(2*np.pi*p['body_freq']*t); be=np.exp(-10*t/d); bp*=be*(1.0-p['noise_level']); return ((np_+bp)*0.8).astype(np.float32)
class Hat(Instrument):
    def __init__(self, sample_rate, name, decay, choke_group=None):
        super().__init__(sample_rate, name)
        self.choke_group = choke_group
        self.params = {'decay': decay, 'fm_amount': 2.5, 'high_pass': 7000}
        self.param_defs = {
            'decay': {'min': 5, 'max': 1000, 'init': decay * 1000, 'scale': 1000.0},
            'fm_amount': {'min': 0, 'max': 100, 'init': 25, 'scale': 10.0},
            'high_pass': {'min': 2000, 'max': 15000, 'init': 7000},
        }
        self.initialize_mod_data()
    def _play_internal(self):
        d,sr,p = self.params['decay'],self.sample_rate,self.params; l=int(d*sr); t=np.linspace(0,d,l,endpoint=False); f=[210,330,470,510,680,920]; fm=np.sin(2*np.pi*110*t)*p['fm_amount']; sm=sum(signal.square(2*np.pi*(fr+fm)*t) for fr in f)/len(f); b,a=butter_highpass(p['high_pass'],sr,order=2); fs=apply_filter(sm,b,a); e=np.exp(-25*t/d); return (fs*e).astype(np.float32)
class Bell(Instrument):
    def __init__(self,s): super().__init__(s,"Bell"); self.params={'decay':1.5,'freq':440.0,'ratio1':1.414,'ratio2':2.718,'mod1':1.0,'mod2':1.5}; self.param_defs={'decay':{'min':100,'max':3000,'init':1500,'scale':1000.0},'freq':{'min':100,'max':2000,'init':440},'ratio1':{'min':1,'max':50,'init':14,'scale':10.0},'ratio2':{'min':1,'max':50,'init':27,'scale':10.0},'mod1':{'min':0,'max':100,'init':10,'scale':10.0},'mod2':{'min':0,'max':100,'init':15,'scale':10.0}}; self.initialize_mod_data()
    def _play_internal(self):
        d,sr,p = self.params['decay'],self.sample_rate,self.params; l=int(d*sr); t=np.linspace(0,d,l,endpoint=False); m2=np.sin(2*np.pi*p['freq']*p['ratio2']*t)*p['mod2']; m1=np.sin(2*np.pi*p['freq']*p['ratio1']*t+m2)*p['mod1']; c=np.sin(2*np.pi*p['freq']*t+m1); e=np.exp(-4*t/d); return (c*e).astype(np.float32)
class Klang(Instrument):
    def __init__(self,s): super().__init__(s,"Klang"); self.params={'decay':0.8,'freq1':120,'freq2':180,'ratio1':3.1,'ratio2':4.2,'mod1':2.0,'mod2':2.5,'filter_freq':1000,'filter_q':2.0}; self.param_defs={'decay':{'min':50,'max':2000,'init':800,'scale':1000.0},'freq1':{'min':50,'max':1000,'init':120},'freq2':{'min':50,'max':1000,'init':180},'ratio1':{'min':1,'max':80,'init':31,'scale':10.0},'ratio2':{'min':1,'max':80,'init':42,'scale':10.0},'mod1':{'min':0,'max':100,'init':20,'scale':10.0},'mod2':{'min':0,'max':100,'init':25,'scale':10.0},'filter_freq':{'min':200,'max':10000,'init':1000},'filter_q':{'min':1,'max':100,'init':20,'scale':10.0}}; self.initialize_mod_data()
    def _play_internal(self):
        d,sr,p = self.params['decay'],self.sample_rate,self.params; l=int(d*sr); t=np.linspace(0,d,l,endpoint=False); m1=np.sin(2*np.pi*p['freq1']*p['ratio1']*t)*p['mod1']; c1=np.sin(2*np.pi*p['freq1']*t+m1); m2=np.sin(2*np.pi*p['freq2']*p['ratio2']*t)*p['mod2']; c2=np.sin(2*np.pi*p['freq2']*t+m2); rs=(c1+c2)/2.0; b,a=resonant_lpf_coeffs(p['filter_freq'],p['filter_q'],sr); fs=apply_filter(rs,b,a); e=np.exp(-6*t/d); return (fs*e).astype(np.float32)
# ... (Synth class is the same as before) ...
class Synth:
    def __init__(self, sample_rate):
        self.sample_rate = sample_rate
        self.instruments = {
            'kick_fm': KickFm(sample_rate), 'kick_reso': KickReso(sample_rate), 'snare': Snare(sample_rate),
            'closed_hat': Hat(sample_rate, "Closed Hat", 0.04, choke_group='hat'),
            'open_hat': Hat(sample_rate, "Open Hat", 0.5, choke_group='hat'),
            'bell': Bell(sample_rate), 'klang': Klang(sample_rate),
        }
        self.voices, self.lock = [], threading.Lock()
    def trigger(self, instrument_name, global_step, track_length):
        with self.lock:
            if instrument_name in self.instruments:
                inst = self.instruments[instrument_name]
                if hasattr(inst, 'choke_group') and inst.choke_group: self.voices = [v for v in self.voices if not ('choke_group' in v and v['choke_group'] == inst.choke_group)]
                params = inst.params.copy()
                for p_name, p_def in inst.param_defs.items():
                    mod_info = inst.mod_data[p_name]
                    if mod_info['depth'] == 0.0: continue
                    mod_len = track_length if mod_info['sync'] == 'Track' else MAX_STEPS; mod_step = global_step % mod_len
                    curve_val = mod_info['curve'][mod_step]
                    if mod_info['polarity'] == 2: curve_val = (curve_val * 2) - 1.0
                    base_val = inst.params[p_name]; min_val = p_def['min']/p_def.get('scale',1.0); max_val = p_def['max']/p_def.get('scale',1.0)
                    mod_amount = (max_val - min_val) * mod_info['depth'] * curve_val
                    params[p_name] = np.clip(base_val + mod_amount, min_val, max_val)
                sound_data = inst.play(temp_params=params)
                voice = {'data': sound_data, 'position': 0, 'instrument': inst}
                if hasattr(inst, 'choke_group'): voice['choke_group'] = inst.choke_group
                self.voices.append(voice)
    def render(self, num_samples):
        buffer = np.zeros(num_samples)
        with self.lock:
            rem_voices = []
            for voice in self.voices:
                pos, data = voice['position'], voice['data']; to_render = min(num_samples, len(data) - pos)
                if to_render > 0: buffer[:to_render] += data[pos:pos+to_render]
                if pos + to_render < len(data): voice['position'] += to_render; rem_voices.append(voice)
            self.voices = rem_voices
        return buffer

# --- Core App ---
class AudioThread(QThread):
    tick = Signal(int)
    def __init__(self, parent=None):
        super().__init__(parent); self.synth = None; self.stream = None; self.playing = False; self.bpm = BPM
        self.global_step = -1; self.step_duration_samples = 0; self.samples_since_last_step = 0
        self.set_bpm(BPM)
    def set_synth(self, synth): self.synth = synth
    def set_playing(self, playing): self.playing = playing
    def set_bpm(self, bpm): self.bpm = bpm; self.step_duration_samples = int((60/self.bpm)*SAMPLE_RATE/STEPS_PER_BEAT)
    def reset_step(self): self.global_step = -1; self.samples_since_last_step = 0
    def audio_callback(self, outdata, frames, time, status):
        if status: print(status, file=sys.stderr)
        buffer = self.synth.render(frames) if self.synth else np.zeros(frames)
        outdata[:] = np.column_stack((buffer, buffer))
        if not self.playing: return
        self.samples_since_last_step += frames
        while self.samples_since_last_step >= self.step_duration_samples:
            self.samples_since_last_step -= self.step_duration_samples
            self.global_step = (self.global_step + 1) % (MAX_STEPS * 100) # Effectively infinite global step
            self.tick.emit(self.global_step)
    def run(self):
        try:
            self.stream = sd.OutputStream(samplerate=SAMPLE_RATE,blocksize=BUFFER_SIZE,channels=2,callback=self.audio_callback,dtype='float32')
            with self.stream: self.exec()
        except Exception as e: print(f"Audio Error: {e}", file=sys.stderr)
    def stop(self):
        if self.stream: self.stream.stop(); self.stream.close()
        self.quit(); self.wait()

class SequencerTrack(QWidget):
    def __init__(self, name, steps=MAX_STEPS):
        super().__init__(); self.track_name=name; self.num_steps=steps; self.track_length=steps
        self.current_step=-1; self.direction=1; self.loop_mode="->"; self.steps=[]
        layout=QHBoxLayout(); layout.setContentsMargins(0,0,0,0); layout.setSpacing(2)
        name_label=QLabel(name); name_label.setFixedWidth(60); layout.addWidget(name_label)
        for i in range(self.num_steps):
            btn = QPushButton(); btn.setCheckable(True); btn.setFixedSize(20,20); layout.addWidget(btn); self.steps.append(btn)
        self.length_spinbox=QSpinBox(); self.length_spinbox.setRange(1,MAX_STEPS); self.length_spinbox.setValue(MAX_STEPS)
        self.length_spinbox.setFixedWidth(40); self.length_spinbox.valueChanged.connect(self.set_length); layout.addWidget(self.length_spinbox)
        self.loop_mode_button=QPushButton(self.loop_mode); self.loop_mode_button.setFixedWidth(40)
        self.loop_mode_button.clicked.connect(self.toggle_loop_mode); layout.addWidget(self.loop_mode_button)
        self.setLayout(layout); self.update_visuals()
    def set_length(self, length): self.track_length=length; self.update_visuals()
    def toggle_loop_mode(self):
        self.loop_mode = "-><-" if self.loop_mode == "->" else "->"; self.loop_mode_button.setText(self.loop_mode)
    def advance_step(self):
        if self.loop_mode == "->":
            self.current_step = (self.current_step + 1) % self.track_length
        else: # Ping-Pong
            if self.current_step + self.direction >= self.track_length or self.current_step + self.direction < 0:
                self.direction *= -1
            self.current_step += self.direction
        self.update_playback_position()
    def reset(self): self.current_step=-1; self.direction=1; self.update_playback_position()
    def update_visuals(self):
        for i, btn in enumerate(self.steps):
            is_active = i < self.track_length; btn.setEnabled(is_active)
            base_color = "#444" if (i//STEPS_PER_BEAT)%2==0 else "#333"; base_color = "#2a2a2a" if not is_active else base_color
            btn.setStyleSheet(f"QPushButton {{ background-color: {base_color}; border: 1px solid #555; border-radius:3px;}} QPushButton:checked {{ background-color: #B85B14; }} QPushButton:disabled {{ background-color: #2a2a2a; }}")
    def is_step_active(self, step): return self.steps[step].isChecked() if step < self.track_length else False
    def update_playback_position(self):
        self.update_visuals()
        if self.current_step >= 0 and self.current_step < len(self.steps):
            self.steps[self.current_step].setStyleSheet(self.steps[self.current_step].styleSheet().replace("}", " border: 1px solid cyan; }"))

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("808 Polyrhythmic Drum Machine"); self.setGeometry(100,100,1200,950)
        self.synth = Synth(SAMPLE_RATE); self.tracks = {}; self.is_playing = False
        self.audio_thread = AudioThread(self)
        self.audio_thread.set_synth(self.synth)

        central_widget=QWidget(); self.setCentralWidget(central_widget); self.main_layout=QVBoxLayout(central_widget)
        self.create_transport_controls()
        self.create_sequencer_ui()

        sep1=QFrame(); sep1.setFrameShape(QFrame.HLine); sep1.setFrameShadow(QFrame.Sunken); self.main_layout.addWidget(sep1)
        scroll_area=QScrollArea(); scroll_area.setWidgetResizable(True); scroll_area.setFixedHeight(250)
        scroll_widget=QWidget(); self.instrument_controls_layout=QGridLayout(scroll_widget)
        scroll_area.setWidget(scroll_widget); self.main_layout.addWidget(scroll_area)
        self.instrument_controls_widgets={}
        self.create_instrument_controls()

        sep2=QFrame(); sep2.setFrameShape(QFrame.HLine); sep2.setFrameShadow(QFrame.Sunken); self.main_layout.addWidget(sep2)
        mod_controls_layout=QHBoxLayout(); self.mod_mode_button=QPushButton("+ Modulation Mode"); self.mod_mode_button.setCheckable(True)
        self.mod_mode_button.toggled.connect(self.on_mod_mode_toggled); mod_controls_layout.addWidget(self.mod_mode_button)
        mod_controls_layout.addStretch(); self.main_layout.addLayout(mod_controls_layout)
        self.mod_editor=ModulationEditor(self); self.mod_editor.modulation_changed.connect(self.update_mod_badges)
        self.main_layout.addWidget(self.mod_editor)

        self.audio_thread.tick.connect(self.on_tick); self.audio_thread.error.connect(self.on_audio_error); self.audio_thread.start()
    def create_transport_controls(self):
        l=QHBoxLayout(); btn=QPushButton("Play"); btn.setCheckable(True); btn.toggled.connect(self.on_play_toggled)
        self.play_button=btn; l.addWidget(btn); btn2=QPushButton("Stop"); btn2.clicked.connect(self.on_stop_clicked)
        l.addWidget(btn2); l.addStretch(); l.addWidget(QLabel("BPM:")); self.bpm_input=QLineEdit(str(BPM))
        self.bpm_input.setFixedWidth(50); self.bpm_input.editingFinished.connect(self.on_bpm_changed); l.addWidget(self.bpm_input)
        self.main_layout.addLayout(l)
    def on_play_toggled(self,c): self.is_playing=c; self.audio_thread.set_playing(c); self.play_button.setText("Pause" if c else "Play")
    def on_stop_clicked(self):
        if self.is_playing: self.play_button.toggle()
        self.audio_thread.reset_step(); [t.reset() for t in self.tracks.values()]
    def on_bpm_changed(self):
        try: self.audio_thread.set_bpm(float(self.bpm_input.text()))
        except ValueError: self.bpm_input.setText(str(self.audio_thread.bpm))
    def create_sequencer_ui(self):
        sg=QGroupBox("Sequencer"); sl=QVBoxLayout(); [sl.addWidget(self.tracks.setdefault(n, SequencerTrack(i.name))) for n,i in self.synth.instruments.items()]
        sg.setLayout(sl); self.main_layout.addWidget(sg)
    def on_tick(self,g_step):
        for name, track in self.tracks.items():
            track.advance_step()
            if track.is_step_active(track.current_step): self.synth.trigger(name, g_step, track.track_length)
    def create_instrument_controls(self):
        r,c,n,cols=0,0,len(self.synth.instruments),(len(self.synth.instruments)+1)//2
        for name, inst in self.synth.instruments.items():
            gb=QGroupBox(inst.name); l=QVBoxLayout()
            for p_name,p_def in inst.param_defs.items():
                cw=ModdableSlider(inst,p_name,p_def); cw.modulation_requested.connect(self.on_modulation_requested)
                self.instrument_controls_widgets[(inst.name,p_name)]=cw; l.addWidget(cw)
            gb.setLayout(l); self.instrument_controls_layout.addWidget(gb,r,c); c+=1
            if c>=cols: c=0; r+=1
    def update_mod_badges(self): [c.update_mod_badge() for c in self.instrument_controls_widgets.values()]
    def on_modulation_requested(self,i,p):
        if self.mod_mode_button.isChecked(): self.mod_editor.set_target(i,p); self.mod_editor.show()
    def on_mod_mode_toggled(self,c): self.mod_mode_button.setStyleSheet("background-color:#007ACC;color:white;" if c else ""); self.mod_editor.hide()
    def on_audio_error(self,e): print(e,file=sys.stderr); QApplication.quit()
    def closeEvent(self,e): self.audio_thread.stop(); e.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    if "Chicago" in QFontDatabase().families(): app.setFont(QFont("Chicago", 12))
    app.setStyleSheet("""
        QWidget { background-color: #1E1E1E; color: #F0F0F0; font-family: Chicago; }
        QMainWindow { background-color: #1E1E1E; }
        QGroupBox { border: 1px solid #4A4A4A; border-radius: 5px; margin-top: 1ex; font-weight: bold; }
        QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top center; padding: 0 5px; color: #993333; }
        QSlider::groove:horizontal { border:1px solid #333; height:4px; background:#3A3A3A; margin:2px 0; border-radius:2px; }
        QSlider::handle:horizontal { background:#B0B0B0; border:1px solid #C0C0C0; width:16px; margin:-6px 0; border-radius:8px; }
        QScrollArea { border: none; }
        QScrollBar:horizontal { border-radius:7px; background:#2A2A2A; height:15px; margin:0; }
        QScrollBar::handle:horizontal { background:#007ACC; min-width:20px; border-radius:7px; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { border:none; background:none; }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background:none; }
        QPushButton { background-color:#4A4A4A; border:1px solid #666; padding:4px; border-radius:3px; }
        QPushButton:hover { background-color:#5A5A5A; } QPushButton:pressed { background-color:#3A3A3A; }
        QPushButton:checkable:checked { background-color:#007ACC; color:white; border-color:#5F9CDD; }
        QSpinBox, QLineEdit { background-color:#3A3A3A; border:1px solid #993333; padding:2px; border-radius:3px; }
        QComboBox { background-color:#3A3A3A; border:1px solid #666; padding:3px; border-radius:3px; }
        QComboBox::drop-down { border:none; }
    """)
    main_win = MainWindow()
    main_win.show()
    sys.exit(app.exec())
