from htmod.errors import SignatureError
from htmod.patch import BinaryMod, BytePatch


DEFAULT_RVA = 0x3695
OPCODE = b"\xc7\x44\x24\x54"
IMMEDIATE_OFFSET = 4
UNPATCHED = OPCODE + bytes(4)
PATCHED = OPCODE + b"\x01\x00\x00\x00"
PROLOGUE = b"\x55\x8b\xec"
MAX_FUNCTION_SIZE = 0x8000


def find_all(data, start, end, pattern):
    hits = []
    position = data.find(pattern, start, end)
    while position >= 0:
        hits.append(position)
        position = data.find(pattern, position + 1, end)
    return hits


class DX8VsyncMod(BinaryMod):
    def __init__(self, rva=DEFAULT_RVA):
        self.rva = rva
        super().__init__("dx8_vsync", "sys/dx8.dll", (), allow_original_restore=False)

    def patches_for(self, image):
        anchor = image.rva_to_offset(self.rva)
        if anchor is None:
            raise SignatureError(f"RVA {self.rva:#x} is not mapped to file bytes")
        text = image.section(".text")
        text_low = text.raw_offset
        text_high = text.raw_offset + text.raw_size
        position = anchor
        function_start = None
        while position >= max(text_low, anchor - MAX_FUNCTION_SIZE):
            if position > 0 and image.data[position - 1:position + 2] == PROLOGUE:
                function_start = position - 1
                break
            position -= 1
        if function_start is None:
            raise SignatureError(
                f"no function prologue found before RVA {self.rva:#x} within {MAX_FUNCTION_SIZE:#x} bytes"
            )
        function_end = min(function_start + MAX_FUNCTION_SIZE, text_high)
        original_hits = find_all(image.data, function_start, function_end, UNPATCHED)
        patched_hits = find_all(image.data, function_start, function_end, PATCHED)
        if len(original_hits) == 1 and not patched_hits:
            hit = original_hits[0]
        elif len(patched_hits) == 1 and not original_hits:
            hit = patched_hits[0]
        else:
            raise SignatureError(
                f"vsync pattern found original={len(original_hits)}, patched={len(patched_hits)} times; "
                "expected exactly one state"
            )
        return (BytePatch(
            "D3DPRESENT_INTERVAL_IMMEDIATE -> ONE",
            hit,
            UNPATCHED,
            PATCHED,
        ),)


MOD = DX8VsyncMod()


def main():
    from htmod.patch import legacy_main
    return legacy_main(MOD, custom_rva=True)
