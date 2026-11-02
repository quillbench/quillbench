#!/usr/bin/env python3
"""
Regenerate the synthetic example dataset shipped with benchmark-annotator.

Everything here is invented. The graph is a toy bookshop: (Author)-[:WROTE]->
(Book)-[:PUBLISHED_BY]->(Publisher), plus (Book)-[:SOLD_IN]->(Store)-[:IN_CITY]
->(City). No real data from any project is included.

Run:  python example/build_example.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

HERE = Path(__file__).parent

ITEMS = [
    dict(
        id=1, local_id=1, category="lookup", difficulty="easy", split="dev",
        nl_query="List the titles of all books written by Ada Sennan.",
        gold_cypher=(
            "MATCH (a:Author {name: 'Ada Sennan'})-[:WROTE]->(b:Book)\n"
            "RETURN b.title AS title\n"
            "ORDER BY title"
        ),
        expects_empty="false",
        records=[{"title": "Quiet Cartography"}, {"title": "The Tin Orchard"}],
        elapsed=0.021,
    ),
    dict(
        id=2, local_id=2, category="aggregate", difficulty="easy", split="dev",
        nl_query="How many books were published by Kainan Press?",
        gold_cypher=(
            "MATCH (b:Book)-[:PUBLISHED_BY]->(p:Publisher {name: 'Kainan Press'})\n"
            "RETURN count(b) AS book_count"
        ),
        expects_empty="false",
        records=[{"book_count": 4}],
        elapsed=0.008,
    ),
    dict(
        id=3, local_id=3, category="aggregate", difficulty="medium", split="dev",
        nl_query=(
            "What is the average price of books sold in stores located in "
            "Sennan? Return a single number."
        ),
        gold_cypher=(
            "MATCH (b:Book)-[:SOLD_IN]->(s:Store)-[:IN_CITY]->(c:City {name: 'Sennan'})\n"
            "RETURN round(avg(b.price), 2) AS avg_price"
        ),
        expects_empty="false",
        # The city name here is deliberate: "Sennan" contains the substring
        # "nan". A naive str(v).replace("nan", "") in the display path — the
        # bug this tool's blank_if_nan() exists to prevent — renders it as
        # "Sen". If you ever see "Sen" in this row, that regression is back.
        records=[{"avg_price": 18.5}],
        elapsed=0.014,
    ),
    dict(
        id=4, local_id=4, category="join", difficulty="medium", split="test",
        nl_query=(
            "For each city, list the city name and the number of bookstores in it, "
            "ordered by store count descending."
        ),
        gold_cypher=(
            "MATCH (s:Store)-[:IN_CITY]->(c:City)\n"
            "RETURN c.name AS city, count(s) AS stores\n"
            "ORDER BY stores DESC, city"
        ),
        expects_empty="false",
        records=[
            {"city": "Sennan", "stores": 3},
            {"city": "Kainan", "stores": 2},
            {"city": "Riverbend", "stores": 1},
        ],
        elapsed=0.019,
    ),
    dict(
        id=5, local_id=5, category="filter", difficulty="hard", split="test",
        nl_query=(
            "Which authors have written more than two books published by "
            "Kainan Press?"
        ),
        gold_cypher=(
            "MATCH (a:Author)-[:WROTE]->(b:Book)-[:PUBLISHED_BY]->"
            "(:Publisher {name: 'Kainan Press'})\n"
            "WITH a, count(b) AS n\n"
            "WHERE n > 2\n"
            "RETURN a.name AS author, n AS books"
        ),
        expects_empty="true",
        # A legitimately empty result: no author clears the threshold. The UI
        # says so explicitly rather than looking broken.
        records=[],
        elapsed=0.011,
    ),
    dict(
        id=6, local_id=6, category="lookup", difficulty="easy", split="test",
        nl_query="Which publisher published 'The Tin Orchard'?",
        gold_cypher=(
            "MATCH (b:Book {title: 'The Tin Orchard'})-[:PUBLISHED_BY]->(p:Publisher)\n"
            "RETURN p.name AS publisher"
        ),
        expects_empty="false",
        records=[{"publisher": "Kainan Press"}],
        elapsed=0.006,
    ),
    dict(
        id=7, local_id=7, category="join", difficulty="hard", split="test",
        nl_query=(
            "List each author together with the cities where their books are sold, "
            "as one row per author."
        ),
        gold_cypher=(
            "MATCH (a:Author)-[:WROTE]->(:Book)-[:SOLD_IN]->(:Store)-[:IN_CITY]->(c:City)\n"
            "RETURN a.name AS author, collect(DISTINCT c.name) AS cities\n"
            "ORDER BY author"
        ),
        expects_empty="false",
        # Nested list values exercise the JSON-rendering path for complex cells.
        records=[
            {"author": "Ada Sennan", "cities": ["Sennan", "Riverbend"]},
            {"author": "Boris Vale", "cities": ["Kainan"]},
        ],
        elapsed=0.023,
    ),
    dict(
        id=8, local_id=8, category="aggregate", difficulty="medium", split="test",
        nl_query="What is the total revenue of the Riverbend store in 2024?",
        gold_cypher=(
            "MATCH (s:Store {name: 'Riverbend Books'})\n"
            "RETURN s.revenue_2024 AS revenue"
        ),
        expects_empty="false",
        # A cached execution error: the property does not exist in the schema.
        # Annotators should be able to see and judge this case too.
        records=[],
        elapsed=0.004,
        error=(
            "Neo.ClientError.Statement.SyntaxError: Unknown property name: "
            "revenue_2024"
        ),
    ),
]

BENCHMARK_COLUMNS = [
    "id", "local_id", "category", "difficulty", "split",
    "nl_query", "gold_cypher", "expects_empty",
]


def main() -> None:
    (HERE / "batches").mkdir(parents=True, exist_ok=True)

    benchmark_path = HERE / "benchmark_example.csv"
    with benchmark_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=BENCHMARK_COLUMNS)
        writer.writeheader()
        for item in ITEMS:
            writer.writerow({k: item[k] for k in BENCHMARK_COLUMNS})

    batch_path = HERE / "batches" / "demo_batch_001.csv"
    with batch_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=BENCHMARK_COLUMNS)
        writer.writeheader()
        for item in ITEMS:
            writer.writerow({k: item[k] for k in BENCHMARK_COLUMNS})

    cache = {}
    for item in ITEMS:
        entry = {"records": item["records"], "elapsed": item["elapsed"]}
        if item.get("error"):
            entry["error"] = item["error"]
        cache[str(item["id"])] = entry
    (HERE / "gold_cache_example.json").write_text(
        json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    jury_columns = [
        "id", "jury_verdict",
        "judge_a_verdict", "judge_a_reason",
        "judge_b_verdict", "judge_b_reason",
    ]
    jury_rows = [
        [1, "CORRECT", "CORRECT", "Query returns titles for the named author.",
         "CORRECT", "Matches the question."],
        [5, "UNCERTAIN", "CORRECT", "Threshold applied as asked.",
         "UNCERTAIN", "Empty result is plausible but unverified."],
        [8, "INCORRECT", "INCORRECT", "Property does not exist in the schema.",
         "INCORRECT", "Query errors out."],
    ]
    with (HERE / "jury_example.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(jury_columns)
        writer.writerows(jury_rows)

    print(f"Wrote {benchmark_path}")
    print(f"Wrote {batch_path}")
    print(f"Wrote {HERE / 'gold_cache_example.json'}")
    print(f"Wrote {HERE / 'jury_example.csv'}")


if __name__ == "__main__":
    main()
