import customtkinter 
from gui.custom_widgets.ctk_tooltip import CTkToolTip
from tkinter import filedialog, END

class CreateBackupFrame(customtkinter.CTkFrame):
    def __init__(self, master, linker, config, **kwargs):
        super().__init__(master, **kwargs)
        self.linker = linker
        self.config = config
        self.create_widgets()
        self.bind_to_config()

    def create_widgets(self):
        self.login_settings_label = customtkinter.CTkLabel(self, text="Create Backup Settings", font=customtkinter.CTkFont(family="Inter", size=30, weight="bold"))
        self.login_settings_label.grid(row=0, column=0, columnspan =2, sticky="nw", padx=20, pady=20)

        self.create_downloadpath_widgets()
        self.create_filename_widgets()
        self.create_folders_widgets()
        self.linker.login = self

    def create_downloadpath_widgets(self):
        self.downloadpath = customtkinter.CTkLabel(self, text="Output Directory:", font=customtkinter.CTkFont(size=20, underline=True))
        self.downloadpath.grid(row=3, column=0, padx=40, pady=(20, 10), sticky="nw")

        self.downloadpath_entry = customtkinter.CTkEntry(self, font=customtkinter.CTkFont(family="Inter", size=16))
        self.downloadpath_entry.grid(row=4, column=0, columnspan=2, padx=(60,0), pady=(20, 10), sticky="nsew")

        self.downloadpath_button = customtkinter.CTkButton(self, width=50, text="Select", command = self.open_folder)
        self.downloadpath_button.grid(row=4, column=2, padx=20, pady=(20, 10), sticky="nsew")

    def create_filename_widgets(self):
        self.filename_label = customtkinter.CTkLabel(self, text="Filename")
        self.filename_label.grid(row=5, column=0)
        self.filename_entry = customtkinter.CTkEntry(self, font=customtkinter.CTkFont(family="Inter", size=16))
        self.filename_entry.grid(row=6, column=0, columnspan=2, padx=(60,0), pady=(20, 10), sticky="nsew")

    def create_folders_widgets(self):
        self.folders_label = customtkinter.CTkLabel(self, text="Game Folders:")
        self.folders_label.grid(row=7, column=0)

        self.userdata_checkbox = customtkinter.CTkCheckBox(self, text="Userdata")
        self.userdata_checkbox.grid(row=8, column=0)

        self.mods_checkbox = customtkinter.CTkCheckBox(self, text="mods")
        self.mods_checkbox.grid(row=8, column=1)
        
        self.bepinex_checkbox = customtkinter.CTkCheckBox(self, text="BepInEx")
        self.bepinex_checkbox.grid(row=8, column=2)

    def bind_to_config(self):
        self.config.bind(self.downloadpath_entry, ["CreateBackup", "OutputPath"])
        self.config.bind(self.filename_entry, ["CreateBackup", "Filename"])
        self.config.bind(self.userdata_checkbox, ["CreateBackup", "GameFolders", "UserData"])
        self.config.bind(self.mods_checkbox, ["CreateBackup", "GameFolders", "mods"])
        self.config.bind(self.bepinex_checkbox, ["CreateBackup", "GameFolders", "BepInEx"])

    def open_folder(self):
        folderpath = filedialog.askdirectory()
        if folderpath != "":
            self.downloadpath_entry.delete(0, END)
            self.downloadpath_entry.insert(0, folderpath)
            self.config.save_to_json(["CreateBackup", "OutputPath"])
