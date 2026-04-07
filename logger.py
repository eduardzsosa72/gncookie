import logging
import re

_RE_OK = re.compile(r"(\[OK\])")
_RE_ERROR = re.compile(r"(\[ERROR\])")
_RE_WARN = re.compile(r"(\[WARN\])")
_RE_HEROSMS = re.compile(r"(\[HEROSMS\])")
_RE_STEP = re.compile(r"(\[\d+\])")
_RE_ARROW = re.compile(r"(-> .+)")
_RE_ELAPSED = re.compile(r"(time elapsed: \S+)", re.I)


class ColoredFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[36m",  # cyan
        logging.INFO: "\033[32m",  # green
        logging.WARNING: "\033[33m",  # yellow
        logging.ERROR: "\033[31m",  # red
        logging.CRITICAL: "\033[1;31m",  # bold red
    }
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    TAGS = {
        logging.DEBUG: "\033[36m[DBUG]\033[0m",
        logging.INFO: "\033[32m[INFO]\033[0m",
        logging.WARNING: "\033[33m[WARN]\033[0m",
        logging.ERROR: "\033[31m[ERR ]\033[0m",
        logging.CRITICAL: "\033[1;31m[CRIT]\033[0m",
    }

    def format(self, record):
        tag = self.TAGS.get(record.levelno, "[????]")
        color = self.COLORS.get(record.levelno, "")
        msg = record.getMessage()

        msg = _RE_OK.sub(f"\033[1;32m\\1{self.RESET}", msg)
        msg = _RE_ERROR.sub(f"\033[1;31m\\1{self.RESET}", msg)
        msg = _RE_WARN.sub(f"\033[1;33m\\1{self.RESET}", msg)
        msg = _RE_HEROSMS.sub(f"\033[35m\\1{self.RESET}", msg)
        msg = _RE_STEP.sub(f"{self.BOLD}\033[36m\\1{self.RESET}", msg)
        msg = _RE_ARROW.sub(f"{self.DIM}\\1{self.RESET}", msg)
        msg = _RE_ELAPSED.sub(f"\033[36m\\1{self.RESET}", msg)

        elapsed = ""
        if hasattr(record, "elapsed"):
            elapsed = f" {self.DIM}({record.elapsed:.2f}s){self.RESET}"

        return f"{tag} {color}{msg}{self.RESET}{elapsed}"


def beautiful_logger(name):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(ColoredFormatter())
    logger.handlers = []
    logger.addHandler(ch)
    logger.propagate = False
    return logger
