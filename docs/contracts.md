# Team contracts

What the API expects from the ETL (Person 2) and the GDS/agent work (Person 3).
If you need to change one of these, open a PR that edits this file first, so the backend can follow.

## 1. Graph schema (written by the ETL)

| Node | Key | Properties the API reads | Type |
|---|---|---|---|
| `Wine` | `id` (unique) | `id`, `title`, `price`, `points`, `vintage`, `description` | int, str, float or null, int, int or null, str |
| `Variety` | `name` (unique) | `name` | str |
| `Winery` | `(name, country)` | `name` | str |
| `Region` | `(name, province)` | `name`, `province` | str, str |
| `Province` | `(name, country)` | `name` | str |
| `Country` | `name` (unique) | `name` | str |
| `Taster` | `name` (unique) | `name` | str |
| `Descriptor` | `name` (unique) | `name` | str (lowercase) |
| `Food` | `name` (unique) | `name` | str (**lowercase**; the API lowercases user input) |

- `Wine.id` is an **integer** (the row id of the WineEnthusiast CSV). The API path parameter is an int, and `{id: $id}` only matches if the stored type is the same.
- `Region.province` must be stored as a property: the API disambiguates regions with `?province=`.
- Ragged hierarchy: `(Wine)-[:FROM]->(Region)-[:IN]->(Province)-[:IN]->(Country)`, or `(Wine)-[:FROM]->(Province)` when `region_1` is missing. The API always reaches the country with `-[:FROM]->()-[:IN*1..2]->(:Country)`.
- Relationships used: `MADE_BY`, `OF_VARIETY`, `FROM`, `IN`, `REVIEWED`, `HAS_NOTE`, `PAIRS_WITH {weight: float}`.

## 2. Indexes (names matter: `make bench` drops and recreates them)

| Name | Definition |
|---|---|
| `wine_points` | `CREATE RANGE INDEX wine_points FOR (w:Wine) ON (w.points)` |
| `wine_price` | `CREATE RANGE INDEX wine_price FOR (w:Wine) ON (w.price)` |
| `descriptor_style` | `CREATE RANGE INDEX descriptor_style FOR (d:Descriptor) ON (d.style_id)` |

Plus the uniqueness constraints from the brief (they create the lookup indexes on the keys above).

## 3. GDS / ML results (written by `make gds`)

The API only **reads** precomputed results; nothing runs GDS inside a request.

| Result | Written as | Read by |
|---|---|---|
| Node Similarity (Jaccard), top-k per wine | `(:Wine)-[:SIMILAR_TO {score: float}]->(:Wine)` | `GET /api/wines/{id}/similar` (falls back to a Cypher Jaccard only if the wine has no `SIMILAR_TO` at all) |
| Louvain flavour families | `Descriptor.style_id` (int) | `GET /api/styles`, `GET /api/styles/{id}` |
| FastRP embeddings | `Wine.fastrp` (list of floats) | `GET /api/wines/{id}/predicted-tier` |
| Classifier | `models/tier_classifier.joblib` (scikit-learn, has `predict_proba` and `classes_`); mounted into the api container with `./models:/srv/models:ro` | same endpoint; class labels are returned as strings, e.g. `"high"` / `"standard"` |

The classifier must be trained on the **same** `fastrp` embedding dimension that is written to the graph.

## 4. NL->Cypher agent (Person 3)

```python
# app/agent/pipeline.py
def answer_question(question: str, client: GraphReader) -> AskResponse: ...
```

- `client.read(cypher, params)` runs a **read transaction** with a timeout. Use it for grounding, `EXPLAIN` and execution.
- Return `AskResponse` (`app/models/ask.py`). `status="rejected"` makes the router answer HTTP 400 with the full body; `"ok"` and `"empty"` give 200.
- Raise `QueryRejectedError` only if you prefer a plain 400 without a body.

## 5. Read-only access

Neo4j **Community** edition has no role-based access control, so a separate read-only user cannot be created there.
Instead, every API query runs in a **read transaction** (`execute_read` with `READ_ACCESS`), and Neo4j refuses writes in that mode. Verified on Neo4j 5 by `tests/test_queries_live.py::test_writes_are_refused_in_read_transactions`:

```cypher
// should fail with "Writing in read access mode not allowed" when sent through client.read()
CREATE (:Test)
```

If the team uses Enterprise (or Aura), also create a user with the `reader` role and put it in `NEO4J_USER`.
