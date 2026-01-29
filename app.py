from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from functools import wraps
import sqlite3
from datetime import datetime, timedelta
import json
import threading
import time
import numpy as np
from collections import deque
import statistics
import logging
from logging.handlers import RotatingFileHandler
import socket
import eventlet
eventlet.monkey_patch()


# Initialize Flask app
app = Flask(__name__)
CORS(app, origins=["http://localhost:5000", "http://127.0.0.1:5000", "http://10.235.96.251:5000"])
socketio = SocketIO(app, cors_allowed_origins="*")

# Enhanced Configuration
CONFIG = {
    'db_path': 'nilm_data.db',
    'api_key': 'nilm-system-api-key-2023',  # MUST MATCH ESP32 AND HTML API KEY
    'power_threshold': 30.0,  # Lowered threshold for better sensitivity
    'window_size': 15,  # Increased window for better analysis
    'std_dev_threshold': 1.5,  # More sensitive threshold
    'min_event_interval': 3,  # Reduced minimum interval
    'max_requests_per_minute': 60,
    'power_history_size': 100,  # Keep more history for analysis
    'steady_state_samples': 5,  # Samples to confirm steady state
    'transient_detection_window': 10  # Window for transient detection
}

# Enhanced data structures for better analysis
power_history = deque(maxlen=CONFIG['power_history_size'])
voltage_history = deque(maxlen=CONFIG['power_history_size'])
current_history = deque(maxlen=CONFIG['power_history_size'])
last_steady_power = 0
last_event_time = 0
appliance_states = {}  # Track current appliance states

# Set up logging
logging.basicConfig(level=logging.INFO)
handler = RotatingFileHandler('nilm_server.log', maxBytes=10000, backupCount=3)
handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
app.logger.addHandler(handler)

# Rate limiting storage
request_timestamps = {}

# API key validation decorator
def require_api_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if not api_key or api_key != CONFIG['api_key']:
            app.logger.warning(f"Invalid API key attempt: {api_key}")
            return jsonify({"status": "error", "message": "Invalid API key"}), 401
        return f(*args, **kwargs)
    return decorated_function

# Rate limiting decorator
def rate_limit(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        client_ip = request.remote_addr
        current_time = time.time()
        
        if client_ip in request_timestamps:
            request_timestamps[client_ip] = [
                timestamp for timestamp in request_timestamps[client_ip] 
                if current_time - timestamp < 60
            ]
        else:
            request_timestamps[client_ip] = []
        
        if len(request_timestamps[client_ip]) >= CONFIG['max_requests_per_minute']:
            app.logger.warning(f"Rate limit exceeded for IP: {client_ip}")
            return jsonify({"status": "error", "message": "Rate limit exceeded"}), 429
        
        request_timestamps[client_ip].append(current_time)
        return f(*args, **kwargs)
    return decorated_function

# Enhanced database setup
def init_db():
    conn = sqlite3.connect(CONFIG['db_path'])
    c = conn.cursor()
    
    # Create table for raw data with enhanced fields
    c.execute('''CREATE TABLE IF NOT EXISTS raw_data
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                  voltage REAL,
                  current REAL,
                  power REAL,
                  energy REAL,
                  frequency REAL,
                  power_factor REAL,
                  data_type TEXT,
                  rssi INTEGER,
                  heap INTEGER,
                  steady_state INTEGER DEFAULT 0,
                  transient_detected INTEGER DEFAULT 0)''')
    
    # Enhanced events table
    c.execute('''CREATE TABLE IF NOT EXISTS events
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                  power_change REAL,
                  power_before REAL,
                  power_after REAL,
                  voltage_change REAL,
                  current_change REAL,
                  event_type TEXT,
                  confidence REAL DEFAULT 0.0,
                  identified INTEGER DEFAULT 0,
                  duration REAL DEFAULT 0.0)''')
    
    # Check for missing columns and add them
    try:
        c.execute("SELECT rssi, heap FROM raw_data LIMIT 1")
    except sqlite3.OperationalError:
        app.logger.info("Adding missing columns to raw_data table")
        c.execute("ALTER TABLE raw_data ADD COLUMN rssi INTEGER")
        c.execute("ALTER TABLE raw_data ADD COLUMN heap INTEGER")
        c.execute("ALTER TABLE raw_data ADD COLUMN steady_state INTEGER DEFAULT 0")
        c.execute("ALTER TABLE raw_data ADD COLUMN transient_detected INTEGER DEFAULT 0")
    
    # Enhanced appliance predictions table
    c.execute('''CREATE TABLE IF NOT EXISTS appliance_predictions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                  appliance_name TEXT,
                  power_consumption REAL,
                  state TEXT,
                  confidence REAL DEFAULT 0.0,
                  event_id INTEGER,
                  FOREIGN KEY (event_id) REFERENCES events (id))''')
    
    # User feedback table
    c.execute('''CREATE TABLE IF NOT EXISTS user_feedback
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                  event_timestamp DATETIME NOT NULL,
                  appliance_name TEXT NOT NULL,
                  power_change REAL,
                  confirmed INTEGER DEFAULT 1,
                  event_id INTEGER,
                  FOREIGN KEY (event_id) REFERENCES events (id))''')
    
    # Enhanced known appliances with more parameters
    c.execute('''CREATE TABLE IF NOT EXISTS known_appliances
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL UNIQUE,
                  typical_power REAL,
                  typical_duration INTEGER,
                  power_variance REAL,
                  min_power REAL,
                  max_power REAL,
                  startup_pattern TEXT,
                  shutdown_pattern TEXT,
                  power_factor_range TEXT,
                  frequency_signature REAL,
                  learning_count INTEGER DEFAULT 0,
                  last_updated DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    
    # Appliance states table
    c.execute('''CREATE TABLE IF NOT EXISTS appliance_states
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  appliance_name TEXT NOT NULL UNIQUE,
                  state TEXT NOT NULL,
                  power_consumption REAL,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                  confidence REAL DEFAULT 0.0)''')
    
    # Enhanced common appliances with better parameters
    common_appliances = [
    ("Washing Machine", 400, 60, 150, 200, 800, "motor_start", "gradual_off", "0.70-0.85", 50.0),
    ("Microwave", 1100, 5, 200, 800, 1500, "instant_on", "instant_off", "0.80-0.90", 50.0),
    ("Coffee Maker", 900, 8, 150, 600, 1200, "heating_cycle", "instant_off", "0.95-0.99", 50.0),
    ("Toaster", 1300, 4, 200, 1000, 1500, "instant_on", "instant_off", "0.95-0.99", 50.0),
    ("Dishwasher", 1400, 120, 300, 800, 2000, "motor_pump", "gradual_off", "0.75-0.90", 50.0),
    ("Air Conditioner", 1800, 60, 400, 1200, 2500, "compressor_start", "gradual_off", "0.80-0.95", 50.0),
    ("Hair Dryer", 1200, 10, 200, 800, 1600, "instant_on", "instant_off", "0.95-0.99", 50.0),
    ("Electric Kettle", 1500, 5, 200, 1200, 1800, "instant_on", "instant_off", "0.95-0.99", 50.0),
    ("Vacuum Cleaner", 1000, 15, 200, 600, 1400, "motor_start", "instant_off", "0.75-0.90", 50.0)
    ]
    
    # Add new columns if they don't exist
    try:
        c.execute("ALTER TABLE known_appliances ADD COLUMN startup_pattern TEXT")
    except sqlite3.OperationalError:
        pass # The column already exists, so we can ignore the error
        
    try:
        c.execute("ALTER TABLE known_appliances ADD COLUMN shutdown_pattern TEXT")
    except sqlite3.OperationalError:
        pass # The column already exists, so we can ignore the error

    # Insert initial data
    for appliance in common_appliances:
        c.execute('''INSERT OR IGNORE INTO known_appliances 
                    (name, typical_power, typical_duration, power_variance, min_power, max_power,
                     startup_pattern, shutdown_pattern, power_factor_range, frequency_signature)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', appliance)
    
    # Create enhanced indexes
    c.execute('''CREATE INDEX IF NOT EXISTS idx_raw_data_timestamp ON raw_data(timestamp)''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_events_identified ON events(identified)''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_events_confidence ON events(confidence)''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_appliance_predictions_timestamp ON appliance_predictions(timestamp)''')
    
    conn.commit()
    conn.close()
    app.logger.info("Enhanced database initialized successfully")

# Initialize database
init_db()

# Helper function for database connections
def get_db_connection():
    conn = sqlite3.connect(CONFIG['db_path'])
    conn.row_factory = sqlite3.Row
    return conn

# Enhanced steady state detection
def detect_steady_state(power_values, threshold=5.0):
    """Detect if the power is in steady state"""
    if len(power_values) < CONFIG['steady_state_samples']:
        return False
    
    recent_values = list(power_values)[-CONFIG['steady_state_samples']:]
    std_dev = statistics.stdev(recent_values) if len(recent_values) > 1 else 0
    return std_dev < threshold

# Enhanced transient detection
def detect_transient(power_values, window_size=5):
    """Detect power transients that might indicate appliance switching"""
    if len(power_values) < window_size:
        return False, 0
    
    recent = list(power_values)[-window_size:]
    if len(recent) < 2:
        return False, 0
    
    # Calculate rate of change
    changes = [recent[i] - recent[i-1] for i in range(1, len(recent))]
    max_change = max(abs(change) for change in changes)
    
    return max_change > CONFIG['power_threshold'], max_change

# Enhanced appliance identification with multiple parameters
def identify_appliance(power_change, current_power, voltage=None, current=None, power_factor=None):
    """Enhanced appliance identification using multiple electrical parameters"""
    global appliance_states
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Get known appliances
    c.execute('''SELECT * FROM known_appliances ORDER BY learning_count DESC''')
    appliances = c.fetchall()
    
    best_matches = []
    
    for appliance in appliances:
        confidence = 0.0
        reasons = []
        
        # Power-based matching (primary factor)
        typical_power = appliance['typical_power']
        power_variance = appliance['power_variance']
        min_power = appliance['min_power']
        max_power = appliance['max_power']
        
        power_magnitude = abs(power_change)
        
        # Check if power is within expected range
        if min_power <= power_magnitude <= max_power:
            # Calculate power confidence
            power_diff = abs(power_magnitude - typical_power)
            power_confidence = max(0, 1 - (power_diff / power_variance)) if power_variance > 0 else 0
            confidence += power_confidence * 0.6  # 60% weight
            reasons.append(f"Power match: {power_confidence:.2f}")
        
        # Power factor matching (if available)
        if power_factor and appliance['power_factor_range']:
            try:
                pf_range = appliance['power_factor_range'].split('-')
                pf_min, pf_max = float(pf_range[0]), float(pf_range[1])
                if pf_min <= power_factor <= pf_max:
                    confidence += 0.2  # 20% weight
                    reasons.append("Power factor match")
            except:
                pass
        
        # State logic - check if appliance can be in this state
        current_state = appliance_states.get(appliance['name'], {'state': 'off', 'power': 0})
        
        if power_change > 0:  # Turning on
            if current_state['state'] == 'off':
                confidence += 0.1  # 10% weight for valid state transition
                reasons.append("Valid ON transition")
            else:
                confidence *= 0.5  # Penalize if already on
        else:  # Turning off
            if current_state['state'] == 'on':
                confidence += 0.1  # 10% weight for valid state transition
                reasons.append("Valid OFF transition")
            else:
                confidence *= 0.5  # Penalize if already off
        
        # Learning factor - prefer appliances we've seen more often
        learning_bonus = min(0.1, appliance['learning_count'] * 0.01)
        confidence += learning_bonus
        
        if confidence > 0.3:  # Minimum confidence threshold
            best_matches.append({
                'name': appliance['name'],
                'confidence': confidence,
                'reasons': reasons,
                'power_consumption': power_magnitude
            })
    
    # Sort by confidence
    best_matches.sort(key=lambda x: x['confidence'], reverse=True)
    
    conn.close()
    
    # Return best match if confidence is high enough
    if best_matches and best_matches[0]['confidence'] > 0.4:
        return best_matches[0]
    
    return None

# Enhanced event detection
def detect_power_event(new_data):
    """Enhanced power event detection with multiple parameters"""
    global power_history, last_steady_power, last_event_time
    
    current_power = new_data.get('power', 0)
    current_time = time.time()
    
    # Add to history
    power_history.append(current_power)
    
    # Need sufficient history
    if len(power_history) < CONFIG['window_size']:
        return None
    
    # Check for steady state before the change
    was_steady = detect_steady_state(list(power_history)[:-CONFIG['transient_detection_window']])
    
    # Detect transient
    has_transient, max_change = detect_transient(power_history)
    
    # Calculate power change from steady state
    if was_steady and len(power_history) >= CONFIG['window_size']:
        steady_power = statistics.mean(list(power_history)[:-CONFIG['transient_detection_window']])
        power_change = current_power - steady_power
    else:
        power_change = current_power - (power_history[-2] if len(power_history) > 1 else current_power)
    
    # Check if this is a significant event
    if (abs(power_change) > CONFIG['power_threshold'] and 
        has_transient and 
        (current_time - last_event_time) > CONFIG['min_event_interval']):
        
        # Create event data
        event_data = {
            'power_change': power_change,
            'power_before': steady_power if was_steady else power_history[-2],
            'power_after': current_power,
            'max_transient': max_change,
            'was_steady': was_steady,
            'confidence': calculate_event_confidence(power_change, has_transient, was_steady),
            'voltage': new_data.get('voltage'),
            'current': new_data.get('current'),
            'power_factor': new_data.get('power_factor')
        }
        
        last_event_time = current_time
        last_steady_power = current_power
        
        return event_data
    
    return None

def calculate_event_confidence(power_change, has_transient, was_steady):
    """Calculate confidence score for detected events"""
    confidence = 0.5  # Base confidence
    
    # Higher confidence for larger power changes
    if abs(power_change) > 100:
        confidence += 0.2
    elif abs(power_change) > 50:
        confidence += 0.1
    
    # Higher confidence if there was a clear transient
    if has_transient:
        confidence += 0.2
    
    # Higher confidence if system was in steady state before
    if was_steady:
        confidence += 0.1
    
    return min(1.0, confidence)

@app.route('/')
def serve_dashboard():
    return render_template('nilm_dashboard.html')

@app.route('/api/data', methods=['POST'])
@require_api_key
@rate_limit
def receive_data():
    try:
        data = request.get_json()
        
        # Validate required fields
        if 'power' not in data:
            app.logger.error("Missing power data in request")
            return jsonify({"status": "error", "message": "Missing power data"}), 400
        
        current_power = float(data.get('power', 0))
        
        # Detect steady state and transients
        is_steady = detect_steady_state(power_history)
        has_transient, _ = detect_transient(power_history)
        
        # Save to database with enhanced fields
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute('''INSERT INTO raw_data 
                    (voltage, current, power, energy, frequency, power_factor, data_type, 
                     rssi, heap, steady_state, transient_detected)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                 (data.get('voltage'), data.get('current'), data.get('power'),
                  data.get('energy'), data.get('frequency'), data.get('power_factor'),
                  'reading', data.get('rssi'), data.get('heap'), 
                  1 if is_steady else 0, 1 if has_transient else 0))
        
        # Enhanced event detection
        event_data = detect_power_event(data)
        
        if event_data:
            event_type = 'on' if event_data['power_change'] > 0 else 'off'
            
            # Insert event with enhanced data
            c.execute('''INSERT INTO events 
                        (power_change, power_before, power_after, voltage_change, 
                         current_change, event_type, confidence)
                        VALUES (?, ?, ?, ?, ?, ?, ?)''',
                     (event_data['power_change'], event_data['power_before'], 
                      event_data['power_after'], 0, 0, event_type, event_data['confidence']))
            
            event_id = c.lastrowid
            
            app.logger.info(f"Power event detected: {event_data['power_change']:.1f}W change, "
                          f"confidence: {event_data['confidence']:.2f}")
            
            # Try to identify the appliance with enhanced parameters
            appliance_match = identify_appliance(
                event_data['power_change'], 
                current_power,
                data.get('voltage'),
                data.get('current'),
                data.get('power_factor')
            )
            
            if appliance_match:
                appliance_name = appliance_match['name']
                confidence = appliance_match['confidence']
                power_consumption = appliance_match['power_consumption']
                
                state = 'on' if event_data['power_change'] > 0 else 'off'
                
                # Insert prediction
                c.execute('''INSERT INTO appliance_predictions 
                            (appliance_name, power_consumption, state, confidence, event_id)
                            VALUES (?, ?, ?, ?, ?)''', 
                         (appliance_name, power_consumption, state, confidence, event_id))
                
                # Update appliance state
                c.execute('''INSERT OR REPLACE INTO appliance_states 
                            (appliance_name, state, power_consumption, confidence)
                            VALUES (?, ?, ?, ?)''',
                         (appliance_name, state, power_consumption, confidence))
                
                # Update appliance learning count
                c.execute('''UPDATE known_appliances 
                            SET learning_count = learning_count + 1, last_updated = CURRENT_TIMESTAMP
                            WHERE name = ?''', (appliance_name,))
                
                # Mark event as identified
                c.execute('''UPDATE events SET identified = 1 WHERE id = ?''', (event_id,))
                
                # Update global appliance states
                appliance_states[appliance_name] = {
                    'state': state,
                    'power': power_consumption,
                    'confidence': confidence
                }
                
                app.logger.info(f"Appliance identified: {appliance_name} ({state}) - "
                              f"Power: {power_consumption:.1f}W, Confidence: {confidence:.2f}")
                
                # Notify clients about the new appliance detection
                socketio.emit('appliance_update', {
                    'appliance_name': appliance_name,
                    'power_consumption': power_consumption,
                    'state': state,
                    'confidence': confidence,
                    'timestamp': datetime.now().isoformat()
                })
                
                # Also send the updated list of all appliances
                c.execute('''SELECT appliance_name, state, power_consumption, confidence, timestamp 
                             FROM appliance_states ORDER BY timestamp DESC''')
                appliances = [dict(row) for row in c.fetchall()]
                socketio.emit('appliance_list', appliances)
            else:
                app.logger.info(f"Unidentified power event: {event_data['power_change']:.1f}W")
                # Emit unidentified event for user labeling
                socketio.emit('unidentified_event', {
                    'id': event_id,
                    'power_change': event_data['power_change'],
                    'power_before': event_data['power_before'],
                    'power_after': event_data['power_after'],
                    'timestamp': datetime.now().isoformat(),
                    'confidence': event_data['confidence']
                })
        
        conn.commit()
        conn.close()
        
        # Also save to file
        save_to_file(data)
        
        # Broadcast to all connected clients via SocketIO
        socketio.emit('data_update', data)
        
        return jsonify({"status": "success", "message": "Data received"}), 200
        
    except Exception as e:
        app.logger.error(f"Error receiving data: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

def save_to_file(data):
    """Save data to a JSON file with timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data_with_timestamp = {
        "timestamp": timestamp,
        **data
    }
    
    try:
        with open('nilm_data.json', 'a') as f:
            f.write(json.dumps(data_with_timestamp) + '\n')
    except Exception as e:
        app.logger.error(f"Error saving to file: {e}")

@app.route('/api/historical', methods=['GET'])
@require_api_key
@rate_limit
def get_historical_data():
    """Get historical data from database"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Get the last 100 records with enhanced fields
        c.execute('''SELECT * FROM raw_data ORDER BY timestamp DESC LIMIT 100''')
        rows = c.fetchall()
        
        result = []
        for row in rows:
            result.append(dict(row))
        
        conn.close()
        return jsonify(result), 200
        
    except Exception as e:
        app.logger.error(f"Error getting historical data: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/appliances', methods=['GET'])
@require_api_key
@rate_limit
def get_appliance_data():
    """Get appliance prediction data with confidence scores"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Get the current state of all appliances with confidence
        c.execute('''SELECT appliance_name, state, power_consumption, confidence, timestamp 
                     FROM appliance_states ORDER BY timestamp DESC''')
        rows = c.fetchall()
        
        appliances = []
        for row in rows:
            appliances.append({
                "appliance_name": row['appliance_name'],
                "power_consumption": row['power_consumption'],
                "state": row['state'],
                "confidence": row['confidence'],
                "timestamp": row['timestamp']
            })
        
        conn.close()
        return jsonify(appliances), 200
        
    except Exception as e:
        app.logger.error(f"Error getting appliance data: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/events', methods=['GET'])
@require_api_key
@rate_limit
def get_events():
    """Get recent events with confidence scores"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Get events from the last 24 hours
        c.execute('''SELECT e.*, ap.appliance_name, ap.confidence as pred_confidence
                     FROM events e
                     LEFT JOIN appliance_predictions ap ON e.id = ap.event_id
                     WHERE e.timestamp > datetime('now', '-1 day')
                     ORDER BY e.timestamp DESC
                     LIMIT 50''')
        rows = c.fetchall()
        
        events = []
        for row in rows:
            events.append(dict(row))
        
        conn.close()
        return jsonify(events), 200
        
    except Exception as e:
        app.logger.error(f"Error getting events: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/unlabeled_events', methods=['GET'])
@require_api_key
@rate_limit
def get_unlabeled_events():
    """Get recent power events that haven't been labeled yet"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Get unlabeled events from the last 24 hours with confidence scores
        c.execute('''SELECT id, timestamp, power_change, power_before, power_after, confidence 
                     FROM events 
                     WHERE identified = 0 AND timestamp > datetime('now', '-1 day')
                     ORDER BY confidence DESC, timestamp DESC
                     LIMIT 20''')
        rows = c.fetchall()
        
        events = []
        for row in rows:
            events.append({
                "id": row['id'],
                "timestamp": row['timestamp'],
                "power_change": row['power_change'],
                "power_before": row['power_before'],
                "power_after": row['power_after'],
                "confidence": row['confidence']
            })
        
        conn.close()
        return jsonify(events), 200
        
    except Exception as e:
        app.logger.error(f"Error getting unlabeled events: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/label_appliance', methods=['POST'])
@require_api_key
@rate_limit
def label_appliance():
    """Enhanced appliance labeling with learning"""
    try:
        data = request.get_json()
        event_id = data.get('event_id')
        event_timestamp = data.get('event_timestamp')
        appliance_name = data.get('appliance_name')
        power_change = data.get('power_change')
        
        if not appliance_name or (not event_id and not event_timestamp):
            app.logger.error("Missing required fields in label_appliance request")
            return jsonify({"status": "error", "message": "Missing required fields"}), 400
        
        conn = get_db_connection()
        c = conn.cursor()
        
        # Save user feedback
        c.execute('''INSERT INTO user_feedback 
                    (event_timestamp, appliance_name, power_change, event_id)
                    VALUES (?, ?, ?, ?)''',
                 (event_timestamp, appliance_name, power_change, event_id))
        
        # Update or create appliance in known_appliances
        if power_change:
            # Try to update existing appliance
            c.execute('''SELECT * FROM known_appliances WHERE name = ?''', (appliance_name,))
            existing = c.fetchone()
            
            power_magnitude = abs(power_change)
            
            if existing:
                # Update with learning
                new_typical = (existing['typical_power'] + power_magnitude) / 2
                new_variance = max(existing['power_variance'], power_magnitude * 0.2)
                new_min = min(existing['min_power'], power_magnitude * 0.7)
                new_max = max(existing['max_power'], power_magnitude * 1.3)
                
                c.execute('''UPDATE known_appliances 
                            SET typical_power = ?, power_variance = ?, min_power = ?, max_power = ?,
                                learning_count = learning_count + 1, last_updated = CURRENT_TIMESTAMP
                            WHERE name = ?''',
                         (new_typical, new_variance, new_min, new_max, appliance_name))
                
                app.logger.info(f"Updated appliance '{appliance_name}' with new power data: {power_magnitude}W")
            else:
                # Create new appliance
                c.execute('''INSERT INTO known_appliances 
                            (name, typical_power, typical_duration, power_variance, min_power, max_power,
                             startup_pattern, shutdown_pattern, power_factor_range, frequency_signature, learning_count)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                         (appliance_name, power_magnitude, 60, power_magnitude * 0.2, 
                          power_magnitude * 0.7, power_magnitude * 1.3,
                          "unknown", "unknown", "0.80-0.95", 50.0, 1))
                
                app.logger.info(f"Created new appliance '{appliance_name}' with power: {power_magnitude}W")
        
        # Mark the event as identified if event_id provided
        if event_id:
            c.execute('''UPDATE events SET identified = 1 WHERE id = ?''', (event_id,))
            
            # Create appliance prediction for this event
            state = 'on' if power_change and power_change > 0 else 'off'
            c.execute('''INSERT INTO appliance_predictions 
                        (appliance_name, power_consumption, state, confidence, event_id)
                        VALUES (?, ?, ?, ?, ?)''',
                     (appliance_name, abs(power_change) if power_change else 0, state, 0.9, event_id))
            
            # Update appliance state
            c.execute('''INSERT OR REPLACE INTO appliance_states 
                        (appliance_name, state, power_consumption, confidence)
                        VALUES (?, ?, ?, ?)''',
                     (appliance_name, state, abs(power_change) if power_change else 0, 0.9))
        
        conn.commit()
        conn.close()
        
        # Notify clients about the labeling
        socketio.emit('appliance_labeled', {
            'appliance_name': appliance_name,
            'event_id': event_id,
            'power_change': power_change,
            'timestamp': datetime.now().isoformat()
        })
        
        app.logger.info(f"User labeled event {event_id} as '{appliance_name}' with {power_change}W change")
        
        return jsonify({"status": "success", "message": "Appliance labeled successfully"}), 200
        
    except Exception as e:
        app.logger.error(f"Error in label_appliance: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/statistics', methods=['GET'])
@require_api_key
@rate_limit
def get_statistics():
    """Get system statistics and performance metrics"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Basic statistics
        c.execute('''SELECT 
                        COUNT(*) as total_readings,
                        COUNT(CASE WHEN steady_state = 1 THEN 1 END) as steady_readings,
                        COUNT(CASE WHEN transient_detected = 1 THEN 1 END) as transient_readings,
                        AVG(power) as avg_power,
                        MIN(power) as min_power,
                        MAX(power) as max_power
                     FROM raw_data 
                     WHERE timestamp > datetime('now', '-24 hours')''')
        stats = dict(c.fetchone())
        
        # Event statistics
        c.execute('''SELECT 
                        COUNT(*) as total_events,
                        COUNT(CASE WHEN identified = 1 THEN 1 END) as identified_events,
                        AVG(confidence) as avg_confidence
                     FROM events 
                     WHERE timestamp > datetime('now', '-24 hours')''')
        event_stats = dict(c.fetchone())
        
        # Appliance statistics
        c.execute('''SELECT appliance_name, COUNT(*) as detection_count
                     FROM appliance_predictions 
                     WHERE timestamp > datetime('now', '-24 hours')
                     GROUP BY appliance_name
                     ORDER BY detection_count DESC
                     LIMIT 10''')
        appliance_stats = [dict(row) for row in c.fetchall()]
        
        # Top unidentified events
        c.execute('''SELECT power_change, confidence, timestamp
                     FROM events 
                     WHERE identified = 0 AND timestamp > datetime('now', '-24 hours')
                     ORDER BY abs(power_change) DESC
                     LIMIT 5''')
        unidentified_events = [dict(row) for row in c.fetchall()]
        
        conn.close()
        
        # Calculate identification rate
        identification_rate = 0
        if event_stats['total_events'] > 0:
            identification_rate = (event_stats['identified_events'] / event_stats['total_events']) * 100
        
        result = {
            'system_stats': stats,
            'event_stats': event_stats,
            'identification_rate': round(identification_rate, 2),
            'appliance_detections': appliance_stats,
            'unidentified_events': unidentified_events,
            'timestamp': datetime.now().isoformat()
        }
        
        return jsonify(result), 200
        
    except Exception as e:
        app.logger.error(f"Error getting statistics: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/known_appliances', methods=['GET'])
@require_api_key
@rate_limit
def get_known_appliances():
    """Get list of known appliances for user selection"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute('''SELECT name, typical_power, learning_count, last_updated
                     FROM known_appliances 
                     ORDER BY learning_count DESC, name ASC''')
        rows = c.fetchall()
        
        appliances = []
        for row in rows:
            appliances.append({
                "name": row['name'],
                "typical_power": row['typical_power'],
                "learning_count": row['learning_count'],
                "last_updated": row['last_updated']
            })
        
        conn.close()
        return jsonify(appliances), 200
        
    except Exception as e:
        app.logger.error(f"Error getting known appliances: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/add_appliance', methods=['POST'])
@require_api_key
@rate_limit
def add_appliance():
    """Add a new appliance to the database"""
    try:
        data = request.get_json()
        name = data.get('name')
        typical_power = data.get('typical_power', 100)
        typical_duration = data.get('typical_duration', 60)
        
        if not name:
            return jsonify({"status": "error", "message": "Appliance name is required"}), 400
        
        conn = get_db_connection()
        c = conn.cursor()
        
        # Check if appliance already exists
        c.execute('''SELECT name FROM known_appliances WHERE name = ?''', (name,))
        if c.fetchone():
            conn.close()
            return jsonify({"status": "error", "message": "Appliance already exists"}), 400
        
        # Add new appliance
        power_variance = typical_power * 0.2
        min_power = typical_power * 0.7
        max_power = typical_power * 1.3
        
        c.execute('''INSERT INTO known_appliances 
                    (name, typical_power, typical_duration, power_variance, min_power, max_power,
                     startup_pattern, shutdown_pattern, power_factor_range, frequency_signature)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                 (name, typical_power, typical_duration, power_variance, min_power, max_power,
                  "unknown", "unknown", "0.80-0.95", 50.0))
        
        conn.commit()
        conn.close()
        
        app.logger.info(f"Added new appliance: {name} ({typical_power}W)")
        
        return jsonify({"status": "success", "message": "Appliance added successfully"}), 200
        
    except Exception as e:
        app.logger.error(f"Error adding appliance: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/delete_appliance', methods=['DELETE'])
@require_api_key
@rate_limit
def delete_appliance():
    """Delete an appliance and all its data"""
    try:
        data = request.get_json()
        appliance_name = data.get('appliance_name')
        
        if not appliance_name:
            return jsonify({"status": "error", "message": "Appliance name is required"}), 400
        
        conn = get_db_connection()
        c = conn.cursor()
        
        # Delete from all related tables
        c.execute('''DELETE FROM appliance_predictions WHERE appliance_name = ?''', (appliance_name,))
        c.execute('''DELETE FROM appliance_states WHERE appliance_name = ?''', (appliance_name,))
        c.execute('''DELETE FROM user_feedback WHERE appliance_name = ?''', (appliance_name,))
        c.execute('''DELETE FROM known_appliances WHERE name = ?''', (appliance_name,))
        
        conn.commit()
        conn.close()
        
        app.logger.info(f"Deleted appliance: {appliance_name}")
        
        return jsonify({"status": "success", "message": "Appliance deleted successfully"}), 200
        
    except Exception as e:
        app.logger.error(f"Error deleting appliance: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/reset_system', methods=['POST'])
@require_api_key
@rate_limit
def reset_system():
    """Reset the system - clear all data and reinitialize"""
    try:
        data = request.get_json()
        confirm = data.get('confirm', False)
        
        if not confirm:
            return jsonify({"status": "error", "message": "Confirmation required"}), 400
        
        conn = get_db_connection()
        c = conn.cursor()
        
        # Clear all data tables but keep structure
        c.execute('''DELETE FROM raw_data''')
        c.execute('''DELETE FROM events''')
        c.execute('''DELETE FROM appliance_predictions''')
        c.execute('''DELETE FROM appliance_states''')
        c.execute('''DELETE FROM user_feedback''')
        
        # Reset learning counts
        c.execute('''UPDATE known_appliances SET learning_count = 0''')
        
        conn.commit()
        conn.close()
        
        # Clear in-memory data structures
        global power_history, voltage_history, current_history, appliance_states
        power_history.clear()
        voltage_history.clear()
        current_history.clear()
        appliance_states.clear()
        
        app.logger.warning("System reset performed - all data cleared")
        
        return jsonify({"status": "success", "message": "System reset successfully"}), 200
        
    except Exception as e:
        app.logger.error(f"Error resetting system: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        # Check database connectivity
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''SELECT COUNT(*) FROM raw_data WHERE timestamp > datetime('now', '-1 hour')''')
        recent_readings = c.fetchone()[0]
        conn.close()
        
        status = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "recent_readings": recent_readings,
            "power_history_size": len(power_history),
            "active_appliances": len([a for a in appliance_states.values() if a['state'] == 'on'])
        }
        
        return jsonify(status), 200
        
    except Exception as e:
        app.logger.error(f"Health check failed: {str(e)}")
        return jsonify({"status": "unhealthy", "error": str(e)}), 500

# SocketIO event handlers
@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    app.logger.info(f"Client connected: {request.sid}")
    emit('connected', {'status': 'Connected to NILM server'})

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    app.logger.info(f"Client disconnected: {request.sid}")

@socketio.on('request_initial_data')
def handle_initial_data_request():
    """Send initial data to newly connected client"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Get current appliance states
        c.execute('''SELECT appliance_name, state, power_consumption, confidence, timestamp 
                     FROM appliance_states ORDER BY timestamp DESC''')
        appliances = [dict(row) for row in c.fetchall()]
        
        # Get unlabeled events
        c.execute('''SELECT id, timestamp, power_change, power_before, power_after, confidence 
                     FROM events 
                     WHERE identified = 0 AND timestamp > datetime('now', '-1 day')
                     ORDER BY confidence DESC, timestamp DESC
                     LIMIT 20''')
        unlabeled_events = [dict(row) for row in c.fetchall()]
        
        # Get system statistics
        c.execute('''SELECT 
                        COUNT(*) as total_events,
                        COUNT(CASE WHEN identified = 1 THEN 1 END) as identified_events,
                        AVG(confidence) as avg_confidence
                     FROM events 
                     WHERE timestamp > datetime('now', '-24 hours')''')
        event_stats = dict(c.fetchone())
        
        # Calculate identification rate
        identification_rate = 0
        if event_stats['total_events'] > 0:
            identification_rate = (event_stats['identified_events'] / event_stats['total_events']) * 100
        
        system_stats = {
            'total_events': event_stats['total_events'],
            'identified_events': event_stats['identified_events'],
            'identification_rate': round(identification_rate, 2),
            'avg_confidence': event_stats['avg_confidence'] or 0
        }
        
        conn.close()
        
        # Send all data to client
        emit('initial_data', {
            'appliances': appliances,
            'unlabeled_events': unlabeled_events,
            'system_stats': system_stats,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error handling initial data request: {str(e)}")
        emit('error', {'message': str(e)})

@socketio.on('request_system_status')
def handle_system_status_request():
    """Send system statistics to client"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Event statistics
        c.execute('''SELECT 
                        COUNT(*) as total_events,
                        COUNT(CASE WHEN identified = 1 THEN 1 END) as identified_events,
                        AVG(confidence) as avg_confidence
                     FROM events 
                     WHERE timestamp > datetime('now', '-24 hours')''')
        event_stats = dict(c.fetchone())
        
        # Calculate identification rate
        identification_rate = 0
        if event_stats['total_events'] > 0:
            identification_rate = (event_stats['identified_events'] / event_stats['total_events']) * 100
        
        system_stats = {
            'total_events': event_stats['total_events'],
            'identified_events': event_stats['identified_events'],
            'identification_rate': round(identification_rate, 2),
            'avg_confidence': event_stats['avg_confidence'] or 0,
            'timestamp': datetime.now().isoformat()
        }
        
        conn.close()
        
        emit('system_stats', system_stats)
        
    except Exception as e:
        app.logger.error(f"Error handling system status request: {str(e)}")
        emit('error', {'message': str(e)})

@socketio.on('request_current_data')
def handle_current_data_request():
    """Send current system status to client"""
    try:
        # Get current power reading
        current_power = list(power_history)[-1] if power_history else 0
        
        # Get active appliances
        active_appliances = [
            {
                'name': name,
                'power': state['power'],
                'confidence': state['confidence']
            }
            for name, state in appliance_states.items()
            if state['state'] == 'on'
        ]
        
        emit('current_status', {
            'current_power': current_power,
            'active_appliances': active_appliances,
            'system_status': 'running',
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error sending current data: {str(e)}")
        emit('error', {'message': str(e)})

# Background task for periodic cleanup
def cleanup_old_data():
    """Clean up old data periodically"""
    while True:
        try:
            time.sleep(3600)  # Run every hour
            
            conn = get_db_connection()
            c = conn.cursor()
            
            # Delete raw data older than 7 days
            c.execute('''DELETE FROM raw_data WHERE timestamp < datetime('now', '-7 days')''')
            
            # Delete events older than 30 days
            c.execute('''DELETE FROM events WHERE timestamp < datetime('now', '-30 days')''')
            
            # Delete old predictions
            c.execute('''DELETE FROM appliance_predictions WHERE timestamp < datetime('now', '-30 days')''')
            
            conn.commit()
            conn.close()
            
            app.logger.info("Periodic cleanup completed")
            
        except Exception as e:
            app.logger.error(f"Error in cleanup task: {str(e)}")

# Start background cleanup thread
cleanup_thread = threading.Thread(target=cleanup_old_data, daemon=True)
cleanup_thread.start()

def get_local_ip():
    """Get the local IP address of the machine"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't matter if there is connectivity, this is just to get local IP
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

def process_data_background():
    """Background task to process data and improve appliance detection"""
    while True:
        try:
            # Process data every 30 seconds
            time.sleep(5)
            
            # Your data processing logic goes here
            # ...
            
            app.logger.info("Processing data for appliance model improvement...")
            
        except Exception as e:
            app.logger.error(f"Error in background processing: {e}")
            time.sleep(60) # Wait longer before retrying on error

if __name__ == '__main__':
    # Start background processing thread
    processing_thread = threading.Thread(target=process_data_background)
    processing_thread.daemon = True
    processing_thread.start()
    
    # Get and print the local IP address
    local_ip = get_local_ip()
    app.logger.info(f"NILM server running on: http://{local_ip}:5000")
    
    # Start Flask app with SocketIO
    app.logger.info("Starting NILM server on http://localhost:5000")
    app.logger.info(f"API Key: {CONFIG['api_key']}")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)