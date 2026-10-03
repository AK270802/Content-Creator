import os
import uuid
import tempfile
import subprocess
from loguru import logger

from app.config import settings


class RenderService:
    """
    Builds a rendered video from an approved EditPlan:
      extract clips → concat with crossfade → optional reframe → burn SRT captions → upload to MinIO.
    """

    def _clip_duration(self, clip_path: str) -> float:
        import ffmpeg
        probe = ffmpeg.probe(clip_path)
        return float(probe["format"]["duration"])

    def _extract_clip(
        self, video_path: str, out_path: str, start: float, end: float,
        *,
        brightness: float = 1.0,
        contrast: float = 1.0,
        saturation: float = 1.0,
        fade_in: float = 0.0,
        fade_out: float = 0.0,
    ) -> None:
        import ffmpeg
        dur = max(end - start, 0.1)
        v = ffmpeg.input(video_path).video.trim(start=start, end=end).setpts("PTS-STARTPTS")
        a = (
            ffmpeg.input(video_path).audio
            .filter("atrim", start=start, end=end)
            .filter("asetpts", "PTS-STARTPTS")
        )
        if brightness != 1.0 or contrast != 1.0 or saturation != 1.0:
            b = max(-1.0, min(1.0, brightness - 1.0))
            v = v.filter("eq", brightness=b, contrast=contrast, saturation=saturation)
        if fade_in > 0:
            d = min(fade_in, dur / 2)
            v = v.filter("fade", t="in", st=0, d=d)
            a = a.filter("afade", t="in", st=0, d=d)
        if fade_out > 0:
            d = min(fade_out, dur / 2)
            st = max(0.0, dur - d)
            v = v.filter("fade", t="out", st=st, d=d)
            a = a.filter("afade", t="out", st=st, d=d)
        (
            ffmpeg
            .output(v, a, out_path, vcodec="libx264", acodec="aac", ar=44100)
            .overwrite_output()
            .run(quiet=True)
        )

    def _write_srt(self, clips: list[dict], srt_path: str) -> None:
        """Generate SRT subtitle file for captions, adjusting to cumulative offset."""
        lines = []
        offset = 0.0
        idx = 1
        for clip in clips:
            cap = clip.get("caption", "").strip()
            dur = clip.get("duration", 5.0)
            if cap:
                start_srt = self._seconds_to_srt(offset + 0.5)
                end_srt = self._seconds_to_srt(offset + dur - 0.5)
                lines += [str(idx), f"{start_srt} --> {end_srt}", cap, ""]
                idx += 1
            offset += dur
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    @staticmethod
    def _seconds_to_srt(s: float) -> str:
        s = max(0.0, s)
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = int(s % 60)
        ms = int((s - int(s)) * 1000)
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

    def _concat_clips(self, clip_paths: list[str], output_path: str) -> None:
        import ffmpeg
        if len(clip_paths) == 1:
            os.replace(clip_paths[0], output_path)
            return

        fade = settings.crossfade_duration
        streams = [ffmpeg.input(p) for p in clip_paths]
        durations = [self._clip_duration(p) for p in clip_paths]

        accumulated = durations[0]
        video_chain = streams[0].video
        audio_chain = streams[0].audio

        for i in range(1, len(clip_paths)):
            offset = max(0.0, accumulated - fade)
            video_chain = ffmpeg.filter(
                [video_chain, streams[i].video],
                "xfade",
                transition="fade",
                duration=fade,
                offset=offset,
            )
            audio_chain = ffmpeg.filter(
                [audio_chain, streams[i].audio],
                "acrossfade",
                duration=fade,
            )
            accumulated += durations[i] - fade

        (
            ffmpeg
            .output(video_chain, audio_chain, output_path, vcodec="libx264", acodec="aac")
            .overwrite_output()
            .run(quiet=True)
        )

    def _burn_captions(self, video_path: str, srt_path: str, output_path: str, font_size: int = 24) -> None:
        import ffmpeg
        (
            ffmpeg
            .input(video_path)
            .filter(
                "subtitles",
                srt_path,
                force_style=f"FontSize={font_size},PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,Outline=1",
            )
            .output(output_path, vcodec="libx264", acodec="copy")
            .overwrite_output()
            .run(quiet=True)
        )

    def _overlay_logo(self, video_path: str, logo_path: str, output_path: str) -> None:
        """Bottom-right watermark/logo overlay (10% width)."""
        import ffmpeg
        vid = ffmpeg.input(video_path)
        logo = ffmpeg.input(logo_path)
        logo_scaled = logo.filter("scale", "iw*0.12", -1)
        overlayed = ffmpeg.overlay(vid, logo_scaled, x="W-w-40", y="H-h-40")
        (
            ffmpeg
            .output(overlayed, vid.audio, output_path, vcodec="libx264", acodec="aac")
            .overwrite_output()
            .run(quiet=True)
        )

    def render(
        self,
        video_path: str,
        kept_segments: list[dict],
        output_path: str,
        *,
        preset_id: str | None = None,
        aspect_ratio: str | None = None,
        burn_captions: bool = True,
        prefer_faces: bool = True,
        logo_path: str | None = None,
        target_width: int | None = None,
        target_height: int | None = None,
        video_bitrate: str | None = None,
        max_duration_s: float | None = None,
    ) -> None:
        """
        kept_segments: list of dicts with keys:
          start_time, end_time, caption (optional)
        """
        if not kept_segments:
            raise ValueError("No segments to render — edit plan has no kept scenes")

        # Resolve preset defaults
        if preset_id and preset_id != "source":
            from app.services.export_presets import get_preset
            try:
                preset = get_preset(preset_id)
                aspect_ratio = aspect_ratio or preset.aspect_ratio
                target_width = target_width or preset.width
                target_height = target_height or preset.height
                video_bitrate = video_bitrate or preset.video_bitrate
                max_duration_s = max_duration_s if max_duration_s is not None else preset.max_duration_s
            except KeyError:
                logger.warning(f"Unknown preset {preset_id}; using source dimensions")

        segments = list(kept_segments)
        if max_duration_s:
            # Truncate cumulative keep duration to platform max
            total = 0.0
            truncated = []
            for seg in segments:
                dur = seg["end_time"] - seg["start_time"]
                if total >= max_duration_s:
                    break
                if total + dur > max_duration_s:
                    seg = {**seg, "end_time": seg["start_time"] + (max_duration_s - total)}
                    truncated.append(seg)
                    break
                truncated.append(seg)
                total += dur
            segments = truncated or segments[:1]

        with tempfile.TemporaryDirectory() as tmpdir:
            clip_info = []
            for i, seg in enumerate(segments):
                clip_path = os.path.join(tmpdir, f"clip_{i:03d}.mp4")
                self._extract_clip(
                    video_path, clip_path,
                    seg["start_time"], seg["end_time"],
                    brightness=float(seg.get("brightness", 1.0) or 1.0),
                    contrast=float(seg.get("contrast", 1.0) or 1.0),
                    saturation=float(seg.get("saturation", 1.0) or 1.0),
                    fade_in=float(seg.get("fade_in", 0.0) or 0.0),
                    fade_out=float(seg.get("fade_out", 0.0) or 0.0),
                )
                dur = self._clip_duration(clip_path)
                clip_info.append({
                    "path": clip_path,
                    "caption": seg.get("caption", ""),
                    "duration": dur,
                })

            concat_path = os.path.join(tmpdir, "concat.mp4")
            self._concat_clips([c["path"] for c in clip_info], concat_path)

            current = concat_path

            # Reframe / scale to target aspect + resolution when a preset requests it
            needs_reframe = bool(
                (preset_id and preset_id != "source")
                and target_width
                and target_height
                and target_width > 0
                and target_height > 0
            )
            if needs_reframe:
                from app.services.reframe import apply_reframe
                reframed = os.path.join(tmpdir, "reframed.mp4")
                ar = aspect_ratio or "16:9"
                apply_reframe(
                    current, reframed, ar, target_width, target_height,
                    prefer_faces=prefer_faces,
                    animated=True,
                )
                current = reframed

            # Brand logo overlay
            if logo_path and os.path.exists(logo_path):
                branded = os.path.join(tmpdir, "branded.mp4")
                try:
                    self._overlay_logo(current, logo_path, branded)
                    current = branded
                except Exception as exc:
                    logger.warning(f"Logo overlay failed (non-fatal): {exc}")

            # Captions
            srt_path = os.path.join(tmpdir, "captions.srt")
            self._write_srt(clip_info, srt_path)
            has_captions = burn_captions and any(c["caption"].strip() for c in clip_info)
            if has_captions and os.path.getsize(srt_path) > 0:
                font_size = 28 if (aspect_ratio or "").startswith("9") else 24
                self._burn_captions(current, srt_path, output_path, font_size=font_size)
            else:
                import shutil
                shutil.copy2(current, output_path)

            # Optional bitrate pass (re-encode if preset bitrate set and no captions path already encoded)
            if video_bitrate and has_captions is False and target_width:
                # Already encoded by reframe; bitrate hint applied there via CRF.
                pass

        logger.info(f"Render complete: {output_path} preset={preset_id} aspect={aspect_ratio}")


render_service = RenderService()
