"""Exercise the real SQLite importer and HTTP API with isolated databases."""
import copy
import json
from pathlib import Path

import pytest

pytest.importorskip("flask")
from webapp import create_app
from webapp.database import get_db, import_records


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.sqlite3")})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def sample():
    return json.loads((Path(__file__).parents[1] / "data/pup/unimatch_ph_raw_data.json").read_text())


def test_home_health_and_real_seeded_data(client):
    home = client.get("/")
    assert home.status_code == 200
    assert b"Your next chapter" in home.data
    assert "script-src 'self'" in home.headers["Content-Security-Policy"]
    assert client.get("/api/health").json == {"status": "ok"}
    stats = client.get("/api/stats").json
    assert stats["universities"] == 4
    assert stats["programs"] == 312
    assert stats["collected_universities"] == 2


def test_university_filtering_and_program_search(client):
    assert client.get("/api/universities?city=Quezon%20City").json["total"] == 1
    assert client.get("/api/universities?sector=Private").json["total"] == 2
    result = client.get("/api/universities?search=computer%20science").json
    assert result["total"] == 2
    assert {s["id"] for s in result["items"]} == {"pup-sta-mesa", "dlsu-manila"}
    assert client.get("/api/universities?search=%27%20OR%201%3D1%20--").json["total"] == 0


def test_program_pagination_and_topic_filter(client):
    first = client.get("/api/programs?limit=12").json
    second = client.get("/api/programs?limit=12&page=2").json
    assert first["total"] == second["total"] == 312
    assert len(first["items"]) == len(second["items"]) == 12
    assert not {p["id"] for p in first["items"]} & {p["id"] for p in second["items"]}
    computing = client.get("/api/programs?topic=Computing").json
    assert computing["total"] >= 2
    assert all(p["topic"] == "Computing" for p in computing["items"])
    assert client.get("/api/programs?university_id=dlsu-manila").json["total"] == 257


@pytest.mark.parametrize("url", ["/api/programs?page=0", "/api/programs?limit=101", "/api/programs?page=banana", "/api/universities?sort=DROP", "/api/compare?ids=", "/api/compare?ids=a,b,c,d", "/api/universities?search=" + "x" * 201])
def test_invalid_api_input(client, url):
    response = client.get(url)
    assert response.status_code == 400
    assert "error" in response.json


def test_detail_preserves_source_conflicts_and_missing_facts(client):
    school = client.get("/api/universities/pup-sta-mesa").json
    assert school["review_required"] is True
    assert school["collection_status"] == "partial"
    assert len(school["programs"]) == 55
    assert len(school["evidence"]["application_status"]) == 1
    assert any("2024-2025" in note for note in school["evidence"]["source_notes"])
    assert school["evidence"]["tuition_estimates"] == []
    assert all(p["strand_alignment"] is None for p in school["programs"])
    pending = client.get("/api/universities/ust-manila").json
    assert pending["collection_status"] == "pending"
    assert pending["programs"] == []
    assert client.get("/api/universities/not-a-school").status_code == 404


def test_comparison_preserves_selection_order_and_detects_unknown_id(client):
    response = client.get("/api/compare?ids=ust-manila,pup-sta-mesa,ust-manila")
    assert [s["id"] for s in response.json["items"]] == ["ust-manila", "pup-sta-mesa"]
    assert client.get("/api/compare?ids=not-a-school").status_code == 404


def test_reimport_replaces_programs_and_is_repeatable(app, client, sample):
    changed = copy.deepcopy(sample)
    changed[0]["programs"] = changed[0]["programs"][:1]
    with app.app_context():
        assert import_records(changed) == 1
        assert import_records(changed) == 1
    assert client.get("/api/stats").json["programs"] == 258
    restarted = create_app({"TESTING": True, "DATABASE": app.config["DATABASE"]})
    assert restarted.test_client().get("/api/stats").json["programs"] == 258


def test_invalid_batch_leaves_database_unchanged(app, client, sample):
    bad = copy.deepcopy(sample)
    bad.append({"university_name": "Invalid university", "campus_location": "Manila", "programs": [{"name": None}]})
    with app.app_context(), pytest.raises(ValueError):
        import_records(bad)
    assert client.get("/api/stats").json["programs"] == 312
    assert client.get("/api/stats").json["universities"] == 4


def test_failed_sql_import_rolls_back_the_whole_transaction(app, client, sample):
    bad = copy.deepcopy(sample)
    # Unserializable evidence fails after SQL operations have begun.
    bad[0]["programs"][0]["name"] = "Replacement program"
    bad.append({"university_name": "Other", "campus_location": "Manila", "errors": [set()]})
    with app.app_context(), pytest.raises(TypeError):
        import_records(bad)
    assert client.get("/api/stats").json["programs"] == 312
    assert client.get("/api/stats").json["universities"] == 4


def test_import_cli_and_malformed_file(app, sample, tmp_path):
    source = tmp_path / "export.json"
    source.write_text(json.dumps(sample))
    runner = app.test_cli_runner()
    assert runner.invoke(args=["import-data", str(source)]).exit_code == 0
    source.write_text("not json")
    result = runner.invoke(args=["import-data", str(source)])
    assert result.exit_code != 0
    assert "Error" in result.output


def test_unsafe_program_urls_are_not_turned_into_links(app, client):
    with app.app_context():
        import_records([{"university_name": "Example", "campus_location": "Manila",
                         "programs": [{"name": "BS Example", "source_url": "javascript:alert(1)"}],
                         "sources": [{"requested_url": "javascript:alert(1)"}]}])
    program = client.get("/api/programs?search=BS%20Example").json["items"][0]
    assert program["source_url"] is None


def test_importing_another_campus_does_not_overwrite_sta_mesa(app, client, sample):
    other = copy.deepcopy(sample)
    other[0]["campus_location"] = "Quezon City campus"
    other[0]["programs"] = other[0]["programs"][:1]
    with app.app_context():
        import_records(other)
    stats = client.get("/api/stats").json
    assert stats["universities"] == 5
    assert stats["programs"] == 313
    original = client.get("/api/universities/pup-sta-mesa").json
    assert original["program_count"] == 55
    assert original["city"] == "Manila"


def test_dlsu_upload_is_seeded_without_presenting_fee_links_as_amounts(client):
    school = client.get("/api/universities/dlsu-manila").json
    assert school["collection_status"] == "partial"
    assert school["program_count"] == 257
    assert school["tuition_evidence_count"] == 2
    assert school["tuition_amount_count"] == 0
    assert school["evidence"]["admission_requirements"] == []
    assert school["evidence"]["application_deadlines"] == []
    notes = " ".join(school["evidence"]["source_notes"])
    assert "old.dlsu.edu.ph" in notes
    assert "no numeric PHP amounts" in notes
    assert "Laguna" in notes and "Rufino" in notes
    assert "concatenated" in notes
    assert "1 repeated program name" in notes
    assert all(p["strand_alignment"] is None for p in school["programs"])


def test_uploaded_dlsu_json_and_csv_agree():
    import csv
    folder = Path(__file__).parents[1] / "data/dlsu"
    record = json.loads((folder / "unimatch_ph_raw_data.json").read_text())[0]
    with (folder / "unimatch_ph_raw_data.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(record["programs"]) == len(rows) == 258
    for row, program in zip(rows, record["programs"]):
        assert row["program_name"] == program["name"]
        assert row["program_source_url"] == program["source_url"]
        assert row["university_name"] == record["university_name"]
        assert json.loads(row["tuition_estimates"]) == record["tuition_estimates"]


def test_dlsu_reimport_deduplicates_titles_and_retains_other_schools(app, client):
    path = Path(__file__).parents[1] / "data/dlsu/unimatch_ph_raw_data.json"
    records = json.loads(path.read_text())
    with app.app_context():
        import_records(records)
        import_records(records)
    assert client.get("/api/stats").json["programs"] == 312
    assert client.get("/api/universities/pup-sta-mesa").json["program_count"] == 55
    assert client.get("/api/universities/dlsu-manila").json["program_count"] == 257
    # The importer annotates the database, not the uploaded source object.
    assert records[0]["source_notes"] == []


@pytest.mark.parametrize("amounts,expected", [
    ([], 0),
    ([{"min_php": 40000, "max_php": 60000}], 1),
    ([{"min_php": 0, "max_php": 0}], 1),
    ([{"min_php": "40000", "max_php": None}], 0),
    ([{"min_php": 60000, "max_php": 40000}], 0),
])
def test_fee_references_are_distinct_from_explicit_amounts(amounts, expected):
    from webapp.database import tuition_amount_count
    assert tuition_amount_count([{"text": "Fee reference", "amounts": amounts}]) == expected
