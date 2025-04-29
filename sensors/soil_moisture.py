import RPi.GPIO as GPIO
import time
from config.settings import SOIL_SENSOR_PIN
from utils.logging_setup import logger

class SoilMoistureSensor:
    def __init__(self, pump_controller=None):
        """
        Initialize the soil moisture sensor.
        Args:
            pump_controller: Reference to pump controller for direct control
        """
        self.pump_controller = pump_controller
        GPIO.setup(SOIL_SENSOR_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        self.last_state = None
        logger.info("Soil moisture sensor initialized")

    def read_and_control(self, auto_mode=True):
        """
        Read sensor and control pump if in auto mode.
        Args:
            auto_mode: Whether to automatically control pump
        Returns:
            bool: True if soil is dry, False if wet
        """
        try:
            # Read sensor (HIGH = dry, LOW = wet)
            is_dry = GPIO.input(SOIL_SENSOR_PIN) == GPIO.HIGH
            state_changed = self.last_state != is_dry
            self.last_state = is_dry
            
            if state_changed:
                logger.info(f"Soil moisture changed: {'DRY' if is_dry else 'WET'}")
            
            # Control pump in auto mode
            if auto_mode and self.pump_controller:
                if is_dry and not self.pump_controller.is_active():
                    logger.info("Soil is dry - activating pump")
                    self.pump_controller.force_pump_state(True)
                elif not is_dry and self.pump_controller.is_active():
                    logger.info("Soil is wet - deactivating pump")
                    self.pump_controller.force_pump_state(False)
                    
            return is_dry
            
        except Exception as e:
            logger.error(f"Error reading soil moisture sensor: {e}")
            if self.pump_controller:
                self.pump_controller.force_pump_state(False)  # Safety measure
            return False  # Default to wet (safe) state on error

    def is_soil_dry(self):
        """Simple read without pump control."""
        try:
            return GPIO.input(SOIL_SENSOR_PIN) == GPIO.HIGH
        except Exception as e:
            logger.error(f"Error reading soil moisture sensor: {e}")
            return False

    def cleanup(self):
        """Clean up GPIO resources."""
        try:
            if self.pump_controller:
                self.pump_controller.force_pump_state(False)  # Ensure pump is off
            GPIO.cleanup(SOIL_SENSOR_PIN)
            logger.info("Soil moisture sensor cleanup completed")
        except Exception as e:
            logger.error(f"Error during soil moisture sensor cleanup: {e}") 