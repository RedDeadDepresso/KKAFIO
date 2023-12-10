import customtkinter
import platform
import customtkinter
import ctypes

from gui.frames.sidebar import Sidebar
from gui.frames.logger import LoggerTextBox
from gui.util.config import Config
from gui.util.linker import Linker

class App(customtkinter.CTk):
    def __init__(self):
        super().__init__("#18173C")
        self.configure_window()
        linker = Linker(self)
        config = Config(linker, "config.json")
        sidebar = Sidebar(self, linker, config, fg_color="#25224F")
        sidebar.grid(row=0, column=0, sticky="nsw")
        logger = LoggerTextBox(self, linker, config, fg_color="#262250")
        logger.grid(row=0, column=2, pady=20, sticky="nsew")
        config.load_config()

    def configure_window(self):
        self.title("KKAFIO")
        self.geometry(f"{1500}x{850}")
        self.iconbitmap('gui/icons/karin.ico')
        """ 
        solution to Settings Frame and Logger Frame widths not being 
        consistent between different windows scaling factors
        """
        self.scaling_factor = self.get_scaling_factor()
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=0, minsize=650*self.scaling_factor)
        self.grid_columnconfigure(2, weight=1, minsize=506*self.scaling_factor)
        self.grid_rowconfigure(0, weight=1)

    def get_scaling_factor(self):
        system = platform.system()
        
        if system == 'Windows':
            user32 = ctypes.windll.user32
            return user32.GetDpiForSystem() / 96.0
        
        return 1.0  # Default scaling factor for unknown or unsupported systems

if __name__ == "__main__":
    app = App()
    app.mainloop()
