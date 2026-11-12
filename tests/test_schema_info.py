import sqlite3

from quillbench import connections, schema_info


def _db(tmp_path):
    path = tmp_path / "t.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE road (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE sensor (id INTEGER PRIMARY KEY, road_id INTEGER REFERENCES road(id), lane INT);
        CREATE TABLE reading_201904 (sensor_id INT, speed REAL);
        CREATE TABLE reading_201905 (sensor_id INT, speed REAL);
        CREATE TABLE reading_201906 (sensor_id INT, speed REAL);
        CREATE TABLE spatial_ref_sys (srid INT);
        INSERT INTO road VALUES (1, 'A1');
    """)
    db.commit()
    db.close()
    return connections.Connection(id="t", name="T", engine="sqlite", path=str(path))


def test_describe_keys_folding_and_hidden_tables(tmp_path):
    conn = _db(tmp_path)
    tables, err = schema_info.describe(conn)
    assert err is None
    assert set(tables) == {"road", "sensor", "reading_201906"}   # system table hidden, dated tables folded
    folded = tables["reading_201906"]                            # listed under a name that exists
    assert folded.parts == ["reading_201904", "reading_201905", "reading_201906"] and not folded.parent
    keys = {c.name: c.key for c in tables["sensor"].columns}
    assert keys == {"id": "PK", "road_id": "→ road.id", "lane": ""}
    assert dict(schema_info.shared_columns(tables))["id"] == ["road", "sensor"]


def test_sample_rows_run_read_only(tmp_path):
    conn = _db(tmp_path)
    tables, _ = schema_info.describe(conn)
    q = schema_info.sample_query(conn, tables["road"])
    assert q == 'SELECT * FROM "road" LIMIT 3'
    res = connections.run(conn, q)
    assert res.ok and res.rows == [[1, "A1"]]


def test_quote_handles_odd_names():
    conn = connections.Connection(id="m", name="M", engine="mysql")
    assert schema_info.quote(conn, "a`b") == "`a``b`"
    pg = connections.Connection(id="p", name="P", engine="postgresql")
    assert schema_info.quote(pg, 'public.x"y') == '"public"."x""y"'
