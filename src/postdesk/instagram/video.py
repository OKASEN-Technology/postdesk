"""Bounded Reel validation; Instagram remains the final media validator."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

MAX_VIDEO_BYTES = 50 * 1024 * 1024


def validate_video(data: bytes) -> tuple[int, int]:
    if not data or len(data) > MAX_VIDEO_BYTES:
        raise ValueError("Choose an MP4 video no larger than 50 MB.")
    try:
        with tempfile.TemporaryDirectory(prefix="postdesk-reel-") as folder:
            source = Path(folder) / "video.mp4"
            source.write_bytes(data)
            result = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
                                     "-show_streams", "-show_format", "-of", "json",
                                     str(source)], capture_output=True, check=True, timeout=20)
        metadata = json.loads(result.stdout)
        fmt = metadata["format"]
        if "mp4" not in fmt.get("format_name", "").split(","):
            raise ValueError("Use an MP4 video.")
        duration = float(fmt.get("duration", 0))
        streams = metadata.get("streams", [])
        video = next(x for x in streams if x.get("codec_type") == "video")
        if video.get("codec_name") not in ("h264", "hevc"):
            raise ValueError("Use H.264 or HEVC video.")
        if any(x.get("codec_name") != "aac" for x in streams if x.get("codec_type") == "audio"):
            raise ValueError("Use AAC audio in the MP4.")
        width, height = int(video["width"]), int(video["height"])
        if not 3 <= duration <= 900 or width > 1920:
            raise ValueError("Reels must be 3 seconds to 15 minutes, with width no more than 1920 pixels.")
        return width, height
    except (OSError, subprocess.SubprocessError, KeyError, StopIteration, json.JSONDecodeError) as exc:
        raise ValueError("Could not read the video. Choose a valid MP4 and try again.") from exc
