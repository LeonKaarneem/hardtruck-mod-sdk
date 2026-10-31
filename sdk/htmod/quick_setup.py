import re

from .errors import PatchError
from .manifest import ID_PATTERN


QUICK_SETUP_DESTINATION = "game/usr/ui/cosetup.sc"
QUICK_SETUP_LABELS = {
    "config": "configuration",
    "camera": "camera",
    "physics": "physics",
    "handling_defs": "handling",
    "economy_def": "economy",
    "driver_def": "driver",
    "ai": "AI",
    "ui_keyboard": "keyboard",
    "dx8_vsync": "DX8 vsync",
}
QUICK_SETUP_ORDER = tuple(QUICK_SETUP_LABELS)
INFO_BLOCK = re.compile(
    br'(\tspawn txt_common \{\r\n\t\tlook:"25"\r\n\t\tvalue:")([^"\r\n]*)("\r\n)'
)
GENERIC_TEXT = b"<br>Features depend on Setup choices"
DYNAMIC_TEXT = b"<br>Enabled:"


def component_label(mod_id):
    if mod_id in QUICK_SETUP_LABELS:
        return QUICK_SETUP_LABELS[mod_id]
    label = mod_id.replace("_", " ")
    if len(label) > 30:
        label = label[:27].rstrip() + "..."
    return label


def enabled_lines(selected):
    selected = tuple(selected)
    if len(set(selected)) != len(selected):
        raise PatchError("Quick Setup selection contains duplicate mod IDs")
    for mod_id in selected:
        if not isinstance(mod_id, str) or not ID_PATTERN.fullmatch(mod_id):
            raise PatchError(f"Quick Setup selection has unsafe mod ID {mod_id!r}")
    selected_set = set(selected)
    ordered = [mod_id for mod_id in QUICK_SETUP_ORDER if mod_id in selected_set]
    ordered.extend(mod_id for mod_id in selected if mod_id not in QUICK_SETUP_LABELS)
    labels = [component_label(mod_id) for mod_id in ordered]
    if not labels:
        return ["Enabled: none"]

    lines = []
    line = "Enabled: "
    for index, label in enumerate(labels):
        token = label if index == 0 else ", " + label
        width = 40 if not lines else 30
        if index and len(line) + len(token) > width:
            lines.append(line + ",")
            line = label
        else:
            line += token
    lines.append(line)
    return lines


def render_quick_setup(template, selected):
    match = INFO_BLOCK.search(template)
    if match is None or INFO_BLOCK.search(template, match.end()) is not None:
        raise PatchError("config ui/cosetup.sc has no unique look25 information block")
    value = match.group(2)
    if GENERIC_TEXT not in value and DYNAMIC_TEXT not in value:
        raise PatchError("config ui/cosetup.sc information block has no replaceable text")
    header = value.split(b"<br>", 1)[0]
    if header + b"<br>" != b"UNOFFICIAL MOD - Hard Truck Modern 0.2.0<br>":
        raise PatchError("config ui/cosetup.sc information header is missing")
    dynamic = "<br>".join(enabled_lines(selected)).encode("ascii")
    replacement = match.group(1) + header + DYNAMIC_TEXT + dynamic.removeprefix(b"Enabled:") + match.group(3)
    return template[:match.start()] + replacement + template[match.end():]
