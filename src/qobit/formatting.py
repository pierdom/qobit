"""Text formatting shared by the Qobuz models and the UI."""


def khz_str(khz: float) -> str:
    """Format a kHz value, dropping the decimal when it's a whole number
    (``192`` -> ``"192"``, ``44.1`` -> ``"44.1"``). Callers must pass a value
    already scaled to kHz — StreamUrl.sampling_rate is stored in Hz while
    Album.maximum_sampling_rate is already kHz, so the conversion stays at
    the call site rather than being guessed here."""
    return f"{int(khz)}" if khz == int(khz) else f"{khz:.1f}"


def quality_text(bit_depth: int, khz: float) -> str:
    """The one way qobit writes a resolution: ``24-BIT · 96 kHz``."""
    return f"{bit_depth}-BIT · {khz_str(khz)} kHz"
