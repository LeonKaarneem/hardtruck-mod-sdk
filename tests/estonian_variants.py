import hashlib
from pathlib import Path


ESTONIAN_GAME_SHA256 = "d943600154c27be3f6ea8162e375c69bc27c477ab5bfc29c7ceebc8221c17653"
ESTONIAN_DX8_SHA256 = "7452e60974df84e533709fd80187931fa5a201073916e79fa1a22fb512665070"
ESTONIAN_GAME_LENGTH = 1007616
ESTONIAN_DX8_LENGTH = 57344
ESTONIAN_GAME_CHANGES = (
    {"offset": 63945, "original_hex": "75", "replacement_hex": "eb"},
    {"offset": 466256, "original_hex": "7e", "replacement_hex": "eb"},
    {"offset": 466328, "original_hex": "7f", "replacement_hex": "eb"},
)
ESTONIAN_DX8_CHANGES = (
    {"offset": 232, "original_hex": "0e80b6", "replacement_hex": "ee85d3"},
    {"offset": 37508, "original_hex": "0e80b6", "replacement_hex": "ee85d3"},
)


def invert_splices(pristine, changes):
    source = bytearray()
    source_cursor = 0
    pristine_cursor = 0
    for change in changes:
        original = bytes.fromhex(change["original_hex"])
        replacement = bytes.fromhex(change["replacement_hex"])
        unchanged_length = change["offset"] - source_cursor
        source.extend(pristine[pristine_cursor:pristine_cursor + unchanged_length])
        source.extend(original)
        source_cursor = change["offset"] + len(original)
        pristine_cursor += unchanged_length + len(replacement)
    source.extend(pristine[pristine_cursor:])
    return bytes(source)


def estonian_binary_images(payload):
    payload = Path(payload)
    game = invert_splices((payload / "game-0000.dll").read_bytes(), ESTONIAN_GAME_CHANGES)
    dx8 = invert_splices((payload / "dx8-stock.dll").read_bytes(), ESTONIAN_DX8_CHANGES)
    assert len(game) == ESTONIAN_GAME_LENGTH
    assert len(dx8) == ESTONIAN_DX8_LENGTH
    assert hashlib.sha256(game).hexdigest() == ESTONIAN_GAME_SHA256
    assert hashlib.sha256(dx8).hexdigest() == ESTONIAN_DX8_SHA256
    return game, dx8
