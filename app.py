"""
Smart Resource Allocation: Data-Driven Volunteer Coordination for Social Impact
Flask Backend API
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
from bson import ObjectId
from bson.json_util import dumps
from datetime import datetime
import json
import math

app = Flask(__name__)
CORS(app)

# ──────────────────────────────────────────────
# MongoDB Connection
# ──────────────────────────────────────────────
MONGO_URI = "mongodb://localhost:27017/"
client = MongoClient(MONGO_URI)
db = client["smart_resource_db"]

users_col       = db["users"]
tasks_col       = db["tasks"]
reports_col     = db["reports"]
assignments_col = db["assignments"]

# ──────────────────────────────────────────────
# Helper: JSON serialiser for ObjectId / datetime
# ──────────────────────────────────────────────
def serialize(doc):
    if doc is None:
        return None
    doc["_id"] = str(doc["_id"])
    for k, v in doc.items():
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
        if isinstance(v, ObjectId):
            doc[k] = str(v)
    return doc

def serialize_list(docs):
    return [serialize(d) for d in docs]

# ──────────────────────────────────────────────
# SMART MATCHING ENGINE
# ──────────────────────────────────────────────

def haversine_distance(lat1, lon1, lat2, lon2):
    """Return distance in km between two lat/lon points."""
    R = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.asin(math.sqrt(a))

URGENCY_WEIGHTS = {"critical": 10, "high": 7, "medium": 4, "low": 1}

def compute_match_score(task, volunteer):
    """
    Scoring formula:
        score = (urgency_weight * 4)
              + (skill_match_count * 3)
              + max(0, 10 - distance_km) * 1.5

    Higher score = better match.
    """
    urgency_score = URGENCY_WEIGHTS.get(task.get("urgency", "low"), 1) * 4

    task_skills  = set(s.lower() for s in task.get("required_skills", []))
    vol_skills   = set(s.lower() for s in volunteer.get("skills", []))
    skill_score  = len(task_skills & vol_skills) * 3

    try:
        t_loc = task["location"]
        v_loc = volunteer["location"]
        dist  = haversine_distance(
            t_loc["lat"], t_loc["lon"],
            v_loc["lat"], v_loc["lon"]
        )
        proximity_score = max(0, 10 - dist) * 1.5
    except Exception:
        proximity_score = 0

    return round(urgency_score + skill_score + proximity_score, 2)

def find_best_volunteer(task):
    """Return the volunteer with the highest match score for a given task."""
    volunteers = list(users_col.find({"role": "volunteer", "available": True}))
    if not volunteers:
        return None, 0

    scored = [(v, compute_match_score(task, v)) for v in volunteers]
    scored.sort(key=lambda x: x[1], reverse=True)
    best_vol, best_score = scored[0]
    return best_vol, best_score

# ──────────────────────────────────────────────
# MOCK NOTIFICATION SYSTEM
# ──────────────────────────────────────────────

def notify_volunteer(volunteer, task):
    msg = (
        f"\n📧  [NOTIFICATION] ─────────────────────────────────\n"
        f"  To      : {volunteer['name']} <{volunteer['email']}>\n"
        f"  Subject : New Task Assigned – {task['title']}\n"
        f"  Body    : Hi {volunteer['name']},\n"
        f"            You have been assigned a new task:\n"
        f"            • Task    : {task['title']}\n"
        f"            • Urgency : {task['urgency'].upper()}\n"
        f"            • Details : {task.get('description', 'N/A')}\n"
        f"            Please log in to the portal to accept.\n"
        f"────────────────────────────────────────────────────\n"
    )
    print(msg)
    return msg

# ──────────────────────────────────────────────
# ROUTES
# ──────────────────────────────────────────────

@app.route("/", methods=["GET"])
def health_check():
    return jsonify({"status": "ok", "message": "Smart Resource API is running"})

# ── Reports ──────────────────────────────────

@app.route("/add-report", methods=["POST"])
def add_report():
    """
    POST /add-report
    Body: { source, description, location:{lat,lon}, needs:[str], urgency }
    """
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400

    required = ["source", "description", "needs"]
    for field in required:
        if field not in data:
            return jsonify({"error": f"Missing field: {field}"}), 400

    report = {
        "source"      : data["source"],
        "description" : data["description"],
        "location"    : data.get("location", {"lat": 0, "lon": 0}),
        "needs"       : data["needs"],
        "urgency"     : data.get("urgency", "medium"),
        "status"      : "pending",
        "submitted_at": datetime.utcnow()
    }
    result = reports_col.insert_one(report)

    # Auto-create a task from the report
    task = {
        "title"          : f"Report from {data['source']}",
        "description"    : data["description"],
        "required_skills": data.get("required_skills", []),
        "location"       : data.get("location", {"lat": 0, "lon": 0}),
        "urgency"        : data.get("urgency", "medium"),
        "status"         : "open",
        "report_id"      : str(result.inserted_id),
        "created_at"     : datetime.utcnow()
    }
    tasks_col.insert_one(task)

    return jsonify({
        "message"  : "Report submitted and task created",
        "report_id": str(result.inserted_id)
    }), 201


@app.route("/reports", methods=["GET"])
def get_reports():
    reports = list(reports_col.find().sort("submitted_at", -1).limit(50))
    return jsonify(serialize_list(reports))

# ── Needs / Tasks ─────────────────────────────

@app.route("/needs", methods=["GET"])
def get_needs():
    """
    GET /needs
    Returns open tasks sorted by urgency priority.
    """
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    tasks = list(tasks_col.find({"status": "open"}))
    tasks.sort(key=lambda t: priority_order.get(t.get("urgency", "low"), 4))
    return jsonify(serialize_list(tasks))


@app.route("/tasks", methods=["GET"])
def get_all_tasks():
    tasks = list(tasks_col.find().sort("created_at", -1).limit(100))
    return jsonify(serialize_list(tasks))

# ── Volunteers ────────────────────────────────

@app.route("/add-volunteer", methods=["POST"])
def add_volunteer():
    """
    POST /add-volunteer
    Body: { name, email, phone, skills:[str], location:{lat,lon}, available }
    """
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400

    required = ["name", "email", "skills"]
    for field in required:
        if field not in data:
            return jsonify({"error": f"Missing field: {field}"}), 400

    if users_col.find_one({"email": data["email"]}):
        return jsonify({"error": "Email already registered"}), 409

    volunteer = {
        "name"       : data["name"],
        "email"      : data["email"],
        "phone"      : data.get("phone", ""),
        "skills"     : data["skills"],
        "location"   : data.get("location", {"lat": 0, "lon": 0}),
        "available"  : data.get("available", True),
        "role"       : "volunteer",
        "registered_at": datetime.utcnow()
    }
    result = users_col.insert_one(volunteer)
    return jsonify({
        "message"     : "Volunteer registered successfully",
        "volunteer_id": str(result.inserted_id)
    }), 201


@app.route("/volunteers", methods=["GET"])
def get_volunteers():
    volunteers = list(users_col.find({"role": "volunteer"}).sort("registered_at", -1))
    return jsonify(serialize_list(volunteers))

# ── Assignments ───────────────────────────────

@app.route("/assign-task", methods=["POST"])
def assign_task():
    """
    POST /assign-task
    Body: { task_id }  — auto-picks best volunteer
          OR { task_id, volunteer_id }  — manual assignment
    """
    data = request.json
    if not data or "task_id" not in data:
        return jsonify({"error": "task_id required"}), 400

    try:
        task = tasks_col.find_one({"_id": ObjectId(data["task_id"])})
    except Exception:
        return jsonify({"error": "Invalid task_id"}), 400

    if not task:
        return jsonify({"error": "Task not found"}), 404
    if task.get("status") != "open":
        return jsonify({"error": "Task is not open for assignment"}), 409

    # Manual or auto volunteer selection
    if "volunteer_id" in data:
        try:
            volunteer = users_col.find_one({"_id": ObjectId(data["volunteer_id"])})
        except Exception:
            return jsonify({"error": "Invalid volunteer_id"}), 400
        if not volunteer:
            return jsonify({"error": "Volunteer not found"}), 404
        score = compute_match_score(task, volunteer)
    else:
        volunteer, score = find_best_volunteer(task)
        if not volunteer:
            return jsonify({"error": "No available volunteers found"}), 404

    assignment = {
        "task_id"     : str(task["_id"]),
        "task_title"  : task["title"],
        "volunteer_id": str(volunteer["_id"]),
        "volunteer_name": volunteer["name"],
        "match_score" : score,
        "status"      : "assigned",
        "assigned_at" : datetime.utcnow()
    }
    assignments_col.insert_one(assignment)

    # Update task & volunteer availability
    tasks_col.update_one({"_id": task["_id"]}, {"$set": {"status": "assigned"}})
    users_col.update_one({"_id": volunteer["_id"]}, {"$set": {"available": False}})

    # Send mock notification
    notification = notify_volunteer(volunteer, task)

    return jsonify({
        "message"       : "Task assigned successfully",
        "volunteer_name": volunteer["name"],
        "task_title"    : task["title"],
        "match_score"   : score,
        "notification"  : notification
    }), 201


@app.route("/volunteer-tasks/<volunteer_id>", methods=["GET"])
def get_volunteer_tasks(volunteer_id):
    """GET /volunteer-tasks/<volunteer_id>"""
    assignments = list(assignments_col.find({"volunteer_id": volunteer_id}))
    return jsonify(serialize_list(assignments))


@app.route("/assignments", methods=["GET"])
def get_all_assignments():
    assignments = list(assignments_col.find().sort("assigned_at", -1))
    return jsonify(serialize_list(assignments))

# ── Seed Data ─────────────────────────────────

@app.route("/seed", methods=["POST"])
def seed_database():
    """Populate DB with sample data for demo purposes."""
    users_col.delete_many({})
    tasks_col.delete_many({})
    reports_col.delete_many({})
    assignments_col.delete_many({})

    sample_volunteers = [
        {"name":"Ananya Krishnan","email":"ananya@example.com","phone":"9876543210",
         "skills":["medical","first_aid","counseling"],"location":{"lat":11.00,"lon":77.01},
         "available":True,"role":"volunteer","registered_at":datetime.utcnow()},
        {"name":"Ravi Shankar","email":"ravi@example.com","phone":"9123456780",
         "skills":["logistics","driving","inventory"],"location":{"lat":11.05,"lon":77.05},
         "available":True,"role":"volunteer","registered_at":datetime.utcnow()},
        {"name":"Meena Patel","email":"meena@example.com","phone":"9000111222",
         "skills":["teaching","counseling","child_care"],"location":{"lat":10.98,"lon":76.99},
         "available":True,"role":"volunteer","registered_at":datetime.utcnow()},
        {"name":"Arjun Das","email":"arjun@example.com","phone":"8765432109",
         "skills":["construction","logistics","driving"],"location":{"lat":11.10,"lon":77.10},
         "available":True,"role":"volunteer","registered_at":datetime.utcnow()},
    ]
    users_col.insert_many(sample_volunteers)

    sample_tasks = [
        {"title":"Flood Relief Medical Aid","description":"Provide first aid to flood victims in Erode",
         "required_skills":["medical","first_aid"],"location":{"lat":11.01,"lon":77.02},
         "urgency":"critical","status":"open","created_at":datetime.utcnow()},
        {"title":"Food Distribution Drive","description":"Distribute ration kits to 200 families",
         "required_skills":["logistics","driving"],"location":{"lat":11.04,"lon":77.04},
         "urgency":"high","status":"open","created_at":datetime.utcnow()},
        {"title":"Children's Education Camp","description":"Conduct catch-up classes for displaced children",
         "required_skills":["teaching","child_care"],"location":{"lat":10.99,"lon":77.00},
         "urgency":"medium","status":"open","created_at":datetime.utcnow()},
        {"title":"Shelter Construction Support","description":"Help rebuild temporary shelters",
         "required_skills":["construction","logistics"],"location":{"lat":11.09,"lon":77.09},
         "urgency":"high","status":"open","created_at":datetime.utcnow()},
    ]
    tasks_col.insert_many(sample_tasks)

    return jsonify({"message": "Database seeded with sample data ✅"}), 201

# ──────────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 Smart Resource Allocation API starting on http://localhost:5000")
    app.run(debug=True, host="0.0.0.0", port=5000)