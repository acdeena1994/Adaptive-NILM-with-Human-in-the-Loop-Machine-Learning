# IoT-Based Non-Intrusive Load Monitoring (NILM) System ⚡

An end-to-end **IoT-based Non-Intrusive Load Monitoring (NILM)** system for real-time electrical monitoring and appliance identification using a single-point sensing approach.  
The system combines **ESP32-based edge sensing**, **Flask backend processing**, **event detection**, and **machine-learning–assisted appliance identification**, enhanced with a **human-in-the-loop feedback mechanism**.

---

## 📌 Project Overview

Non-Intrusive Load Monitoring (NILM) aims to identify individual appliance usage patterns from aggregate electrical data without installing sensors on each appliance.  
This project implements a **modular, scalable NILM architecture** that supports real-time monitoring, adaptive learning, and user interaction through a web dashboard.

**Key Objectives:**
- Measure real-time electrical parameters from a single sensing point
- Detect appliance ON/OFF events accurately
- Identify appliances using signature-based matching
- Improve accuracy through user feedback and adaptive learning

---

## 🏗️ System Architecture

The system follows a **client–server architecture** and is organized into four major stages:

1. **Sensing and Local Processing**  
   Electrical parameters are measured and preprocessed by the ESP32.

2. **Backend Ingestion and Event Detection**  
   Sensor data is transmitted to a Flask server where significant power changes are detected.

3. **Advanced Analytics and Identification**  
   Detected events are analyzed using a signature-matching algorithm for appliance identification.

4. **Storage, Visualization, and Feedback**  
   Results are stored in a database and visualized on a web dashboard, enabling user feedback for continuous improvement.

---

## 🔧 Hardware Components

The physical prototype consists of the following components:

- **ESP32 Microcontroller**  
  Acts as the central processing unit with integrated Wi-Fi connectivity.

- **PZEM-004T Energy Meter Module**  
  Measures:
  - Voltage (V)
  - Current (A)
  - Power (W)
  - Energy (kWh)
  - Frequency (Hz)
  - Power Factor (PF)  
  Uses a **non-intrusive CT clamp** for safe installation.

- **SSD1306 0.96-inch OLED Display**  
  Displays real-time electrical parameters such as:
  - Power & Energy
  - Voltage & Current
  - Frequency & Power Factor

---

## 💻 Software Stack

### Backend
- **Language:** Python  
- **Framework:** Flask  
- **Real-time Communication:** Flask-SocketIO  
- **Database:** SQLite  

### Frontend
- **HTML5, CSS3, JavaScript**
- **Chart.js** for real-time data visualization

### Firmware
- **ESP32 programmed using Arduino IDE**
- Periodic sensor polling and HTTP POST data transmission

---

## 📊 Data Processing Pipeline

### 1. Data Acquisition & Validation
- ESP32 sends sensor data to the Flask backend via REST API
- API key–based authentication and rate limiting are applied

### 2. Event Detection
- Events are defined as **significant changes in power consumption (ΔP)**
- A sustained power difference beyond a threshold triggers an event
- Noise and transient spikes are filtered

### 3. Appliance Identification
- Event characteristics are matched against stored appliance signatures
- Parameters used include:
  - Power change magnitude
  - Power factor
  - Time-of-day usage patterns
- Output:
  - Appliance label
  - Confidence score (0–1)

---

## 🤖 Machine Learning & Human-in-the-Loop Feedback

A key innovation of this system is its **adaptive learning capability**:

- Events with **low confidence scores** are marked as *unidentified*
- Users manually label appliances via the dashboard
- User-verified labels are stored as ground truth
- The appliance signature database is continuously updated

This approach enables the system to **self-improve over time**, learning appliance behaviors specific to each household.

---

## 📈 Web Dashboard Features

- Real-time electrical parameter display
- Power consumption time-series graphs
- Detected appliance events list
- Manual appliance labeling interface
- Feedback-driven learning loop

---

## 🚀 How to Run the Project

### 1. ESP32 Firmware
- Upload the firmware using **Arduino IDE**
- Configure Wi-Fi credentials and server IP
- Connect PZEM-004T and OLED display

### 2. Backend Server
```bash
pip install flask flask-socketio
python app.py
