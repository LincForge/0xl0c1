"""Text embedding adapters for Loci hybrid matching (Vertex AI text-embedding-004 + deterministic local double).

Zero-pixel invariant: only text descriptions and verbatim markings are embedded.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Any, Protocol

import httpx

_WORD_RE = re.compile(r"[a-z0-9]+")


class Transport(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response: ...


class EmbeddingProvider(Protocol):
    model_id: str

    def embed(
        self, text: str, *, task_type: str = "RETRIEVAL_DOCUMENT"
    ) -> list[float]: ...


def l2_normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    if norm <= 1e-12:
        return [0.0 for _ in values]
    return [round(v / norm, 6) for v in values]


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity in [0.0, 1.0]. Mismatched dimensions or empty vectors return 0.0."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a <= 1e-12 or norm_b <= 1e-12:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


class DeterministicEmbeddingProvider:
    """Fast, offline 64-dimensional L2-normalized word + character n-gram hash projection."""

    model_id = "deterministic-64d"

    def __init__(self, dimensions: int = 64) -> None:
        self.dimensions = dimensions

    def embed(self, text: str, *, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:  # noqa: ARG002
        tokens = _WORD_RE.findall(text.casefold())
        vec = [0.0] * self.dimensions
        if not tokens:
            return vec
        features: list[tuple[str, float]] = []
        for tok in tokens:
            features.append((f"w:{tok}", 2.0))
            if len(tok) >= 4:
                features.append((f"p4:{tok[:4]}", 1.0))
            for i in range(max(0, len(tok) - 2)):
                features.append((f"c3:{tok[i : i + 3]}", 0.5))
        for feat, weight in features:
            digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if (digest[4] & 1) == 0 else -1.0
            vec[bucket] += sign * weight
        return l2_normalize(vec)


class VertexAIEmbeddingProvider:
    """Google Cloud Vertex AI text-embedding-004 adapter with automatic local fallback."""

    model_id = "vertex-text-embedding-004"

    def __init__(
        self,
        *,
        project: str | None = None,
        location: str | None = None,
        model: str = "text-embedding-004",
        access_token: str | None = None,
        transport: Transport | None = None,
        timeout: float = 4.0,
    ) -> None:
        self.project = (
            project
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("LOCI_GCP_PROJECT")
            or "loci-0xl0c1"
        )
        self.location = location or os.environ.get("LOCI_GCP_REGION") or "us-west1"
        self.model = model
        self._explicit_token = access_token or os.environ.get(
            "LOCI_VERTEX_ACCESS_TOKEN"
        )
        self.transport: Any = transport or httpx.Client()
        self.timeout = timeout
        self._fallback = DeterministicEmbeddingProvider()

    def _resolve_access_token(self) -> str | None:
        if self._explicit_token:
            return self._explicit_token
        try:
            resp = self.transport.request(
                "GET",
                "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                headers={"Metadata-Flavor": "Google"},
                timeout=1.5,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and isinstance(data.get("access_token"), str):
                    return str(data["access_token"])
        except Exception:  # noqa: BLE001
            return None
        return None

    def embed(self, text: str, *, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
        cleaned = " ".join(text.split()).strip()
        if not cleaned:
            return []
        token = self._resolve_access_token()
        if not token:
            return self._fallback.embed(cleaned, task_type=task_type)
        url = (
            f"https://{self.location}-aiplatform.googleapis.com/v1/projects/{self.project}"
            f"/locations/{self.location}/publishers/google/models/{self.model}:predict"
        )
        try:
            resp = self.transport.request(
                "POST",
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "instances": [
                        {
                            "content": cleaned[:2000],
                            "task_type": task_type,
                        }
                    ]
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            body = resp.json()
            predictions = body.get("predictions", []) if isinstance(body, dict) else []
            if predictions and isinstance(predictions[0], dict):
                emb = predictions[0].get("embeddings", {})
                values = emb.get("values", []) if isinstance(emb, dict) else []
                if isinstance(values, list) and values:
                    return l2_normalize([float(x) for x in values])
        except Exception:  # noqa: BLE001
            return self._fallback.embed(cleaned, task_type=task_type)
        return self._fallback.embed(cleaned, task_type=task_type)


def get_embedding_provider() -> EmbeddingProvider:
    if os.environ.get("LOCI_EMBEDDING_PROVIDER", "").lower() == "vertex":
        return VertexAIEmbeddingProvider()
    return DeterministicEmbeddingProvider()
