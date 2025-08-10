"""
A polyrhythmic drum machine and sequencer built with Python, Dear PyGui, and Pyo.

This application features a step sequencer where each instrument can have a unique
pattern length, allowing for polyrhythms. It includes several custom-built
synthesizers, MIDI input for transport control, and MIDI output for triggering
external gear.
"""
import dearpygui.dearpygui as dpg
import threading
import time
import sys
from pyo import *
import mido

# --- Constants ---
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 768
DRUM_SOUNDS = [
    "Kick (FM)", "Kick (Filter)", "Snare", "Closed Hat", "Open Hat", "Bell", "Klang"
]
DEFAULT_BPM = 120
DEFAULT_STEPS = 16
MAX_STEPS = 32

# --- App State & Data ---
# This dictionary holds the sequencer state for each sound.
pattern_data = {
    sound: {
        "steps": [0] * DEFAULT_STEPS,  # The sequence of triggers (0 or 1)
        "length": DEFAULT_STEPS,      # The number of steps in the pattern
        "loop_mode": "->",            # The loop mode ('->' or '-><-')
        "current_step": -1,           # The currently playing step index
        "direction": 1                # The direction of play (1 for fwd, -1 for rev)
    }
    for sound in DRUM_SOUNDS
}
# Global references to the main sequencer and MIDI listener threads.
sequencer = None
midi_listener = None
is_playing = False

# Global reference to the opened Mido port for MIDI output.
midi_out_port = None

# MIDI settings: default note mappings and variables for selected ports.
midi_note_map = {sound: 60 + i for i, sound in enumerate(DRUM_SOUNDS)}
selected_midi_in_name = ""
selected_midi_out_name = ""
selected_audio_out_name = ""


# --- MIDI Listener Thread ---
class MidiListener(threading.Thread):
    """
    A background thread that listens for MIDI transport messages (start/stop)
    on a selected port.
    """
    def __init__(self):
        super().__init__(daemon=True)
        self.port_name = None
        self.port = None
        self._lock = threading.Lock()

    def set_port_name(self, name):
        """Thread-safely sets the name of the MIDI port to listen to."""
        with self._lock:
            if self.port_name != name:
                self.port_name = name
                if self.port:
                    self.port.close()
                    self.port = None

    def run(self):
        """
        The main loop for the thread. It periodically checks if it should be
        listening and handles opening/closing the MIDI port and processing messages.
        """
        while True:
            time.sleep(0.1)
            with self._lock:
                port_name = self.port_name

            # If we shouldn't be listening, ensure the port is closed and skip.
            if not dpg.get_value("follow_midi_checkbox") or not port_name:
                if self.port:
                    self.port.close()
                    self.port = None
                continue

            # If we should be listening but the port is not open, open it.
            if not self.port:
                try:
                    self.port = mido.open_input(port_name)
                    print(f"Opened MIDI input port: {port_name}")
                except Exception as e:
                    print(f"Could not open MIDI input port {port_name}: {e}")
                    self.port_name = None # Stop trying to open a broken port.
                    continue

            # Process all pending MIDI messages.
            for msg in self.port.iter_pending():
                if msg.type == 'start' and not is_playing:
                    play_stop_sequencer()
                elif msg.type == 'stop' and is_playing:
                    play_stop_sequencer()


# --- Sequencer Logic ---
class Sequencer(threading.Thread):
    """
    The main sequencer thread. It drives the timing of the application based
    on the BPM and calls a callback function on each 'tick'.
    """
    def __init__(self, bpm_getter, tick_callback):
        super().__init__(daemon=True)
        self.bpm_getter = bpm_getter
        self.tick_callback = tick_callback
        self._running = threading.Event()

    def run(self):
        """
        The main loop for the sequencer. Sleeps for a duration calculated from
        the BPM, then calls the tick_callback to advance the sequence.
        """
        while True:
            self._running.wait() # Pauses thread until start_seq() is called
            if not self._running.is_set(): # Exit loop if stop_seq() was called
                break

            bpm = self.bpm_getter()
            if bpm <= 0:
                time.sleep(0.1) # Avoid division by zero if BPM is invalid
                continue

            # Calculate time per 16th note (a quarter note divided by 4)
            time_per_step = 60.0 / bpm / 4.0

            self.tick_callback()

            time.sleep(time_per_step)

    def start_seq(self):
        """Starts the sequencer."""
        self._running.set()

    def stop_seq(self):
        """Stops the sequencer and resets all pattern states."""
        self._running.clear()
        # Reset all steps immediately on stop to clear highlighting
        for sound in DRUM_SOUNDS:
            if pattern_data[sound]["current_step"] != -1:
                 # De-highlight last step
                dpg.set_value(f"step_{sound}_{pattern_data[sound]['current_step']}", False)
            pattern_data[sound]["current_step"] = -1
            pattern_data[sound]["direction"] = 1
        global is_playing
        is_playing = False
        dpg.set_item_label("play_button", "Play")

# --- Audio Engine & Synthesizer Definitions ---
audio_server = None
synths = {}

class BellSynth:
    """
    A 3-oscillator FM synth for a bell sound.
    Signal path: env -> mod2 -> mod1 -> carrier -> out
    """
    def __init__(self, base_freq=300):
        # Controllable parameters are Pyo 'Sig' objects for smooth real-time changes.
        self.base_freq = Sig(base_freq)
        self.decay = Sig(0.5)
        self.ratio = Sig(1.4)
        self.index = Sig(10)

        # Main amplitude envelope
        self.env = Adsr(attack=0.001, decay=self.decay, sustain=0, release=0.1, mul=0.3)
        # Modulator 2 (controls modulator 1)
        self.mod2 = Sine(freq=self.base_freq * self.ratio * self.ratio, mul=self.env * self.index)
        # Modulator 1 (controls carrier)
        self.mod1 = Sine(freq=self.base_freq * self.ratio, mul=self.mod2)
        # Carrier oscillator
        self.carrier = Sine(freq=self.base_freq + self.mod1, mul=self.env).out()

    def play(self):
        """Triggers the synth's envelope."""
        self.env.play()

    def set(self, param, value):
        """Sets a synth parameter."""
        if param == 'decay': self.decay.setValue(value)
        elif param == 'ratio': self.ratio.setValue(value)
        elif param == 'index': self.index.setValue(value)
        elif param == 'freq': self.base_freq.setValue(value)

class SnareSynth:
    """
    A snare synth with two filtered noise sources and a feedback delay.
    Signal path: (Noise1 -> Filter1) + (Noise2 -> Filter2) -> Mixer -> Delay -> Out
    """
    def __init__(self):
        # Controllable parameters as signals
        self.decay = Sig(0.25)
        self.noise_mix = Sig(0.5) # 0 for snap (high-pass), 1 for body (band-pass)
        self.snap_freq = Sig(3000)
        self.body_freq = Sig(200)
        self.feedback = Sig(0.12)

        # Master envelope
        self.env = Adsr(attack=0.001, decay=self.decay, sustain=0, release=0.05, mul=0.4)

        # Noise sources
        self.snap_noise = PinkNoise(mul=self.env)
        self.body_noise = BrownNoise(mul=self.env)

        # Filters
        self.snap_filter = Biquad(self.snap_noise, freq=self.snap_freq, q=3, type=2) # High-pass
        self.body_filter = Biquad(self.body_noise, freq=self.body_freq, q=1.5, type=5) # Band-pass

        # Mixer for the two noise components
        self.noise_mixer = Crossfade(self.snap_filter, self.body_filter, cross=self.noise_mix)

        # Micro-delay with feedback to add metallic resonance
        self.delay = Delay(self.noise_mixer, delay=0.005, feedback=self.feedback)

        # Final output mixer - mix dry and delayed signal
        self.output = Mix([self.noise_mixer, self.delay], voices=2, mul=0.8).out()

    def play(self):
        """Triggers the synth's envelope."""
        self.env.play()

    def set(self, param, value):
        """Sets a synth parameter."""
        if param == 'decay': self.decay.value = value
        elif param == 'noise_mix': self.noise_mix.value = value
        elif param == 'snap_freq': self.snap_freq.value = value
        elif param == 'body_freq': self.body_freq.value = value
        elif param == 'feedback': self.feedback.value = value

class FmKickSynth:
    """
    A classic FM kick synth using a pitch envelope and two oscillators.
    Signal path: pitchenv -> mod -> car -> ampenv -> out
    """
    def __init__(self):
        # Controllable parameters
        self.decay = Sig(0.5)
        self.start_freq = Sig(150)
        self.end_freq = Sig(50)
        self.fm_amount = Sig(1.5) # Ratio-based

        # Pitch envelope: a sharp, exponential drop from start_freq to end_freq
        self.pitchenv = Expseg([(0, 1), (0.01, 1), (self.decay, 0)], loop=False, mul=self.start_freq - self.end_freq, add=self.end_freq)

        # Amplitude envelope
        self.ampenv = Adsr(attack=0.001, decay=self.decay, sustain=0, release=0.05, mul=0.5)

        # FM oscillator setup
        self.mod = Sine(freq=self.pitchenv * self.fm_amount, mul=self.pitchenv * self.fm_amount * 5)
        self.car = Sine(freq=self.pitchenv + self.mod, mul=self.ampenv).out()

    def play(self):
        """Triggers the synth's envelopes."""
        self.pitchenv.play()
        self.ampenv.play()

    def set(self, param, value):
        """Sets a synth parameter."""
        if param == 'decay': self.decay.value = value
        elif param == 'start_freq': self.start_freq.value = value
        elif param == 'end_freq': self.end_freq.value = value
        elif param == 'fm_amount': self.fm_amount.value = value

def setup_audio():
    """Initializes the Pyo audio server and creates all synth instances."""
    global audio_server, synths
    # TODO: Add device selection logic from setup popup

    # Explicitly select the audio backend to avoid issues on systems without JACK.
    if sys.platform == "win32":
        print("Windows detected. Using 'DirectSound' audio backend.")
        audio_server = Server(audio="ds").boot()
    else:
        # For other systems (Linux, macOS), let Pyo choose the best backend.
        audio_server = Server().boot()

    # Create a synth instance for each sound defined in the constants.
    for sound in DRUM_SOUNDS:
        if sound == "Bell":
            synths[sound] = BellSynth()
        elif sound == "Snare":
            synths[sound] = SnareSynth()
        elif sound == "Kick (FM)":
            synths[sound] = FmKickSynth()
        else:
            # For unimplemented synths, use a simple placeholder sound.
            env = Adsr(attack=0.001, decay=0.2, sustain=0, release=0.01, dur=0.21)
            osc = Sine(freq=220, mul=env).out()
            synths[sound] = (osc, env)

def handle_trigger(sound_name):
    """
    Handles all actions for a triggered step, including playing audio
    and sending MIDI messages.
    """
    # 1. Trigger internal audio synth
    if sound_name in synths:
        synth = synths[sound_name]
        if hasattr(synth, 'play'):
            synth.play()
        elif isinstance(synth, tuple): # Handle placeholder synths
            synth[1].play()

    # 2. Send MIDI message if enabled
    if dpg.get_value("send_midi_checkbox"):
        global midi_out_port
        if midi_out_port:
            note = midi_note_map.get(sound_name, 60) # Default to note 60
            # Send Note On then immediately Note Off to simulate a drum hit
            msg_on = mido.Message('note_on', note=note, velocity=100)
            msg_off = mido.Message('note_off', note=note, velocity=0)
            try:
                midi_out_port.send(msg_on)
                midi_out_port.send(msg_off)
            except Exception as e:
                print(f"Error sending MIDI: {e}")


def update_synth_param(sender, app_data, user_data):
    """A generic callback for synth parameter sliders."""
    sound_name, param = user_data
    if sound_name in synths:
        synth = synths[sound_name]
        if hasattr(synth, 'set'):
            synth.set(param, app_data)

# --- GUI Callbacks ---
def open_setup_popup():
    """Opens the setup modal window."""
    dpg.show_item("setup_popup")

def apply_setup_changes(sender, app_data, user_data):
    """
    Applies the settings from the setup popup, such as changing MIDI ports.
    """
    global selected_midi_in_name, selected_midi_out_name, midi_out_port, midi_listener

    new_midi_in_name = dpg.get_value("midi_in_combo")
    if new_midi_in_name:
        selected_midi_in_name = new_midi_in_name
        if midi_listener:
            midi_listener.set_port_name(selected_midi_in_name)

    new_midi_out_name = dpg.get_value("midi_out_combo")

    # Handle MIDI output port change, safely closing the old one if it exists.
    if new_midi_out_name != selected_midi_out_name or (new_midi_out_name and not midi_out_port):
        if midi_out_port:
            midi_out_port.close()
            midi_out_port = None
        if new_midi_out_name:
            try:
                midi_out_port = mido.open_output(new_midi_out_name)
                selected_midi_out_name = new_midi_out_name
                print(f"Opened MIDI output port: {selected_midi_out_name}")
            except Exception as e:
                print(f"Could not open MIDI output port {new_midi_out_name}: {e}")
                selected_midi_out_name = ""

    # TODO: Handle Audio Output device change (requires server reboot)

    dpg.hide_item("setup_popup")

def update_note_map(sender, app_data, user_data):
    """Callback for when a MIDI note mapping is changed in the setup popup."""
    sound_name = user_data
    midi_note_map[sound_name] = app_data

def toggle_step(sender, app_data, user_data):
    """Callback for clicking a step in the sequencer grid."""
    sound, step_index = user_data
    is_active = dpg.get_value(sender)
    pattern_data[sound]["steps"][step_index] = 1 if is_active else 0

def update_ui_on_tick():
    """
    The main callback from the sequencer thread. This function is the "heartbeat"
    of the application, advancing the sequence and updating the GUI.
    """
    triggers = []
    for sound in DRUM_SOUNDS:
        pattern = pattern_data[sound]

        # De-highlight the previous step before advancing
        if pattern["current_step"] != -1:
            is_on = pattern["steps"][pattern["current_step"]] == 1
            dpg.set_value(f"step_{sound}_{pattern['current_step']}", is_on)

        # Advance the step according to the current direction
        pattern["current_step"] += pattern["direction"]

        # Handle loop boundaries based on the selected mode
        if pattern["loop_mode"] == "->":
            if pattern["current_step"] >= pattern["length"]:
                pattern["current_step"] = 0
        elif pattern["loop_mode"] == "-><-":
            if pattern["current_step"] >= pattern["length"]:
                pattern["current_step"] = max(0, pattern["length"] - 2)
                pattern["direction"] = -1
            elif pattern["current_step"] < 0:
                pattern["current_step"] = 1 if pattern["length"] > 1 else 0
                pattern["direction"] = 1

        # If a pattern has 0 length, it shouldn't play.
        if pattern["length"] == 0:
            pattern["current_step"] = -1

        # Highlight the new current step
        if pattern["current_step"] != -1:
            dpg.set_value(f"step_{sound}_{pattern['current_step']}", True)
            # If the step is active, add it to the list of sounds to trigger
            if pattern["steps"][pattern["current_step"]] == 1:
                triggers.append(sound)

    # Trigger all sounds for the current beat
    if triggers:
        for sound in triggers:
            handle_trigger(sound)

def play_stop_sequencer():
    """Toggles the play/stop state of the sequencer."""
    global is_playing
    is_playing = not is_playing
    if is_playing:
        sequencer.start_seq()
        dpg.set_item_label("play_button", "Stop")
    else:
        sequencer.stop_seq()
        dpg.set_item_label("play_button", "Play")

def get_bpm():
    """Returns the current BPM from the GUI."""
    return dpg.get_value("bpm_input")

def change_pattern_length(sender, app_data, user_data):
    """Callback for changing a pattern's length."""
    sound = user_data
    pattern_data[sound]["length"] = app_data

def change_loop_mode(sender, app_data, user_data):
    """Callback for changing a pattern's loop mode."""
    sound = user_data
    pattern_data[sound]["loop_mode"] = app_data


# --- GUI Creation ---
dpg.create_context()

def setup_theme():
    """Sets a theme inspired by Apple Lisa Office System 1."""
    with dpg.theme() as global_theme:
        with dpg.theme_component(dpg.mvAll):
            # Window background
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (211, 211, 211), category=dpg.mvThemeCat_Core)
            # Widget backgrounds
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (230, 230, 230), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_Button, (200, 200, 200), category=dpg.mvThemeCat_Core)
            # Borders
            dpg.add_theme_color(dpg.mvThemeCol_Border, (50, 50, 50), category=dpg.mvThemeCat_Core)
            # Text
            dpg.add_theme_color(dpg.mvThemeCol_Text, (0, 0, 0), category=dpg.mvThemeCat_Core)
            # Styles
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 0, category=dpg.mvThemeCat_Core)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 0, category=dpg.mvThemeCat_Core)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 0, category=dpg.mvThemeCat_Core)
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 1, category=dpg.mvThemeCat_Core)
            dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 1, category=dpg.mvThemeCat_Core)
            dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 1, category=dpg.mvThemeCat_Core)

    dpg.bind_theme(global_theme)

def create_setup_popup():
    """Creates the setup modal window and all its widgets."""
    with dpg.window(label="Setup", modal=True, show=False, tag="setup_popup", width=600, height=400):
        dpg.add_text("Device and Note Configuration")
        dpg.add_separator()

        # --- Device Selection ---
        with dpg.group():
            dpg.add_text("Audio/MIDI Devices")
            try:
                midi_ins = mido.get_input_names()
                dpg.add_combo(midi_ins, label="MIDI Input", tag="midi_in_combo")
            except Exception as e:
                dpg.add_text(f"Could not get MIDI inputs: {e}")

            try:
                midi_outs = mido.get_output_names()
                dpg.add_combo(midi_outs, label="MIDI Output", tag="midi_out_combo")
            except Exception as e:
                dpg.add_text(f"Could not get MIDI outputs: {e}")

            # Audio device listing
            try:
                audio_devices_info = pa_get_devices_info()
                out_devices = []
                for i, device in audio_devices_info.items():
                    if device['host api name'] == 'ALSA' and device['max output chans'] > 0:
                         out_devices.append(f"{i}: {device['name']}")
                dpg.add_combo(out_devices, label="Audio Output", tag="audio_out_combo")
            except Exception as e:
                dpg.add_text(f"Could not get audio outputs: {e}")


        dpg.add_separator()

        # --- Note Mapping ---
        dpg.add_text("MIDI Note Mapping")
        with dpg.child_window(height=150):
            for sound in DRUM_SOUNDS:
                with dpg.group(horizontal=True):
                    dpg.add_text(f"{sound:<12}")
                    dpg.add_input_int(label="Note", default_value=midi_note_map[sound], width=100,
                                      callback=update_note_map, user_data=sound)

        dpg.add_separator()
        with dpg.group(horizontal=True):
            dpg.add_button(label="Apply", callback=apply_setup_changes)
            dpg.add_button(label="Cancel", callback=lambda: dpg.hide_item("setup_popup"))


def create_main_window():
    """Creates the main application window and its content."""
    with dpg.window(tag="Primary Window"):
        # --- Top Bar ---
        with dpg.group(horizontal=True):
            dpg.add_input_int(label="BPM", tag="bpm_input", default_value=DEFAULT_BPM, width=80)
            dpg.add_button(label="Play", tag="play_button", callback=play_stop_sequencer)
            dpg.add_checkbox(label="Follow MIDI", tag="follow_midi_checkbox")
            dpg.add_checkbox(label="Send MIDI", tag="send_midi_checkbox")
            dpg.add_button(label="Setup", callback=open_setup_popup)

        dpg.add_separator()

        # --- Main Content Area with Scrollbar ---
        with dpg.child_window(tag="MainContent", autosize_x=True, autosize_y=True):
            # --- Pattern Sequencer Section ---
            with dpg.group():
                dpg.add_text("Pattern Sequencer")
                for sound in DRUM_SOUNDS:
                    with dpg.group(horizontal=True):
                        dpg.add_text(f"{sound:<12}", tag=f"label_{sound}")
                        dpg.add_input_int(label="Length", default_value=DEFAULT_STEPS, width=80, min_value=0, max_value=MAX_STEPS,
                                          callback=change_pattern_length, user_data=sound)
                        dpg.add_combo(items=["->", "-><-"], label="Loop", default_value="->", width=80,
                                      callback=change_loop_mode, user_data=sound)
                        # Pattern grid
                        with dpg.group(horizontal=True):
                            for i in range(MAX_STEPS):
                                dpg.add_selectable(label=" ", width=20, height=20, tag=f"step_{sound}_{i}",
                                                   callback=toggle_step, user_data=(sound, i))


            dpg.add_separator()

            # --- Synth Parameters Section ---
            with dpg.group():
                dpg.add_text("Synth Parameters")
                # First row of synth controls
                with dpg.group(horizontal=True):
                    with dpg.group():
                        dpg.add_text("Kick (FM)")
                        dpg.add_slider_float(label="Decay", min_value=0.1, max_value=2.0, default_value=0.5, width=150,
                                             callback=update_synth_param, user_data=("Kick (FM)", "decay"))
                        dpg.add_slider_float(label="Start Freq", min_value=100, max_value=400, default_value=150, width=150,
                                             callback=update_synth_param, user_data=("Kick (FM)", "start_freq"))
                        dpg.add_slider_float(label="End Freq", min_value=30, max_value=100, default_value=50, width=150,
                                             callback=update_synth_param, user_data=("Kick (FM)", "end_freq"))
                        dpg.add_slider_float(label="FM Amount", min_value=0, max_value=5, default_value=1.5, width=150,
                                             callback=update_synth_param, user_data=("Kick (FM)", "fm_amount"))
                    with dpg.group():
                        dpg.add_text("Snare")
                        dpg.add_slider_float(label="Decay", min_value=0.01, max_value=1.0, default_value=0.25, width=150,
                                             callback=update_synth_param, user_data=("Snare", "decay"))
                        dpg.add_slider_float(label="Noise Mix", min_value=0, max_value=1, default_value=0.5, width=150,
                                             callback=update_synth_param, user_data=("Snare", "noise_mix"))
                        dpg.add_slider_float(label="Snap Freq", min_value=1000, max_value=8000, default_value=3000, width=150,
                                             callback=update_synth_param, user_data=("Snare", "snap_freq"))
                        dpg.add_slider_float(label="Body Freq", min_value=100, max_value=500, default_value=200, width=150,
                                             callback=update_synth_param, user_data=("Snare", "body_freq"))
                        dpg.add_slider_float(label="Feedback", min_value=0, max_value=0.95, default_value=0.12, width=150,
                                             callback=update_synth_param, user_data=("Snare", "feedback"))
                    with dpg.group():
                        dpg.add_text("Closed Hat")
                        dpg.add_slider_float(label="Decay", width=150)
                        dpg.add_slider_float(label="FM Amount", width=150)

                # Second row of synth params
                with dpg.group(horizontal=True):
                    with dpg.group():
                        dpg.add_text("Bell")
                        dpg.add_slider_float(label="Freq", min_value=50, max_value=1000, default_value=300, width=150,
                                             callback=update_synth_param, user_data=("Bell", "freq"))
                        dpg.add_slider_float(label="Decay", min_value=0.01, max_value=2.0, default_value=0.5, width=150,
                                             callback=update_synth_param, user_data=("Bell", "decay"))
                        dpg.add_slider_float(label="FM Ratio", min_value=0.1, max_value=5.0, default_value=1.4, width=150,
                                             callback=update_synth_param, user_data=("Bell", "ratio"))
                        dpg.add_slider_float(label="FM Index", min_value=0, max_value=30, default_value=10, width=150,
                                             callback=update_synth_param, user_data=("Bell", "index"))


            dpg.add_separator()

            # --- Modulation Section (Placeholder) ---
            with dpg.group():
                dpg.add_text("Modulation")
                with dpg.group(horizontal=True):
                    dpg.add_button(label="+ Mod")
                    dpg.add_text("Modulation Target:")
                    dpg.add_text("...", tag="mod_target_label")
                dpg.add_combo(items=["Mod 1", "Mod 2"], label="Edit Modulation")
                # Placeholder for envelope editor
                with dpg.drawlist(width=600, height=200):
                    dpg.draw_rectangle(pmin=(0, 0), pmax=(600, 200), color=(200, 200, 220), fill=(240, 240, 240))
                with dpg.group(horizontal=True):
                    dpg.add_button(label="Clear")
                    dpg.add_button(label="Random")
                    dpg.add_slider_float(label="Smooth", width=150)


# --- Main Application Logic ---
if __name__ == "__main__":
    # 1. Initialize backend systems
    setup_audio()

    # 2. Create the GUI
    setup_theme()
    create_main_window()
    create_setup_popup()

    # 3. Start background threads
    sequencer = Sequencer(bpm_getter=get_bpm, tick_callback=update_ui_on_tick)
    sequencer.start()

    midi_listener = MidiListener()
    midi_listener.start()

    # 4. Start the audio server
    audio_server.start()

    # 5. Setup and show the main window
    dpg.create_viewport(title='Polyrhythm Sequencer', width=WINDOW_WIDTH, height=WINDOW_HEIGHT)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("Primary Window", True)

    # 6. Start the GUI event loop
    while dpg.is_dearpygui_running():
        dpg.render_dearpygui_frame()

    # 7. Cleanup on exit
    if audio_server and audio_server.getIsStarted():
        audio_server.stop()
    if sequencer:
        sequencer.stop_seq()
        sequencer.join() # Wait for sequencer thread to finish
    # No need to join midi_listener as it's a daemon thread
    dpg.destroy_context()
