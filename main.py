import time
import logging
import signal
import sys
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
from config.settings import SOIL_SENSOR_PIN, PUMP_PIN, DHT_PIN

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
        
        # System configuration
        self.running = False
        self.last_dht_read = 0
        self.dht_read_interval = 10  # seconds between DHT readings
        self.soil_check_timer = None
        self.temperature_timer = None
        self.pump_refresh_timer = None
        self.last_pump_refresh = 0
        self.pump_refresh_interval = 0.5  # Refresh pump every 0.5 seconds just like in aza.py
        self.connection = None
        self.channel = None
        
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
                
            # Set running state to True
            self.running = True
            
            logger.info("Smart irrigation system initialized")
            return True
            
        except Exception as e:
            logger.error(f"Error during system initialization: {e}")
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
        # Initialize message handler now that we have a connection
        self.message_handler = MessageHandler(host="192.168.43.124", port=5672)
        self.start_monitoring()

    def start_monitoring(self):
        """Start monitoring soil moisture and temperature."""
        logger.info("Starting sensor monitoring...")
        self.schedule_soil_check()
        self.schedule_temperature_check()
        self.schedule_pump_refresh()  # Start the pump refresh cycle

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
        if self.running and self.connection:
            self.temperature_timer = self.connection.ioloop.call_later(
                self.dht_read_interval, self.read_temperature)
            logger.debug("Temperature check scheduled")
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
            
            # Update state
            self.state_manager.update_state(soil_moist=not soil_is_dry)
            
            # ALWAYS log soil moisture status for debugging
            logger.info(f"[{current_time}] SOIL MOISTURE CHECK: {'DRY' if soil_is_dry else 'WET'}")
            
            # Get current pump GPIO state
            current_gpio = GPIO.input(self.hardware.pump_pin)
            actual_pump_on = (current_gpio == 0)  # LOW (0) means pump is ON
            
            # Log state changes
            if state_changed:
                logger.info(f"[{current_time}] Soil moisture changed: {'WET' if not soil_is_dry else 'DRY'} (previous: {'WET' if previous_state else 'DRY'})")
            
            # In automatic mode, control pump based on soil moisture
            if self.state_manager.get_state()["mode"] == "auto":
                # CORRECTED LOGIC: Activate pump when soil is DRY (normal irrigation behavior)
                if soil_is_dry and not actual_pump_on:
                    logger.info(f"[{current_time}] Soil is DRY! Activating pump.")
                    if self.hardware.force_pump_state(True):
                        self.state_manager.update_state(pump_on=True)
                        self.state_manager.record_watering()
                        self.last_pump_refresh = time.time()  # Reset the refresh timer
                        logger.info(f"[{current_time}] Pump activated successfully")
                    else:
                        logger.error(f"[{current_time}] Failed to activate pump!")
                        
                # Turn off pump when soil is WET - ALWAYS check this regardless of state changes
                elif not soil_is_dry and actual_pump_on:
                    logger.info(f"[{current_time}] Soil is WET! Turning off pump.")
                    if self.hardware.force_pump_state(False):
                        self.state_manager.update_state(pump_on=False)
                        logger.info(f"[{current_time}] Pump deactivated successfully")
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
            else:
                logger.warning(f"[{current_time}] DHT READING FAILED: Temperature or humidity is None")
                
        except Exception as e:
            logger.error(f"Error reading temperature: {e}")
        finally:
            # Schedule next check
            self.schedule_temperature_check()

    def send_status_update(self):
        """Send current system status."""
        try:
            if self.message_handler and hasattr(self.message_handler, 'send_status_update'):
                state = self.state_manager.get_state()
                self.message_handler.send_status_update(state)
            else:
                logger.warning("Cannot send status update - message handler not initialized")
        except Exception as e:
            logger.error(f"Error sending status update: {e}")

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
            
            logger.info("Cleanup complete")
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

def main():
    system = SmartIrrigationSystem()
    
    if system.initialize():
        system.run()
    else:
        logger.error("System initialization failed")
        system.cleanup()
        sys.exit(1)

if __name__ == "__main__":
    main() 