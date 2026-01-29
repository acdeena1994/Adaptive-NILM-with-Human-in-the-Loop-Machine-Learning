#include <WiFi.h>
#include <HTTPClient.h>
#include <PZEM004Tv30.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// PZEM-004T connections (using Hardware Serial2 on ESP32)
#define PZEM_RX_PIN 16
#define PZEM_TX_PIN 17
#define PZEM_SERIAL Serial2

// OLED display settings
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
#define SCREEN_ADDRESS 0x3C

// WiFi credentials
const char* ssid = "ACD";
const char* password = "pass1234";

// Server details
const char* serverURL = "http://10.235.96.251:5000/api/data";

// API Key - MUST MATCH SERVER API KEY
const char* apiKey = "nilm-system-api-key-2023"; // Change this to match your server

// PZEM object
PZEM004Tv30 pzem(PZEM_SERIAL, PZEM_RX_PIN, PZEM_TX_PIN);

// OLED display object
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// Variables for event detection
float prevPower = 0;
const float powerThreshold = 20.0; // 20W threshold for event detection
unsigned long lastSaveTime = 0;
const unsigned long saveInterval = 10000; // Save every 10 seconds

// Variables for OLED display cycling
unsigned long lastDisplayChange = 0;
const unsigned long displayInterval = 2000; // Change display every 2 seconds
int displayState = 0;

// System status variables
unsigned long systemStartTime = 0;
int dataPointsSent = 0;
int eventsDetected = 0;
int failedRequests = 0;

void setup() {
  Serial.begin(115200);
  
  // Initialize OLED display
  if(!display.begin(SSD1306_SWITCHCAPVCC, SCREEN_ADDRESS)) {
    Serial.println(F("SSD1306 allocation failed"));
    for(;;); // Don't proceed, loop forever
  }
  
  // Show initial display buffer contents on the screen
  display.display();
  delay(2000);
  
  // Clear the buffer
  display.clearDisplay();
  
  // Display startup message
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println(F("NILM System"));
  display.println(F("Initializing..."));
  display.display();
  
  // Initialize PZEM
  PZEM_SERIAL.begin(9600, SERIAL_8N1, PZEM_RX_PIN, PZEM_TX_PIN);
  
  // Connect to WiFi
  WiFi.begin(ssid, password);
  display.clearDisplay();
  display.setCursor(0, 0);
  display.println(F("Connecting to WiFi"));
  display.display();
  
  Serial.print("Connecting to WiFi");
  int wifiRetries = 0;
  while (WiFi.status() != WL_CONNECTED && wifiRetries < 20) {
    delay(500);
    Serial.print(".");
    display.print(".");
    display.display();
    wifiRetries++;
  }
  
  if (WiFi.status() != WL_CONNECTED) {
    display.clearDisplay();
    display.setCursor(0, 0);
    display.println(F("WiFi connection"));
    display.println(F("failed!"));
    display.display();
    Serial.println("\nWiFi connection failed");
    while(1) delay(1000); // Stop here if WiFi fails
  }
  
  display.clearDisplay();
  display.setCursor(0, 0);
  display.println(F("WiFi connected"));
  display.print(F("IP: "));
  display.println(WiFi.localIP());
  display.display();
  delay(2000);
  
  Serial.println("\nConnected to WiFi");
  Serial.print("IP address: ");
  Serial.println(WiFi.localIP());
  Serial.print("API Key: ");
  Serial.println(apiKey);
  
  // Record system start time
  systemStartTime = millis();
}

void loop() {
  // Read data from PZEM-004T
  float voltage = pzem.voltage();
  float current = pzem.current();
  float power = pzem.power();
  float energy = pzem.energy();
  float frequency = pzem.frequency();
  float pf = pzem.pf();
  
  // Check if readings are valid
  if (isnan(voltage) || isnan(current)) {
    Serial.println("Error reading from PZEM-004T");
    displayError("PZEM Read Error");
    delay(1000);
    return;
  }
  
  // Update OLED display
  updateDisplay(voltage, current, power, energy, frequency, pf);
  
  // Print data to serial
  Serial.print("Voltage: "); Serial.print(voltage); Serial.println("V");
  Serial.print("Current: "); Serial.print(current); Serial.println("A");
  Serial.print("Power: "); Serial.print(power); Serial.println("W");
  Serial.print("Energy: "); Serial.print(energy); Serial.println("kWh");
  Serial.print("Frequency: "); Serial.print(frequency); Serial.println("Hz");
  Serial.print("PF: "); Serial.println(pf);
  Serial.print("RSSI: "); Serial.print(WiFi.RSSI()); Serial.println(" dBm");
  
  // Event detection (simple threshold-based)
  if (abs(power - prevPower) > powerThreshold) {
    Serial.println("Power event detected!");
    eventsDetected++;
    String eventData = createEventJSON(voltage, current, power, energy, frequency, pf, "event");
    sendDataToServer(eventData);
  }
  
  // Periodic data saving
  if (millis() - lastSaveTime > saveInterval) {
    String periodicData = createEventJSON(voltage, current, power, energy, frequency, pf, "periodic");
    if (sendDataToServer(periodicData)) {
      dataPointsSent++;
    }
    lastSaveTime = millis();
  }
  
  prevPower = power;
  delay(1000); // Read every second
}

void updateDisplay(float voltage, float current, float power, float energy, float frequency, float pf) {
  // Change display state every displayInterval milliseconds
  if (millis() - lastDisplayChange > displayInterval) {
    displayState = (displayState + 1) % 5; // Cycle through 5 display states
    lastDisplayChange = millis();
  }
  
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  
  switch (displayState) {
    case 0:
      // Display voltage and current
      display.setTextSize(2);
      display.print(voltage, 1);
      display.println(" V");
      display.print(current, 2);
      display.println(" A");
      display.setTextSize(1);
      display.println("Voltage & Current");
      break;
      
    case 1:
      // Display power and energy
      display.setTextSize(2);
      display.print(power, 1);
      display.println(" W");
      display.print(energy, 2);
      display.println(" kWh");
      display.setTextSize(1);
      display.println("Power & Energy");
      break;
      
    case 2:
      // Display frequency and power factor
      display.setTextSize(2);
      display.print(frequency, 1);
      display.println(" Hz");
      display.print(pf, 2);
      display.println(" PF");
      display.setTextSize(1);
      display.println("Freq & Power Fact");
      break;
      
    case 3:
      // Display system status
      display.setTextSize(1);
      display.println("System Status");
      display.print("WiFi: ");
      display.println(WiFi.SSID());
      display.print("RSSI: ");
      display.print(WiFi.RSSI());
      display.println(" dBm");
      display.print("IP: ");
      display.println(WiFi.localIP());
      break;
      
    case 4:
      // Display NILM system info
      display.setTextSize(1);
      display.println("NILM Status");
      display.print("Uptime: ");
      display.print(millis() / 60000);
      display.println(" m");
      display.print("Data sent: ");
      display.println(dataPointsSent);
      display.print("Events: ");
      display.println(eventsDetected);
      display.print("Failures: ");
      display.println(failedRequests);
      break;
  }
  
  display.display();
}

void displayError(const char* errorMessage) {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println("ERROR:");
  display.setTextSize(2);
  display.setCursor(0, 20);
  display.println(errorMessage);
  display.display();
}

String createEventJSON(float voltage, float current, float power, float energy, float frequency, float pf, String type) {
  String data = "{";
  data += "\"type\":\"" + type + "\",";
  data += "\"voltage\":" + String(voltage, 2) + ",";
  data += "\"current\":" + String(current, 3) + ",";
  data += "\"power\":" + String(power, 2) + ",";
  data += "\"energy\":" + String(energy, 3) + ",";
  data += "\"frequency\":" + String(frequency, 2) + ",";
  data += "\"power_factor\":" + String(pf, 2) + ",";
  data += "\"rssi\":" + String(WiFi.RSSI()) + ",";
  data += "\"heap\":" + String(ESP.getFreeHeap());
  data += "}";
  return data;
}

bool sendDataToServer(String data) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi Disconnected");
    displayError("WiFi Disconnected");
    
    // Attempt to reconnect
    WiFi.disconnect();
    WiFi.begin(ssid, password);
    
    int retries = 0;
    while (WiFi.status() != WL_CONNECTED && retries < 10) {
      delay(500);
      Serial.print(".");
      retries++;
    }
    
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("Reconnection failed");
      failedRequests++;
      return false;
    }
    
    Serial.println("Reconnected to WiFi");
  }
  
  HTTPClient http;
  http.begin(serverURL);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-API-Key", apiKey);  // Add API key header
  
  int httpResponseCode = http.POST(data);
  
  if (httpResponseCode > 0) {
    String response = http.getString();
    Serial.println("HTTP Response code: " + String(httpResponseCode));
    Serial.println("Response: " + response);
    http.end();
    return true;
  } else {
    Serial.println("Error in HTTP request: " + String(httpResponseCode));
    if (httpResponseCode == 401) {
      Serial.println("API key rejected by server - check API key configuration");
      displayError("API Key Error");
    }
    failedRequests++;
    http.end();
    return false;
  }
}