"""Prompts for the NL->Cypher agent: the schema, the rules and a few worked examples.

The examples teach the awkward parts of this graph (the ragged place hierarchy, lowercase
notes and foods, the GDS relationships) and are deliberately different from the questions
in tests/eval/nl_questions.json, so the evaluation is not measuring memorised answers.
"""

SCHEMA = """
Nodes (properties):
- Wine (id: int, title, description, points: int 80-100, price: float or null, vintage: int or null)
- Variety (name, wine_type, body, acidity, crowd_rating: float or null)   grape variety, e.g. 'Pinot Noir', 'Red Blend'
- Winery (name, country)
- Region (name, province)  e.g. 'Napa Valley'
- Province (name, country) e.g. 'California', 'Bordeaux', 'Douro'
- Country (name)           e.g. 'US', 'France', 'Italy'
- Taster (name, twitter)   wine critics, e.g. 'Roger Voss'
- Descriptor (name: lowercase tasting note, style_id: int flavour family, common: bool)
- Food (name: lowercase, e.g. 'beef', 'shellfish')

Relationships:
- (Wine)-[:OF_VARIETY]->(Variety)
- (Wine)-[:MADE_BY]->(Winery)
- (Wine)-[:FROM]->(Region)-[:IN]->(Province)-[:IN]->(Country)
- (Wine)-[:FROM]->(Province)   when the review has no region: the hierarchy is ragged
- (Taster)-[:REVIEWED]->(Wine)
- (Wine)-[:HAS_NOTE]->(Descriptor)
- (Variety)-[:PAIRS_WITH {weight: share of wines of that grape that list the food}]->(Food)
- (Wine)-[:SIMILAR_TO {score: Jaccard similarity of tasting notes}]->(Wine)
"""

RULES = """
Rules:
- Answer with ONE read-only Cypher query and nothing else: no explanation, no markdown.
- Never use CREATE, MERGE, SET, DELETE, REMOVE, DROP, CALL, LOAD CSV or FOREACH.
- To reach a Country or Province from a Wine, always use -[:FROM]->()-[:IN*1..2]->(:Country)
  or -[:FROM]->(x)-[:IN*0..1]->(:Province), because some wines have no Region.
- Descriptor and Food names are lowercase. Use the exact names listed under "Names found in the graph".
- Return named columns (AS ...), never whole nodes, never w.description unless asked, never w.fastrp.
- Order results when the question implies a ranking and end with LIMIT (at most 25).
- If the question is not about wines, grapes, regions, wineries, critics, tasting notes or food
  pairings, answer exactly: OUT_OF_SCOPE
"""

EXAMPLES = """
Examples:
Q: Best value Riesling under 20 dollars
MATCH (w:Wine)-[:OF_VARIETY]->(:Variety {name: 'Riesling'}) WHERE w.price < 20
RETURN w.title AS wine, w.points AS points, w.price AS price ORDER BY points DESC, price ASC LIMIT 10

Q: Average price of wines from Argentina
MATCH (w:Wine)-[:FROM]->()-[:IN*1..2]->(:Country {name: 'Argentina'})
RETURN round(avg(w.price), 2) AS avg_price, count(w) AS wines

Q: Best wines from Burgundy
MATCH (w:Wine)-[:FROM]->(x)-[:IN*0..1]->(:Province {name: 'Burgundy'})
RETURN w.title AS wine, w.points AS points ORDER BY points DESC LIMIT 10

Q: Which regions of Spain have the most wines?
MATCH (w:Wine)-[:FROM]->(r:Region)-[:IN]->(:Province)-[:IN]->(:Country {name: 'Spain'})
RETURN r.name AS region, count(w) AS wines ORDER BY wines DESC LIMIT 10

Q: Wines with notes of smoke and leather
MATCH (w:Wine)-[:HAS_NOTE]->(:Descriptor {name: 'smoke'})
MATCH (w)-[:HAS_NOTE]->(:Descriptor {name: 'leather'})
RETURN w.title AS wine, w.points AS points ORDER BY points DESC LIMIT 10

Q: What should I drink with lamb?
MATCH (v:Variety)-[p:PAIRS_WITH]->(:Food {name: 'lamb'})
RETURN v.name AS variety, p.weight AS share ORDER BY share DESC LIMIT 10

Q: Wines most similar to wine 42
MATCH (:Wine {id: 42})-[s:SIMILAR_TO]->(o:Wine)
RETURN o.title AS wine, round(s.score, 2) AS similarity ORDER BY similarity DESC LIMIT 10
"""

GENERATE_SYSTEM = (
    "You translate questions about a wine knowledge graph (Neo4j) into Cypher.\n" + SCHEMA + RULES + EXAMPLES
)

SUMMARY_SYSTEM = (
    "You answer a question about wines using ONLY the database rows you are given. "
    "Do not add any fact, number, wine or opinion that is not in the rows. "
    "Answer in at most three sentences of plain English, without markdown."
)


def generation_prompt(question: str, names: list[str]) -> str:
    """User message for the Cypher generation step."""
    found = "; ".join(names) if names else "none"
    return f"Names found in the graph: {found}\nQ: {question}"


def retry_prompt(question: str, names: list[str], cypher: str, problem: str) -> str:
    """User message for the single retry, with the problem found in the first attempt."""
    return (
        generation_prompt(question, names)
        + f"\n\nYour previous query was:\n{cypher}\nIt failed: {problem}\n"
        + "Write a corrected query that follows the schema and the rules."
    )


def summary_prompt(question: str, rows_json: str) -> str:
    """User message for the summary step."""
    return f"Question: {question}\nRows (JSON): {rows_json}"
