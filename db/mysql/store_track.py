from flask import request, jsonify
import json
import psycopg2  # you already use Postgres via db/connection.py

@server.route('/track', methods=['POST'])
def track():
    try:
        event = json.loads(request.data)
    except Exception:
        return jsonify({'error': 'bad payload'}), 400
    # insert into a tracking table — reuse db/connection.py's connection logic
    ...
    return '', 204