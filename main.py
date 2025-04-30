#region IMPORTS
from binance.enums import *
import tkinter as tk
from UI import CryptoBotGUI
from misc.config import trade_counter
from core.shared_state import get_gui_instance, set_gui_instance
#endregion 


        
def initialize_gui():
    root = tk.Tk()
    gui = CryptoBotGUI(root)
    set_gui_instance(gui)  # Set the global GUI instance
    #print("GUI instance set in shared_state:", get_gui_instance())
    return gui, root

if __name__ == "__main__":
    gui_instance, root = initialize_gui()
    gui_instance.update_trade_number(trade_counter)
    gui_instance.close_button.config(state=tk.DISABLED)  # Use the GUI instance
    root.mainloop()
    

