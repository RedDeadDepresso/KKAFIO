import logging
import sys
from typing import Union


class Logger:
    """
    Logger class for logging
    """

    def __init__(self, signalBus=None):
        """
        :param logger_signal: Logger Box signal
        """
        # Init logger box signal, logs and logger
        # logger box signal is used to output log to logger box
        self.logs = ""
        self.logger_signal = signalBus.loggerSignal if signalBus else None
        self.logger = logging.getLogger("KAFFIO_Logger")
        formatter = logging.Formatter("%(levelname)s |%(category)s | %(message)s ")
        handler1 = logging.StreamHandler(stream=sys.stdout)
        handler1.setFormatter(formatter)
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(handler1)
        # Status Text: INFO, SUCCESS, ERROR, SKIPPED, REPLACED, RENAMED, REMOVED
        self.status = ['INFO', 'SUCCESS', 'ERROR', 'WARNING', 'SKIPPED', 'REPLACED', 'RENAMED', 'REMOVED']
        # Status Color: Blue, Red, Green, Orange
        self.statusColor = ['#2d8cf0', '#00c12b', '#ed3f14', '#f90', '#f90', '#f90', '#f90', '#f90']
        # Create a list with each status padded with spaces
        self.paddedStatus = [self.align(s) for s in self.status]
        # Status HTML: <b style="color:$color">status</b>
        self.statusHtml = [
            f'<b style="color:{_color};">{status}</b>'
            for _color, status in zip(self.statusColor, self.paddedStatus)]

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
            self.logger_signal.emit(message)
            return

        while len(logging.root.handlers) > 0:
            logging.root.handlers.pop()

        # If logger box is not None, output log to logger box
        # else output log to console
        category = self.align(category)
        if self.logger_signal is not None:
            message = message.replace('\n', '<br>').replace(' ', '&nbsp;')
            adding = (f'''
                    <div style="font-family: Consolas, monospace;color:{self.statusColor[level - 1]};">
                        {self.statusHtml[level - 1]} | {category} | {message}
                    </div>
                        ''')
            # self.logs += adding
            self.logger_signal.emit(adding)
        else:
            print(f'{self.paddedStatus[level - 1]} | {category} | {message}')

    def align(self, string, maxLength=8):
        space = ' '
        return f"{string}{space * (maxLength - len(string))}"

    def colorize(self, adding: str):
        for i, s in enumerate(self.paddedStatus):
            if s in adding:
                adding = (f'''
                        <div style="font-family: Consolas, monospace;color:{self.statusColor[i]};">
                            {adding.replace(' ', '&nbsp;')}
                        </div>
                            ''')
                # self.logs += adding
                self.logger_signal.emit(adding)
                return

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
        # While the line print do not need wrapping, we
        # use raw_print=True to output log to logger box
        if self.logger_signal is not None:
            self.__out__("",
                         '<div style="font-family: Consolas, monospace;color:#2d8cf0;">'
                         '--------------------------------------------------------------------'
                         '</div>', raw_print=True)
        else:
            print('--------------------------------------------------------------------')
