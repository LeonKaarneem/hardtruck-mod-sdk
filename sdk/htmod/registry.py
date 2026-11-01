import re


BINARY_HANDLERS = frozenset({"camera", "physics", "ai", "ui_keyboard", "dx8_vsync", "frame_cap"})


def strip_r_renderer(data):
    text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    sanitized = re.sub(
        r"^[ \t]*uset\s+r_renderer\s+.*(?:\n|$)",
        "",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return sanitized.encode("utf-8")


TRANSFORMS = {"strip_r_renderer": strip_r_renderer}
