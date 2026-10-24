import struct

from htmod.errors import SignatureError
from htmod.patch import Anchor, BinaryMod, BytePatch


UI_VTABLE = 0x100B1730
SLOT93_OFF = 0x174
REMOVE_ME = 0x1004AA10
UI_STUB = 0x10050AF0
NEIGHBOR_SLOTS = {
    0x164: 0x10049140,
    0x168: 0x10050AB0,
    0x16C: 0x10049500,
    0x170: 0x10050E00,
}
STUB_BYTES = bytes.fromhex("6804b60c1051c781d407000000000000ff1528b20a1083c408c3")
REMOVE_ME_BYTES = bytes.fromhex("568bf18b8608080000")


class AIMod(BinaryMod):
    def __init__(self):
        super().__init__("ai", "game/sys/game.dll", ())

    def patches_for(self, image):
        slot = image.va_to_offset(UI_VTABLE + SLOT93_OFF, 4)
        if slot is None:
            raise SignatureError("ui_ai_car vfn[93] is not mapped to file bytes")
        anchors = []
        for offset, pointer in NEIGHBOR_SLOTS.items():
            neighbor = image.va_to_offset(UI_VTABLE + offset, 4)
            if neighbor is None:
                raise SignatureError(f"ui_ai_car neighbor slot {offset:#x} is not mapped")
            anchors.append(Anchor(neighbor, struct.pack("<I", pointer)))
        for address, expected, name in (
            (UI_STUB, STUB_BYTES, "ui error stub"),
            (REMOVE_ME, REMOVE_ME_BYTES, "ai_car remove_me"),
        ):
            offset = image.va_to_offset(address, len(expected))
            if offset is None:
                raise SignatureError(f"{name} is not mapped to file bytes")
            anchors.append(Anchor(offset, expected))
        return (BytePatch(
            "ui_ai_car vfn[93]",
            slot,
            struct.pack("<I", UI_STUB),
            struct.pack("<I", REMOVE_ME),
            tuple(anchors),
        ),)


MOD = AIMod()


def main():
    from htmod.patch import legacy_main
    return legacy_main(MOD)
