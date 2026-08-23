import os
import uuid
import tempfile
import subprocess
from loguru import logger

from app.config import settings


class RenderService:
    """
    Builds a rendered video from an approved EditPlan:
      extract clips → concat with crossfade → burn SRT captions → upload to MinIO.
    All heavy dependencies (ffmpeg-python, ffprobe) are imported lazily so the
    service can be imported in tests without the system ffmpeg binary present.
    """

    def _clip_duration(self, clip_path: str) -> float:
        import ffmpeg
        probe = ffmpeg.probe(clip_path)
        return float(probe["format"]["duration"])

    def _extract_clip(
        self, video_path: str, out_path: str, start: float, end: float
    ) -> None:
        import ffmpeg
        (
            ffmpeg
            .input(video_path)
            .trim(start=start, end=end)
            .setpts("PTS-STARTPTS")
            .output(out_path, vcodec="libx264", acodec="aac", ar=44100)
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

        # Build xfade chain for crossfade transitions
        fade = settings.crossfade_duration
        streams = [ffmpeg.input(p) for p in clip_paths]
        durations = [self._clip_duration(p) for p in clip_paths]

        # Accumulate offset for each xfade
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

    def _burn_captions(self, video_path: str, srt_path: str, output_path: str) -> None:
        import ffmpeg
        (
            ffmpeg
            .input(video_path)
            .filter("subtitles", srt_path, force_style="FontSize=24,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,Outline=1")
            .output(output_path, vcodec="libx264", acodec="copy")
            .overwrite_output()
            .run(quiet=True)
        )

    def render(
        self,
        video_path: str,
        kept_segments: list[dict],
        output_path: str,
    ) -> None:
        """
        kept_segments: list of dicts with keys:
          start_time, end_time, caption (optional)
        """
        if not kept_segments:
            raise ValueError("No segments to render — edit plan has no kept scenes")

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. Extract individual clips
            clip_info = []
            for i, seg in enumerate(kept_segments):
                clip_path = os.path.join(tmpdir, f"clip_{i:03d}.mp4")
                self._extract_clip(
                    video_path, clip_path,
                    seg["start_time"], seg["end_time"]
                )
                dur = self._clip_duration(clip_path)
                clip_info.append({
                    "path": clip_path,
                    "caption": seg.get("caption", ""),
                    "duration": dur,
                })

            # 2. Concat with crossfade
            concat_path = os.path.join(tmpdir, "concat.mp4")
            self._concat_clips([c["path"] for c in clip_info], concat_path)

            # 3. Burn captions if any have text
            srt_path = os.path.join(tmpdir, "captions.srt")
            self._write_srt(clip_info, srt_path)
            has_captions = any(c["caption"].strip() for c in clip_info)
            if has_captions and os.path.getsize(srt_path) > 0:
                self._burn_captions(concat_path, srt_path, output_path)
            else:
                import shutil
                shutil.copy2(concat_path, output_path)

        logger.info(f"Render complete: {output_path}")


render_service = RenderService()
