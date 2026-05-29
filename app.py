from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_file
import sqlite3, os, csv, io
from dataclasses import dataclass
from typing import List, Tuple
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from datetime import datetime

app = Flask(__name__)
app.secret_key = "smart-hostel-ai-ultimate-secret"
DB_NAME = "hostel_ultimate.db"
UPLOAD_FOLDER = os.path.join("static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# ---------------- DATABASE ----------------

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS rooms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_no TEXT NOT NULL UNIQUE,
        gender TEXT NOT NULL,
        year TEXT NOT NULL,
        capacity INTEGER NOT NULL,
        block TEXT NOT NULL DEFAULT 'A',
        floor INTEGER NOT NULL DEFAULT 1,
        noise_level TEXT NOT NULL DEFAULT 'Quiet',
        room_type TEXT NOT NULL DEFAULT 'Standard'
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        gender TEXT NOT NULL,
        year TEXT NOT NULL,
        study_habit TEXT NOT NULL,
        sleep_time TEXT NOT NULL,
        cleanliness TEXT NOT NULL,
        noise TEXT NOT NULL,
        profile_pic TEXT DEFAULT '',
        allocated_room INTEGER,
        compatibility_score INTEGER DEFAULT 0,
        conflict_risk TEXT DEFAULT 'Not calculated',
        explanation TEXT DEFAULT '',
        waitlisted INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(allocated_room) REFERENCES rooms(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS announcements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    if cur.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 0:
        cur.execute("INSERT INTO admins(username,password) VALUES(?,?)",
                    ("admin", generate_password_hash("admin123")))

    if cur.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0:
        rooms = [
            ("A101", "Male", "1st Year", 3, "A", 1, "Quiet", "Premium"),
            ("A102", "Male", "1st Year", 3, "A", 1, "Moderate", "Standard"),
            ("A201", "Male", "2nd Year", 3, "A", 2, "Quiet", "Premium"),
            ("A202", "Male", "3rd Year", 2, "A", 2, "Quiet", "Premium"),
            ("C301", "Male", "3rd Year", 2, "C", 3, "Moderate", "Standard"),
            ("B101", "Female", "1st Year", 3, "B", 1, "Quiet", "Premium"),
            ("B102", "Female", "1st Year", 3, "B", 1, "Moderate", "Standard"),
            ("B201", "Female", "2nd Year", 3, "B", 2, "Quiet", "Premium"),
            ("B202", "Female", "3rd Year", 2, "B", 2, "Quiet", "Premium"),
            ("D301", "Female", "3rd Year", 2, "D", 3, "Moderate", "Standard"),
        ]
        cur.executemany("""
            INSERT INTO rooms(room_no,gender,year,capacity,block,floor,noise_level,room_type)
            VALUES (?,?,?,?,?,?,?,?)
        """, rooms)

    if cur.execute("SELECT COUNT(*) FROM announcements").fetchone()[0] == 0:
        cur.execute("INSERT INTO announcements(title,message) VALUES(?,?)",
                    ("Welcome", "Hostel room allocation portal is now open."))

    conn.commit()
    conn.close()

# ---------------- AUTH ----------------

def student_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if session.get("role") != "student":
            flash("Please login as student first.")
            return redirect(url_for("student_login"))
        return func(*args, **kwargs)
    return wrapper

def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if session.get("role") != "admin":
            flash("Please login as admin first.")
            return redirect(url_for("admin_login"))
        return func(*args, **kwargs)
    return wrapper

# ---------------- AI LOGIC ----------------

@dataclass
class StudentProfile:
    id: int
    name: str
    gender: str
    year: str
    study_habit: str
    sleep_time: str
    cleanliness: str
    noise: str

def conflict_risk_from_score(score: int) -> str:
    if score >= 80:
        return "Low"
    if score >= 55:
        return "Medium"
    return "High"

def compatibility_score(student: StudentProfile, roommates: List[sqlite3.Row], room=None) -> Tuple[int, List[str]]:
    reasons = []

    if not roommates:
        base_score = 92
        reasons += ["Empty room gives low conflict probability", "No roommate preference mismatch"]
    else:
        total = 0
        for r in roommates:
            score = 0
            checks = [
                ("study_habit", "Study habit matched", "Study habit mismatch"),
                ("sleep_time", "Sleep schedule matched", "Sleep schedule mismatch"),
                ("cleanliness", "Cleanliness level matched", "Cleanliness mismatch"),
                ("noise", "Noise preference matched", "Noise preference mismatch"),
            ]
            for field, yes, no in checks:
                if getattr(student, field) == r[field]:
                    score += 25
                    reasons.append(yes)
                else:
                    reasons.append(no)
            total += score
        base_score = total // len(roommates)

    if room and room["noise_level"] == student.noise:
        base_score += 4
        reasons.append("Room noise level matches preference")
    elif room:
        reasons.append("Room noise level is not perfect but acceptable")

    return min(base_score, 100), list(dict.fromkeys(reasons))

def get_available_rooms_for_student(student_id):
    conn = get_db()
    student = conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    rooms = conn.execute("""
        SELECT r.*, COUNT(s.id) AS occupied, (r.capacity - COUNT(s.id)) AS available_beds
        FROM rooms r
        LEFT JOIN students s ON r.id = s.allocated_room
        WHERE r.gender=? AND r.year=?
        GROUP BY r.id
        HAVING available_beds > 0
        ORDER BY r.block, r.floor, r.room_no
    """, (student["gender"], student["year"])).fetchall()

    profile = StudentProfile(
        student["id"], student["name"], student["gender"], student["year"],
        student["study_habit"], student["sleep_time"], student["cleanliness"], student["noise"]
    )

    result = []
    for room in rooms:
        roommates = conn.execute("SELECT * FROM students WHERE allocated_room=?", (room["id"],)).fetchall()
        score, reasons = compatibility_score(profile, roommates, room)
        result.append({
            "room": room,
            "score": score,
            "risk": conflict_risk_from_score(score),
            "reasons": ", ".join(reasons)
        })

    conn.close()
    result.sort(key=lambda x: x["score"], reverse=True)
    return result

def auto_allocate(student_id):
    available = get_available_rooms_for_student(student_id)
    conn = get_db()

    if not available:
        conn.execute("""
            UPDATE students
            SET allocated_room=NULL, compatibility_score=0, conflict_risk='High',
            waitlisted=1,
            explanation='No room is currently available for this profile. Student added to waitlist.'
            WHERE id=?
        """, (student_id,))
        conn.commit()
        conn.close()
        return

    best = None
    best_utility = -1

    for item in available:
        room = item["room"]
        premium_bonus = 3 if room["room_type"] == "Premium" else 0
        utility = item["score"] + (room["available_beds"] * 2) + premium_bonus
        if utility > best_utility:
            best_utility = utility
            best = item

    room = best["room"]
    explanation = (
        f"AI selected Room {room['room_no']} using search + CSP + utility logic. "
        f"Constraints satisfied: gender={room['gender']}, year={room['year']}, available beds={room['available_beds']}. "
        f"Compatibility={best['score']}%, conflict risk={best['risk']}. "
        f"Main reasons: {best['reasons']}. Final utility score={best_utility}."
    )

    conn.execute("""
        UPDATE students
        SET allocated_room=?, compatibility_score=?, conflict_risk=?, explanation=?, waitlisted=0
        WHERE id=?
    """, (room["id"], best["score"], best["risk"], explanation, student_id))
    conn.commit()
    conn.close()

# ---------------- PUBLIC ----------------

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.")
    return redirect(url_for("index"))

# ---------------- STUDENT ----------------

@app.route("/student/register", methods=["GET", "POST"])
def student_register():
    if request.method == "POST":
        profile_pic = ""
        file = request.files.get("profile_pic")
        if file and file.filename:
            filename = secure_filename(str(int(datetime.now().timestamp())) + "_" + file.filename)
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            profile_pic = filename

        conn = get_db()
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO students
                (name,email,password,gender,year,study_habit,sleep_time,cleanliness,noise,profile_pic)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                request.form["name"],
                request.form["email"],
                generate_password_hash(request.form["password"]),
                request.form["gender"],
                request.form["year"],
                request.form["study_habit"],
                request.form["sleep_time"],
                request.form["cleanliness"],
                request.form["noise"],
                profile_pic
            ))
            conn.commit()
            session["role"] = "student"
            session["student_id"] = cur.lastrowid
            flash("Student account created successfully.")
            return redirect(url_for("student_dashboard"))
        except sqlite3.IntegrityError:
            flash("Email already exists. Please login.")
        finally:
            conn.close()

    return render_template("student_register.html")

@app.route("/student/login", methods=["GET", "POST"])
def student_login():
    if request.method == "POST":
        conn = get_db()
        student = conn.execute("SELECT * FROM students WHERE email=?", (request.form["email"],)).fetchone()
        conn.close()

        if student and check_password_hash(student["password"], request.form["password"]):
            session["role"] = "student"
            session["student_id"] = student["id"]
            flash("Student login successful.")
            return redirect(url_for("student_dashboard"))
        flash("Invalid student email or password.")

    return render_template("student_login.html")

@app.route("/student/dashboard")
@student_required
def student_dashboard():
    student_id = session["student_id"]
    conn = get_db()
    student = conn.execute("""
        SELECT s.*, r.room_no, r.block, r.floor, r.room_type, r.noise_level
        FROM students s
        LEFT JOIN rooms r ON s.allocated_room = r.id
        WHERE s.id=?
    """, (student_id,)).fetchone()
    announcements = conn.execute("SELECT * FROM announcements ORDER BY id DESC LIMIT 5").fetchall()
    conn.close()
    available = get_available_rooms_for_student(student_id)
    return render_template("student_dashboard.html", student=student, available=available, announcements=announcements)

@app.route("/student/update-profile", methods=["POST"])
@student_required
def update_profile():
    conn = get_db()
    conn.execute("""
        UPDATE students
        SET year=?, study_habit=?, sleep_time=?, cleanliness=?, noise=?,
        allocated_room=NULL, compatibility_score=0, conflict_risk='Not calculated',
        explanation='Profile updated. Apply again for AI allocation.', waitlisted=0
        WHERE id=?
    """, (
        request.form["year"], request.form["study_habit"], request.form["sleep_time"],
        request.form["cleanliness"], request.form["noise"], session["student_id"]
    ))
    conn.commit()
    conn.close()
    flash("Profile updated.")
    return redirect(url_for("student_dashboard"))

@app.route("/student/apply-allocation")
@student_required
def apply_allocation():
    auto_allocate(session["student_id"])
    flash("AI allocation completed.")
    return redirect(url_for("student_dashboard"))

@app.route("/chatbot", methods=["POST"])
@student_required
def chatbot():
    question = request.json.get("question", "").lower()
    student_id = session["student_id"]
    conn = get_db()
    student = conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    conn.close()
    available = get_available_rooms_for_student(student_id)

    if "best" in question or "recommend" in question:
        if available:
            top = available[0]
            return jsonify({"answer": f"Your best recommended room is {top['room']['room_no']} with {top['score']}% compatibility and {top['risk']} conflict risk."})
        return jsonify({"answer": "No room is available right now. You will be added to the waitlist if you apply."})
    if "why" in question or "reason" in question:
        return jsonify({"answer": student["explanation"] or "Apply for allocation first to generate reasoning."})
    if "available" in question:
        return jsonify({"answer": f"You currently have {len(available)} matching available room(s) based on your gender and year."})
    return jsonify({"answer": "I can answer about best room, available rooms, compatibility, and AI reasoning."})

# ---------------- ADMIN ----------------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        conn = get_db()
        admin = conn.execute("SELECT * FROM admins WHERE username=?", (request.form["username"],)).fetchone()
        conn.close()

        if admin and check_password_hash(admin["password"], request.form["password"]):
            session["role"] = "admin"
            session["admin_id"] = admin["id"]
            flash("Admin login successful.")
            return redirect(url_for("admin_dashboard"))
        flash("Invalid admin username or password.")

    return render_template("admin_login.html")

@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    search = request.args.get("search", "")
    gender_filter = request.args.get("gender", "")
    year_filter = request.args.get("year", "")

    conn = get_db()
    rooms = conn.execute("""
        SELECT r.*, COUNT(s.id) AS occupied, (r.capacity - COUNT(s.id)) AS available_beds
        FROM rooms r
        LEFT JOIN students s ON r.id = s.allocated_room
        GROUP BY r.id
        ORDER BY r.block, r.floor, r.room_no
    """).fetchall()

    query = """
        SELECT s.*, r.room_no
        FROM students s
        LEFT JOIN rooms r ON s.allocated_room = r.id
        WHERE 1=1
    """
    params = []
    if search:
        query += " AND (s.name LIKE ? OR s.email LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    if gender_filter:
        query += " AND s.gender=?"
        params.append(gender_filter)
    if year_filter:
        query += " AND s.year=?"
        params.append(year_filter)
    query += " ORDER BY s.id DESC"
    students = conn.execute(query, params).fetchall()

    all_students = conn.execute("SELECT * FROM students").fetchall()
    announcements = conn.execute("SELECT * FROM announcements ORDER BY id DESC LIMIT 5").fetchall()
    conn.close()

    total_capacity = sum(r["capacity"] for r in rooms)
    occupied = sum(r["occupied"] for r in rooms)
    avg_score = round(sum(s["compatibility_score"] for s in all_students) / len(all_students), 1) if all_students else 0
    male = sum(1 for s in all_students if s["gender"] == "Male")
    female = sum(1 for s in all_students if s["gender"] == "Female")
    waitlisted = sum(1 for s in all_students if s["waitlisted"] == 1)
    occupancy_percent = round((occupied / total_capacity) * 100, 1) if total_capacity else 0

    analytics = {
        "total_rooms": len(rooms),
        "total_students": len(all_students),
        "total_capacity": total_capacity,
        "occupied": occupied,
        "available": total_capacity - occupied,
        "occupancy_percent": occupancy_percent,
        "avg_score": avg_score,
        "male": male,
        "female": female,
        "waitlisted": waitlisted,
    }

    return render_template("admin_dashboard.html", rooms=rooms, students=students, analytics=analytics, announcements=announcements)

@app.route("/admin/rooms/add", methods=["POST"])
@admin_required
def add_room():
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO rooms(room_no,gender,year,capacity,block,floor,noise_level,room_type)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            request.form["room_no"], request.form["gender"], request.form["year"],
            int(request.form["capacity"]), request.form["block"], int(request.form["floor"]),
            request.form["noise_level"], request.form["room_type"]
        ))
        conn.commit()
        flash("Room added successfully.")
    except sqlite3.IntegrityError:
        flash("Room number already exists.")
    finally:
        conn.close()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/rooms/edit/<int:room_id>", methods=["GET", "POST"])
@admin_required
def edit_room(room_id):
    conn = get_db()
    if request.method == "POST":
        conn.execute("""
            UPDATE rooms SET room_no=?, gender=?, year=?, capacity=?, block=?, floor=?, noise_level=?, room_type=?
            WHERE id=?
        """, (
            request.form["room_no"], request.form["gender"], request.form["year"],
            int(request.form["capacity"]), request.form["block"], int(request.form["floor"]),
            request.form["noise_level"], request.form["room_type"], room_id
        ))
        conn.commit()
        conn.close()
        flash("Room updated successfully.")
        return redirect(url_for("admin_dashboard"))
    room = conn.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
    conn.close()
    return render_template("edit_room.html", room=room)

@app.route("/admin/rooms/delete/<int:room_id>")
@admin_required
def delete_room(room_id):
    conn = get_db()
    occupied = conn.execute("SELECT COUNT(*) FROM students WHERE allocated_room=?", (room_id,)).fetchone()[0]
    if occupied:
        flash("Cannot delete room because students are allocated in it.")
    else:
        conn.execute("DELETE FROM rooms WHERE id=?", (room_id,))
        conn.commit()
        flash("Room deleted successfully.")
    conn.close()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/student/remove-allocation/<int:student_id>")
@admin_required
def remove_student_allocation(student_id):
    conn = get_db()
    conn.execute("""
        UPDATE students
        SET allocated_room=NULL, compatibility_score=0, conflict_risk='Not calculated',
        explanation='Allocation removed by admin.', waitlisted=0
        WHERE id=?
    """, (student_id,))
    conn.commit()
    conn.close()
    flash("Student allocation removed.")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/student/auto-allocate/<int:student_id>")
@admin_required
def admin_auto_allocate(student_id):
    auto_allocate(student_id)
    flash("AI allocation completed for selected student.")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/announcement", methods=["POST"])
@admin_required
def add_announcement():
    conn = get_db()
    conn.execute("INSERT INTO announcements(title,message) VALUES(?,?)",
                 (request.form["title"], request.form["message"]))
    conn.commit()
    conn.close()
    flash("Announcement posted.")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/report")
@admin_required
def export_report():
    conn = get_db()
    rows = conn.execute("""
        SELECT s.name, s.email, s.gender, s.year, COALESCE(r.room_no,'Not allocated') AS room,
        s.compatibility_score, s.conflict_risk, s.waitlisted, s.explanation
        FROM students s
        LEFT JOIN rooms r ON s.allocated_room = r.id
        ORDER BY s.name
    """).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "Email", "Gender", "Year", "Room", "Compatibility", "Risk", "Waitlisted", "Explanation"])
    for r in rows:
        writer.writerow([r["name"], r["email"], r["gender"], r["year"], r["room"], r["compatibility_score"], r["conflict_risk"], r["waitlisted"], r["explanation"]])

    mem = io.BytesIO()
    mem.write(output.getvalue().encode("utf-8"))
    mem.seek(0)
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="hostel_allocation_report.csv")

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
