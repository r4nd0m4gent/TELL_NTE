"""
build_embeddings.py
───────────────────
Compute the company embeddings the semantic classification needs, and store
them so the dashboard does not have to.

Embedding every company is the whole cost of a classification run - minutes on
the server - while the classes a user defines take milliseconds. The companies
change rarely, so the vectors are computed here, once, and the dashboard reuses
them: a run then takes seconds instead of minutes.

Run it after deploying, and again whenever the companies or their tags change
(the cache is keyed on the exact texts, so stale vectors are never used - a
change just means the dashboard would rebuild them itself, slowly, inside a
request).

Usage
-----
    python build_embeddings.py
    CLASSIF_EMBED_DIR=/some/where python build_embeddings.py
"""

from __future__ import annotations

import sys
import time


def main() -> int:
    start = time.time()
    # Importing the dashboard loads the companies exactly as it serves them.
    import textile_companies_NL as dashboard
    import classification

    texts = classification.company_texts(dashboard.data)
    print(f"{len(texts):,} companies · "
          f"{sum(map(len, texts)) / max(len(texts), 1):.0f} characters each on average")

    try:
        model = classification._load_model()
    except Exception as exc:
        print(f"the embedding model is unavailable: {exc}", file=sys.stderr)
        print("install it with: pip install sentence-transformers", file=sys.stderr)
        return 1

    vectors = classification.company_vectors(texts, model, verbose=True)
    print(f"{vectors.shape[0]:,} vectors of {vectors.shape[1]} dimensions "
          f"in {time.time() - start:.0f}s")
    print(f"cache directory: {classification._EMBED_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
