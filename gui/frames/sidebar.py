import customtkinter
from PIL import Image
from gui.frames.install_chara import InstallCharaFrame
from gui.frames.remove_chara import RemoveCharaFrame
from gui.frames.fc_kks import FilterConvertKKSFrame
from gui.frames.create_backup import CreateBackupFrame
from gui.custom_widgets.ctk_tooltip import CTkToolTip
from tkinter import filedialog, END


class Sidebar(customtkinter.CTkFrame):
    def __init__(self, master, linker, config, **kwargs):
        self.master = master
        self.linker = linker
        self.config = config
        super().__init__(master=self.master, **kwargs)
        self.grid_rowconfigure((0, 1, 2), weight=1)
        self.grid_columnconfigure(0, weight=1)
        karin_logo = customtkinter.CTkImage(light_image=Image.open("gui/icons/karin.png"), size=(152,152))
        karin_logo_label = customtkinter.CTkLabel(self, image=karin_logo, text="")
        karin_logo_label.grid(row=0, column=0, sticky="nsew")
        self.gear_on = customtkinter.CTkImage(Image.open("gui/icons/gear_on.png"), size=(50,38))
        self.gear_off = customtkinter.CTkImage(Image.open("gui/icons/gear_off.png"), size=(50,38))
        self.create_module_frames()
        self.create_all_button_frame()
        self.create_gamepath_frame()
        self.create_start_button()
        self.create_notification_frames()
        self.linker.sidebar = self
        
    def create_module_frames(self):

        self.checkbox_frame = customtkinter.CTkFrame(self, fg_color="transparent", border_color="white", border_width=2)
        self.checkbox_frame.grid(row=1, column=0, columnspan=4, padx=10, pady=10, sticky="w")
        self.prettify = {
            "InstallChara": "Install Chara",
            "RemoveChara": "Remove Chara",
            "CreateBackup": "Create Backup",
            "FCKKS": "F&C KKS"
        }

        self.module_list = [["CreateBackup", CreateBackupFrame], ["FCKKS", FilterConvertKKSFrame], ["InstallChara", InstallCharaFrame], ["RemoveChara", RemoveCharaFrame]]               
        for index, sublist in enumerate(self.module_list):
            module = sublist[0]
            self.linker.modules_dictionary[module] = {}
            self.create_module_checkbox(module, index)
            self.create_module_button(module, index)
            frame = sublist[1](self.master, self.linker, self.config, fg_color="#262250") 
            self.linker.modules_dictionary[module]['frame'] = frame
        self.linker.modules_dictionary["CreateBackup"]["button"].configure(image=self.gear_on)
        self.linker.modules_dictionary["CreateBackup"]["checkbox"].configure(text_color="#53B9E9")   
        self.current_frame = self.linker.modules_dictionary["CreateBackup"]["frame"]  # Update the current frame
        self.current_frame.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")

    def create_module_checkbox(self, module, i):
        self.linker.modules_dictionary[module]['checkbox'] = customtkinter.CTkCheckBox(
            self.checkbox_frame, text=self.prettify[module], text_color="#FFFFFF", font=("Inter", 16), command=lambda x=[module, "Enable"]: self.config.save_to_json(x))
        self.linker.modules_dictionary[module]['checkbox'].grid(row=i, column=0, columnspan=2,padx=20, pady=(10, 5), sticky="nw")
        self.linker.widgets[module]['Enable'] = self.linker.modules_dictionary[module]['checkbox']

    def create_module_button(self, module, i):
        self.linker.modules_dictionary[module]['button'] = customtkinter.CTkButton(
            self.checkbox_frame, width=50, image=self.gear_off, text="", fg_color="transparent", command=lambda x=module: self.display_settings(module))
        self.linker.modules_dictionary[module]['button'].grid(row=i, column=1, padx=(40,0), pady=(2,0), sticky="nw")        

    def create_all_button_frame(self):
        self.select_all_button = customtkinter.CTkButton(self.checkbox_frame, width=100, text="Select All", fg_color="#DC621D", font=("Inter",20), command=self.select_all)
        self.select_all_button.grid(row=4, column=0, padx=10, pady=(15,20), sticky="w")
        self.clear_all_button = customtkinter.CTkButton(self.checkbox_frame, width=100, text="Clear All", fg_color="#DC621D", font=("Inter",20), command=self.clear_all)
        self.clear_all_button.grid(row=4, column=1, padx=10, pady=(15,20), sticky="w")

    def create_gamepath_frame(self):
        self.gamepath_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        self.gamepath_frame.grid(row=2, column=0)

        self.gamepath_label = customtkinter.CTkLabel(self.gamepath_frame, text="Game Directory", font=customtkinter.CTkFont(size=16, family="Inter", underline=True))
        self.gamepath_label.grid(row=0, column=0, padx=(0, 10), sticky="nw")
        
        self.gamepath_entry = customtkinter.CTkEntry(self.gamepath_frame, font=customtkinter.CTkFont(family="Inter", size=16))
        self.gamepath_entry.grid(row=1, column=0, columnspan=2)
        self.config.bind(self.gamepath_entry, ["Core", "GamePath"])
        self.gamepath_button = customtkinter.CTkButton(self.gamepath_frame, width=50, text="Select", command = self.open_folder)
        self.gamepath_button.grid(row=1, column=1)
    
    def create_start_button(self):
        self.start_button = customtkinter.CTkButton(self, text="Start", width=200, height=40, command=self.linker.start_stop, font=customtkinter.CTkFont(family="Inter", size=16))
        self.start_button.grid(row=3, column=0, pady=20, sticky="n")

    def create_notification_frames(self):
        for index, element in enumerate(["Template", "Queue", "Configuration"]):
            frame = customtkinter.CTkFrame(self, fg_color="transparent", height=50)
            if index == 0:
                top_pady=170
            else:
                top_pady=0
            frame.grid(row=3+index, column=0, sticky="s", pady=(top_pady,0))
            self.linker.name_to_sidebar_frame[element] = frame

    def select_all(self):
        for module in self.linker.modules_dictionary:
            self.linker.modules_dictionary[module]["checkbox"].select()
            self.config.config_data[module]["Enable"] = True
        self.config.save_file("Configuration")

    def clear_all(self):
        for module in self.linker.modules_dictionary:
            self.linker.modules_dictionary[module]["checkbox"].deselect()
            self.config.config_data[module]["Enable"] = False
        self.config.save_file("Configuration")

    def display_settings(self, module):
        for key in self.linker.modules_dictionary:
            if key == module:
                self.linker.modules_dictionary[key]["button"].configure(image=self.gear_on)
                self.linker.modules_dictionary[key]["checkbox"].configure(text_color="#53B9E9")                
                self.current_frame.grid_remove()  # Hide the current frame
                self.current_frame = self.linker.modules_dictionary[key]["frame"]  # Update the current frame
                self.current_frame.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")
            else:
                self.linker.modules_dictionary[key]["button"].configure(image=self.gear_off)
                self.linker.modules_dictionary[key]["checkbox"].configure(text_color="#FFFFFF")  

    def open_folder(self):
        folderpath = filedialog.askdirectory()
        if folderpath != "":
            self.gamepath_entry.delete(0, END)
            self.gamepath_entry.insert(0, folderpath)
            self.config.save_to_json(["Core", "GamePath"])
