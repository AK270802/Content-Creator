import uuid
from loguru import logger
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance

from app.config import settings

VECTOR_SIZE = 384  # all-MiniLM-L6-v2


class EmbeddingService:
    def __init__(self) -> None:
        self._client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)
        self._encoder = None

    def _get_encoder(self):
        if self._encoder is None:
            from sentence_transformers import SentenceTransformer
            self._encoder = SentenceTransformer("all-MiniLM-L6-v2")
        return self._encoder

    def _collection_name(self, video_id: str) -> str:
        return f"video_{video_id.replace('-', '_')}_segments"

    def _ensure_collection(self, name: str) -> None:
        existing = {c.name for c in self._client.get_collections().collections}
        if name not in existing:
            self._client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )

    def upsert_segments(self, video_id: str, segments: list[dict]) -> None:
        if not segments:
            return
        col = self._collection_name(video_id)
        self._ensure_collection(col)
        encoder = self._get_encoder()
        texts = [s["text"] for s in segments]
        vectors = encoder.encode(texts, show_progress_bar=False).tolist()
        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vectors[i],
                payload={
                    "video_id": video_id,
                    "start": segments[i].get("start", 0.0),
                    "end": segments[i].get("end", 0.0),
                    "text": segments[i]["text"],
                },
            )
            for i in range(len(segments))
        ]
        self._client.upsert(collection_name=col, points=points)
        logger.info(f"Upserted {len(points)} segments for video {video_id}")

    def search_similar(self, video_id: str, query: str, top_k: int = 5) -> list[dict]:
        col = self._collection_name(video_id)
        encoder = self._get_encoder()
        vector = encoder.encode([query], show_progress_bar=False)[0].tolist()
        try:
            hits = self._client.search(collection_name=col, query_vector=vector, limit=top_k)
            return [{"score": h.score, **h.payload} for h in hits]
        except Exception as e:
            logger.warning(f"Qdrant search failed for {video_id}: {e}")
            return []


embedding_service = EmbeddingService()
