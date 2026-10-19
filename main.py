import os
import uuid
from datetime import datetime, timezone

from flask import Flask, request, render_template_string
from google.cloud import firestore
from google.cloud import bigquery

app = Flask(__name__)

# Google Cloud clients
firestore_client = firestore.Client()
bigquery_client = bigquery.Client()

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")

DATASET_ID = os.environ.get(
    "BIGQUERY_DATASET",
    "community_analytics"
)

TABLE_ID = os.environ.get(
    "BIGQUERY_TABLE",
    "participants"
)

TABLE_FULL_ID = (
    f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
)


HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Community Project Tracker</title>
<style>
body {
    font-family: Arial, sans-serif;
    max-width: 1000px;
    margin: 40px auto;
    padding: 20px;
    background: #f5f7fa;
}
h1 {
    text-align: center;
}
.card {
    background: white;
    padding: 25px;
    margin-bottom: 25px;
    border-radius: 10px;
}
input, select {
    width: 100%;
    padding: 10px;
    margin: 8px 0 15px;
    box-sizing: border-box;
}
button {
    padding: 10px 20px;
    border: none;
    border-radius: 5px;
    cursor: pointer;
}
table {
    width: 100%;
    border-collapse: collapse;
}
th, td {
    padding: 10px;
    border-bottom: 1px solid #ddd;
}
.stat {
    font-size: 24px;
    font-weight: bold;
}
</style>
</head>
<body>

<h1>🌱 Community Project Tracker</h1>

<div class="card">
<h2>Add a Community Project</h2>
<form method="POST" action="/add-project">
<label>Project name</label>
<input type="text" name="project_name" required>

<label>Category</label>
<select name="category">
<option>Sports</option>
<option>Education</option>
<option>Arts</option>
<option>Environment</option>
<option>Community</option>
</select>

<label>Description</label>
<input type="text" name="description" required>

<button type="submit">Add Project</button>
</form>
</div>

<div class="card">
<h2>Register a Participant</h2>
<form method="POST" action="/register">
<label>Participant name</label>
<input type="text" name="participant_name" required>

<label>Project</label>
<select name="project_name">
{% for project in projects %}
<option value="{{ project.name }}">{{ project.name }}</option>
{% endfor %}
</select>

<label>Volunteer hours</label>
<input type="number" name="hours" min="0" step="0.5" required>

<button type="submit">Register Participant</button>
</form>
</div>

<div class="card">
<h2>Projects</h2>
<table>
<tr>
<th>Project</th>
<th>Category</th>
<th>Description</th>
</tr>
{% for project in projects %}
<tr>
<td>{{ project.name }}</td>
<td>{{ project.category }}</td>
<td>{{ project.description }}</td>
</tr>
{% endfor %}
</table>
</div>

<div class="card">
<h2>📊 Analytics</h2>
<div class="stat">{{ total_participants }}</div>
<p>Total participants</p>

<div class="stat">{{ total_hours }}</div>
<p>Total volunteer hours</p>

<h3>Participants by Project</h3>
<table>
<tr>
<th>Project</th>
<th>Participants</th>
<th>Hours</th>
</tr>
{% for row in analytics %}
<tr>
<td>{{ row.project_name }}</td>
<td>{{ row.participants }}</td>
<td>{{ row.hours }}</td>
</tr>
{% endfor %}
</table>
</div>

</body>
</html>
"""


def get_projects():
    try:
        docs = firestore_client.collection("projects").stream()
        projects = []
        for doc in docs:
            data = doc.to_dict()
            projects.append({
                "name": data.get("name"),
                "category": data.get("category"),
                "description": data.get("description")
            })
        return projects
    except Exception as e:
        print(f"Firestore error: {e}")
        return []


def get_analytics():
    try:
        query = f"""
            SELECT
                project_name,
                COUNT(*) AS participants,
                SUM(hours) AS hours
            FROM `{TABLE_FULL_ID}`
            GROUP BY project_name
            ORDER BY participants DESC
        """
        result = bigquery_client.query(query)
        return list(result.result())
    except Exception as e:
        print(f"BigQuery error or table empty: {e}")
        return []


@app.route("/", methods=["GET"])
def home():
    projects = get_projects()
    analytics = get_analytics()

    total_participants = sum(
        int(row.participants)
        for row in analytics
    ) if analytics else 0

    total_hours = sum(
        float(row.hours or 0)
        for row in analytics
    ) if analytics else 0

    return render_template_string(
        HTML,
        projects=projects,
        analytics=analytics,
        total_participants=total_participants,
        total_hours=total_hours
    )


@app.route("/add-project", methods=["POST"])
def add_project():
    project_name = request.form["project_name"]
    category = request.form["category"]
    description = request.form["description"]

    firestore_client.collection("projects").add({
        "name": project_name,
        "category": category,
        "description": description,
        "created_at": firestore.SERVER_TIMESTAMP
    })

    return """
    <p>Project added successfully.</p>
    <a href="/">Return to tracker</a>
    """


@app.route("/register", methods=["POST"])
def register():
    participant_name = request.form["participant_name"]
    project_name = request.form["project_name"]
    hours = float(request.form["hours"])

    # Find project
    project_query = firestore_client.collection(
        "projects"
    ).where(
        "name",
        "==",
        project_name
    ).limit(1).stream()

    project_docs = list(project_query)
    category = "Unknown"

    if project_docs:
        category = project_docs[0].to_dict().get("category", "Unknown")

    participant_id = str(uuid.uuid4())
    registered_at = datetime.now(timezone.utc)

    # 1. Store operational data in Firestore
    firestore_client.collection("participants").document(participant_id).set({
        "participant_name": participant_name,
        "project_name": project_name,
        "category": category,
        "hours": hours,
        "registered_at": registered_at
    })

    # 2. Store analytics data in BigQuery
    row = {
        "participant_id": participant_id,
        "participant_name": participant_name,
        "project_name": project_name,
        "category": category,
        "hours": hours,
        "registered_at": registered_at.isoformat()
    }

    try:
        errors = bigquery_client.insert_rows_json(TABLE_FULL_ID, [row])
        if errors:
            print("BigQuery errors:", errors)
    except Exception as e:
        print("BigQuery insertion exception:", e)

    return """
    <p>Participant registered successfully.</p>
    <a href="/">Return to tracker</a>
    """


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080))
    )
