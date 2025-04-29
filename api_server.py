from fastapi import FastAPI, HTTPException
import uvicorn
import pika
import json
import logging
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any, Optional
import time

# Import RabbitMQ settings from config
from config.settings import (
    RABBITMQ_HOST, RABBITMQ_PORT, RABBITMQ_USER, RABBITMQ_PASS,
    EXCHANGE_NAME, RPI_ID, COMMAND_QUEUE
)
from utils.logging_setup import logger

app = FastAPI(title="Smart Irrigation API")

# Add CORS middleware to allow requests from any origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Store the latest sensor data
latest_sensor_data = {
    "temperature": None,
    "humidity": None,
    "soil_is_dry": None,
    "pump_active": False,
    "last_updated": None
}

class MessageResponse(BaseModel):
    success: bool
    message: str

class SensorData(BaseModel):
    temperature: Optional[float]
    humidity: Optional[float]
    soil_is_dry: Optional[bool]
    pump_active: bool
    last_updated: Optional[float]

def send_rabbitmq_message(routing_key: str, message: Dict[str, Any]) -> bool:
    """Send a message to RabbitMQ."""
    try:
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                port=RABBITMQ_PORT,
                credentials=credentials
            )
        )
        channel = connection.channel()
        
        # Ensure exchange exists
        channel.exchange_declare(
            exchange=EXCHANGE_NAME,
            exchange_type='topic',
            durable=True
        )
        
        # Publish message
        channel.basic_publish(
            exchange=EXCHANGE_NAME,
            routing_key=routing_key,
            body=json.dumps(message),
            properties=pika.BasicProperties(
                delivery_mode=2,  # Make message persistent
                content_type='application/json'
            )
        )
        
        connection.close()
        logger.info(f"Message sent to {routing_key}: {message}")
        return True
    except Exception as e:
        logger.error(f"Failed to send message to RabbitMQ: {e}")
        return False

@app.get("/")
def read_root():
    return {"message": "Smart Irrigation API is running"}

@app.get("/irrigation/pump/on", response_model=MessageResponse)
def turn_pump_on():
    """Turn the irrigation pump on and automatically switch to manual mode, ignoring soil moisture readings."""
    routing_key = f"command.{RPI_ID}"
    message = {
        "target_id": RPI_ID,
        "pump_control": "ON", 
        "force_manual_mode": True,  # Flag to force manual mode regardless of current mode
        "mode": "manual",  # Set mode to manual to prevent auto-control
        "timestamp": time.time()
    }
    
    if send_rabbitmq_message(routing_key, message):
        return MessageResponse(success=True, message="Command to turn pump ON and switch to manual mode has been sent (ignoring soil moisture state)")
    else:
        raise HTTPException(status_code=500, detail="Failed to send command to irrigation system")

@app.get("/irrigation/pump/off", response_model=MessageResponse)
def turn_pump_off():
    """Turn the irrigation pump off while staying in manual mode."""
    routing_key = f"command.{RPI_ID}"
    message = {
        "target_id": RPI_ID,
        "pump_control": "OFF",
        "mode": "manual",  # Keep in manual mode
        "timestamp": time.time()
    }
    
    if send_rabbitmq_message(routing_key, message):
        return MessageResponse(success=True, message="Command to turn pump OFF while staying in manual mode has been sent")
    else:
        raise HTTPException(status_code=500, detail="Failed to send command to irrigation system")

@app.get("/irrigation/mode/auto", response_model=MessageResponse)
def set_auto_mode():
    """Switch to automatic mode where soil moisture controls the pump."""
    routing_key = f"command.{RPI_ID}"
    message = {"set_mode": "auto"}
    
    if send_rabbitmq_message(routing_key, message):
        return MessageResponse(success=True, message="Command to switch to automatic mode has been sent (soil moisture will control pump)")
    else:
        raise HTTPException(status_code=500, detail="Failed to send command to irrigation system")

@app.get("/irrigation/mode/manual", response_model=MessageResponse)
def set_manual_mode():
    """Switch to manual mode where the pump is only controlled by API calls, ignoring soil moisture state."""
    routing_key = f"command.{RPI_ID}"
    message = {"set_mode": "manual"}
    
    if send_rabbitmq_message(routing_key, message):
        return MessageResponse(success=True, message="Command to switch to manual mode has been sent (soil moisture readings will be ignored)")
    else:
        raise HTTPException(status_code=500, detail="Failed to send command to irrigation system")

@app.get("/irrigation/dht/on", response_model=MessageResponse)
def turn_dht_on():
    """Turn the DHT temperature sensor on."""
    routing_key = f"command.{RPI_ID}"
    message = {"dht_control": "ON"}
    
    if send_rabbitmq_message(routing_key, message):
        return MessageResponse(success=True, message="Command to turn DHT sensor ON has been sent")
    else:
        raise HTTPException(status_code=500, detail="Failed to send command to irrigation system")

@app.get("/irrigation/dht/off", response_model=MessageResponse)
def turn_dht_off():
    """Turn the DHT temperature sensor off."""
    routing_key = f"command.{RPI_ID}"
    message = {"dht_control": "OFF"}
    
    if send_rabbitmq_message(routing_key, message):
        return MessageResponse(success=True, message="Command to turn DHT sensor OFF has been sent")
    else:
        raise HTTPException(status_code=500, detail="Failed to send command to irrigation system")

@app.get("/irrigation/data", response_model=SensorData)
def get_sensor_data():
    """Get the latest sensor data."""
    if latest_sensor_data["last_updated"] is None:
        raise HTTPException(status_code=503, detail="Sensor data not yet available")
        
    return latest_sensor_data

@app.get("/irrigation/status")
def get_system_status():
    """Get comprehensive system status."""
    if latest_sensor_data["last_updated"] is None:
        raise HTTPException(status_code=503, detail="System status not yet available")
    
    # Return more detailed status information
    return {
        **latest_sensor_data,
        "system_time": datetime.now().isoformat(),
        "rpi_id": RPI_ID
    }

# This function will be called by the sensor data consumer
def update_sensor_data(data):
    """Update the stored sensor data with new values."""
    latest_sensor_data.update(data)
    logger.info(f"Sensor data updated: {latest_sensor_data}")

if __name__ == "__main__":
    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, reload=True) 