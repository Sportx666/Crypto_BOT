#region IMPORTS
from binance.enums import *
import customtkinter as ctk
from UI_new import CryptoBotGUI
from misc.config import trade_counter
from core.shared_state_v2 import get_gui_instance, set_gui_instance
#endregion


def initialize_gui():
    root = ctk.CTk()          # was tk.Tk() — CTk needed for dark theme
    gui = CryptoBotGUI(root)
    set_gui_instance(gui)
    return gui, root

if __name__ == "__main__":
    gui_instance, root = initialize_gui()
    gui_instance.update_trade_number(trade_counter)
    gui_instance.close_button.config(state="disabled")   # uses CompatButton.config()
    root.mainloop()
