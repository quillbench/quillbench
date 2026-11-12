from quillbench.categories import CATALOG, detect, guess_language


def test_simple_and_filter():
    assert detect("SELECT * FROM sensors") == ["simple"]
    assert detect("SELECT road FROM t WHERE speed > 50") == ["simple", "filter"]


def test_joins_counted_including_old_style():
    assert "join" in detect("SELECT * FROM a JOIN b ON a.id = b.id")
    assert "multi_join" in detect("SELECT * FROM a JOIN b ON a.i=b.i JOIN c ON c.i=b.i")
    assert "multi_join" in detect("SELECT a.x FROM a, b, c WHERE a.id=b.id AND b.id=c.id")
    assert "optional" in detect("SELECT * FROM a LEFT JOIN b ON a.id = b.id")


def test_words_in_strings_and_comments_do_not_count():
    cats = detect("SELECT name FROM t WHERE note = 'join group by sum(x)' -- union\n")
    assert cats == ["simple", "filter"]


def test_totals_grouping_and_top_n():
    cats = detect("SELECT road, AVG(speed), MAX(volume) FROM r GROUP BY road HAVING COUNT(*) > 3 "
                  "ORDER BY 2 DESC LIMIT 5")
    for c in ("avg", "max", "count", "group_by", "having", "order_by", "top_n"):
        assert c in cats
    assert "simple" not in cats


def test_cte_window_subquery_are_told_apart():
    cats = detect("WITH x AS (SELECT road, SUM(v) s FROM t GROUP BY road) "
                  "SELECT road, RANK() OVER (ORDER BY s) FROM x")
    assert {"cte", "window", "sum"} <= set(cats)
    assert "subquery" not in cats and "order_by" not in cats
    assert "subquery" in detect("SELECT id FROM t WHERE id IN (SELECT id FROM u)")


def test_dates_by_function_or_literal_not_column_name():
    assert "date_time" in detect("SELECT * FROM t WHERE ts >= '2024-03-01'")
    assert "date_time" in detect("SELECT EXTRACT(HOUR FROM ts) FROM t")
    assert "date_time" not in detect("SELECT date, hour FROM t")


def test_cypher():
    assert detect("MATCH (s:Sensor) RETURN s.name", "cypher") == ["simple"]
    cats = detect("MATCH (s:Sensor)-[:ON]->(r:Road)<-[:ON]-(t:Sensor) WHERE r.name CONTAINS 'A' "
                  "RETURN r.name, count(*) ORDER BY count(*) DESC LIMIT 3", "cypher")
    assert {"multi_hop", "filter", "count", "top_n", "text_match"} <= set(cats)
    cats = detect("MATCH p=shortestPath((a:J)-[:LINK*..5]-(b:J)) RETURN p", "cypher")
    assert {"one_hop", "var_length", "shortest_path"} <= set(cats)
    assert guess_language("MATCH (n) RETURN n") == "cypher"


def test_catalogue_ids_unique():
    ids = [c.id for c in CATALOG]
    assert len(ids) == len(set(ids))


def test_complexity_from_the_hardest_category():
    from quillbench.categories import complexity
    assert complexity(["simple", "filter"]) == "Easy"
    assert complexity(["filter", "join", "avg"]) == "Medium"
    assert complexity(["having", "sum"]) == "Hard"
    assert complexity(["multi_join", "window"]) == "Very hard"
