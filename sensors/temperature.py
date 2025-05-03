import time
import board
import adafruit_dht
import RPi.GPIO as GPIO
from typing import Tuple, Optional
from config.settings import DHT_PIN
from utils.logging_setup import logger

class DHTSensor:
    def __init__(self, pin=None):
        """Initialize DHT11 sensor with robust error handling."""
        # Store pin information
        self.pin_obj = pin if pin is not None else board.D20
        self.pin_number = DHT_PIN  # Numeric pin value (e.g., 20)
        
        # Status tracking
        self.temp_history = []
        self.humidity_history = []
        self.last_valid_temp = None
        self.last_valid_humidity = None
        self.enabled = True
        self.status = "initializing"
        self.consecutive_failures = 0
        self.last_successful_read = 0
        
        # Attempt to initialize the sensor
        logger.info(f"Initializing DHT11 sensor on pin {self.pin_number}")
        self._initialize_sensor()
        
        # Force an initial reading to verify sensor
        self._force_initial_reading()
        
    def _initialize_sensor(self):
        """Initialize or reinitialize the DHT device."""
        try:
            # Clean up any previous instance
            if hasattr(self, 'dht_device') and self.dht_device is not None:
                try:
                    self.dht_device.exit()
                    time.sleep(0.5)
                except:
                    pass
            
            # Make sure to release and clean up the GPIO pin before initializing
            try:
                GPIO.cleanup(self.pin_number)
            except:
                pass
                
            # Wait for the GPIO system to stabilize
            time.sleep(1)
            
            # Create a new instance with use_pulseio=False for better compatibility
            self.dht_device = adafruit_dht.DHT11(self.pin_obj, use_pulseio=False)
            
            # Allow time for the sensor to stabilize after initialization
            time.sleep(2)
            
            logger.info("DHT11 sensor initialized successfully")
            self.status = "initialized"
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize DHT11 sensor: {e}")
            self.dht_device = None
            self.status = "error"
            return False
    
    def _force_initial_reading(self):
        """Force an initial reading to verify the sensor is working."""
        for attempt in range(3):
            try:
                logger.info(f"Performing initial DHT11 test read (attempt {attempt+1}/3)")
                
                # Add a delay between attempts
                if attempt > 0:
                    time.sleep(2)
                    
                # Try to read from the sensor
                if self.dht_device:
                    temperature = self.dht_device.temperature
                    humidity = self.dht_device.humidity
                    
                    if temperature is not None and humidity is not None:
                        logger.info(f"Initial DHT11 reading successful: {temperature}°C, {humidity}%")
                        
                        # Store the initial values
                        self.last_valid_temp = temperature
                        self.last_valid_humidity = humidity
                        self.temp_history = [temperature]
                        self.humidity_history = [humidity]
                        self.status = "active"
                        self.last_successful_read = time.time()
                        return True
            except Exception as e:
                logger.warning(f"Initial DHT11 reading failed (attempt {attempt+1}): {e}")
                
        # If we reach here, all initial reading attempts failed
        logger.error("Failed to get initial reading from DHT11 sensor after multiple attempts")
        return False
    
    def read(self) -> Tuple[Optional[float], Optional[float]]:
        """Read temperature and humidity with advanced error handling."""
        if not self.enabled:
            logger.debug("DHT sensor is disabled")
            self.status = "disabled"
            return self.last_valid_temp, self.last_valid_humidity
            
        if not self.dht_device:
            # Try to re-initialize if we don't have a device
            if not self._initialize_sensor():
                return self.last_valid_temp, self.last_valid_humidity
        
        # Check if we've had a successful read recently (within 30 seconds)
        current_time = time.time()
        recent_successful_read = (current_time - self.last_successful_read) < 30
        
        # If we've had too many consecutive failures and no recent success, reinitialize
        if self.consecutive_failures >= 5 and not recent_successful_read:
            logger.warning(f"Too many consecutive DHT11 read failures ({self.consecutive_failures}). Reinitializing sensor...")
            self._initialize_sensor()
            self.consecutive_failures = 0
        
        # Try to read from the sensor with multiple attempts
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                # Delay between attempts
                if attempt > 0:
                    time.sleep(1)
                
                temperature = self.dht_device.temperature
                humidity = self.dht_device.humidity
                
                if temperature is not None and humidity is not None:
                    logger.info(f"DHT11 reading successful: {temperature}°C, {humidity}%")
                    self.status = "active"
                    self.consecutive_failures = 0
                    self.last_successful_read = current_time
                    
                    # Check for unrealistic values
                    if temperature < -10 or temperature > 60 or humidity < 0 or humidity > 100:
                        logger.warning(f"DHT11 reported unrealistic values: {temperature}°C, {humidity}%. Using previous values.")
                        return self.last_valid_temp, self.last_valid_humidity
                    
                    # Add to history for smoothing (up to 3 readings)
                    self.temp_history.append(temperature)
                    self.humidity_history.append(humidity)
                    if len(self.temp_history) > 3:
                        self.temp_history.pop(0)
                    if len(self.humidity_history) > 3:
                        self.humidity_history.pop(0)
                    
                    # Calculate smoothed values
                    smoothed_temp = sum(self.temp_history) / len(self.temp_history)
                    smoothed_humidity = sum(self.humidity_history) / len(self.humidity_history)
                    
                    # Update last valid values
                    self.last_valid_temp = smoothed_temp
                    self.last_valid_humidity = smoothed_humidity
                    
                    return smoothed_temp, smoothed_humidity
                
                logger.debug(f"DHT11 returned None values on attempt {attempt+1}")
                time.sleep(1)
                
            except Exception as e:
                logger.debug(f"DHT11 read failed on attempt {attempt+1}: {e}")
                time.sleep(1)
        
        # If we get here, all attempts failed
        self.consecutive_failures += 1
        logger.warning(f"All DHT11 read attempts failed. Consecutive failures: {self.consecutive_failures}")
        
        # On 10 consecutive failures, try to completely reinitialize
        if self.consecutive_failures >= 10:
            logger.error("DHT11 sensor appears to be completely unresponsive after many attempts")
            self.status = "error"
            self._initialize_sensor()
            self.consecutive_failures = 0
        
        return self.last_valid_temp, self.last_valid_humidity
        
    def cleanup(self):
        """Clean up DHT sensor resources."""
        try:
            if self.dht_device:
                self.dht_device.exit()
            
            # Attempt to clean up the GPIO pin
            try:
                GPIO.cleanup(self.pin_number)
            except:
                pass
                
            logger.info("DHT11 sensor cleanup completed")
        except Exception as e:
            logger.error(f"Error during DHT11 cleanup: {e}") 