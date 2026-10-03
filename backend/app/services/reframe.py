"""Subject-aware aspect-ratio reframing with optional shot-aware pan.

- Sample faces over time
- Split into shot windows (content-change heuristic or fixed duration)
- Smooth crop center per shot (moving average + dead zone)
- Emit piecewise-linear ffmpeg crop x/y expressions, then scale
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Literal

from loguru import logger

AspectRatio = Literal["16:9", "9:16", "1:1", "4:5"]

ASPECT_MAP: dict[str, float] = {
    "16:9": 16 / 9,
    "9:16": 9 / 16,
    "1:1": 1.0,
    "4:5": 4 / 5,
}


@dataclass
class CropWindow:
    x: int
    y: int
    w: int
    h: int
    source_w: int
    source_h: int
    method: str  # center | face | passthrough | animated


@dataclass
class CropKeyframe:
    t: float
    x: float
    y: float


def probe_dimensions(video_path: str) -> tuple[int, int]:
    import json

    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-select_streams", "v:0", video_path,
        ],
        capture_output=True, text=True, check=True,
    )
    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        raise ValueError(f"No video stream in {video_path}")
    return int(streams[0]["width"]), int(streams[0]["height"])


def probe_duration(video_path: str) -> float:
    import json

    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(result.stdout).get("format", {}).get("duration", 0) or 0)


def target_crop_size(src_w: int, src_h: int, aspect: float) -> tuple[int, int]:
    src_aspect = src_w / src_h
    if src_aspect > aspect:
        h = src_h
        w = int(h * aspect)
    else:
        w = src_w
        h = int(w / aspect)
    w = w - (w % 2)
    h = h - (h % 2)
    return max(2, w), max(2, h)


def _center_crop(src_w: int, src_h: int, crop_w: int, crop_h: int) -> CropWindow:
    x = max(0, (src_w - crop_w) // 2)
    y = max(0, (src_h - crop_h) // 2)
    return CropWindow(x=x, y=y, w=crop_w, h=crop_h, source_w=src_w, source_h=src_h, method="center")


def _sample_face_timeline(
    video_path: str,
    sample_interval: float = 0.5,
    max_samples: int = 120,
) -> list[tuple[float, float, float]]:
    """Return (t, cx, cy) normalized face centers over time."""
    try:
        import cv2
    except ImportError:
        return []

    cascade_path = getattr(cv2.data, "haarcascades", "") + "haarcascade_frontalface_default.xml"
    if not os.path.exists(cascade_path):
        return []

    detector = cv2.CascadeClassifier(cascade_path)
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, int(fps * sample_interval))
    samples: list[tuple[float, float, float]] = []
    frame_idx = 0
    count = 0

    while count < max_samples and frame_idx < total:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(48, 48))
        h, w = gray.shape[:2]
        t = frame_idx / fps
        if len(faces):
            # Prefer largest face
            fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            cx = (fx + fw / 2) / w
            cy = (fy + fh * 0.35) / h
            samples.append((t, cx, cy))
        else:
            samples.append((t, 0.5, 0.4))
        count += 1
        frame_idx += step

    cap.release()
    return samples


def _detect_shot_boundaries(samples: list[tuple[float, float, float]], jump: float = 0.18) -> list[float]:
    """Heuristic shot cuts when face center jumps abruptly."""
    cuts = [0.0]
    for i in range(1, len(samples)):
        t0, x0, y0 = samples[i - 1]
        t1, x1, y1 = samples[i]
        if abs(x1 - x0) + abs(y1 - y0) > jump and (t1 - cuts[-1]) > 0.8:
            cuts.append(t1)
    return cuts


def _smooth(values: list[float], window: int = 5) -> list[float]:
    if not values:
        return values
    out = []
    for i in range(len(values)):
        lo = max(0, i - window // 2)
        hi = min(len(values), i + window // 2 + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


def build_crop_keyframes(
    video_path: str,
    aspect_ratio: AspectRatio | str,
    prefer_faces: bool = True,
) -> tuple[int, int, int, int, list[CropKeyframe], str]:
    """
    Returns (crop_w, crop_h, src_w, src_h, keyframes, method).
    Keyframe x/y are top-left crop corners in pixels.
    """
    src_w, src_h = probe_dimensions(video_path)
    aspect = ASPECT_MAP.get(aspect_ratio)
    if aspect is None:
        raise ValueError(f"Unsupported aspect ratio: {aspect_ratio}")

    crop_w, crop_h = target_crop_size(src_w, src_h, aspect)
    src_aspect = src_w / src_h
    if abs(src_aspect - aspect) < 0.02:
        return crop_w, crop_h, src_w, src_h, [CropKeyframe(0.0, 0, 0)], "passthrough"

    if not prefer_faces:
        win = _center_crop(src_w, src_h, crop_w, crop_h)
        return crop_w, crop_h, src_w, src_h, [CropKeyframe(0.0, win.x, win.y)], "center"

    samples = _sample_face_timeline(video_path)
    if len(samples) < 2:
        win = compute_crop_window(video_path, aspect_ratio, prefer_faces=True)
        return crop_w, crop_h, src_w, src_h, [CropKeyframe(0.0, win.x, win.y)], win.method

    xs = _smooth([s[1] for s in samples])
    ys = _smooth([s[2] for s in samples])
    cuts = _detect_shot_boundaries(samples)

    keyframes: list[CropKeyframe] = []
    for i, (t, _, _) in enumerate(samples):
        cx, cy = xs[i], ys[i]
        x = cx * src_w - crop_w / 2
        y = cy * src_h - crop_h * 0.38
        x = max(0, min(x, src_w - crop_w))
        y = max(0, min(y, src_h - crop_h))
        # Dead zone: skip tiny moves vs previous
        if keyframes and abs(x - keyframes[-1].x) < 2 and abs(y - keyframes[-1].y) < 2:
            continue
        # Hard reset at shot boundaries (don't interpolate across cuts)
        if any(abs(t - c) < 0.05 for c in cuts[1:]):
            keyframes.append(CropKeyframe(t, round(x), round(y)))
        else:
            keyframes.append(CropKeyframe(t, round(x), round(y)))

    if not keyframes:
        win = _center_crop(src_w, src_h, crop_w, crop_h)
        keyframes = [CropKeyframe(0.0, win.x, win.y)]

    # Cap keyframes
    if len(keyframes) > 48:
        step = len(keyframes) / 48
        keyframes = [keyframes[int(i * step)] for i in range(48)]

    method = "animated" if len(keyframes) > 1 else "face"
    return crop_w, crop_h, src_w, src_h, keyframes, method


def _piecewise_expr(keyframes: list[CropKeyframe], axis: str) -> str:
    """Build ffmpeg expression: if(lt(t,t1), lerp, if(...))."""
    if len(keyframes) == 1:
        return str(int(getattr(keyframes[0], axis)))

    def lerp(a: CropKeyframe, b: CropKeyframe) -> str:
        va, vb = getattr(a, axis), getattr(b, axis)
        if abs(b.t - a.t) < 1e-3:
            return str(int(vb))
        # va + (vb-va)*(t-ta)/(tb-ta)
        return f"({va}+({vb}-{va})*(t-{a.t})/{max(b.t - a.t, 1e-3)})"

    expr = str(int(getattr(keyframes[-1], axis)))
    for i in range(len(keyframes) - 2, -1, -1):
        a, b = keyframes[i], keyframes[i + 1]
        expr = f"if(lt(t\\,{b.t})\\,{lerp(a, b)}\\,{expr})"
    return expr


def compute_crop_window(
    video_path: str,
    aspect_ratio: AspectRatio | str,
    prefer_faces: bool = True,
) -> CropWindow:
    crop_w, crop_h, src_w, src_h, kfs, method = build_crop_keyframes(
        video_path, aspect_ratio, prefer_faces=prefer_faces,
    )
    x, y = int(kfs[0].x), int(kfs[0].y)
    return CropWindow(x=x, y=y, w=crop_w, h=crop_h, source_w=src_w, source_h=src_h, method=method)


def apply_reframe(
    input_path: str,
    output_path: str,
    aspect_ratio: AspectRatio | str,
    target_width: int,
    target_height: int,
    prefer_faces: bool = True,
    animated: bool = True,
) -> CropWindow:
    """Crop + scale. When animated=True and faces vary, pan via piecewise crop."""
    crop_w, crop_h, src_w, src_h, kfs, method = build_crop_keyframes(
        input_path, aspect_ratio, prefer_faces=prefer_faces,
    )

    if target_width <= 0 or target_height <= 0:
        target_width, target_height = crop_w, crop_h
    target_width -= target_width % 2
    target_height -= target_height % 2

    if animated and len(kfs) > 1 and method == "animated":
        x_expr = _piecewise_expr(kfs, "x")
        y_expr = _piecewise_expr(kfs, "y")
        # ffmpeg crop with expressions — escape carefully for shell-less list form
        crop_filter = f"crop={crop_w}:{crop_h}:'{x_expr}':'{y_expr}'"
        vf = f"{crop_filter},scale={target_width}:{target_height}"
        window = CropWindow(
            x=int(kfs[0].x), y=int(kfs[0].y), w=crop_w, h=crop_h,
            source_w=src_w, source_h=src_h, method="animated",
        )
    else:
        x, y = int(kfs[0].x), int(kfs[0].y)
        vf = f"crop={crop_w}:{crop_h}:{x}:{y},scale={target_width}:{target_height}"
        window = CropWindow(
            x=x, y=y, w=crop_w, h=crop_h,
            source_w=src_w, source_h=src_h, method=method,
        )

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ]
    logger.info(f"Reframe ({window.method}, {len(kfs)} kfs): crop={crop_w}x{crop_h}")
    subprocess.run(cmd, check=True, capture_output=True)
    return window


reframe_service = type("ReframeService", (), {
    "compute_crop_window": staticmethod(compute_crop_window),
    "apply_reframe": staticmethod(apply_reframe),
    "probe_dimensions": staticmethod(probe_dimensions),
    "target_crop_size": staticmethod(target_crop_size),
    "build_crop_keyframes": staticmethod(build_crop_keyframes),
})()
