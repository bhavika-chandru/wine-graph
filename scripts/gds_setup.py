"""Graph Data Science pipeline: writes everything the API reads (run with `make gds`).

Steps, in order (see docs/contracts.md section 3):
1. Mark very common descriptors (supernodes) so they are left out of similarity and styles.
2. Node Similarity (Jaccard on shared descriptors) -> (:Wine)-[:SIMILAR_TO {score}]->(:Wine), top-k per wine.
3. Louvain on the descriptor co-occurrence graph -> Descriptor.style_id (flavour families).
   Pairs are weighted by lift (how much more often two notes meet than by chance), so popular
   notes do not glue everything into one style.
4. FastRP on wines + their descriptors, varieties, places and wineries -> Wine.fastrp.
5. A classifier on the embeddings predicts the quality tier -> models/tier_classifier.joblib,
   with its scores written to docs/ml_results.md.

Safe to re-run: previous results are removed first. Needs the GDS plugin and the admin user.
"""

import os
import time
from pathlib import Path
from typing import Any

import joblib
from dotenv import load_dotenv
from neo4j import GraphDatabase, Session
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

load_dotenv()

URI = os.getenv("NEO4J_ADMIN_URI", "bolt://localhost:7687")
AUTH = (os.getenv("NEO4J_ADMIN_USER", "neo4j"), os.getenv("NEO4J_ADMIN_PASSWORD", "devpassword123"))
MODEL_PATH = Path(os.getenv("MODEL_PATH", "models/tier_classifier.joblib"))
REPORT_PATH = Path("docs/ml_results.md")

MAX_NOTE_SHARE = 0.20  # a descriptor on more than 20% of wines says nothing about style
TOP_K = 10  # SIMILAR_TO neighbours kept per wine
SIMILARITY_CUTOFF = 0.1  # below this Jaccard score two wines are not similar
MIN_COOCCURRENCE = 3  # two notes must appear together this often to be linked for Louvain
MIN_LIFT = 1.2  # ...and at least 20% more often than if notes were assigned at random
EMBEDDING_DIM = 64  # the API reads whatever length is stored; the model must match it
HIGH_TIER_POINTS = 90  # "high" = 90 points or more (WineEnthusiast "outstanding")
SEED = 42

GRAPHS = ("wine_notes", "note_pairs", "wine_embed")

MARK_COMMON = """
MATCH (w:Wine) WITH count(w) AS total
MATCH (d:Descriptor)
WITH d, total, COUNT { (d)<-[:HAS_NOTE]-(:Wine) } AS wines
SET d.common = wines > $share * total
RETURN d.name AS name, wines, d.common AS common
ORDER BY wines DESC
"""

PROJECT_WINE_NOTES = """
MATCH (w:Wine)-[:HAS_NOTE]->(d:Descriptor)
WHERE NOT d.common
WITH gds.graph.project('wine_notes', w, d,
       {sourceNodeLabels: 'Wine', targetNodeLabels: 'Descriptor', relationshipType: 'HAS_NOTE'}) AS g
RETURN g.nodeCount AS nodes, g.relationshipCount AS rels
"""

SIMILARITY = """
CALL gds.nodeSimilarity.write('wine_notes', {
  topK: $top_k, similarityCutoff: $cutoff,
  writeRelationshipType: 'SIMILAR_TO', writeProperty: 'score'
})
YIELD nodesCompared, relationshipsWritten
RETURN nodesCompared, relationshipsWritten
"""

PROJECT_NOTE_PAIRS = """
MATCH (w:Wine) WITH count(w) AS total
MATCH (d1:Descriptor)<-[:HAS_NOTE]-(:Wine)-[:HAS_NOTE]->(d2:Descriptor)
WHERE elementId(d1) < elementId(d2) AND NOT d1.common AND NOT d2.common
WITH total, d1, d2, count(*) AS together
WHERE together >= $min_together
WITH d1, d2, together * total / toFloat(COUNT { (d1)<-[:HAS_NOTE]-() } * COUNT { (d2)<-[:HAS_NOTE]-() }) AS lift
WHERE lift >= $min_lift
WITH gds.graph.project('note_pairs', d1, d2,
       {relationshipProperties: {weight: lift}},
       {undirectedRelationshipTypes: ['*']}) AS g
RETURN g.nodeCount AS nodes, g.relationshipCount AS rels
"""

LOUVAIN = """
CALL gds.louvain.write('note_pairs', {
  relationshipWeightProperty: 'weight', writeProperty: 'style_id', consecutiveIds: true
})
YIELD communityCount, modularity
RETURN communityCount, modularity
"""

PROJECT_EMBED = """
CALL gds.graph.project('wine_embed',
  ['Wine', 'Descriptor', 'Variety', 'Region', 'Province', 'Winery'],
  {HAS_NOTE: {orientation: 'UNDIRECTED'}, OF_VARIETY: {orientation: 'UNDIRECTED'},
   FROM: {orientation: 'UNDIRECTED'}, MADE_BY: {orientation: 'UNDIRECTED'}})
YIELD nodeCount, relationshipCount
RETURN nodeCount AS nodes, relationshipCount AS rels
"""

FASTRP = """
CALL gds.fastRP.mutate('wine_embed', {
  embeddingDimension: $dim, iterationWeights: [0.0, 1.0, 1.0],
  randomSeed: $seed, mutateProperty: 'fastrp'
})
YIELD nodePropertiesWritten
RETURN nodePropertiesWritten
"""

WRITE_EMBEDDINGS = """
CALL gds.graph.nodeProperties.write('wine_embed', ['fastrp'], ['Wine'])
YIELD propertiesWritten
RETURN propertiesWritten
"""

TRAINING_DATA = """
MATCH (w:Wine) WHERE w.fastrp IS NOT NULL AND w.points IS NOT NULL
RETURN w.fastrp AS x, w.points AS points
"""

CLEANUP = [
    "MATCH ()-[r:SIMILAR_TO]->() CALL (r) { DELETE r } IN TRANSACTIONS OF 50000 ROWS",
    "MATCH (d:Descriptor) WHERE d.style_id IS NOT NULL REMOVE d.style_id",
    "MATCH (w:Wine) WHERE w.fastrp IS NOT NULL CALL (w) { REMOVE w.fastrp } IN TRANSACTIONS OF 50000 ROWS",
]


def run(session: Session, query: str, **params: Any) -> list[dict[str, Any]]:
    """Run one auto-commit query and return its rows as dicts."""
    return [record.data() for record in session.run(query, **params)]


def tier(points: int) -> str:
    """Quality tier used as the classification label."""
    return "high" if points >= HIGH_TIER_POINTS else "standard"


def reset(session: Session) -> None:
    """Drop old in-memory graphs and remove results of a previous run."""
    for name in GRAPHS:
        session.run("CALL gds.graph.drop($name, false) YIELD graphName RETURN graphName", name=name).consume()
    for query in CLEANUP:
        session.run(query).consume()


def mark_common_descriptors(session: Session) -> None:
    """Flag supernode descriptors and print the ones left out."""
    rows = run(session, MARK_COMMON, share=MAX_NOTE_SHARE)
    common = [f"{r['name']} ({r['wines']})" for r in rows if r["common"]]
    print(f"Descriptors: {len(rows)}; left out as too common (> {MAX_NOTE_SHARE:.0%} of wines): {common or 'none'}")


def similarity(session: Session) -> None:
    """Node Similarity on the wine-descriptor graph."""
    print(f"  projected {run(session, PROJECT_WINE_NOTES)[0]}")
    result = run(session, SIMILARITY, top_k=TOP_K, cutoff=SIMILARITY_CUTOFF)[0]
    print(f"Node Similarity: {result['relationshipsWritten']:,} SIMILAR_TO from {result['nodesCompared']:,} wines")


def styles(session: Session) -> None:
    """Louvain communities of descriptors that appear together more often than chance."""
    projected = run(session, PROJECT_NOTE_PAIRS, min_together=MIN_COOCCURRENCE, min_lift=MIN_LIFT)[0]
    print(f"  projected {projected}")
    if not projected["rels"]:
        print("Louvain skipped: no two notes appear together more often than chance (random data?)")
        return
    result = run(session, LOUVAIN)[0]
    print(f"Louvain: {result['communityCount']} styles, modularity {result['modularity']:.3f}")


def embeddings(session: Session) -> None:
    """FastRP embeddings, written to Wine nodes only."""
    print(f"  projected {run(session, PROJECT_EMBED)[0]}")
    run(session, FASTRP, dim=EMBEDDING_DIM, seed=SEED)
    written = run(session, WRITE_EMBEDDINGS)[0]["propertiesWritten"]
    print(f"FastRP: {written:,} wine embeddings of size {EMBEDDING_DIM}")


def train_classifier(session: Session) -> dict[str, Any]:
    """Logistic regression on the embeddings, compared with always guessing the majority tier."""
    rows = run(session, TRAINING_DATA)
    features = [row["x"] for row in rows]
    labels = [tier(row["points"]) for row in rows]
    x_train, x_test, y_train, y_test = train_test_split(
        features, labels, test_size=0.2, random_state=SEED, stratify=labels
    )
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, class_weight="balanced"))
    model.fit(x_train, y_train)
    baseline = DummyClassifier(strategy="most_frequent").fit(x_train, y_train)
    predicted = model.predict(x_test)
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    return {
        "wines": len(rows),
        "high_share": labels.count("high") / len(labels),
        "accuracy": accuracy_score(y_test, predicted),
        "macro_f1": f1_score(y_test, predicted, average="macro"),
        "baseline_accuracy": accuracy_score(y_test, baseline.predict(x_test)),
        "baseline_macro_f1": f1_score(y_test, baseline.predict(x_test), average="macro"),
    }


def render_report(metrics: dict[str, Any]) -> str:
    """Markdown summary of the classifier, for the README."""
    return "\n".join(
        [
            "# Quality tier from graph embeddings",
            "",
            f"Generated by `make gds`. Label: `high` if points >= {HIGH_TIER_POINTS}, else `standard` "
            + f"({metrics['high_share']:.0%} of {metrics['wines']:,} wines are `high`). "
            + f"Features: FastRP embedding (size {EMBEDDING_DIM}) only; points and price are not inputs. "
            + "80/20 stratified split. The tiers are imbalanced, so macro F1 is the main metric: "
            + "always guessing the majority tier already gets a high accuracy.",
            "",
            "| Model | Accuracy | Macro F1 |",
            "|---|---|---|",
            f"| Always predict the majority tier | {metrics['baseline_accuracy']:.3f} "
            + f"| {metrics['baseline_macro_f1']:.3f} |",
            f"| Logistic regression on FastRP | {metrics['accuracy']:.3f} | {metrics['macro_f1']:.3f} |",
            "",
        ]
    )


def main() -> None:
    """Run every step and report timings."""
    start = time.perf_counter()
    with GraphDatabase.driver(URI, auth=AUTH) as driver, driver.session() as session:
        print(f"GDS {run(session, 'RETURN gds.version() AS version')[0]['version']} on {URI}")
        reset(session)
        mark_common_descriptors(session)
        similarity(session)
        styles(session)
        embeddings(session)
        metrics = train_classifier(session)
        for name in GRAPHS:
            session.run("CALL gds.graph.drop($name, false) YIELD graphName RETURN graphName", name=name).consume()
    REPORT_PATH.write_text(render_report(metrics), encoding="utf-8")
    print(
        f"Classifier: accuracy {metrics['accuracy']:.3f} (baseline {metrics['baseline_accuracy']:.3f}), "
        + f"macro F1 {metrics['macro_f1']:.3f}; saved {MODEL_PATH} and {REPORT_PATH}"
    )
    print(f"Done in {time.perf_counter() - start:.0f}s")


if __name__ == "__main__":
    main()
