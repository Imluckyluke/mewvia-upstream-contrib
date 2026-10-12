"""Generates media fixtures dynamically for phase-3 & audio media engine tests.

Uses ffmpeg `lavfi testsrc` and `sine=f=440` plus a minimal gzipped Lottie JSON string so that no
binary media files are committed to the git repository.
"""

from __future__ import annotations

import gzip
import json
import subprocess
from pathlib import Path

# Minimal valid Lottie JSON (a 100x100 30fps animation with 1 frame)
MINIMAL_LOTTIE_JSON = {
    "v": "5.5.2",
    "fr": 30,
    "ip": 0,
    "op": 30,
    "w": 512,
    "h": 512,
    "nm": "Test Animation",
    "ddd": 0,
    "assets": [],
    "layers": [
        {
            "ddd": 0,
            "ind": 1,
            "ty": 1,
            "nm": "Solid",
            "sr": 1,
            "ks": {
                "o": {"a": 0, "k": 100},
                "r": {"a": 0, "k": 0},
                "p": {"a": 0, "k": [256, 256, 0]},
                "a": {"a": 0, "k": [256, 256, 0]},
                "s": {"a": 0, "k": [100, 100, 100]},
            },
            "ao": 0,
            "sw": 512,
            "sh": 512,
            "sc": "#ff0000",
            "ip": 0,
            "op": 30,
            "st": 0,
            "bm": 0,
        }
    ],
}


def generate_png_fixture(out_path: Path, width: int = 512, height: int = 512) -> Path:
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={width}x{height}:rate=1",
        "-vframes",
        "1",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_path


def generate_mp4_fixture(
    out_path: Path, width: int = 640, height: int = 480, duration: float = 2.0
) -> Path:
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={width}x{height}:rate=30",
        "-t",
        str(duration),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_path


def generate_mp4_with_audio_fixture(
    out_path: Path, width: int = 640, height: int = 480, duration: float = 2.0
) -> Path:
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={width}x{height}:rate=30",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=44100",
        "-t",
        str(duration),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_path


def generate_audio_fixture(out_path: Path, duration: float = 2.0) -> Path:
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=44100",
        "-t",
        str(duration),
        "-c:a",
        "libmp3lame",
        "-b:a",
        "128k",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_path


def generate_tgs_fixture(out_path: Path) -> Path:
    """Write gzipped minimal Lottie JSON as a valid .tgs file."""
    data = json.dumps(MINIMAL_LOTTIE_JSON).encode("utf-8")
    compressed = gzip.compress(data)
    out_path.write_bytes(compressed)
    return out_path
