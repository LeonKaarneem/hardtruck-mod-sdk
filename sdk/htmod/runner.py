import re
import shutil


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


def materialize_data(release, payload):
    data_root = payload / "data"
    components = {}
    for mod in release.mods.values():
        if mod.kind != "data":
            continue
        owned = []
        for record in mod.files:
            relative = record.destination.removeprefix("game/usr/")
            target = data_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            source = mod.source_path(record)
            if record.transforms:
                content = source.read_bytes()
                for transform in record.transforms:
                    content = TRANSFORMS[transform](content)
                target.write_bytes(content)
            else:
                shutil.copyfile(source, target)
            owned.append(f"data/{relative}")
        components[mod.id] = owned
    return components
