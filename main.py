import time
import logging
import signal
import sys
import json
import threading
import subprocess
import os
from datetime import datetime
import pika
import board
from pika.adapters.select_connection import SelectConnection
from core.hardware import HardwareManager
from core.message_handler import MessageHandler
from core.state_manager import StateManager
from sensors.temperature import DHTSensor
from utils.logging_setup import logger
import RPi.GPIO as GPIO
from config.settings import SOIL_SENSOR_PIN, PUMP_PIN, DHT_PIN, EXCHANGE_NAME, COMMAND_QUEUE, RPI_ID

class SmartIrrigationSystem:
    def __init__(self):
        # Initialize components
        self.hardware = HardwareManager()
        self.state_manager = StateManager()
        self.message_handler = None  # Initialize later after connection is established
        
        # Verify pins to make sure they match settings
        logger.info(f"Using pins - Soil: {SOIL_SENSOR_PIN}, Pump: {PUMP_PIN}, DHT: {DHT_PIN}")
        
        # Initialize DHT sensor with explicit pin from settings
        self.dht_sensor = DHTSensor(board.D20)  # Using D20 for DHT sensor
        self.dht_enabled = True  # Default is enabled
        
        # System configuration
        self.running = False
        self.last_dht_read = 0
        self.dht_read_interval = 10  # seconds between DHT readings
        self.soil_check_timer = None
        self.temperature_timer = None
        self.pump_refresh_timer = None
        self.status_update_timer = None  # Timer for regular status updates
        self.last_pump_refresh = 0
        self.last_status_update = 0
        self.pump_refresh_interval = 0.5  # Refresh pump every 0.5 seconds just like in aza.py
        self.status_update_interval = 5.0  # Send status updates every 5 seconds
        self.connection = None
        self.channel = None
        
        # API server process
        self.api_process = None
        self.consumer_process = None
        
    def initialize(self) -> bool:
        """Initialize the system."""
        try:
            # Initialize hardware
            if not self.hardware.initialize():
                logger.error("Failed to initialize hardware")
                return False
                
            # Test hardware components
            if not self.hardware.test_hardware():
                logger.error("Hardware test failed! Check connections and try again.")
                return False
                
            # Connect to RabbitMQ
            if not self.connect_to_rabbitmq():
                logger.error("Failed to connect to RabbitMQ")
                return False
                
            # Start API server and sensor data consumer in separate processes
            if not self.start_api_components():
                logger.error("Failed to start API components")
                return False
                
            # Set running state to True
            self.running = True
            
            logger.info("Smart irrigation system initialized")
            return True
            
        except Exception as e:
            logger.error(f"Error during system initialization: {e}")
            return False

    def start_api_components(self):
        """Start the API server and sensor data consumer."""
        try:
            # Start API server
            logger.info("Starting API server...")
            self.api_process = subprocess.Popen(
                [sys.executable, "api_server.py"],
                cwd=os.path.dirname(os.path.abspath(__file__))
            )
            logger.info(f"API server started with PID {self.api_process.pid}")
            
            # Start sensor data consumer
            logger.info("Starting sensor data consumer...")
            self.consumer_process = subprocess.Popen(
                [sys.executable, "sensor_data_consumer.py"],
                cwd=os.path.dirname(os.path.abspath(__file__))
            )
            logger.info(f"Sensor data consumer started with PID {self.consumer_process.pid}")
            
            # Wait a bit to make sure processes start successfully
            time.sleep(2)
            
            # Check if processes are still running
            if self.api_process.poll() is not None:
                logger.error("API server failed to start")
                return False
                
            if self.consumer_process.poll() is not None:
                logger.error("Sensor data consumer failed to start")
                return False
                
            return True
        except Exception as e:
            logger.error(f"Failed to start API components: {e}")
            return False

    def connect_to_rabbitmq(self):
        """Connect to RabbitMQ using SelectConnection."""
        try:
            credentials = pika.PlainCredentials('smart_farming', 'password123')
            parameters = pika.ConnectionParameters(
                host="192.168.43.124",
                port=5672,
                credentials=credentials,
                heartbeat=60
            )
            self.connection = SelectConnection(
                parameters=parameters,
                on_open_callback=self.on_connection_open,
                on_open_error_callback=self.on_connection_open_error,
                on_close_callback=self.on_connection_closed
            )
            return True
        except Exception as e:
            logger.error(f"Failed to create RabbitMQ connection: {e}")
            return False

    def on_connection_open(self, connection):
        """Called when connection is established."""
        logger.info("Connection opened")
        self.connection = connection
        self.channel = self.connection.channel(on_open_callback=self.on_channel_open)

    def on_connection_open_error(self, _unused_connection, err):
        """Called if connection can't be opened."""
        logger.error(f"Connection open failed: {err}")

    def on_connection_closed(self, _unused_connection, reason):
        """Called when connection is closed."""
        logger.warning(f"Connection closed: {reason}")
        self.channel = None

    def on_channel_open(self, channel):
        """Called when channel is opened."""
        logger.info("Channel opened")
        self.channel = channel
        self.setup_exchange()

    def setup_exchange(self):
        """Setup the exchange on RabbitMQ."""
        logger.info(f'Declaring exchange: {EXCHANGE_NAME}')
        self.channel.exchange_declare(
            exchange=EXCHANGE_NAME,
            exchange_type='topic',
            durable=True,
            callback=self.on_exchange_declareok
        )

    def on_exchange_declareok(self, _unused_frame):
        """Called when the exchange is declared."""
        logger.info(f'Exchange declared: {EXCHANGE_NAME}')
        self.setup_command_queue()

    def setup_command_queue(self):
        """Setup command queue for receiving control commands."""
        logger.info(f"Declaring command queue: {COMMAND_QUEUE}")
        self.channel.queue_declare(
            queue=COMMAND_QUEUE,
            durable=True,
            callback=self.on_command_queue_declareok
        )

    def on_command_queue_declareok(self, _unused_frame):
        """Called when command queue is declared."""
        logger.info('Command queue declared')
        routing_key = f"command.{RPI_ID}"
        logger.info(f"Binding command queue to routing key: {routing_key}")
        self.channel.queue_bind(
            queue=COMMAND_QUEUE,
            exchange=EXCHANGE_NAME,
            routing_key=routing_key,
            callback=self.on_command_bindok
        )

    def on_command_bindok(self, _unused_frame):
        """Called when command queue is bound."""
        logger.info('Command queue bound')
        self.start_consuming()

    def start_consuming(self):
        """Start consuming messages."""
        logger.info(f'Starting consumption on queue: {COMMAND_QUEUE}')
        self.channel.basic_consume(
            queue=COMMAND_QUEUE,
            on_message_callback=self.on_message,
            auto_ack=False
        )
        self.start_monitoring()

    def on_message(self, channel, basic_deliver, properties, body):
        """Called when a message is received."""
        delivery_tag = basic_deliver.delivery_tag
        try:
            message = json.loads(body)
            logger.info(f"Received command: {message}")
            
            # Process mode setting - check for mode key first to prioritize it
            if "mode" in message:
                mode_value = message["mode"].lower() if isinstance(message["mode"], str) else message["mode"]
                logger.info(f"Received MODE command: {mode_value}")
                
                # Convert to internal mode format (auto/manual)
                if mode_value.upper() in ["AUTOMATIC", "AUTO"]:
                    mode = "auto"
                elif mode_value.upper() in ["MANUAL", "MAN"]:
                    mode = "manual"
                else:
                    logger.error(f"Invalid mode value: {mode_value}")
                    mode = None
                
                if mode:
                    logger.info(f"Changing system mode from {self.state_manager.get_state()['mode']} to {mode}")
                    if self.state_manager.set_mode(mode):
                        logger.info(f"Successfully set system mode to {mode.upper()}")
                        
                        # If switching to auto mode, immediately check soil to set pump state
                        if mode == "auto":
                            logger.info("Mode is now AUTO - checking soil moisture to control pump")
                            self.check_soil_and_control_pump()
                        else:
                            logger.info("Mode is now MANUAL - pump will only respond to direct commands")
                            
                        # Send status update to reflect mode change
                        self.send_status_update()
                    else:
                        logger.error(f"Failed to set mode to {mode}")
            
            # Process pump control command
            if "pump_control" in message:
                state = message["pump_control"] == "ON"
                
                # Check if we need to force manual mode
                if "force_manual_mode" in message and message["force_manual_mode"] and state:
                    current_mode = self.state_manager.get_state()["mode"]
                    if current_mode != "manual":
                        logger.info(f"Force switching from {current_mode} mode to manual mode due to pump ON command")
                        if self.state_manager.set_mode("manual"):
                            logger.info("Successfully forced system to MANUAL mode")
                            # Send status update to reflect mode change
                            self.send_status_update()
                        else:
                            logger.error("Failed to force system to MANUAL mode")
                
                # Log the pump control command
                logger.info(f"Processing pump control command: {'ON' if state else 'OFF'}")
                
                # Try to set the pump state
                if self.hardware.force_pump_state(state):
                    # Update state manager
                    self.state_manager.update_state(pump_on=state)
                    
                    if state:
                        self.state_manager.record_watering()
                        self.last_pump_refresh = time.time()
                    
                    current_mode = self.state_manager.get_state()["mode"]
                    logger.info(f"Pump turned {'ON' if state else 'OFF'} successfully in {current_mode.upper()} mode")
                    
                    # Send immediate status update
                    self.send_status_update()
                else:
                    logger.error(f"Failed to set pump state to {'ON' if state else 'OFF'}")
            
            # Process alternative mode setting command
            elif "set_mode" in message and "mode" not in message:
                mode_value = message["set_mode"].lower() if isinstance(message["set_mode"], str) else message["set_mode"]
                logger.info(f"Received SET_MODE command: {mode_value}")
                
                # Convert to internal mode format (auto/manual)
                if mode_value in ["automatic", "auto"]:
                    mode = "auto"
                elif mode_value in ["manual", "man"]:
                    mode = "manual"
                else:
                    logger.error(f"Invalid set_mode value: {mode_value}")
                    mode = None
                
                if mode:
                    logger.info(f"Changing system mode from {self.state_manager.get_state()['mode']} to {mode}")
                    if self.state_manager.set_mode(mode):
                        logger.info(f"Successfully set system mode to {mode.upper()}")
                        
                        # If switching to auto mode, immediately check soil to set pump state
                        if mode == "auto":
                            logger.info("Mode is now AUTO - checking soil moisture to control pump")
                            self.check_soil_and_control_pump()
                        else:
                            logger.info("Mode is now MANUAL - pump will only respond to direct commands")
                        
                        # Send status update
                        self.send_status_update()
                    else:
                        logger.error(f"Failed to set mode to {mode}")
            
            # Process DHT sensor control command
            elif "dht_control" in message:
                dht_enabled = message["dht_control"] == "ON"
                logger.info(f"Processing DHT sensor control command: {'ON' if dht_enabled else 'OFF'}")
                
                # Update DHT sensor state
                self.dht_enabled = dht_enabled
                
                # Log the action
                if dht_enabled:
                    logger.info("DHT temperature sensor enabled")
                    # Schedule temperature reading
                    self.schedule_temperature_check()
                else:
                    logger.info("DHT temperature sensor disabled")
                
                # Send immediate status update
                self.send_status_update()
                
            # Acknowledge message
            channel.basic_ack(delivery_tag)
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message as JSON: {e}")
            channel.basic_nack(delivery_tag=delivery_tag, requeue=False)
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            channel.basic_nack(delivery_tag=delivery_tag, requeue=False)

    def start_monitoring(self):
        """Start monitoring soil moisture and temperature."""
        logger.info("Starting sensor monitoring...")
        self.schedule_soil_check()
        self.schedule_temperature_check()
        self.schedule_pump_refresh()  # Start the pump refresh cycle
        self.schedule_status_update()  # Start regular status updates

    def schedule_soil_check(self):
        """Schedule the next soil moisture check."""
        logger.debug("Scheduling next soil check")
        if self.running and self.connection:
            self.soil_check_timer = self.connection.ioloop.call_later(
                1.0, self.check_soil_and_control_pump)
            logger.debug("Soil check scheduled")
        else:
            logger.warning(f"Could not schedule soil check - running: {self.running}, connection available: {self.connection is not None}")

    def schedule_temperature_check(self):
        """Schedule the next temperature check."""
        logger.debug("Scheduling next temperature check")
        if self.running and self.connection and self.dht_enabled:
            self.temperature_timer = self.connection.ioloop.call_later(
                self.dht_read_interval, self.read_temperature)
            logger.debug("Temperature check scheduled")
        else:
            if not self.dht_enabled:
                logger.debug("DHT sensor is disabled - skipping temperature check")
            else:
                logger.warning(f"Could not schedule temperature check - running: {self.running}, connection available: {self.connection is not None}")

    def schedule_pump_refresh(self):
        """Schedule the next pump refresh."""
        if self.running and self.connection:
            self.pump_refresh_timer = self.connection.ioloop.call_later(
                self.pump_refresh_interval, self.refresh_pump_state)
            logger.debug("Pump refresh scheduled")
        else:
            logger.warning(f"Could not schedule pump refresh - running: {self.running}, connection available: {self.connection is not None}")
    
    def schedule_status_update(self):
        """Schedule the next status update."""
        if self.running and self.connection:
            self.status_update_timer = self.connection.ioloop.call_later(
                self.status_update_interval, self.send_status_update)
            logger.debug("Status update scheduled")
        else:
            logger.warning(f"Could not schedule status update - running: {self.running}, connection available: {self.connection is not None}")
            
    def refresh_pump_state(self):
        """Periodically refresh the pump state if it's supposed to be on."""
        try:
            if self.running:
                current_time = time.time()
                
                # Only refresh if pump should be active
                if self.state_manager.get_state()["pump_on"]:
                    if current_time - self.last_pump_refresh >= self.pump_refresh_interval:
                        logger.debug("Refreshing pump ON state")
                        if self.hardware.force_pump_state(True):
                            self.last_pump_refresh = current_time
                            logger.debug("Pump state refreshed successfully")
                        else:
                            logger.error("Failed to refresh pump ON state")
        except Exception as e:
            logger.error(f"Error in pump refresh: {e}")
            # Safety measure: turn off pump in case of error
            try:
                self.hardware.force_pump_state(False)
                self.state_manager.update_state(pump_on=False)
            except Exception as ex:
                logger.error(f"Error during emergency pump shutdown: {ex}")
        finally:
            # Schedule next refresh
            self.schedule_pump_refresh()

    def check_soil_and_control_pump(self):
        """Check soil moisture and control pump in automatic mode."""
        try:
            # Read soil moisture
            soil_is_dry = self.hardware.read_soil_moisture()
            previous_state = self.state_manager.get_state()["soil_moist"]
            state_changed = (not soil_is_dry) != previous_state
            
            # Current time for logging
            current_time = datetime.now().strftime("%H:%M:%S")
            
            # Update state manager with new soil moisture state
            self.state_manager.update_state(soil_moist=not soil_is_dry)
            
            # ALWAYS log soil moisture status for debugging
            logger.info(f"[{current_time}] SOIL MOISTURE CHECK: {'DRY' if soil_is_dry else 'WET'}")
            
            # Get current pump GPIO state
            current_gpio = GPIO.input(self.hardware.pump_pin)
            actual_pump_on = (current_gpio == 0)  # LOW (0) means pump is ON
            
            # Log state changes
            if state_changed:
                logger.info(f"[{current_time}] Soil moisture changed: {'WET' if not soil_is_dry else 'DRY'} (previous: {'WET' if previous_state else 'DRY'})")
                # Send status update when soil moisture changes
                self.send_status_update()
            
            # Check system mode - GET FRESH STATE to ensure latest mode is used
            state = self.state_manager.get_state()
            current_mode = state["mode"]
            mode_set_by_user = state.get("mode_set_by_user", False)
            
            # IMPORTANT: Add more detailed mode logging
            logger.info(f"[{current_time}] Current system mode: {current_mode.upper()} (set by user: {mode_set_by_user})")
            
            # Check if in manual mode - if so, don't automatically control the pump at all
            if current_mode == "manual":
                logger.info(f"[{current_time}] MANUAL MODE ACTIVE - soil moisture reading {'DRY' if soil_is_dry else 'WET'} is IGNORED")
                logger.info(f"[{current_time}] In manual mode, pump stays {'ON' if actual_pump_on else 'OFF'} regardless of soil state")
                
                # CRITICAL: In manual mode, never change pump state automatically
                # Do nothing with the pump here - in manual mode, pump state is controlled only by direct commands
                
            # In automatic mode, control pump based on soil moisture
            else:  # auto mode
                logger.info(f"[{current_time}] AUTO MODE ACTIVE - controlling pump based on soil moisture")
                
                # CORRECTED LOGIC: Activate pump when soil is DRY (normal irrigation behavior)
                if soil_is_dry and not actual_pump_on:
                    logger.info(f"[{current_time}] Soil is DRY! Activating pump.")
                    if self.hardware.force_pump_state(True):
                        self.state_manager.update_state(pump_on=True)
                        self.last_pump_refresh = time.time()  # Reset the refresh timer
                        logger.info(f"[{current_time}] Pump activated successfully")
                        # Send status update when pump is activated
                        self.send_status_update()
                    else:
                        logger.error(f"[{current_time}] Failed to activate pump!")
                        
                # Turn off pump when soil is WET - ALWAYS check this regardless of state changes
                elif not soil_is_dry and actual_pump_on:
                    logger.info(f"[{current_time}] Soil is WET! Turning off pump.")
                    if self.hardware.force_pump_state(False):
                        self.state_manager.update_state(pump_on=False)
                        logger.info(f"[{current_time}] Pump deactivated successfully")
                        # Send status update when pump is deactivated
                        self.send_status_update()
                    else:
                        logger.error(f"[{current_time}] Failed to deactivate pump!")
            
            # Log current pump state and GPIO reading
            current_gpio = GPIO.input(self.hardware.pump_pin)
            # Correct pump state reporting based on GPIO value (LOW=0=ON, HIGH=1=OFF)
            actual_pump_state = "ON" if current_gpio == 0 else "OFF"
            logger.info(f"[{current_time}] Current pump state: {actual_pump_state}, GPIO: {current_gpio}")
                
        except Exception as e:
            logger.error(f"Error in soil check and pump control: {e}")
            # Safety measure: turn off pump on error
            try:
                self.hardware.force_pump_state(False)
                self.state_manager.update_state(pump_on=False)
            except Exception as ex:
                logger.error(f"Error during emergency pump shutdown: {ex}")
        finally:
            # Schedule next check
            self.schedule_soil_check()

    def read_temperature(self):
        """Read temperature and humidity from DHT sensor."""
        try:
            # Skip if DHT sensor is disabled
            if not self.dht_enabled:
                logger.debug("DHT sensor is disabled - skipping temperature reading")
                return
                
            # Current time for logging
            current_time = datetime.now().strftime("%H:%M:%S")
            
            logger.info(f"[{current_time}] Reading temperature and humidity...")
            temp, humidity = self.dht_sensor.read()
            
            if temp is not None and humidity is not None:
                logger.info(f"[{current_time}] DHT READING: Temperature={temp:.1f}°C, Humidity={humidity:.1f}%")
                self.state_manager.update_state(
                    temperature=temp,
                    humidity=humidity
                )
                # Send status update with new temperature/humidity data
                self.send_status_update()
            else:
                logger.warning(f"[{current_time}] DHT READING FAILED: Temperature or humidity is None")
                
        except Exception as e:
            logger.error(f"Error reading temperature: {e}")
        finally:
            # Schedule next check if DHT is enabled
            if self.dht_enabled:
                self.schedule_temperature_check()

    def send_status_update(self):
        """Send current system status via RabbitMQ."""
        try:
            if self.channel:
                current_time = time.time()
                # Rate limit status updates
                if current_time - self.last_status_update >= 1.0:  # Maximum 1 update per second
                    state = self.state_manager.get_state()
                    
                    # Convert state to message format
                    message = {
                        "rpi_id": RPI_ID,
                        "timestamp": current_time,
                        "dht_enabled": self.dht_enabled,
                        **state
                    }
                    
                    # Send message to RabbitMQ
                    self.channel.basic_publish(
                        exchange=EXCHANGE_NAME,
                        routing_key=f"status.{RPI_ID}",
                        body=json.dumps(message),
                        properties=pika.BasicProperties(
                            delivery_mode=2,  # Make message persistent
                            content_type='application/json'
                        )
                    )
                    logger.debug(f"Status update sent: soil_moist={state['soil_moist']}, pump_on={state['pump_on']}")
                    self.last_status_update = current_time
            else:
                logger.warning("Cannot send status update - channel not available")
                
        except Exception as e:
            logger.error(f"Error sending status update: {e}")
        finally:
            # Schedule next regular status update
            self.schedule_status_update()

    def run(self):
        """Run the system using event loop."""
        logger.info("Starting run method - setting running state to True")
        self.running = True
        
        def handle_shutdown(signum, frame):
            logger.info("Shutdown signal received...")
            self.running = False
            if self.connection:
                self.connection.ioloop.stop()
            
        signal.signal(signal.SIGINT, handle_shutdown)
        signal.signal(signal.SIGTERM, handle_shutdown)
        
        try:
            logger.info("Starting event loop...")
            # Start the event loop
            self.connection.ioloop.start()
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
            self.running = False
            if self.connection:
                self.connection.ioloop.stop()
        except Exception as e:
            logger.error(f"Error in event loop: {e}")
        finally:
            self.cleanup()

    def cleanup(self):
        """Clean up system resources."""
        logger.info("Cleaning up...")
        try:
            # Turn off pump
            self.hardware.force_pump_state(False)
            
            # Clean up hardware
            self.hardware.cleanup()
            
            # Close RabbitMQ connection
            if self.connection and not self.connection.is_closed:
                self.connection.close()
                
            # Stop API server and sensor data consumer
            if self.api_process:
                try:
                    logger.info("Stopping API server...")
                    self.api_process.terminate()
                    self.api_process.wait(timeout=5)
                    logger.info("API server stopped")
                except subprocess.TimeoutExpired:
                    logger.warning("API server didn't stop gracefully, killing...")
                    self.api_process.kill()
                except Exception as e:
                    logger.error(f"Error stopping API server: {e}")
            
            if self.consumer_process:
                try:
                    logger.info("Stopping sensor data consumer...")
                    self.consumer_process.terminate()
                    self.consumer_process.wait(timeout=5)
                    logger.info("Sensor data consumer stopped")
                except subprocess.TimeoutExpired:
                    logger.warning("Sensor data consumer didn't stop gracefully, killing...")
                    self.consumer_process.kill()
                except Exception as e:
                    logger.error(f"Error stopping sensor data consumer: {e}")
            
            logger.info("Cleanup complete")
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

def main():
    system = SmartIrrigationSystem()
    
    if system.initialize():
        # Print API endpoints information
        print("""
Smart Irrigation System is running!
API endpoints are available at:
- http://localhost:8000/irrigation/pump/on      (Turn pump ON in manual mode)
- http://localhost:8000/irrigation/pump/off     (Turn pump OFF in manual mode)
- http://localhost:8000/irrigation/mode/auto    (Switch to automatic mode)
- http://localhost:8000/irrigation/mode/manual  (Switch to manual mode)
- http://localhost:8000/irrigation/dht/on       (Enable DHT temperature sensor)
- http://localhost:8000/irrigation/dht/off      (Disable DHT temperature sensor)
- http://localhost:8000/irrigation/data         (Get sensor data)
- http://localhost:8000/docs                    (API documentation)
""")
        system.run()
    else:
        logger.error("System initialization failed")
        system.cleanup()
        sys.exit(1)

if __name__ == "__main__":
    main() 