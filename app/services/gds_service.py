"""Read the results written by the GDS / ML pipeline (scripts/gds_setup.py, Person 3).

The API only reads precomputed results: running Louvain or training a model inside a
request would be slow and non-deterministic. Contract (see docs/contracts.md):
- Louvain writes ``Descriptor.style_id`` (int)
- FastRP writes ``Wine.fastrp`` (list of floats)
- the classifier is saved with joblib at ``settings.model_path``
"""

import logging
from pathlib import Path
from typing import Any

from app.errors import NotFoundError, ServiceNotReadyError
from app.models.common import PageParams
from app.services.base import WINE_SUMMARY_RETURN, BaseService, split_page

log = logging.getLogger(__name__)

STYLES_QUERY = """
MATCH (d:Descriptor)
WHERE d.style_id IS NOT NULL
WITH d, COUNT { (d)<-[:HAS_NOTE]-() } AS usage
ORDER BY usage DESC
WITH d.style_id AS id, collect(d.name) AS notes
RETURN id, size(notes) AS size, notes[0..8] AS top_notes
ORDER BY size DESC, id ASC
SKIP $skip LIMIT $limit
"""

STYLE_NOTES_QUERY = """
MATCH (d:Descriptor {style_id: $id})
WITH d, COUNT { (d)<-[:HAS_NOTE]-() } AS usage
ORDER BY usage DESC
RETURN collect(d.name) AS notes
"""

STYLE_WINES_QUERY = """
MATCH (w:Wine)-[:HAS_NOTE]->(d:Descriptor {style_id: $id})
WITH w, count(d) AS hits
ORDER BY hits DESC, w.points DESC
LIMIT $limit
""" + WINE_SUMMARY_RETURN

EMBEDDING_QUERY = "MATCH (w:Wine {id: $id}) RETURN w.fastrp AS embedding, w.points AS points"


class StyleService(BaseService):
    """Flavour families from Louvain communities of descriptors."""

    def list_styles(self, page: PageParams) -> tuple[list[dict[str, Any]], bool]:
        """Styles ordered by number of descriptors."""
        rows = self.client.read(STYLES_QUERY, {"skip": page.skip, "limit": page.limit + 1})
        if not rows and page.skip == 0:
            raise ServiceNotReadyError("Styles not computed yet: run `make gds`")
        return split_page(rows, page.limit)

    def get_style(self, style_id: int, examples: int = 10) -> dict[str, Any]:
        """Notes of one style and the wines with the most notes from it."""
        rows = self.client.read(STYLE_NOTES_QUERY, {"id": style_id})
        notes = rows[0]["notes"] if rows else []
        if not notes:
            raise NotFoundError("Style", style_id)
        wines = self.client.read(STYLE_WINES_QUERY, {"id": style_id, "limit": examples})
        return {"id": style_id, "notes": notes, "example_wines": wines}


class TierPredictor:
    """Lazily loads the scikit-learn classifier once per process."""

    def __init__(self, model_path: str) -> None:
        self._path = Path(model_path)
        self._model: Any = None

    @property
    def model(self) -> Any:
        """The loaded model; raises ServiceNotReadyError if the file is missing."""
        if self._model is None:
            if not self._path.exists():
                raise ServiceNotReadyError("Tier classifier not trained yet: run `make gds`")
            import joblib  # pylint: disable=import-outside-toplevel

            self._model = joblib.load(self._path)
            log.info(f"Loaded tier classifier from {self._path}")
        return self._model

    def predict(self, embedding: list[float]) -> tuple[str, float]:
        """Return (predicted label, probability of that label)."""
        probabilities = self.model.predict_proba([embedding])[0]
        best = int(probabilities.argmax())
        return str(self.model.classes_[best]), float(probabilities[best])


class PredictionService(BaseService):
    """Predicted quality tier for one wine from its FastRP embedding."""

    def __init__(self, client, predictor: TierPredictor) -> None:
        super().__init__(client)
        self.predictor = predictor

    def predicted_tier(self, wine_id: int) -> dict[str, Any]:
        """Predicted tier plus the observed points, so the user can judge the prediction."""
        rows = self.client.read(EMBEDDING_QUERY, {"id": wine_id})
        if not rows:
            raise NotFoundError("Wine", wine_id)
        if rows[0]["embedding"] is None:
            raise ServiceNotReadyError("FastRP embeddings not computed yet: run `make gds`")
        label, probability = self.predictor.predict(rows[0]["embedding"])
        return {
            "wine_id": wine_id,
            "predicted_tier": label,
            "probability": probability,
            "actual_points": rows[0]["points"],
        }
