# https://github.com/pur1fying/blue_archive_auto_script/blob/master/core/utils.py
import logging
import sys
from typing import Union


class Logger:
    """
    Logger class for logging
    """

    COLORS = {
        "RESET": "\033[0m",
        "INFO": "\033[94m",     # blue
        "SUCCESS": "\033[92m",  # green
        "ERROR": "\033[91m",    # red
        "SKIPPED": "\033[93m",  # yellow/orange
        "REPLACED": "\033[93m",  # yellow/orange
        "RENAMED": "\033[93m",  # yellow/orange
        "REMOVED": "\033[93m",  # yellow/orange
    }

    def __init__(self):
        # Init logger box signal, logs and logger
        self.logs = ""

        # When running as script.exe stdout is block-buffered by default,
        # which causes QProcess in the GUI to only receive output after the
        # process exits. Reconfigure to line-buffered so each line is flushed
        # immediately. Only needed when there is no signalBus (i.e. script.exe).
        sys.stdout.reconfigure(line_buffering=True)
        self.logger = logging.getLogger("KAFFIO_Logger")
        formatter = logging.Formatter("%(levelname)s |%(category)s | %(message)s ")
        handler1 = logging.StreamHandler(stream=sys.stdout)
        handler1.setFormatter(formatter)
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(handler1)
        # Status Text: INFO, SUCCESS, ERROR, SKIPPED, REPLACED, RENAMED, REMOVED
        self.status = ['INFO', 'SUCCESS', 'ERROR', 'WARNING', 'SKIPPED', 'REPLACED', 'RENAMED', 'REMOVED']
        # Create a list with each status padded with spaces
        self.paddedStatus = [self.align(s) for s in self.status]
        # Status HTML: <b style="color:$color">status</b>
        self._line = '--------------------------------------------------------------------'

    def __out__(self, category: str, message: str, level: int = 1, raw_print=False) -> None:
        """
        Output log
        :param message: log message
        :param level: log level
        :return: None
        """
        # If raw_print is True, output log to logger box
        if raw_print:
            self.logs += message
            return

        while len(logging.root.handlers) > 0:
            logging.root.handlers.pop()

        # If logger box is not None, output log to logger box
        # else output log to console
        category = self.align(category)
        status = self.paddedStatus[level - 1]
        color_key = self.status[level - 1]
        color = self.COLORS.get(color_key, "")
        reset = self.COLORS["RESET"]    
        try:
            print(f"{color}{status} | {category} | {message}{reset}", flush=True)
        except OSError:
            pass

    def align(self, string, maxLength=8):
        space = ' '
        return f"{string}{space * (maxLength - len(string))}"

    def info(self, category: str, message: str) -> None:
        """
        :param message: log message

        Output info log
        """
        self.__out__(category, message, 1)

    def success(self, category: str, message: Union[str, Exception]) -> None:
        """
        :param message: log message

        Output error log
        """
        self.__out__(category, message, 2)

    def error(self, category: str, message: Union[str, Exception]) -> None:
        """
        :param message: log message

        Output error log
        """
        self.__out__(category, message, 3)

    def warning(self, category: str, message: str) -> None:
        """
        :param message: log message

        Output warn log
        """
        self.__out__(category, message, 4)

    def skipped(self, category: str, message: str) -> None:
        """
        :param message: log message

        Output warn log
        """
        self.__out__(category, message, 5)

    def replaced(self, category: str, message: str) -> None:
        """
        :param message: log message

        Output warn log
        """
        self.__out__(category, message, 6)

    def renamed(self, category: str, message: str) -> None:
        """
        :param message: log message

        Output warn log
        """
        self.__out__(category, message, 7)

    def removed(self, category: str, message: str) -> None:
        """
        :param message: log message

        Output warn log
        """
        self.__out__(category, message, 8)

    def line(self) -> None:
        """
        Output line
        """
        print('--------------------------------------------------------------------', flush=True)


logger = Logger()