"""Flask application factory: local frontend, JSON API and SQLite datastore."""
import json
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from .database import close_db, get_db, import_data_command, init_db, safe_url, tuition_amount_count


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(DATABASE=str(Path(app.instance_path) / "unimatch.sqlite3"))
    if test_config:
        app.config.update(test_config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    app.teardown_appcontext(close_db)
    app.cli.add_command(import_data_command)
    with app.app_context():
        init_db()

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def school_dict(row, detail=False):
        item = dict(row)
        item["review_required"] = True
        item["official_url"] = safe_url(item.get("official_url"))
        sources = json.loads(item.pop("sources_json", "[]"))
        evidence = json.loads(item.pop("evidence_json", "{}"))
        if detail:
            item["sources"] = sources
            item["evidence"] = evidence
        else:
            item["source_notes"] = evidence.get("source_notes", [])
        item["tuition_evidence_count"] = len(evidence.get("tuition_estimates", []))
        item["tuition_amount_count"] = tuition_amount_count(evidence.get("tuition_estimates", []))
        return item

    school_select = """SELECT u.*, (SELECT COUNT(*) FROM programs p WHERE p.university_id = u.id) AS program_count
                       FROM universities u"""

    def text_arg(name):
        value = request.args.get(name, "").strip()
        if len(value) > 200:
            raise ValueError(f"{name} must be at most 200 characters")
        return value

    @app.errorhandler(ValueError)
    def bad_input(error):
        return jsonify(error=str(error)), 400

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        get_db().execute("SELECT COUNT(*) FROM universities").fetchone()
        return jsonify(status="ok")

    @app.get("/api/stats")
    def stats():
        connection = get_db()
        return jsonify(
            universities=connection.execute("SELECT COUNT(*) FROM universities").fetchone()[0],
            programs=connection.execute("SELECT COUNT(*) FROM programs").fetchone()[0],
            collected_universities=connection.execute("SELECT COUNT(*) FROM universities WHERE collection_status != 'pending'").fetchone()[0],
            cities=[r[0] for r in connection.execute("SELECT DISTINCT city FROM universities ORDER BY city")],
            topics=[r[0] for r in connection.execute("SELECT DISTINCT topic FROM programs ORDER BY topic")],
            latest_collection=connection.execute("SELECT MAX(collected_at) FROM universities").fetchone()[0],
        )

    @app.get("/api/universities")
    def universities():
        search, city, sector = text_arg("search"), text_arg("city"), text_arg("sector")
        conditions, params = [], []
        if search:
            conditions.append("""(instr(lower(u.name || ' ' || u.short_name || ' ' || u.campus_location), lower(?)) > 0
                                  OR EXISTS (SELECT 1 FROM programs p WHERE p.university_id=u.id AND instr(lower(p.name), lower(?)) > 0))""")
            params.extend([search, search])
        for column, value in (("city", city), ("sector", sector)):
            if value:
                conditions.append(f"u.{column} = ?")
                params.append(value)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        order = {"name": "u.name", "programs": "program_count DESC, u.name", "featured": "program_count DESC, u.name"}
        sort = text_arg("sort") or "featured"
        if sort not in order:
            raise ValueError("Unknown sort order")
        rows = get_db().execute(school_select + where + " ORDER BY " + order[sort], params).fetchall()
        return jsonify(items=[school_dict(row) for row in rows], total=len(rows))

    @app.get("/api/universities/<school_id>")
    def university(school_id):
        row = get_db().execute(school_select + " WHERE u.id = ?", (school_id,)).fetchone()
        if row is None:
            return jsonify(error="University not found"), 404
        item = school_dict(row, detail=True)
        item["programs"] = [dict(p) for p in get_db().execute("SELECT * FROM programs WHERE university_id = ? ORDER BY name", (school_id,))]
        return jsonify(item)

    @app.get("/api/programs")
    def programs():
        search, school_id, topic = text_arg("search"), text_arg("university_id"), text_arg("topic")
        try:
            page, limit = int(request.args.get("page", "1")), int(request.args.get("limit", "12"))
        except ValueError as exc:
            raise ValueError("page and limit must be integers") from exc
        if page < 1 or page > 10000 or not 1 <= limit <= 100:
            raise ValueError("page must be 1–10000 and limit must be 1–100")
        conditions, params = [], []
        if search:
            conditions.append("instr(lower(p.name || ' ' || u.name || ' ' || u.short_name), lower(?)) > 0")
            params.append(search)
        for column, value in (("university_id", school_id), ("topic", topic)):
            if value:
                conditions.append(f"p.{column} = ?")
                params.append(value)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        joined = " FROM programs p JOIN universities u ON p.university_id=u.id"
        total = get_db().execute("SELECT COUNT(*)" + joined + where, params).fetchone()[0]
        rows = get_db().execute(
            "SELECT p.*, u.name AS university_name, u.short_name, u.city, u.color" + joined + where
            + " ORDER BY p.name, p.id LIMIT ? OFFSET ?", [*params, limit, (page - 1) * limit]).fetchall()
        return jsonify(items=[dict(row) for row in rows], total=total, page=page, limit=limit)

    @app.get("/api/compare")
    def compare():
        ids = list(dict.fromkeys(text_arg("ids").split(",")))
        if not 1 <= len(ids) <= 3 or not all(ids):
            raise ValueError("Select between one and three universities")
        items = []
        for school_id in ids:
            row = get_db().execute(school_select + " WHERE u.id = ?", (school_id,)).fetchone()
            if row is None:
                return jsonify(error="University not found"), 404
            items.append(school_dict(row, detail=True))
        return jsonify(items=items)

    return app
