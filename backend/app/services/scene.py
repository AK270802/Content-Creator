from loguru import logger


class SceneService:
    def detect_scenes(self, video_path: str, threshold: float = 27.0) -> list[dict]:
        try:
            from scenedetect import open_video, SceneManager
            from scenedetect.detectors import ContentDetector

            video = open_video(video_path)
            scene_manager = SceneManager()
            scene_manager.add_detector(ContentDetector(threshold=threshold))
            scene_manager.detect_scenes(video, show_progress=False)
            scene_list = scene_manager.get_scene_list()

            result = []
            for i, (start, end) in enumerate(scene_list, start=1):
                result.append({
                    "scene_number": i,
                    "start_time": round(start.get_seconds(), 3),
                    "end_time": round(end.get_seconds(), 3),
                })
            if not result:
                result.append({"scene_number": 1, "start_time": 0.0, "end_time": -1.0})
            logger.info(f"Detected {len(result)} scenes in {video_path}")
            return result
        except Exception as e:
            logger.error(f"Scene detection failed for {video_path}: {e}")
            return [{"scene_number": 1, "start_time": 0.0, "end_time": -1.0}]


scene_service = SceneService()
