import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEGACY_GAME_SHA256 = {
    "0000": "eb01589a2f6c05e67623a26994b0167df20536e976012f0d470ed526e54292e3",
    "0001": "30fa4d5fa1d28bb4278973eec4272d5a25886a16b020e9879a4d266ab8c7d3a2",
    "0010": "b27816cfec9d99f877db3da33081b5b059d736ba3a4aad223da9536291e6c6e9",
    "0011": "701d27b7b41b27d89e59657ec928a23fb962f0c6ffd9593a6f5358088f16ee2a",
    "0100": "b24dd16859d02ba13e022399ad3d11ac7174c734fdc0b052da854dc715c02432",
    "0101": "1d1d434f925d0cbbdfe3f63d68be5755376d6ce5a088fe1c4eef70e7c872068f",
    "0110": "a98cb455f390e426054bf092799db6185f0721e6a3781cbf3e55f472f262ab97",
    "0111": "fff9a0b9ece1110f3516f5010405b1e65f2bd11e340fc18aad60020c31877b85",
    "1000": "c4d445c785f1dca932c40248606d8a5ff8a1b2c1a061426378c52bf39cbcf438",
    "1001": "1df051a43cb033aea893bffda7b27263285b93c501b3801710d703a7c9888bc4",
    "1010": "a9cff2a548efd32b94cc447f6a58691abe208bf9bb8732cf66286780e1c98232",
    "1011": "e75ed1b0f2d2562275d2eda21bb1651047af888c993b3cea3adc99f35932e9c9",
    "1100": "b53c85e37cc700f8cccaaf3761b164384b4134d27421e7f9252a33bca39c811c",
    "1101": "93d627ff4d05e7bd5eb12ce6725fd16321fa47a601e973340cf7b2f4dc680450",
    "1110": "0c44694c264b0074f963753413b6f52b2c328c273c36355b5f88175173bcbd79",
    "1111": "ba2bcb61f28751b464bf2c8a675c5fd5dfe92e10bcbc605ea4b0fb7205e8a374",
}
FIXED_EYE_GAME_SHA256 = {
    "0001": "b160648068fc3c6894dde0c097f3c8778f848edd529d0dc5f0248a623172d36e",
    "0011": "d6a570c2506b28c9a9adbcfd6a90141816e02ddda84dcdfac38e5b8912e4883e",
    "0101": "f621a87583c48c4f64484634c3a0a3e8c4dab99ffdbbddb604e980701824af6a",
    "0111": "e3ed2d3f7759acb9f0575fc9f28cdfb9aeea74fd29245e7e1992e1ba7b6e44f3",
    "1001": "3606b520e0958acced418909bd16fa307fe81e0dea8ac81227280c09b4f4c413",
    "1011": "ddd77b97e419488b4acb96f2c464abae1aeaa1ba3a3ca13fe400515a795f4c77",
    "1101": "6bd78d97e9bbf112581125d32bd1a93016b0359d127f670d8d89ab2cee230184",
    "1111": "cf421034c41ecf1c0b794117d4b97bcd7decfa32c573b0478e3cc2d54aaedd18",
}
CURRENT_ALL_COMPONENTS_SHA256 = "42fd32de8059823bc13361948cbc7edfb7da561b67e1968889bf5739434a81d2"


def apply_fixture(image, filename, replacement_field):
    fixture = json.loads((ROOT / "tests/fixtures" / filename).read_text(encoding="utf-8"))
    for change in fixture["changes"]:
        offset = change["offset"]
        current = bytes.fromhex(change["current_hex"])
        assert image[offset:offset + len(current)] == current
        image[offset:offset + len(current)] = bytes.fromhex(change[replacement_field])


def write_fixed_eye_variant(payload, name, destination):
    image = bytearray((Path(payload) / f"game-{name}.dll").read_bytes())
    apply_fixture(image, "fixed_eye_camera_delta.json", "fixed_eye_hex")
    assert hashlib.sha256(image).hexdigest() == FIXED_EYE_GAME_SHA256[name]
    Path(destination).write_bytes(image)


def write_legacy_variant(payload, name, destination):
    image = bytearray((Path(payload) / f"game-{name}.dll").read_bytes())
    if int(name, 2) & 1:
        apply_fixture(image, "fixed_eye_camera_delta.json", "fixed_eye_hex")
        apply_fixture(image, "legacy_camera_delta.json", "legacy_hex")
    digest = hashlib.sha256(image).hexdigest()
    assert digest == LEGACY_GAME_SHA256[name]
    Path(destination).write_bytes(image)
    return digest
