# Languages

The GUI (task names, option labels, dialogs, etc.) and the [right-click context menu](context-menu.md) are available in English, Simplified Chinese, Traditional Chinese, Japanese, Korean, and Russian. Pick a language in the GUI's settings, or in the language prompt shown when registering the context menu via `kkafio_setup.bat`.

The native dialogs that Python opens during a task (the Copy/Paste dialog, password prompts, and the Review Similar Characters window) are translated too. They follow the language chosen in the GUI's settings; if the GUI is set to follow the system, they use your operating system's display language, falling back to English.

Everything else — CLI output, log files, and error messages — is English-only, and intentionally so: keeping logs in one language makes them far easier to search for, share when reporting a bug, and debug against the source.
