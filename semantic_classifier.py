"""
Semantic Classification Model
==============================
Classifies text into predefined classes using sentence embeddings and cosine similarity.
No training data required — just class names and optional keywords.

Requirements:
    pip install sentence-transformers numpy scikit-learn
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity


# ─────────────────────────────────────────────
# 1. DATA STRUCTURES
# ─────────────────────────────────────────────

@dataclass
class ClassDefinition:
    """A single class with its name, keywords, and optional prototype sentences."""
    name: str
    keywords: list[str] = field(default_factory=list)
    prototypes: list[str] = field(default_factory=list)  # e.g. "This article discusses stock markets"

    def all_text_signals(self) -> list[str]:
        """All text signals that represent this class."""
        return [self.name] + self.keywords + self.prototypes


@dataclass
class ClassificationResult:
    label: str
    score: float                          # cosine similarity to winning class (0–1)
    scores: dict[str, float]             # similarity to every class
    is_confident: bool                   # True if score >= threshold


# ─────────────────────────────────────────────
# 2. SEMANTIC CLASSIFIER
# ─────────────────────────────────────────────

class SemanticClassifier:
    """
    Classifies text by comparing its embedding to weighted class centroids.

    Strategy
    --------
    Each class is represented by a centroid vector, computed as the
    weighted average of embeddings for its name, keywords, and prototypes.
    At inference, the input is embedded and compared to every centroid
    via cosine similarity. The closest class wins.
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        confidence_threshold: float = 0.35,
        weights: dict[str, float] | None = None,
        model: SentenceTransformer | None = None,
    ):
        """
        Args:
            model_name:            Any sentence-transformers model name.
            confidence_threshold:  Minimum cosine similarity to return a label
                                   instead of "unknown".
            weights:               Per-signal-type weights for centroid averaging.
                                   Defaults: name=1.0, keyword=1.5, prototype=2.0
            model:                 An already-loaded SentenceTransformer to reuse.
                                   When given, *model_name* is ignored and no model
                                   is loaded (avoids reloading on every request).
        """
        if model is not None:
            self.model = model
        else:
            print(f"Loading embedding model '{model_name}'...")
            self.model = SentenceTransformer(model_name)
        self.threshold = confidence_threshold
        self.weights = weights or {"name": 1.0, "keyword": 1.5, "prototype": 2.0}

        self._classes: list[ClassDefinition] = []
        self._centroids: np.ndarray | None = None   # shape (n_classes, embed_dim)

    # ── Setup ─────────────────────────────────

    def add_class(self, cls: ClassDefinition) -> None:
        """Register one class. Call build() after adding all classes."""
        self._classes.append(cls)
        self._centroids = None  # invalidate cache

    def add_classes(self, classes: list[ClassDefinition]) -> None:
        for cls in classes:
            self.add_class(cls)

    def build(self) -> None:
        """
        Embed all class signals and compute weighted centroids.
        Must be called before classify().
        """
        if not self._classes:
            raise ValueError("No classes registered. Call add_class() first.")

        centroids = []
        for cls in self._classes:
            centroid = self._compute_centroid(cls)
            centroids.append(centroid)

        self._centroids = np.vstack(centroids)  # (n_classes, dim)
        print(f"Built centroids for {len(self._classes)} classes.")

    def _compute_centroid(self, cls: ClassDefinition) -> np.ndarray:
        """Weighted average of embeddings for all signals of a class."""
        vectors, w_values = [], []

        # Class name
        vectors.append(self.model.encode(cls.name))
        w_values.append(self.weights["name"])

        # Keywords
        for kw in cls.keywords:
            vectors.append(self.model.encode(kw))
            w_values.append(self.weights["keyword"])

        # Prototype sentences
        for pt in cls.prototypes:
            vectors.append(self.model.encode(pt))
            w_values.append(self.weights["prototype"])

        vectors = np.array(vectors)          # (n_signals, dim)
        w = np.array(w_values).reshape(-1, 1)
        centroid = (vectors * w).sum(axis=0) / w.sum()
        return centroid / np.linalg.norm(centroid)  # L2-normalise

    # ── Inference ─────────────────────────────

    def classify(self, text: str) -> ClassificationResult:
        """Classify a single piece of text."""
        if self._centroids is None:
            raise RuntimeError("Call build() before classify().")

        vec = self.model.encode(text).reshape(1, -1)
        sims = cosine_similarity(vec, self._centroids)[0]  # (n_classes,)

        scores = {cls.name: float(sims[i]) for i, cls in enumerate(self._classes)}
        best_idx = int(np.argmax(sims))
        best_score = float(sims[best_idx])
        best_label = self._classes[best_idx].name

        return ClassificationResult(
            label=best_label if best_score >= self.threshold else "unknown",
            score=best_score,
            scores=scores,
            is_confident=best_score >= self.threshold,
        )

    def classify_batch(self, texts: list[str]) -> list[ClassificationResult]:
        """Classify multiple texts efficiently (single encode call)."""
        if self._centroids is None:
            raise RuntimeError("Call build() before classify_batch().")

        vecs = self.model.encode(texts)                          # (n, dim)
        sims = cosine_similarity(vecs, self._centroids)          # (n, n_classes)

        results = []
        for i, row in enumerate(sims):
            scores = {cls.name: float(row[j]) for j, cls in enumerate(self._classes)}
            best_idx = int(np.argmax(row))
            best_score = float(row[best_idx])
            best_label = self._classes[best_idx].name
            results.append(ClassificationResult(
                label=best_label if best_score >= self.threshold else "unknown",
                score=best_score,
                scores=scores,
                is_confident=best_score >= self.threshold,
            ))
        return results

    # ── Inspection ────────────────────────────

    def explain(self, result: ClassificationResult, top_n: int = 3) -> str:
        """Human-readable breakdown of a classification result."""
        ranked = sorted(result.scores.items(), key=lambda x: x[1], reverse=True)
        lines = [f"Prediction : {result.label} (score={result.score:.3f})"]
        lines.append(f"Confident  : {result.is_confident}")
        lines.append("Top scores :")
        for name, score in ranked[:top_n]:
            bar = "█" * int(score * 20)
            lines.append(f"  {name:<20} {score:.3f}  {bar}")
        return "\n".join(lines)


# ─────────────────────────────────────────────
# 3. DEMO
# ─────────────────────────────────────────────

def main():
    # --- Define your classes ---
    classes = [
        ClassDefinition(
            name="Finance",
            keywords=["stocks", "investment", "revenue", "market cap", "dividends", "bonds"],
            prototypes=["The company reported strong quarterly earnings this year."],
        ),
        ClassDefinition(
            name="Health",
            keywords=["medicine", "therapy", "symptoms", "treatment", "clinical", "wellness"],
            prototypes=["Researchers found a new approach to treating chronic pain."],
        ),
        ClassDefinition(
            name="Technology",
            keywords=["software", "algorithm", "hardware", "AI", "cloud", "startup"],
            prototypes=["The new chip architecture dramatically reduces inference latency."],
        ),
        ClassDefinition(
            name="Sports",
            keywords=["match", "tournament", "athlete", "score", "league", "championship"],
            prototypes=["The team secured a dramatic win in the final minutes of the game."],
        ),
    ]

    # --- Build classifier ---
    clf = SemanticClassifier(confidence_threshold=0.30)
    clf.add_classes(classes)
    clf.build()

    # --- Test inputs ---
    test_inputs = [
        "Central banks raised interest rates again amid inflation concerns.",
        "A new vaccine candidate showed promising results in phase-3 trials.",
        "The open-source model outperformed GPT on several benchmarks.",
        "She won gold in the 400m hurdles at the World Athletics Championship.",
        "My cat knocked over a glass of water.",  # should be "unknown"
    ]

    print("\n" + "=" * 60)
    print("CLASSIFICATION RESULTS")
    print("=" * 60)
    results = clf.classify_batch(test_inputs)
    for text, result in zip(test_inputs, results):
        print(f"\nInput  : {text}")
        print(clf.explain(result))


if __name__ == "__main__":
    main()
