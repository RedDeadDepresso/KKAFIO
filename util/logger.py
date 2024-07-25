class Logger(object):

    @classmethod
    def log_msg(cls, type, msg):
        print(f"[MSG][{type}] {msg}")

    @classmethod
    def log_success(cls, type, msg):
        print(f"[SUCCESS][{type}] {msg}")

    @classmethod
    def log_skipped(cls, type, msg):
        print(f"[SKIPPED][{type}] {msg}")

    @classmethod
    def log_replaced(cls, type, msg):
        print(f"[REPLACED][{type}] {msg}")

    @classmethod
    def log_renamed(cls, type, msg):
        print(f"[RENAMED][{type}] {msg}")

    @classmethod
    def log_removed(cls, type, msg):
        print(f"[REMOVED][{type}] {msg}")

    @classmethod
    def log_error(cls, type, msg):
        print(f"[ERROR][{type}] {msg}")

    @classmethod
    def log_info(cls, type, msg):
        print(f"[INFO][{type}] {msg}")

