import tkinter as tk
from tkinter import simpledialog


class PasswordDialog(simpledialog.Dialog):
    def __init__(self, title: str, content: str):
        self.content = content
        self.password = ''
        super().__init__(parent=None, title=title)

    def body(self, master):
        tk.Label(master, text=self.content, wraplength=300).pack(padx=10, pady=10)

        self.entry = tk.Entry(master)
        self.entry.pack(padx=10, pady=5)
        self.entry.focus_set()

        return self.entry  # initial focus

    def apply(self):
        self.password = self.entry.get()


def password_dialog(title: str, content: str) -> str:
    root = tk.Tk()
    root.withdraw()  # hide main window

    dialog = PasswordDialog(title, content)

    root.destroy()  # clean up Tk instance

    return dialog.password if dialog.password else ''


if __name__ == "__main__":
    password = password_dialog("Enter Password", "Please enter the archive password:")
    print(f"Password: {password}")
