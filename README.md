# Wine Explorer: a graph-backed sommelier

<!-- TODO (P2): add the GitHub Actions badges once lint.yml and test.yml are merged -->
![lint](https://img.shields.io/badge/pylint-10.00-brightgreen) ![coverage](https://img.shields.io/badge/coverage-95%25-brightgreen)

Wine Explorer answers questions about wines through the connections between them: wines that share tasting notes, regions and grapes, best-value picks, region profiles, taster bias, food pairings, flavour families, and free-form questions in plain English that are translated to Cypher.

Built with **Neo4j 5 + Graph Data Science**, **FastAPI**, an **NL→Cypher** agent with guardrails, and **Docker Compose** (Neo4j, API, Nginx).

**Contents:** [Quick start](#quick-start) · [Architecture](#architecture) · [Data sources](#data-sources) · [Graph model](#graph-model) · [API](#api) · [NL→Cypher](#nlcypher-agent) · [Graph Data Science](#graph-data-science-and-ml) · [Performance](#performance) · [Tests and quality](#tests-and-quality) · [Makefile](#makefile) · [Anti-genericity appendix](#anti-genericity-appendix) · [Team](#team)

---

## Quick start

Requirements: Docker Desktop, Python 3.11+, `make` (on Windows, use WSL Ubuntu).

```bash
git clone https://github.com/bhavika-chandru/wine-graph.git
cd wine-graph
cp .env.example .env          # fill in passwords and API_KEY; .env is never committed
make install                  # .venv + dependencies
make docker-run               # neo4j + api + nginx
make seed                     # load both datasets
make gds                      # similarity, flavour families, embeddings, classifier
```

Open **http://localhost/docs** for the interactive API (Swagger), served through Nginx.

`POST /api/ask` needs the `API_KEY` from `.env` in an `X-API-Key` header; without it the API answers 401.

<!-- TODO (P2): link to the image on Docker Hub / GHCR -->
Pre-built API image: `TODO`

---

## Architecture

<!-- TODO (P2): docs/architecture.png (services, ports, data flows) -->
![architecture](docs/architecture.png)

| Service | Image | Port | Role |
|---|---|---|---|
| `nginx` | nginx:1.27-alpine | 80 (public) | Reverse proxy, body-size limit, rate limit on `/api/ask` |
| `api` | built from `Dockerfile` | 8000 (internal only) | FastAPI, read-only Neo4j client |
| `neo4j` | neo4j:5 + GDS plugin | 7474 / 7687 | Graph database |

The classifier file is mounted into the API container (`./models:/srv/models:ro`), so retraining does not require rebuilding the image.

---

## Data sources

Two distinct sources, joined at the level of shared dimensions (variety, country, region) rather than wine by wine:

| Source | What it provides | Access |
|---|---|---|
| **WineEnthusiast reviews** (Kaggle, zynicide, `winemag-data-130k-v2.csv`) | ~130k expert reviews: points, price, taster, region hierarchy, descriptions | Kaggle download |
| **X-Wines** (Azambuja, Morais, Filipe, 2023) | Wine type, body, acidity, food pairings, crowd ratings | GitHub, slim version |

> Azambuja, R. X., Morais, A. J., & Filipe, V. (2023). X-Wines: A Wine Dataset for Recommender Systems and Machine Learning. *Big Data and Cognitive Computing*, 7(1), 20. https://doi.org/10.3390/bdcc7010020

**ETL notes** (`make seed`, numbers from the last run)

- 129,971 rows read. 9,979 exact duplicate reviews (same title and description) and 63 rows without a country or province are dropped, leaving **119,929 wines**.
- `Wine.id` is the CSV row id. 19,501 wines have no `region_1` and are linked straight to their province. 8,391 have no price (kept; they never match a price filter). The vintage is the first year in the title; 4,283 have none (mostly non-vintage sparkling).
- Descriptors: 121 tasting terms found in the reviews, from a fixed vocabulary of about 150, with synonyms merged ("earthy" -> earth, "cassis" -> blackcurrant). 1,857 reviews contain none.
- X-Wines is joined on grape name ("Syrah/Shiraz" matches either): 1,007 wines match 78 of the 701 varieties and give 504 `PAIRS_WITH` (a food is kept when at least 25% of that variety's X-Wines wines list it), plus the typical type, body, acidity and average crowd rating. Varieties with fewer than 3 X-Wines wines get no pairings.
- 19 tasters; 95,012 reviews name their taster.
- Known limitation: descriptors are matched word by word, so a few terms can be read out of context ("orange" can be the colour of an orange wine, "cream" can come from "cream sherry"). Very common terms are left out of similarity and styles by `make gds` anyway.

---

## Graph model

<!-- TODO (P2): docs/schema.png and docs/er_diagram.png -->
![schema](docs/schema.png)

```
(:Wine {id, title, price, points, vintage, description})
(:Wine)-[:MADE_BY]->(:Winery)
(:Wine)-[:OF_VARIETY]->(:Variety)-[:PAIRS_WITH {weight}]->(:Food)
(:Wine)-[:FROM]->(:Region)-[:IN]->(:Province)-[:IN]->(:Country)
(:Wine)-[:FROM]->(:Province)          // when the review has no region_1
(:Taster)-[:REVIEWED]->(:Wine)
(:Wine)-[:HAS_NOTE]->(:Descriptor)
```

The exact property names and types agreed between ETL, API and GDS are in [docs/contracts.md](docs/contracts.md).

**Node or property?** A field becomes a node when we traverse through it, group by it, or share it across many wines (variety, winery, region hierarchy, taster, descriptor, food). Numbers we only filter or sort on stay properties (price, points, vintage).

**Rejected alternatives** (each needs a query that justifies it, and these did not have one):
1. A separate `Review` node: each wine has exactly one review in the source data, so it adds a hop and doubles the node count for no new query.
2. Precomputed `SIMILAR_TO` between all pairs of wines: millions of edges that go stale. GDS writes only the top-k neighbours per wine.
3. Variety, region and country as properties only: no rollups, no region profiles, no traversal-based similarity.
4. Matching individual wines across the two datasets: names and vintages differ, and fuzzy matching would be hard to defend.
5. LLM-based descriptor extraction: costly and non-deterministic. A fixed keyword vocabulary is used instead.

<!-- TODO (P2): ragged hierarchy, region key (name, province), supernode filtering -->

---

## API

| Method | Path | Purpose | Technique |
|---|---|---|---|
| GET | `/health` | API + Neo4j liveness (200 / 503) | Driver connectivity check |
| GET | `/api/wines` | Filtered, sorted list | Query built from fixed clauses, range indexes |
| GET | `/api/wines/value` | Best points per price above a quality floor | Range filter + computed score |
| GET | `/api/wines/{id}` | Wine with location chain, taster and notes | Pattern comprehensions |
| GET | `/api/wines/{id}/similar` | Similar wines (`?max_price=` for cheaper ones) | **GDS Node Similarity**, Cypher Jaccard fallback |
| GET | `/api/wines/{id}/predicted-tier` | Quality tier from graph embeddings | **FastRP + scikit-learn** |
| GET | `/api/regions/{name}/profile` | Varieties, averages, typical notes | Hierarchy + `CALL {}` subqueries |
| GET | `/api/tasters/{name}/bias` | Taster vs other tasters, per variety | Aggregation + correlated subquery |
| GET | `/api/pairings?food=` | Wines for a food | Food ← Variety ← Wine (joins both sources) |
| GET | `/api/styles`, `/api/styles/{id}` | Flavour families | **GDS Louvain** |
| POST | `/api/ask` | Natural-language question | NL→Cypher agent + guardrails |

```bash
curl "http://localhost/api/wines?variety=Riesling&min_points=92&max_price=40&limit=5"
curl "http://localhost/api/wines/123/similar?max_price=25"
curl "http://localhost/api/regions/Napa%20Valley/profile?province=California"
curl -X POST http://localhost/api/ask -H "Content-Type: application/json" \
     -H "X-API-Key: $API_KEY" -d '{"question": "Best value Riesling under 20 dollars"}'
```

**Errors.** 400 query rejected by a guardrail (full body kept) · 401 missing API key on `/api/ask` · 404 unknown wine, region, taster, food or style · 409 region name in several provinces (body lists `candidates`) · 422 invalid parameters · 429 rate limit (Nginx) · 503 database down or GDS results not computed · 504 query timeout.

**Pagination.** Every list takes `skip` and `limit` (max 100) and returns `has_more`, computed by fetching `limit + 1` rows instead of running a second `COUNT` query.

Interactive docs are served at `/docs` (Swagger UI).

![Swagger endpoint list](docs/img/swagger_endpoints.png)

<details>
<summary>Example responses: a filtered list (200) and an unknown wine (404)</summary>

`GET /api/wines?min_points=90&limit=3`, run on the synthetic benchmark dataset:

![200 response](docs/img/swagger_list_200.png)

`GET /api/wines/999999999`: a clean JSON error, no stack trace:

![404 response](docs/img/swagger_404.png)

</details>

Design decisions behind the API (parameterised queries, read-only transactions, error mapping, precomputed GDS results, the value formula) are explained in [docs/backend.md](docs/backend.md).

---

## NL→Cypher agent

<!-- TODO (P3): pipeline, provider, prompt design, examples -->

Pipeline: scope check → entity grounding (full-text indexes) → generation (schema + few-shot, temperature 0) → static validation → `EXPLAIN` check with one retry → read-only execution → summary written only from the returned rows.

| Failure | Detection | Handling |
|---|---|---|
| Write or admin clause (`CREATE`, `DELETE`, `CALL dbms`…) | Blocklist | Rejected (HTTP 400) |
| Unknown label, relationship or property | Whitelist | Rejected before execution |
| Unbounded path, missing `LIMIT` | Static check | Bounded / `LIMIT` injected |
| Syntax error | `EXPLAIN` | One retry with the error, then 400 |
| Hallucinated answer | Summary built from rows only | `provenance` marks rows as *verified (database)* and summary as *AI-generated* |
| Everything above fails | Read transaction + timeout | Neo4j refuses writes (verified by `tests/test_queries_live.py`) |

<!-- TODO (P3): evaluation results on tests/eval/nl_questions.json (% valid, % correct, % correctly rejected) -->

---

## Graph Data Science and ML

Results of `make gds` on the full data (119,929 wines, 390 s). Details and settings are in `scripts/gds_setup.py`.

| Step | Projection | Settings | Result | Used by |
|---|---|---|---|---|
| Supernode filter | none | descriptors on more than 20% of wines get `common = true` | only "fruit" (38% of wines) | similarity and styles |
| Node Similarity (Jaccard) | `wine_notes`: Wine → Descriptor, common descriptors left out | top 10 per wine, cutoff 0.1 | 1,149,750 `SIMILAR_TO` for 114,975 wines | `/api/wines/{id}/similar` |
| Louvain | `note_pairs`: descriptor pairs that meet at least 3 times and 1.2× more often than chance, weighted by lift | weighted, consecutive ids | 3 flavour families, modularity 0.394 | `/api/styles` |
| FastRP | `wine_embed`: Wine, Descriptor, Variety, Region, Province, Winery (undirected) | 64 dims, iteration weights [0, 1, 1], seed 42 | 119,929 `Wine.fastrp` | `/api/wines/{id}/predicted-tier` |
| Logistic regression | FastRP vectors only: points and price are not inputs | balanced class weights, 80/20 stratified split | table below | same |

**Why lift for Louvain:** weighting pairs by raw co-occurrence lets the most popular notes link everything into one community. Lift keeps only pairs that meet more often than their popularity predicts, which is what a flavour family is.

**Classifier** (`high` = 90 points or more):

| Model | Accuracy | Macro F1 |
|---|---|---|
| Always predict the majority tier | 0.620 | 0.383 |
| Logistic regression on FastRP | 0.599 | 0.591 |

The tiers are imbalanced (62% `standard`), so accuracy is misleading: always answering `standard` already scores 0.620. Macro F1 weighs both tiers equally. The model reaches 0.591 against 0.383 for the baseline, so a wine's neighbourhood (its notes, grape, place and producer) carries real information about its quality, but far from enough to replace the critic's score.

PageRank is deliberately not used: it measures endorsement through directed links, which a wine–attribute graph does not have.

---

## Performance

Indexes give the query its starting point; after that, traversal follows relationships directly (index-free adjacency), so its cost depends on the degree of the nodes touched, not on the total size of the graph.

Measured with `make bench` on 100,000 synthetic wines (`make bench-data`, same shape as the real data), median of 10 runs after 2 warm-up runs:

| Case | Before | After | Speed-up | db hits |
|---|---|---|---|---|
| Range filter: no index vs range index | 22.6 ms | 3.1 ms | ×7.3 | 202,503 → 123 |
| Optional filters: `$x IS NULL OR` vs needed clauses only | 46.3 ms | 36.3 ms | ×1.3 | 278,248 → 140,426 |
| Wine detail: OPTIONAL MATCH chain vs pattern comprehensions | 3.6 ms | 7.6 ms | ×0.5 | 83 → 113 |
| Pagination: full result vs `SKIP/LIMIT` | 1,734 ms | 6.1 ms | ×285 | 272,404 → 61 |

What the numbers say:
- **Indexes and pagination** give the large gains. Without the range index Neo4j reads every Wine node; with it, the plan switches from `NodeByLabelScan` to `NodeIndexSeekByRange`.
- **Catch-all filters** (`$x IS NULL OR w.points >= $x`) cannot be answered from an index, so the list query is built from only the clauses that are set. The gain is real but modest here, because the planner chose the price index.
- **Pattern comprehensions** are not faster for a single wine. They are kept for correctness: they never duplicate rows, so no `collect()` or `DISTINCT` is needed.

Full plans per case: [docs/benchmarks.md](docs/benchmarks.md).

| Without index: `NodeByLabelScan` | With range index: `NodeIndexSeekByRange` |
|---|---|
| ![no index](docs/img/profile_no_index.png) | ![with index](docs/img/profile_with_index.png) |

---

## Tests and quality

| Command | What it runs |
|---|---|
| `make test` | Unit and API tests with a fake database (coverage ≥ 60%, currently ~95%) |
| `make test-db` | Every endpoint's Cypher against a real Neo4j loaded with `tests/fixtures/seed.cypher` (wipes that database) |
| `make test-integration` | Container health checks through Nginx (needs `make docker-run`) |
| `make lint` | pylint with the course `.pylintrc` (must stay ≥ 9.5, currently 10.00) |

The test dataset covers the awkward parts of the real data on purpose: a wine with no region, a region name shared by two provinces, a wine with no price, and a wine without GDS results (to test the similarity fallback).

---

## Makefile

| Command | Description |
|---|---|
| `make help` | List all commands |
| `make install` | Create `.venv` and install dependencies |
| `make run` | Run the API locally with auto-reload |
| `make docker-build` / `make docker-run` / `make docker-down` | Build the image, start or stop the stack |
| `make test` / `make test-db` / `make test-integration` | Tests (see above) |
| `make lint` / `make format` | pylint / black |
| `make seed` | ETL for both sources |
| `make gds` | Projections, algorithms, embeddings and classifier |
| `make eval` | NL→Cypher evaluation |
| `make bench-data` / `make bench` | Synthetic data for benchmarks / before-after report |
| `make tree` / `make clean` | Project structure / remove caches |

---

## Anti-genericity appendix

We gave a fresh ChatGPT chat one line, *"Design a Neo4j graph schema for a wine recommendation app using a wine reviews dataset."*, and compared its answer with what we built. The full answer is in [docs/appendix/naive_schema_chatgpt.pdf](docs/appendix/naive_schema_chatgpt.pdf).

In short, it proposed `Wine`, `Winery`, `Region`, `Country`, `Grape`, `Review`, `TastingNote`, `Style` and `User` nodes, a `User` layer with `RATED`/`LIKED`/`WANTS_TO_TRY`, and a recommendation query that scores candidates by shared grape (×3) and shared region (×2). The schema is reasonable, but it is written for any wine dataset, not for this one.

| Naive AI schema | Our schema | Why |
|---|---|---|
| `TastingNote` nodes listed, but never loaded ("add flavor-note extraction later"); similarity = shared grape + shared region | `Descriptor` nodes extracted in the ETL, `HAS_NOTE`, GDS Node Similarity written as `SIMILAR_TO {score}` | The review text is the richest field in the data. Two Cabernets from Napa are not similar just because they share a grape and a region; shared descriptors measure what the critic actually tasted |
| No handling of very common notes | Supernode filtering on descriptors | Very common notes ("fruit") connect to most wines and would make every wine similar to every other |
| `Region {name: row.province}`: province loaded as region, keyed by name only; `Region → Country` | `Region` keyed by `(name, province)`; ragged `Region → Province → Country`, or `Wine → Province` when `region_1` is empty | The dataset has three levels (`country`, `province`, `region_1`) and `region_1` is often missing. Region names repeat across provinces; the API answers 409 with the candidates instead of merging them |
| `MERGE (:Winery {name})` | `Winery` keyed by `(name, country)` | Different producers in different countries can share a name |
| Separate `Review` node, plus derived `avgPoints` / `reviewCount` on `Wine` to refresh after each load | No `Review` node; `points` on `Wine`, `(:Taster)-[:REVIEWED]->(:Wine)` | Each row is one critic's tasting of one bottle, and the dataset has no wine identifier to group rows by. A `Review` node adds a hop and a derived property to keep in sync for no new query. Making the taster a node is what gives us the taster-bias insight |
| `User` layer (`RATED`, `LIKED`, `WANTS_TO_TRY`, `PREFERS`) | No users | The dataset has critics, not consumers. The ChatGPT answer itself says this layer cannot come from the data; we did not invent it |
| `Style` = red / white / rosé, set by hand | `Descriptor.style_id` from Louvain (flavour families) | Styles found in the graph structure, not a fixed label list |
| One source | Second source (X-Wines) joined on `Variety`, `(:Variety)-[:PAIRS_WITH {weight}]->(:Food)` | Food pairings are not in WineEnthusiast. The two sources cannot be matched wine by wine reliably, so they meet at the variety level |
| Indexes on `name`, `avgPoints`, `vintage`; the query filters `price <= $maxPrice` on every wine | Range indexes `wine_points` and `wine_price` only, measured with `PROFILE` (202,503 → 123 db hits); optional filters added only when given | Indexes chosen from the queries we actually run. A filter written as `price <= $maxPrice` silently drops wines with no price; ours only applies it when the caller asks |

**A decision we changed from the original brief:** the similarity endpoint first fell back to a Cypher Jaccard whenever GDS returned too few neighbours. After review, the fallback only runs when a wine has no `SIMILAR_TO` at all, so one response never mixes scores from two different methods.

---

## Team

<!-- TODO (Bhavika, last step): fill from GitHub Insights → Contributors -->

| Member | Role | Commits | % | Merged PRs |
|---|---|---|---|---|
| Bhavika Chandru | Backend, API, performance, team lead | | | |
| | Data and infrastructure | | | |
| | AI agent and Graph Data Science | | | |

## Use of AI tools

<!-- TODO (team): state honestly which AI tools were used and for what -->
