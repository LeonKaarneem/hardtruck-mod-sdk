import struct

from htmod.errors import SignatureError, StateError
from htmod.patch import Anchor, BinaryMod, BytePatch, Signature, find_unique


PATCHES = (
    {
        "name": "surf governor 400->40",
        "site": 0x2A2DB,
        "prefix": b"\xd9\x05",
        "orig": 0x100B0100,
        "patch": 0x100B049C,
        "patch_val_check": 40.0,
        "context_before": (0x2A2C9, bytes.fromhex("db4424108a86cd0a000084c0d95c2410")),
        "context_after": (0x2A2DF, bytes.fromhex("d8742410d99ecc090000")),
    },
    {
        "name": "full-stop snap |F|^2 10000->100",
        "site": 0x294DF,
        "prefix": b"\xd8\x1d",
        "orig": 0x100B00A0,
        "patch": 0x100ACFB0,
        "patch_val_check": 100.0,
        "context_before": (0x294D3, bytes.fromhex("d9442414d84c2414dec1")),
        "context_after": (0x294E3, bytes.fromhex("dfe0f6c4057a1a899e74090000")),
    },
    {
        "name": "trailer mass divisor 3->5",
        "site": 0x3014E,
        "prefix": b"\xd8\x35",
        "orig": 0x100AFEDC,
        "patch": 0x100B00B8,
        "patch_val_check": 5.0,
        "context_before": (0x30140, bytes.fromhex("d981bc0b0000d881b80b0000")),
        "context_after": (0x30152, b"\xc3"),
    },
)


class PhysicsMod(BinaryMod):
    def __init__(self):
        super().__init__("physics", "game/sys/game.dll", ())

    def patches_for(self, image):
        if image.image_base != 0x10000000:
            raise StateError(f"unexpected image base {image.image_base:#x}; wrong game.dll version?")
        patches = []
        for record in PATCHES:
            before_offset, before = record["context_before"]
            after_offset, after = record["context_after"]
            site = image.rva_to_offset(record["site"], 4)
            before_offset = image.rva_to_offset(before_offset, len(before))
            after_offset = image.rva_to_offset(after_offset, len(after))
            if site is None or before_offset is None or after_offset is None:
                raise SignatureError(f"patch site '{record['name']}' is not mapped to file bytes")
            pattern = before + record["prefix"] + bytes(4) + after
            mask = b"\xff" * (len(before) + len(record["prefix"])) + bytes(4) + b"\xff" * len(after)
            match = find_unique(image.data, Signature(pattern, mask))
            if match != before_offset or match + len(before) + len(record["prefix"]) != site:
                raise SignatureError(f"context window for '{record['name']}' is not at its pinned site")
            target = image.va_to_offset(record["patch"], 4)
            if target is None:
                raise SignatureError(f"target constant for '{record['name']}' is not mapped")
            anchors = (
                Anchor(before_offset, before),
                Anchor(site - len(record["prefix"]), record["prefix"]),
                Anchor(after_offset, after),
                Anchor(target, struct.pack("<f", record["patch_val_check"])),
            )
            patches.append(BytePatch(
                record["name"],
                site,
                struct.pack("<I", record["orig"]),
                struct.pack("<I", record["patch"]),
                anchors,
            ))
        return tuple(patches)


MOD = PhysicsMod()


def main():
    from htmod.patch import legacy_main
    return legacy_main(MOD)
