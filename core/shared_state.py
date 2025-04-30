gui_instance = None

def set_gui_instance(gui):
    global gui_instance
    gui_instance = gui

def get_gui_instance():
    return gui_instance
