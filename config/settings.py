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

# Sensor Configuration
DHT_READ_INTERVAL = 10  # seconds
SOIL_CHECK_INTERVAL = 1  # seconds
HEARTBEAT_INTERVAL = 30  # seconds

# Pump Configuration
PUMP_REFRESH_INTERVAL = 0.5  # seconds

# Logging Configuration
LOG_LEVEL = "INFO"
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
LOG_FILE = "smart_irrigation_system.log" 