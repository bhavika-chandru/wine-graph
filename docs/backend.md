# Backend: API design notes

Draft for the README's API section and for the defense (backend owner leads on query performance and API design).

## Endpoints

| Method | Path | Purpose | Technique |
|---|---|---|---|
| GET | `/health` | API + Neo4j liveness (200 / 503) | `verify_connectivity` |
| GET | `/api/wines` | Filtered, sorted list | Clause builder, range indexes |
| GET | `/api/wines/value` | Points per price under a cap | Range filter + computed score |
| GET | `/api/wines/{id}` | Detail with location chain and notes | Pattern comprehensions |
| GET | `/api/wines/{id}/similar` | Similar wines (`?max_price=` for cheaper ones) | GDS Node Similarity; Cypher Jaccard fallback |
| GET | `/api/wines/{id}/predicted-tier` | Quality tier from graph embeddings | FastRP + scikit-learn |
| GET | `/api/regions/{name}/profile` | Varieties, averages, notes of a region | Hierarchy + `CALL {}` subqueries |
| GET | `/api/tasters/{name}/bias` | Taster vs other tasters, per variety | `WITH` aggregation + correlated subquery |
| GET | `/api/pairings?food=` | Wines for a food | Food <- Variety <- Wine (joins both sources) |
| GET | `/api/styles`, `/api/styles/{id}` | Flavour families | GDS Louvain results |
| POST | `/api/ask` | Natural language to Cypher | Agent pipeline + guardrails |

Interactive docs: `http://localhost/docs` (through Nginx).

```bash
curl "http://localhost/api/wines?variety=Riesling&min_points=92&max_price=40&limit=5"
curl "http://localhost/api/wines/123/similar?max_price=25"
curl "http://localhost/api/regions/Napa%20Valley/profile?province=California"
curl -X POST http://localhost/api/ask -H "Content-Type: application/json" \
     -H "X-API-Key: $API_KEY" -d '{"question": "Best value Riesling under 20 dollars"}'
```

## Errors

| Status | When |
|---|---|
| 400 | NL query rejected by a guardrail (body keeps `cypher`, `status`, `reason`) |
| 401 | Missing or wrong `X-API-Key` on `/api/ask` (only if `API_KEY` is set) |
| 404 | Unknown wine, region, taster, food or style |
| 409 | Region name exists in several provinces: the body lists `candidates` |
| 422 | Invalid parameters (FastAPI/Pydantic validation, `min_price > max_price`) |
| 429 | Rate limit on `/api/ask` (Nginx) |
| 503 | Neo4j down, or GDS/model results not computed yet |
| 504 | Query exceeded the server-side timeout |

Errors are raised as domain exceptions in services and mapped in one place (`app/errors.py`); no stack trace reaches the client.

## Design decisions to defend

1. **Layers.** Routers validate and shape responses; services hold all Cypher; `Neo4jClient` owns the single driver (connection pool) created at startup. Tests swap the client for a fake, so the API is tested without a database.
2. **Parameterised queries only.** User input always goes into parameters, never into query text (no injection, and Neo4j caches one plan per query shape). Sort orders are a whitelist mapped to fixed `ORDER BY` clauses.
3. **Optional filters.** The list query is built from fixed clause templates, adding only the filters that are set. The catch-all form `WHERE $x IS NULL OR w.points >= $x` stops the planner from using the range index; `make bench` shows the difference.
4. **Start from the most selective node.** With a variety filter, the query starts at the `Variety` node (unique index) and expands to its wines, instead of scanning all wines.
5. **Pattern comprehensions instead of OPTIONAL MATCH chains.** Chained OPTIONAL MATCH clauses multiply rows (notes x paths) before aggregation; comprehensions fetch each value once per wine, so no `collect()`/`DISTINCT` is needed. This is a correctness choice: for a single wine `make bench` shows no speed gain (both take a few ms).
6. **Pagination with `limit + 1`.** Each page fetches one extra row to set `has_more`, so no second `COUNT` query is needed. `skip` is capped at 10,000: `SKIP` still walks the skipped rows, so deep pages get slower. Keyset pagination (`WHERE w.id > $last_id`) would fix this and is a candidate challenge to the brief for the anti-genericity appendix.
7. **Region keys.** Region is keyed by `(name, province)` because a name is not guaranteed to be unique across provinces, so the profile endpoint returns 409 with the candidate provinces instead of guessing. Check with the full data how often this happens.
8. **Precomputed GDS results.** Louvain, Node Similarity and FastRP run in `make gds`; requests only read their output. Running them per request would be slow, and every user would see the same styles between calls.
9. **Similarity fallback.** If a wine has no `SIMILAR_TO` relationships at all, Jaccard is computed in Cypher at query time. If it has GDS neighbours but a filter removes them, the page is empty instead: switching method would mix two score scales. Its cost depends on the degree of the anchor's descriptors, not on graph size, which is why very common descriptors (supernodes) should be filtered during ETL.
10. **Value formula.** `value_score = points / price`, computed only among wines with at least `min_points` (default 88). On its own the ratio always favours the cheapest bottles, so the quality floor is part of the definition: "the cheapest way to get a wine of at least N points". Alternatives considered: `(points - 80) / price` (same bias) and a log-price denominator (harder to explain).
11. **Read-only and bounded.** Every query runs in a read transaction with a server-side timeout (`QUERY_TIMEOUT_S`). Nginx caps body size, rate-limits `/api/ask`, and is the only public entry point.
