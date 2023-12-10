import sys
import json
import customtkinter

class Config:
    def __init__(self, linker, config_file):
        self.linker = linker
        self.config_file = config_file
        self.config_data = self.read()
        self.linker.widgets = self.set_values_to_none(self.config_data)
        linker.config = self

    def read(self):
        # Read the JSON file
        try:
            with open(self.config_file, 'r') as json_file:
                config_data = json.load(json_file)
            return config_data
        except FileNotFoundError:
            print(f"Config file '{self.config_file}' not found.")
            sys.exit(1)
        except json.JSONDecodeError:
            print(f"Invalid JSON format in '{self.config_file}'.")
            sys.exit(1)

    def set_values_to_none(self, input_dict):
        result = {}
        for key, value in input_dict.items():
            if isinstance(value, dict):
                result[key] = self.set_values_to_none(value)
            else:
                result[key] = None
        return result
    
    def load_config(self, widgets=None, config_data=None):
        if widgets == None:
            widgets = self.linker.widgets
            config_data = self.config_data
        for key in widgets:
            if isinstance(widgets[key], dict) and isinstance(config_data[key], dict):
                self.load_config(widgets[key], config_data[key])
            else:
                if widgets[key] is not None:
                    if isinstance(widgets[key], customtkinter.CTkCheckBox):
                        if config_data[key] == True:
                            widgets[key].select()
                        else:
                            widgets[key].deselect()
                    elif isinstance(widgets[key], customtkinter.CTkEntry):
                        widgets[key].insert(0, config_data[key])
                    else:                    
                        widgets[key].set(config_data[key])

    def bind(self, widget, list_keys):

        if isinstance(widget, customtkinter.CTkEntry):
            widget.bind("<KeyRelease>", lambda event, x=list_keys: self.save_to_json(x))
        elif isinstance(widget, (customtkinter.CTkCheckBox)):
            widget.configure(command=lambda x=list_keys: self.save_to_json(x))
        else:
            widget.configure(command=lambda x, y=list_keys: self.save_to_json(y))

        widgets_dictionary = self.linker.widgets
        for key in list_keys[:-1]:
            widgets_dictionary = widgets_dictionary[key]
        widgets_dictionary[list_keys[-1]] = widget

    def save_to_json(self, list_keys):
        widget = self.linker.widgets
        data = self.config_data
        for i in list_keys[:-1]:
            widget = widget[i]
            data = data[i]
        widget = widget[list_keys[-1]] 
        value = widget.get()
        if isinstance(widget, customtkinter.CTkCheckBox):
            value = True if value==1 else False
        data[list_keys[-1]] = value
        self.save_file("Configuration")

    def save_file(self, name=None):
        with open("config.json", "w") as config_file:
            json.dump(self.config_data, config_file, indent=2)
        if name:    
            self.linker.show_notification(name)