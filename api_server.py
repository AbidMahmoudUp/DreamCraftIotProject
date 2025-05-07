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
    light_detected: Optional[bool]
    led_active: Optional[bool]
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
    """Switch entire system to automatic mode where sensors control both irrigation and ventilation."""
    routing_key = f"command.{RPI_ID}"
    
    # First message controls irrigation system mode
    irrigation_message = {"set_mode": "auto"}
    success1 = send_rabbitmq_message(routing_key, irrigation_message)
    
    # Second message controls ventilator mode
    ventilator_message = {
        "target_id": RPI_ID,
        "ventilator_mode": "AUTO",
        "timestamp": time.time()
    }
    success2 = send_rabbitmq_message(routing_key, ventilator_message)
    
    if success1 and success2:
        return MessageResponse(success=True, message="Command to switch entire system to automatic mode has been sent (sensors will control pump and ventilator)")
    else:
        raise HTTPException(status_code=500, detail="Failed to send commands to irrigation system")

@app.get("/irrigation/mode/manual", response_model=MessageResponse)
def set_manual_mode():
    """Switch entire system to manual mode where both pump and ventilator are only controlled by API calls."""
    routing_key = f"command.{RPI_ID}"
    
    # First message controls irrigation system mode
    irrigation_message = {"set_mode": "manual"}
    success1 = send_rabbitmq_message(routing_key, irrigation_message)
    
    # Second message controls ventilator mode
    ventilator_message = {
        "target_id": RPI_ID,
        "ventilator_mode": "MANUAL",
        "timestamp": time.time()
    }
    success2 = send_rabbitmq_message(routing_key, ventilator_message)
    
    if success1 and success2:
        return MessageResponse(success=True, message="Command to switch entire system to manual mode has been sent (all sensors will be ignored)")
    else:
        raise HTTPException(status_code=500, detail="Failed to send commands to irrigation system")

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

@app.get("/irrigation/light")
def get_light_sensor_status():
    """
    Get the current light level from the LDR sensor.
    Note that the LED operates with inverted logic:
    - LED is ON when it's dark (light_detected = false)
    - LED is OFF when light is detected (light_detected = true)
    """
    if latest_sensor_data["last_updated"] is None:
        raise HTTPException(status_code=503, detail="Sensor data not yet available")
        
    light_detected = latest_sensor_data.get("light_detected", False)
    led_active = latest_sensor_data.get("led_active", False)
    
    return {
        "light_detected": light_detected,
        "led_active": led_active,
        "timestamp": latest_sensor_data.get("last_updated"),
        "status": "light" if light_detected else "dark",
        # Add explanation of inverted logic
        "note": "LED uses inverted logic: ON when dark, OFF when light detected"
    }

@app.get("/irrigation/led/{state}")
def control_led(state: str):
    """Manually control the LED state."""
    # Only 'on' and 'off' are accepted
    if state.lower() not in ["on", "off"]:
        raise HTTPException(status_code=400, detail="State must be 'on' or 'off'")
        
    # Send command to control the LED
    routing_key = f"command.{RPI_ID}"
    message = {
        "target_id": RPI_ID,
        "led_control": state.upper(),
        "timestamp": time.time()
    }
    
    if send_rabbitmq_message(routing_key, message):
        return MessageResponse(success=True, message=f"Command to turn LED {state.upper()} has been sent")
    else:
        raise HTTPException(status_code=500, detail="Failed to send LED control command")

@app.get("/irrigation/ventilator/on")
def turn_ventilator_on():
    """Turn the ventilator on and switch to manual mode."""
    routing_key = f"command.{RPI_ID}"
    message = {
        "target_id": RPI_ID,
        "ventilator_control": "ON",
        "timestamp": time.time()
    }
    
    if send_rabbitmq_message(routing_key, message):
        return MessageResponse(success=True, message="Command to turn ventilator ON has been sent")
    else:
        raise HTTPException(status_code=500, detail="Failed to send command to irrigation system")

@app.get("/irrigation/ventilator/off")
def turn_ventilator_off():
    """Turn the ventilator off and remain in manual mode."""
    routing_key = f"command.{RPI_ID}"
    message = {
        "target_id": RPI_ID,
        "ventilator_control": "OFF",
        "timestamp": time.time()
    }
    
    if send_rabbitmq_message(routing_key, message):
        return MessageResponse(success=True, message="Command to turn ventilator OFF has been sent")
    else:
        raise HTTPException(status_code=500, detail="Failed to send command to irrigation system")

@app.get("/irrigation/ventilator/mode/{mode}")
def set_ventilator_mode(mode: str):
    """
    DEPRECATED: Use /irrigation/mode/{mode} instead which controls both irrigation and ventilator.
    This endpoint will be removed in future versions.
    
    Set the ventilator control mode.
    - auto: Automatically control ventilator based on temperature with cycling
    - manual: Manually control ventilator
    """
    if mode.lower() not in ["auto", "automatic", "manual", "man"]:
        raise HTTPException(status_code=400, detail="Mode must be 'auto' or 'manual'")
    
    # Show deprecation warning
    logger.warning("Deprecated endpoint used: /irrigation/ventilator/mode/{mode}. Use /irrigation/mode/{mode} instead.")
    
    routing_key = f"command.{RPI_ID}"
    message = {
        "target_id": RPI_ID,
        "ventilator_mode": mode.upper(),
        "timestamp": time.time()
    }
    
    if send_rabbitmq_message(routing_key, message):
        mode_display = "AUTOMATIC" if mode.lower() in ["auto", "automatic"] else "MANUAL"
        return MessageResponse(
            success=True, 
            message=f"Command to set ventilator mode to {mode_display} has been sent. NOTE: For future requests, use /irrigation/mode/{mode} to control the entire system."
        )
    else:
        raise HTTPException(status_code=500, detail="Failed to send command to irrigation system")

@app.get("/irrigation/ventilator/status")
def get_ventilator_status():
    """Get the current ventilator status."""
    if latest_sensor_data["last_updated"] is None:
        raise HTTPException(status_code=503, detail="Sensor data not yet available")
    
    return {
        "ventilator_on": latest_sensor_data.get("ventilator_on", False),
        "ventilator_auto": latest_sensor_data.get("ventilator_auto", True),
        "ventilator_cycling": latest_sensor_data.get("ventilator_cycling", False),
        "temperature": latest_sensor_data.get("temperature", 0.0),
        "timestamp": latest_sensor_data.get("last_updated"),
        "mode": "automatic" if latest_sensor_data.get("ventilator_auto", True) else "manual",
        "note": "The ventilator mode is now controlled by the unified system mode. Use /irrigation/mode/{auto|manual} to control both irrigation and ventilator modes."
    }

@app.get("/irrigation/sensors/all")
def get_all_sensors_data():
    """
    Get comprehensive data from all sensors in the system.
    Specifically focuses on DHT sensor data (temperature and humidity).
    """
    if latest_sensor_data["last_updated"] is None:
        raise HTTPException(status_code=503, detail="Sensor data not yet available")
    
    # Debug log the raw sensor data to verify humidity is present
    humidity_value = latest_sensor_data.get("humidity", 0.0)
    temperature_value = latest_sensor_data.get("temperature", 0.0)
    
    logger.info(f"CRITICAL DEBUG - Raw latest_sensor_data: {latest_sensor_data}")
    logger.info(f"CRITICAL DEBUG - Direct access humidity value: {humidity_value}")
    logger.info(f"CRITICAL DEBUG - Direct access temperature value: {temperature_value}")
    
    return {
        # DHT sensor data (highlighted)
        "dht": {
            "temperature": temperature_value,
            "humidity": humidity_value,
            "enabled": latest_sensor_data.get("dht_enabled", True)
        },
        # Soil moisture sensor
        "soil": {
            "is_dry": latest_sensor_data.get("soil_is_dry"),
            "moisture_level": latest_sensor_data.get("soil_moisture_level", 0)
        },
        # Actuator states
        "actuators": {
            "pump": {
                "active": latest_sensor_data.get("pump_active", False),
                "mode": "automatic" if latest_sensor_data.get("automatic_mode", True) else "manual"
            },
            "ventilator": {
                "active": latest_sensor_data.get("ventilator_on", False),
                "auto_mode": latest_sensor_data.get("ventilator_auto", True),
                "cycling": latest_sensor_data.get("ventilator_cycling", False)
            },
            "led": {
                "active": latest_sensor_data.get("led_active", False)
            }
        },
        # Light sensor
        "light": {
            "detected": latest_sensor_data.get("light_detected", False)
        },
        "last_updated": latest_sensor_data.get("last_updated"),
        "rpi_id": RPI_ID
    }

# This function will be called by the sensor data consumer
def update_sensor_data(data):
    """Update the stored sensor data with new values."""
    latest_sensor_data.update(data)
    logger.info(f"Sensor data updated: {latest_sensor_data}")

if __name__ == "__main__":
    uvicorn.run("api_server:app", host="0.0.0.0", port=8006, reload=True) 