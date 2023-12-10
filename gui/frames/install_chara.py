import customtkinter 
from gui.custom_widgets.ctk_tooltip import CTkToolTip
from tkinter import filedialog, END

class InstallCharaFrame(customtkinter.CTkFrame):
    def __init__(self, master, linker, config, **kwargs):
        super().__init__(master, **kwargs)
        self.linker = linker
        self.config = config
        self.create_widgets()
        self.bind_to_config()

    def create_widgets(self):
        self.login_settings_label = customtkinter.CTkLabel(self, text="Install Chara Settings", font=customtkinter.CTkFont(family="Inter", size=30, weight="bold"))
        self.login_settings_label.grid(row=0, column=0, columnspan =2, sticky="nw", padx=20, pady=20)

        self.create_downloadpath_widgets()
        self.create_conflict_widgets()
        self.create_password_widgets()
        self.linker.login = self

    def create_downloadpath_widgets(self):
        self.downloadpath = customtkinter.CTkLabel(self, text="Input Directory:", font=customtkinter.CTkFont(size=20, underline=True))
        self.downloadpath.grid(row=3, column=0, padx=40, pady=(20, 10), sticky="nw")

        self.downloadpath_entry = customtkinter.CTkEntry(self, font=customtkinter.CTkFont(family="Inter", size=16))
        self.downloadpath_entry.grid(row=4, column=0, columnspan=2, padx=(60,0), pady=(20, 10), sticky="nsew")

        self.downloadpath_button = customtkinter.CTkButton(self, width=50, text="Select", command = self.open_folder)
        self.downloadpath_button.grid(row=4, column=2, padx=20, pady=(20, 10), sticky="nsew")

    def create_conflict_widgets(self):
        self.conflict_label = customtkinter.CTkLabel(self, text="If file conflicts:", font=customtkinter.CTkFont(size=20))
        self.conflict_label.grid(row=6, column=0, padx=20, pady=(20, 10))

        self.conflict_dropdown = customtkinter.CTkOptionMenu(self, values=["Skip", "Replace", "Rename"])
        self.conflict_dropdown.grid(row=6, column=1, padx=20, pady=(20, 10))

    def create_password_widgets(self):
        self.password_label = customtkinter.CTkLabel(self, text="If password required for archives:", font=customtkinter.CTkFont(size=20), wraplength=200)
        self.password_label.grid(row=7, column=0, padx=20, pady=(20, 10))

        self.password_dropdown = customtkinter.CTkOptionMenu(self, values=["Skip", "Request Password"])
        self.password_dropdown.grid(row=7, column=1, padx=20, pady=(20, 10))

    def bind_to_config(self):
        self.config.bind(self.downloadpath_entry, ["InstallChara", "InputPath"])
        self.config.bind(self.conflict_dropdown, ["InstallChara", "FileConflicts"])
        self.config.bind(self.password_dropdown, ["InstallChara", "ArchivePassword"])

    def open_folder(self):
        folderpath = filedialog.askdirectory()
        if folderpath != "":
            self.downloadpath_entry.delete(0, END)
            self.downloadpath_entry.insert(0, folderpath)
            self.config.save_to_json(["InstallChara", "InputPath"])
