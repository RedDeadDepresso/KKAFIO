import subprocess
import threading
from gui.custom_widgets.ctk_notification import CTkNotification

class Linker:
    def __init__(self, master):
        self.config = None
        self.widgets = {}
        self.logger = None
        self.login = None
        # script.py process
        self.script = None
        self.master = master
        self.modules_dictionary = {}
        self.name_to_sidebar_frame = {}

    def terminate_script(self):
        # If process is running, terminate it
        self.script.terminate()
        self.script = None
        self.sidebar.start_button.configure(text="Start", fg_color = ['#3B8ED0', '#1F6AA5'])
        
    def start_stop(self):
        if hasattr(self, 'script') and self.script is not None:
            self.terminate_script()
        else:
            # If process is not running, start it
            self.script = subprocess.Popen(['python', 'script.py'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            threading.Thread(target=self.read_output).start()
            self.sidebar.start_button.configure(text="Stop", fg_color = "crimson")

    def read_output(self):
        while self.script is not None:
            line = self.script.stdout.readline().decode('utf-8')
            if line == "":
                if hasattr(self, 'script') and self.script is not None:
                    self.master.after(10, self.terminate_script)
                return

            # Check if line contains any log level
            for level, color in self.logger.log_level_colors.items():
                if level in line:
                    # Display output in text box with color
                    self.logger.log_textbox.configure(state="normal")           
                    if level == "[MSG]":
                        self.logger.log_textbox.insert("end", "-" * 87 + "\n", level)
                        line = line.replace("[MSG]", "")
                        self.logger.log_textbox.insert("end", line, level)
                    elif level == "[INFO]" and "Start Task:" in line:
                        self.logger.log_textbox.insert("end", "*" * 87 + "\n", level)
                        self.logger.log_textbox.insert("end", line, level)
                    else:
                        self.logger.log_textbox.insert("end", line, level)
                    self.logger.log_textbox.configure(state="disabled")
                    break

            if self.logger.autoscroll_enabled:
                self.logger.log_textbox.yview_moveto(1.0)

    def show_notification(self, name):
        sidebar_frame = self.name_to_sidebar_frame[name]
        if self.script:
            new_notification = CTkNotification(text= f"{name} was saved but will be read by the script in the next run.", master=sidebar_frame, fg_color="orange")
        else:
            new_notification = CTkNotification(text= f"{name} was saved successfully.", master=sidebar_frame, fg_color="green")
        new_notification.grid(row=0, column=0, sticky="nsew")
        self.sidebar.master.after(2500, new_notification.destroy)