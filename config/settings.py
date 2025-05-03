import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# RabbitMQ Configuration
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "192.168.43.124")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "smart_farming")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "password123")
EXCHANGE_NAME = "irrigation"

# Device Configuration
RPI_ID = "irrigation_system_1"
COMMAND_QUEUE = f"irrigation_system.{RPI_ID}.commands"
STATUS_QUEUE = f"irrigation_system.{RPI_ID}.status"

# GPIO Pin Configuration
SOIL_SENSOR_PIN = 4
PUMP_PIN = 21
DHT_PIN = 20
TEMP_MODULE_PIN = 16  # Pin for temperature-controlled module/fan
LDR_PIN = 7  # Pin for Light Dependent Resistor
LED_PIN = 24  # Pin for LED that activates when light is detected
VENTILATOR_PIN = TEMP_MODULE_PIN  # Use the same pin (16) for ventilator/fan as temperature module

# Temperature Threshold (°C)
TEMP_THRESHOLD = 20.0  # Activate module when temperature exceeds this value
VENTILATOR_TEMP_THRESHOLD = 20.0  # Temperature threshold for ventilator activation

# Ventilator Cycle Times (seconds)
VENTILATOR_ON_TIME = 20  # Ventilator stays on for 20 seconds
VENTILATOR_OFF_TIME = 10  # Ventilator stays off for 10 seconds

# Sensor Configuration
DHT_READ_INTERVAL = 10  # seconds
SOIL_CHECK_INTERVAL = 1  # seconds
HEARTBEAT_INTERVAL = 30  # seconds
LDR_READ_INTERVAL = 10  # seconds

# Moisture Thresholds (0-100%)
MOISTURE_THRESHOLD_LOW = 30  # Turn on pump when below this value
MOISTURE_THRESHOLD_HIGH = 70  # Turn off pump when above this value

# Pump Configuration
PUMP_REFRESH_INTERVAL = 0.5  # seconds

# Logging Configuration
LOG_LEVEL = "INFO"
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
LOG_FILE = "smart_irrigation_system.log" 