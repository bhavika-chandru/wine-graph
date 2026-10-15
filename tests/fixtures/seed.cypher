// Small hand-made dataset for live query tests (make test-db).
// Covers every label and relationship in docs/contracts.md plus edge cases:
// - a wine with no Region (linked straight to its Province)  -> wine 6
// - a Region name that exists in two provinces ("Twin Valley") -> 409 on /regions
// - a wine with no price                                      -> wine 9
// - GDS-style SIMILAR_TO only on wine 1                      -> wine 3 uses the Cypher fallback
// WARNING: the first statement wipes the database. Only run against a dev instance.

MATCH (n) DETACH DELETE n;

CREATE
  (fr:Country {name: 'France'}), (us:Country {name: 'US'}),
  (bur:Province {name: 'Burgundy', country: 'France'})-[:IN]->(fr),
  (bdx:Province {name: 'Bordeaux', country: 'France'})-[:IN]->(fr),
  (cal:Province {name: 'California', country: 'US'})-[:IN]->(us),
  (wa:Province {name: 'Washington', country: 'US'})-[:IN]->(us),
  (cdn:Region {name: 'Cote de Nuits', province: 'Burgundy'})-[:IN]->(bur),
  (napa:Region {name: 'Napa Valley', province: 'California'})-[:IN]->(cal),
  (twc:Region {name: 'Twin Valley', province: 'California'})-[:IN]->(cal),
  (tww:Region {name: 'Twin Valley', province: 'Washington'})-[:IN]->(wa),

  (pn:Variety {name: 'Pinot Noir'}), (cab:Variety {name: 'Cabernet Sauvignon'}),
  (ries:Variety {name: 'Riesling'}),

  (da:Winery {name: 'Domaine A', country: 'France'}), (cb:Winery {name: 'Chateau B', country: 'France'}),
  (cc:Winery {name: 'Coastal Cellars', country: 'US'}), (ne:Winery {name: 'Napa Estate', country: 'US'}),
  (nw:Winery {name: 'Northern Wines', country: 'US'}),

  (rv:Taster {name: 'Roger Voss', twitter: '@vossroger'}),
  (vb:Taster {name: 'Virginie Boone', twitter: '@vboone'}),
  (pg:Taster {name: 'Paul Gregutt', twitter: '@paulgwine'}),

  (cherry:Descriptor {name: 'cherry', style_id: 0}), (earthy:Descriptor {name: 'earthy', style_id: 0}),
  (mineral:Descriptor {name: 'mineral', style_id: 0}), (oak:Descriptor {name: 'oak', style_id: 1}),
  (vanilla:Descriptor {name: 'vanilla', style_id: 1}), (bc:Descriptor {name: 'blackcurrant', style_id: 1}),
  (citrus:Descriptor {name: 'citrus', style_id: 2}),

  (beef:Food {name: 'beef'}), (fish:Food {name: 'fish'}),
  (cab)-[:PAIRS_WITH {weight: 0.9}]->(beef), (pn)-[:PAIRS_WITH {weight: 0.6}]->(beef),
  (ries)-[:PAIRS_WITH {weight: 0.8}]->(fish),

  (w1:Wine {id: 1, title: 'Domaine A 2015 Pinot Noir', points: 94, price: 60.0, vintage: 2015, description: 'Cherry, earthy, mineral.'}),
  (w2:Wine {id: 2, title: 'Domaine A 2018 Pinot Noir', points: 90, price: 35.0, vintage: 2018, description: 'Cherry and earth.'}),
  (w3:Wine {id: 3, title: 'Coastal Cellars 2019 Pinot Noir', points: 88, price: 25.0, vintage: 2019, description: 'Cherry and oak.'}),
  (w4:Wine {id: 4, title: 'Napa Estate 2014 Cabernet Sauvignon', points: 95, price: 120.0, vintage: 2014, description: 'Oak, vanilla, blackcurrant.'}),
  (w5:Wine {id: 5, title: 'Napa Estate 2017 Cabernet Sauvignon', points: 89, price: 40.0, vintage: 2017, description: 'Oak and blackcurrant.'}),
  (w6:Wine {id: 6, title: 'Chateau B 2016 Cabernet Sauvignon', points: 92, price: 45.0, vintage: 2016, description: 'Blackcurrant, earthy.'}),
  (w7:Wine {id: 7, title: 'Northern Wines 2020 Riesling', points: 91, price: 18.0, vintage: 2020, description: 'Citrus and mineral.'}),
  (w8:Wine {id: 8, title: 'Northern Wines 2021 Riesling', points: 87, price: 12.0, vintage: 2021, description: 'Citrus.'}),
  (w9:Wine {id: 9, title: 'Domaine A 2012 Pinot Noir', points: 93, vintage: 2012, description: 'Earthy.'}),
  (w10:Wine {id: 10, title: 'Napa Estate 2022 Cabernet Sauvignon', points: 85, price: 15.0, vintage: 2022, description: 'Oak.'}),

  (w1)-[:OF_VARIETY]->(pn), (w2)-[:OF_VARIETY]->(pn), (w3)-[:OF_VARIETY]->(pn), (w9)-[:OF_VARIETY]->(pn),
  (w4)-[:OF_VARIETY]->(cab), (w5)-[:OF_VARIETY]->(cab), (w6)-[:OF_VARIETY]->(cab), (w10)-[:OF_VARIETY]->(cab),
  (w7)-[:OF_VARIETY]->(ries), (w8)-[:OF_VARIETY]->(ries),

  (w1)-[:MADE_BY]->(da), (w2)-[:MADE_BY]->(da), (w9)-[:MADE_BY]->(da), (w3)-[:MADE_BY]->(cc),
  (w4)-[:MADE_BY]->(ne), (w5)-[:MADE_BY]->(ne), (w10)-[:MADE_BY]->(ne), (w6)-[:MADE_BY]->(cb),
  (w7)-[:MADE_BY]->(nw), (w8)-[:MADE_BY]->(nw),

  (w1)-[:FROM]->(cdn), (w2)-[:FROM]->(cdn), (w9)-[:FROM]->(cdn), (w3)-[:FROM]->(twc),
  (w4)-[:FROM]->(napa), (w5)-[:FROM]->(napa), (w10)-[:FROM]->(napa),
  (w6)-[:FROM]->(bdx),
  (w7)-[:FROM]->(tww), (w8)-[:FROM]->(tww),

  (rv)-[:REVIEWED]->(w1), (rv)-[:REVIEWED]->(w2), (rv)-[:REVIEWED]->(w9), (rv)-[:REVIEWED]->(w6),
  (vb)-[:REVIEWED]->(w3), (vb)-[:REVIEWED]->(w4), (vb)-[:REVIEWED]->(w5), (vb)-[:REVIEWED]->(w10),
  (pg)-[:REVIEWED]->(w7), (pg)-[:REVIEWED]->(w8),

  (w1)-[:HAS_NOTE]->(cherry), (w1)-[:HAS_NOTE]->(earthy), (w1)-[:HAS_NOTE]->(mineral),
  (w2)-[:HAS_NOTE]->(cherry), (w2)-[:HAS_NOTE]->(earthy),
  (w3)-[:HAS_NOTE]->(cherry), (w3)-[:HAS_NOTE]->(oak),
  (w4)-[:HAS_NOTE]->(oak), (w4)-[:HAS_NOTE]->(vanilla), (w4)-[:HAS_NOTE]->(bc),
  (w5)-[:HAS_NOTE]->(oak), (w5)-[:HAS_NOTE]->(bc),
  (w6)-[:HAS_NOTE]->(bc), (w6)-[:HAS_NOTE]->(earthy),
  (w7)-[:HAS_NOTE]->(citrus), (w7)-[:HAS_NOTE]->(mineral),
  (w8)-[:HAS_NOTE]->(citrus),
  (w9)-[:HAS_NOTE]->(earthy),
  (w10)-[:HAS_NOTE]->(oak),

  (w1)-[:SIMILAR_TO {score: 0.67}]->(w2), (w1)-[:SIMILAR_TO {score: 0.33}]->(w9);
