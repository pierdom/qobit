import logging

# textual-image asks the terminal for its cell size at import and, when the
# terminal doesn't answer (the Linux console, a plain pty), logs a warning
# with a traceback. With no logging set up, Python prints it to stderr just
# before the TUI starts. The fallback it then uses is fine: keep it quiet.
logging.getLogger("textual_image").setLevel(logging.ERROR)
