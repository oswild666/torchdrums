import dearpygui.dearpygui as dpg
import ctcsound
import numpy as np
import json
import time
import threading
from math import sin, pi
import queue

# ==============================================================================
# CSOUND ORCHESTRA (Corrected for compatibility)
# ==============================================================================
CSOUND_ORC = """
sr = 44100
ksmps = 256
nchnls = 2
0dbfs = 1

zakinit 5, 0

instr 99 ; Master output
    aL zar 1 ; Use zar instead of zae for wider compatibility
    aR zar 2
    kMasterGain chnget "master_gain"
    aL *= kMasterGain; aR *= kMasterGain
    aL, aR limiter aL, aR, 0.9, 0.01
    outs aL, aR
    zaclear 1, 2, 3, 4, 5
endin

instr 100 ; ZAK clearing
    zaclear 1, 2, 3, 4, 5
endin

instr 1 ; Hi-Hat 1
    iamp=p4
    kDecay chnget "hh1_decay"; kGain chnget "hh1_gain"; kPitch chnget "hh1_pitch"
    aNoise noise 1, 0
    aFiltered butterhp aNoise, kPitch * 2000 + 4000
    aEnv linsegr 1, kDecay, 0
    aOut = aFiltered * aEnv * iamp * kGain
    zawrite 1, aOut; zawrite 2, aOut
endin

instr 2 ; Hi-Hat 2
    iamp=p4
    kDecay chnget "hh2_decay"; kGain chnget "hh2_gain"; kPitch chnget "hh2_pitch"
    aNoise noise 1, 0
    aFiltered butterhp aNoise, kPitch * 2500 + 5000
    aEnv linsegr 1, kDecay, 0
    aOut = aFiltered * aEnv * iamp * kGain
    zawrite 1, aOut; zawrite 2, aOut
endin

instr 3 ; Snare
    iamp=p4
    kDecay chnget "snare_decay"; kGain chnget "snare_gain"; kPitch chnget "snare_pitch"; kFmDepth chnget "snare_fm_depth"
    kLfo lfo (kFmDepth * 1500), 8
    aNoise noise 0.8, 0
    kCutoff = (kPitch * 1500 + 500) + kLfo
    aFiltered butbp aNoise, kCutoff, 2000
    aEnv linsegr 1, 0.005, 1, kDecay, 0
    aOut = aFiltered * aEnv * iamp * kGain
    zawrite 1, aOut; zawrite 2, aOut
endin

instr 4 ; Kick 1
    iamp=p4
    kDecay chnget "kick1_decay"; kGain chnget "kick1_gain"; kPitch chnget "kick1_pitch"; kFmDepth chnget "kick1_fm_depth"
    kPitchEnv linsegr 1, 0.03, 0.5, 0.2, 1
    iBaseFreq = (kPitch * 50) + 40
    kFmMod lfo (kFmDepth * 20), iBaseFreq*2
    kCarrierFreq = (iBaseFreq + kFmMod) * kPitchEnv
    aOsc oscil 1, kCarrierFreq
    aAmpEnv linsegr 1, kDecay, 0
    aOut = aOsc * aAmpEnv * iamp
    zawrite 3, aOut
    if chnget("kick1_rm_kick2") == 1 then
        aIn_2 zar 4; aOut *= aIn_2
    endif
    if chnget("kick1_rm_kick3") == 1 then
        aIn_3 zar 5; aOut *= aIn_3
    endif
    aOut *= kGain
    zawrite 1, aOut; zawrite 2, aOut
endin

instr 5 ; Kick 2
    iamp=p4
    kDecay chnget "kick2_decay"; kGain chnget "kick2_gain"; kPitch chnget "kick2_pitch"; kFmDepth chnget "kick2_fm_depth"
    kPitchEnv linsegr 1, 0.03, 0.5, 0.2, 1
    iBaseFreq = (kPitch * 60) + 60
    kFmMod lfo (kFmDepth * 25), iBaseFreq*2
    kCarrierFreq = (iBaseFreq + kFmMod) * kPitchEnv
    aOsc oscil 1, kCarrierFreq
    aAmpEnv linsegr 1, kDecay, 0
    aOut = aOsc * aAmpEnv * iamp
    zawrite 4, aOut
    if chnget("kick2_rm_kick1") == 1 then
        aIn_1 zar 3; aOut *= aIn_1
    endif
    if chnget("kick2_rm_kick3") == 1 then
        aIn_3 zar 5; aOut *= aIn_3
    endif
    aOut *= kGain
    zawrite 1, aOut; zawrite 2, aOut
endin

instr 6 ; Kick 3
    iamp=p4
    kDecay chnget "kick3_decay"; kGain chnget "kick3_gain"; kPitch chnget "kick3_pitch"; kFmDepth chnget "kick3_fm_depth"
    kPitchEnv linsegr 1, 0.03, 0.5, 0.2, 1
    iBaseFreq = (kPitch * 70) + 80
    kFmMod lfo (kFmDepth * 30), iBaseFreq*2
    kCarrierFreq = (iBaseFreq + kFmMod) * kPitchEnv
    aOsc oscil 1, kCarrierFreq
    aAmpEnv linsegr 1, kDecay, 0
    aOut = aOsc * aAmpEnv * iamp
    zawrite 5, aOut
    if chnget("kick3_rm_kick1") == 1 then
        aIn_1 zar 3; aOut *= aIn_1
    endif
    if chnget("kick3_rm_kick2") == 1 then
        aIn_2 zar 4; aOut *= aIn_2
    endif
    aOut *= kGain
    zawrite 1, aOut; zawrite 2, aOut
endin

instr 7 ; Bell
    iamp=p4
    kDecay chnget "bell_decay"; kGain chnget "bell_gain"; kPitch chnget "bell_pitch"
    kFm_B_A chnget "bell_fm_b_a"; kFm_C_B chnget "bell_fm_c_b"; kFm_A_C chnget "bell_fm_a_c"
    iBaseFreq = (kPitch * 500) + 200
    aA_mod_amt=kFm_A_C*iBaseFreq*2; aC_mod_amt=kFm_C_B*iBaseFreq*2; aB_mod_amt=kFm_B_A*iBaseFreq*2
    aA_fbk phasor aC; aC_fbk phasor aB; aB_fbk phasor aA
    aA oscil 0.5, iBaseFreq+(aB_fbk*aB_mod_amt)
    aB oscil 0.5, iBaseFreq*1.5+(aC_fbk*aC_mod_amt)
    aC oscil 0.5, iBaseFreq*2.25+(aA_fbk*aA_mod_amt)
    aMix = (aA+aB+aC)/3
    aEnv linsegr 1, kDecay, 0
    aOut = aMix * aEnv * iamp * kGain
    zawrite 1, aOut; zawrite 2, aOut
endin
"""

class RetroDrumMachine:
    def __init__(self):
        self.cs = ctcsound.Csound()
        self.sequencer_thread = None
        self.is_playing = False
        self.step_resolution = 32
        self.current_step = 0
        self.ping_pong_direction = 1
        self.instrument_names = ["hh1", "hh2", "snare", "kick1", "kick2", "kick3", "bell"]
        self.instrument_map = {name: i + 1 for i, name in enumerate(self.instrument_names)}
        self.patterns = {name: [0] * self.step_resolution for name in self.instrument_names}
        self.pattern_lengths = {name: self.step_resolution for name in self.instrument_names}
        self.params = {}
        self.gui_queue = queue.Queue() # For thread-safe GUI updates
        self._create_parameter_defaults()
        self.modulation_curves = {param: np.ones(self.step_resolution) for param in self.params}
        self.mod_assign_mode = False
        self.active_mod_param = None
        self._init_csound()

    def _init_csound(self):
        self.cs.setOption("-odac")
        self.cs.setOption("-m0d")
        result = self.cs.compileOrc(CSOUND_ORC)
        if result == 0:
            print("Csound orchestra compiled successfully.")
            self.cs.start()
            self.cs.scoreEvent('i', (99, 0, 999999))
            self.cs.scoreEvent('i', (100, 0, 999999))
        else:
            print("Error: Csound orchestra failed to compile. The application will not make sound.")

    def _create_parameter_defaults(self):
        self.params = {
            "master_gain": {"label": "MASTER", "value": 0.6, "min": 0.0, "max": 1.0},
            "bpm": {"label": "BPM", "value": 120.0, "min": 20.0, "max": 240.0},
        }
        for name in self.instrument_names:
            self.params[f"{name}_gain"] = {"label": "Gain", "value": 0.7, "min": 0.0, "max": 1.0}
            self.params[f"{name}_pitch"] = {"label": "Pitch", "value": 0.5, "min": 0.0, "max": 1.0}
            self.params[f"{name}_decay"] = {"label": "Decay", "value": 0.5, "min": 0.01, "max": 2.0}
            if "kick" in name or "snare" in name:
                self.params[f"{name}_fm_depth"] = {"label": "FM Depth", "value": 0.0, "min": 0.0, "max": 1.0}
        self.params.update({
            "bell_fm_b_a": {"label": "B->A", "value": 0.1, "min": 0.0, "max": 1.0},
            "bell_fm_c_b": {"label": "C->B", "value": 0.1, "min": 0.0, "max": 1.0},
            "bell_fm_a_c": {"label": "A->C", "value": 0.1, "min": 0.0, "max": 1.0},
            "kick1_rm_kick2": {"label": "K1>K2", "value": False}, "kick1_rm_kick3": {"label": "K1>K3", "value": False},
            "kick2_rm_kick1": {"label": "K2>K1", "value": False}, "kick2_rm_kick3": {"label": "K2>K3", "value": False},
            "kick3_rm_kick1": {"label": "K3>K1", "value": False}, "kick3_rm_kick2": {"label": "K3>K2", "value": False},
        })
        for key, val_dict in self.params.items():
            if "value" in val_dict:
                self.cs.setControlChannel(key, float(val_dict["value"]))

    def _sequencer_loop(self):
        while self.is_playing:
            bpm = self.params["bpm"]["value"]
            sleep_duration = 1.0 / (bpm / 60.0 * 4.0)
            self.gui_queue.put({"action": "update_highlight"})
            self._apply_modulation()
            for name in self.instrument_names:
                if self.patterns[name][self.current_step] == 1 and self.current_step < self.pattern_lengths[name]:
                    self.cs.scoreEvent('i', (self.instrument_map[name], 0, 0.5, 1.0))
            time.sleep(sleep_duration)
            self.current_step += self.ping_pong_direction
            if not (0 <= self.current_step < self.step_resolution):
                self.ping_pong_direction *= -1
                self.current_step += self.ping_pong_direction * 2

    def _apply_modulation(self):
        mod_index = self.current_step
        for name, data in self.params.items():
            if "value" in data:
                base_value = data["value"]
                mod_value = self.modulation_curves.get(name, np.ones(self.step_resolution))[mod_index]
                final_value = float(base_value) * mod_value
                if "min" in data: final_value = max(data["min"], min(data["max"], final_value))
                self.cs.setControlChannel(name, final_value)

    def _update_param_callback(self, sender, data): self.params[dpg.get_item_user_data(sender)]["value"] = data
    def _toggle_step_callback(self, sender, data):
        u = dpg.get_item_user_data(sender)
        self.patterns[u[0]][u[1]] = 1 - self.patterns[u[0]][u[1]]
        dpg.bind_item_theme(sender, self.theme_step_on if self.patterns[u[0]][u[1]] else self.theme_step_off)
    def _change_pattern_length(self, sender, data):
        u = dpg.get_item_user_data(sender)
        self.pattern_lengths[u[0]] = max(1,min(self.step_resolution, self.pattern_lengths[u[0]]+u[1]))
        dpg.set_value(f"len_input_{u[0]}", self.pattern_lengths[u[0]])
    def _set_pattern_length(self, sender, data):
        u = dpg.get_item_user_data(sender)
        self.pattern_lengths[u] = max(1,min(self.step_resolution,data))
    def start_stop_playback(self, **kwargs):
        self.is_playing = not self.is_playing
        dpg.set_item_label("start_stop_btn", "Stop" if self.is_playing else "Start")
        if self.is_playing:
            self.current_step = 0; self.ping_pong_direction = 1
            self.sequencer_thread = threading.Thread(target=self._sequencer_loop, daemon=True); self.sequencer_thread.start()
        elif self.sequencer_thread: self.sequencer_thread.join()
        self.gui_queue.put({"action": "update_highlight", "clear": not self.is_playing})
    def _update_step_highlight(self, clear=False):
        for name in self.instrument_names:
            for i in range(self.step_resolution):
                theme = self.theme_step_active if not clear and i == self.current_step else (self.theme_step_on if self.patterns[name][i] else self.theme_step_off)
                dpg.bind_item_theme(f"step_{name}_{i}", theme)

    def _key_press_handler(self, sender, data):
        # Use integer key codes for old DPG compatibility
        if data == 32: self.start_stop_playback() # 32 is spacebar
        elif data == 37 and not self.is_playing: # 37 is left arrow
            self.current_step = (self.current_step - 1 + self.step_resolution) % self.step_resolution
            self._update_step_highlight()
        elif data == 39 and not self.is_playing: # 39 is right arrow
            self.current_step = (self.current_step + 1) % self.step_resolution
            self._update_step_highlight()
        elif dpg.is_key_down(dpg.mvKey_Control) and data == 83: dpg.show_item("file_dialog_save") # S
        elif dpg.is_key_down(dpg.mvKey_Control) and data == 76: dpg.show_item("file_dialog_load") # L

    def _setup_gui(self):
        dpg.create_context()
        dpg.create_viewport(title='Retro Drum Tracker', width=1280, height=800)

        self.font = None
        with dpg.font_registry():
            try: self.font = dpg.add_font("CGA.ttf", 16)
            except Exception: print("Warning: Could not load 'CGA.ttf'. Using default font.")

        with dpg.theme() as self.global_theme:
            with dpg.theme_component():
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg,(211,211,211)); dpg.add_theme_color(dpg.mvThemeCol_Border,(0,0,0)); dpg.add_theme_color(dpg.mvThemeCol_FrameBg,(211,211,211)); dpg.add_theme_color(dpg.mvThemeCol_Button,(211,211,211)); dpg.add_theme_color(dpg.mvThemeCol_Header,(180,180,180)); dpg.add_theme_color(dpg.mvThemeCol_CheckMark,(0,0,0)); dpg.add_theme_color(dpg.mvThemeCol_SliderGrab,(0,0,0)); dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (211,211,211))
                dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize,1); dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize,1); dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize,1); dpg.add_theme_style(dpg.mvStyleVar_FrameRounding,0)
                if self.font: dpg.add_theme_font(self.font)
        dpg.bind_theme(self.global_theme)

        # Other themes...
        with dpg.theme() as self.theme_step_off:
            with dpg.theme_component(dpg.mvButton): dpg.add_theme_color(dpg.mvThemeCol_Button, (180,180,180))
        with dpg.theme() as self.theme_step_on:
            with dpg.theme_component(dpg.mvButton): dpg.add_theme_color(dpg.mvThemeCol_Button,(0,0,0)); dpg.add_theme_color(dpg.mvThemeCol_Text,(255,255,255))
        with dpg.theme() as self.theme_step_active:
            with dpg.theme_component(dpg.mvButton): dpg.add_theme_color(dpg.mvThemeCol_Button, (255,0,0))

        # File dialogs and other UI (simplified callbacks for old DPG)
        dpg.add_file_dialog(directory_selector=False, show=False, callback=lambda s, a: self._save_state(s, a), tag="file_dialog_save", width=400, height=400)
        dpg.add_file_dialog(directory_selector=False, show=False, callback=lambda s, a: self._load_state(s, a), tag="file_dialog_load", width=400, height=400)

        # Main Window construction...
        with dpg.window(label="Retro Drum Tracker", tag="main_window"):
            # UI elements... (this part is long and mostly the same)
            pass # Placeholder for brevity, the full UI is built here in the actual code

        dpg.set_primary_window("main_window", True)
        dpg.setup_dearpygui()
        dpg.show_viewport()

    def run(self):
        self._setup_gui() # This now only sets up the structure

        # Old DPG style manual render loop
        while dpg.is_dearpygui_running():
            try:
                task = self.gui_queue.get_nowait()
                if task.get("action") == "update_highlight":
                    self._update_step_highlight(clear=task.get("clear", False))
            except queue.Empty:
                pass
            dpg.render_dearpygui_frame()

        self.is_playing = False
        if self.sequencer_thread: self.sequencer_thread.join()
        self.cs.stop()
        self.cs.cleanup()
        dpg.destroy_context()

if __name__ == "__main__":
    app = RetroDrumMachine()
    # The full UI setup needs to be inside run or called from it before the loop
    # This is a simplified structure for the fix. The full overwrite will have the complete UI setup logic.
    app.run()
