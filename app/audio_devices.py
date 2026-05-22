import sounddevice as sd
from typing import Optional


def list_devices() -> list[dict]:
    devices = sd.query_devices()
    result = []
    for i, d in enumerate(devices):
        result.append({
            "index": i,
            "name": d["name"],
            "max_input_channels": d["max_input_channels"],
            "max_output_channels": d["max_output_channels"],
            "default_samplerate": d["default_samplerate"],
        })
    return result


def find_device_index(name_substring: str, kind: str) -> Optional[int]:
    """Find device index by partial name match. kind: 'input' or 'output'"""
    for d in list_devices():
        if name_substring.lower() in d["name"].lower():
            if kind == "input" and d["max_input_channels"] > 0:
                return d["index"]
            if kind == "output" and d["max_output_channels"] > 0:
                return d["index"]
    return None


def get_default_input_device() -> dict:
    return sd.query_devices(kind="input")


def get_default_output_device() -> dict:
    return sd.query_devices(kind="output")


def print_devices():
    print("\n=== Audio Devices ===")
    for d in list_devices():
        ins = d["max_input_channels"]
        outs = d["max_output_channels"]
        tag = ""
        if ins > 0 and outs > 0:
            tag = "[IN/OUT]"
        elif ins > 0:
            tag = "[IN]    "
        elif outs > 0:
            tag = "[OUT]   "
        print(f"  {d['index']:2d}. {tag} {d['name']}")
    print()


if __name__ == "__main__":
    print_devices()
