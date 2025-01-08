from flask import Flask, jsonify, request
from pymongo import MongoClient
from datetime import datetime, timedelta
from collections import defaultdict
import random
from bson import ObjectId
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
client = MongoClient("mongodb+srv://fakkreddine10:Y0MtR2LREsVOcigT@fet.jqpxr.mongodb.net/?retryWrites=true&w=majority&appName=Fet")
db = client["timetable"]
sessions_collection = db["sessions"]

def generate_time_slots(start_time, end_time, subject_name):
    time_slots = []
    current_time = start_time
    duration = timedelta(minutes=90)
    if 'atelier' in subject_name.lower():
        duration = timedelta(hours=3)
    while current_time + duration <= end_time:
        slot_start = current_time
        slot_end = current_time + duration
        time_slots.append(f"{slot_start.strftime('%H:%M')} - {slot_end.strftime('%H:%M')}")
        current_time = slot_end
    return time_slots

def is_time_slot_available(day, time_slot, room_name, teacher_name, time_slot_bookings, atelier_count, subject_name):
    if time_slot in time_slot_bookings.get(day, {}).get(room_name, []):
        return False
    if teacher_name in time_slot_bookings.get(day, {}).get('teachers', {}).get(time_slot, []):
        return False
    if "atelier" in subject_name.lower() and atelier_count[day].get(time_slot, 0) >= 2:
        return False
    return True

def generate_timetable_by_group(session_data):
    teachers = {teacher['_id']: teacher for teacher in session_data['teachers']}
    subjects = {subject['_id']: subject for subject in session_data['subjects']}
    departments = session_data.get('department', [])
    groups = []
    for department in departments:
        groups.extend(department.get('groups', []))
    
    group_timetables = {}
    room_bookings = {}
    time_slot_bookings = {}
    atelier_count = defaultdict(lambda: defaultdict(int))
    
    active_days = session_data.get('activeDays', ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'])
    time_day_start = session_data.get('timeDayStart', '08:00')
    time_day_end = session_data.get('timeDayEnd', '18:00')
    start_time = datetime.strptime(time_day_start, '%H:%M')
    end_time = datetime.strptime(time_day_end, '%H:%M')
    
    for group in groups:
        group_name = group.get('groupName')
        if not group_name:
            raise KeyError("Missing field in group data: 'groupName'")
        
        program = group['program']
        group_subjects = [sub['subject']['subjectName'] for sub in program.get('subjects', [])]
        random.shuffle(group_subjects)
        assigned_subjects = {}
        used_days = set()
        time_slot_index = 0
        
        for subject_name in group_subjects:
            subject = next((s for s in session_data['subjects'] if s['subjectName'] == subject_name), None)
            if subject:
                subject_id = subject['_id']
                if subject_id in assigned_subjects:
                    continue
                assigned_subjects[subject_id] = subject_name
                available_rooms = [
                    room['nameRoom']
                    for room in session_data['rooms']
                    if room['type'] == subject['type']
                ]
                assigned = False
                for day in active_days:
                    subjects_for_day = [entry for entry in group_timetables.get(group_name, []) if entry['day'] == day]
                    if len(subjects_for_day) >= 3:
                        continue
                    
                    for room_name in available_rooms:
                        time_slots = generate_time_slots(start_time, end_time, subject_name=subject_name)
                        time_slot = time_slots[time_slot_index % len(time_slots)]
                        
                        available_teachers = [
                            teacher for teacher in teachers.values()
                            if any(sub['subjectName'] == subject_name for sub in teacher.get('subjectsCanTeach', []))
                        ]
                        assigned_teacher = available_teachers[0]['teacherName'] if available_teachers else 'No teacher available'
                        
                        if not is_time_slot_available(day, time_slot, room_name, assigned_teacher, time_slot_bookings, atelier_count, subject_name):
                            continue

                        time_slot_bookings.setdefault(day, {}).setdefault(room_name, []).append(time_slot)
                        time_slot_bookings.setdefault(day, {}).setdefault('teachers', {}).setdefault(time_slot, []).append(assigned_teacher)

                        if "atelier" in subject_name.lower():
                            atelier_count[day][time_slot] += 1

                        if group_name not in group_timetables:
                            group_timetables[group_name] = []
                        
                        group_timetables[group_name].append({
                            'day': day,
                            'subject': subject_name,
                            'teacher': assigned_teacher,
                            'room': room_name,
                            'time_slot': time_slot
                        })
                        assigned = True
                        time_slot_index += 1
                        break
                    if assigned:
                        break

    return group_timetables

@app.route('/generate-timetable', methods=['POST'])
def generate_timetable():
    data = request.json
    session_id = data.get('sessionId')
    if not session_id:
        return jsonify({"error": "Session ID is required."}), 400
    try:
        session_id = ObjectId(session_id)
    except Exception as e:
        return jsonify({"error": f"Invalid session ID format: {str(e)}"}), 400
    session_data = sessions_collection.find_one({"_id": session_id})
    if not session_data:
        return jsonify({"error": "Session data not found for the specified ID."}), 404
    try:
        group_timetables = generate_timetable_by_group(session_data)
        sessions_collection.update_one(
            {"_id": session_id},
            {"$set": {"timetables": group_timetables}},
            upsert=True
        )
        return jsonify({"message": "Timetable generated successfully.", "timetables": group_timetables}), 200
    except KeyError as e:
        return jsonify({"error": f"Missing required field in session data: {str(e)}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def objectid_to_str(obj):
    if isinstance(obj, ObjectId):
        return str(obj)
    elif isinstance(obj, dict):
        return {key: objectid_to_str(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [objectid_to_str(item) for item in obj]
    else:
        return obj

@app.route('/sessions/<session_id>', methods=['GET'])
def get_session_by_id(session_id):
    try:
        session_data = sessions_collection.find_one({"_id": ObjectId(session_id)})
        if not session_data:
            return jsonify({"error": "Session not found"}), 404

        session_data = objectid_to_str(session_data)
        return jsonify(session_data), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/get-timetable-by-teacher-and-session', methods=['GET'])
def get_timetable_by_teacher_name_and_session():
    teacher_name = request.args.get("teacherName")
    session_id = request.args.get("sessionId")

    if not teacher_name or not session_id:
        return jsonify({"error": "Both teacherName and sessionId are required."}), 400

    try:
        session_id = ObjectId(session_id)
    except Exception as e:
        return jsonify({"error": f"Invalid session ID format: {str(e)}"}), 400

    session = sessions_collection.find_one({"_id": session_id})

    if not session:
        return jsonify({"error": "No session found for the given session ID."}), 404
    group_timetables = session.get('timetables', {})
    if not group_timetables:
        return jsonify({"error": "No timetables available for the session."}), 404
    timetable = []
    for group_name, timetable_entries in group_timetables.items():
        for entry in timetable_entries:
            if entry.get("teacher") == teacher_name:
                timetable.append({
                    'group_name': group_name,
                    'subject': entry.get('subject', "Unnamed Subject"),
                    'day': entry.get('day', "Unassigned Day"),
                    'time_slot': entry.get('time_slot', "Unassigned Time Slot"),
                    'room': entry.get('room', "Unassigned Room"),
                })

    if not timetable:
        return jsonify({"error": "No timetable found for the teacher in the specified session."}), 404
    return jsonify({"timetable": timetable}), 200

@app.route('/get-timetable-by-group-and-session', methods=['GET'])
def get_timetable_by_group_name_and_session():
    group_name = request.args.get("groupName")
    session_id = request.args.get("sessionId")
    if not group_name or not session_id:
        return jsonify({"error": "Both groupName and sessionId are required."}), 400
    try:
        session_id = ObjectId(session_id)
    except Exception as e:
        return jsonify({"error": f"Invalid session ID format: {str(e)}"}), 400
    session = sessions_collection.find_one({"_id": session_id})

    if not session:
        return jsonify({"error": "No session found for the given session ID."}), 404
    group_timetables = session.get('timetables', {})
    if not group_timetables:
        return jsonify({"error": "No timetables available for the session."}), 404

    timetable = []
    if group_name in group_timetables:
        for entry in group_timetables[group_name]:
            timetable.append({
                'group_name': group_name,
                'subject': entry.get('subject', "Unnamed Subject"),
                'day': entry.get('day', "Unassigned Day"),
                'time_slot': entry.get('time_slot', "Unassigned Time Slot"),
                'room': entry.get('room', "Unassigned Room"),
                'teacher': entry.get('teacher', "No teacher assigned")
            })
    else:
        return jsonify({"error": f"No timetable found for the group '{group_name}' in the specified session."}), 404

    return jsonify({"timetable": timetable}), 200

@app.route('/get-group-names', methods=['GET'])
def get_group_names():
    session_id = request.args.get('sessionId')

    if not session_id:
        return jsonify({"error": "Session ID is required."}), 400

    try:
        session_id = ObjectId(session_id)
    except Exception as e:
        return jsonify({"error": f"Invalid session ID format: {str(e)}"}), 400

    session_data = sessions_collection.find_one({"_id": session_id})

    if not session_data:
        return jsonify({"error": "Session data not found for the specified ID."}), 404

    groups = []
    departments = session_data.get('department', [])

    for department in departments:
        for group in department.get('groups', []):
            groups.append(group['groupName'])

    if not groups:
        return jsonify({"error": "No groups found in session data."}), 404

    return jsonify({"group_names": groups}), 200

@app.route('/get-subjects', methods=['GET'])
def get_subjects():
    session_id = request.args.get('sessionId')

    if not session_id:
        return jsonify({"error": "Session ID is required."}), 400

    try:
        session_id = ObjectId(session_id)
    except Exception as e:
        return jsonify({"error": f"Invalid session ID format: {str(e)}"}), 400

    session_data = sessions_collection.find_one({"_id": session_id})

    if not session_data:
        return jsonify({"error": "Session data not found for the specified ID."}), 404

    subjects = session_data.get('subjects', [])

    if not subjects:
        return jsonify({"error": "No subjects found in session data."}), 404

    return jsonify({"subjects": subjects}), 200

@app.route('/get-group-id', methods=['GET'])
def get_group_id():
    group_name = request.args.get('groupName')
    session_id = request.args.get('sessionId')
    department_id = request.args.get('departmentId')

    if not group_name or not session_id or not department_id:
        return jsonify({"error": "Missing required parameters"}), 400

    session_data = sessions_collection.find_one({"_id": ObjectId(session_id)})

    if not session_data:
        return jsonify({"error": "Session not found"}), 404

    department = next((dept for dept in session_data.get('department', []) if str(dept['_id']) == department_id), None)
    
    if not department:
        return jsonify({"error": "Department not found"}), 404

    group = next((group for group in department.get('groups', []) if group['groupName'] == group_name), None)

    if not group:
        return jsonify({"error": "Group not found"}), 404

    return jsonify({"groupId": str(group['_id'])}), 200

@app.route('/db-stats', methods=['GET'])
def get_db_stats():
    try:
        # Fetch the database stats
        db_stats = db.command("dbstats")

        stats = {
            "databaseStats": {
                "databaseName": db_stats.get("db"),
                "collections": db_stats.get("collections"),
                "views": db_stats.get("views"),
                "objects": db_stats.get("objects"),
                "avgObjSize": db_stats.get("avgObjSize"),
                "dataSize": db_stats.get("dataSize"),
                "storageSize": db_stats.get("storageSize"),
                "indexes": db_stats.get("indexes"),
                "indexSize": db_stats.get("indexSize"),
                "totalUsage": db_stats.get("dataSize", 0) + db_stats.get("indexSize", 0),
            }
        }

        return jsonify(stats), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    

@app.route('/db-network-stats', methods=['GET'])
def get_network_stats():
    try:
        # Fetch server status to get network stats
        server_status = db.command('serverStatus')
        network_stats = server_status['network']

        # Extract bytes in (download) and bytes out (upload)
        bytes_in = network_stats['bytesIn']
        bytes_out = network_stats['bytesOut']

        return jsonify({
            'bytesIn': bytes_in,
            'bytesOut': bytes_out
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500



if __name__ == '__main__':
    app.run(debug=True)
