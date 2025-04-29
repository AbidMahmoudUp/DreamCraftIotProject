import time
import board
import adafruit_dht
from typing import Tuple, Optional
from config.settings import DHT_PIN
from utils.logging_setup import logger

class DHTSensor:
    def __init__(self, pin=None):
        """Initialize DHT11 sensor."""
        try:
            # Use the specified pin or DHT_PIN from settings
            pin_to_use = pin if pin is not None else board.D20  # Use D20 (DHT_PIN)
            logger.info(f"Initializing DHT11 sensor on pin {DHT_PIN} (D20)")
            self.dht_device = adafruit_dht.DHT11(pin_to_use, use_pulseio=False)
            self.temp_history = []  # Store last 5 successful readings
            self.last_valid_temp = None
            self.last_valid_humidity = None
            self.unstable = False
            self.unstable_start_time = 0
            self.enabled = True
            self.status = "initializing"
            logger.info("DHT11 sensor initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize DHT11: {e}")
            self.dht_device = None
            self.enabled = False
            self.status = "error"
            
    def read(self) -> Tuple[Optional[float], Optional[float]]:
        """
        Read temperature and humidity from DHT11 sensor.
        Returns:
            Tuple[Optional[float], Optional[float]]: (temperature, humidity) or (None, None) if read fails
        """
        if not self.enabled or not self.dht_device:
            self.status = "disabled"
            return None, None
            
        # Handle unstable sensor by attempting recovery after cooldown
        if self.unstable:
            current_time = time.time()
            if current_time - self.unstable_start_time > 300:  # After 5 minutes, try again
                logger.info("Attempting to recover DHT11 sensor after cooldown period...")
                self.unstable = False
                self.status = "recovering"
                try:
                    self.dht_device.exit()
                    time.sleep(2)
                    self.dht_device = adafruit_dht.DHT11(board.D20, use_pulseio=False)  # Use D20 (DHT_PIN)
                except:
                    pass
            else:
                return self.last_valid_temp, self.last_valid_humidity
                
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                temperature = self.dht_device.temperature
                humidity = self.dht_device.humidity
                
                if temperature is not None and humidity is not None:
                    logger.debug(f"Temperature: {temperature}°C, Humidity: {humidity}%")
                    self.status = "active"
                    
                    # Add to history for smoothing
                    self.temp_history.append(temperature)
                    if len(self.temp_history) > 5:
                        self.temp_history.pop(0)
                        
                    # Calculate smoothed temperature
                    self.last_valid_temp = sum(self.temp_history) / len(self.temp_history)
                    self.last_valid_humidity = humidity
                    
                    return self.last_valid_temp, self.last_valid_humidity
                    
                time.sleep(2)
                
            except Exception as e:
                logger.debug(f"DHT11 read attempt {attempt+1}/{max_attempts} failed: {e}")
                time.sleep(2)
                
        # Track consecutive failures
        self.consecutive_failures = getattr(self, 'consecutive_failures', 0) + 1
        
        if self.consecutive_failures >= 10:
            logger.error("DHT11 sensor appears to be unstable. Marking as unusable temporarily.")
            self.unstable = True
            self.unstable_start_time = time.time()
            self.status = "unstable"
            self.consecutive_failures = 0
            
            try:
                self.dht_device.exit()
                time.sleep(2)
                self.dht_device = adafruit_dht.DHT11(board.D20, use_pulseio=False)  # Use D20 (DHT_PIN)
            except:
                pass
                
        return self.last_valid_temp, self.last_valid_humidity
        
    def cleanup(self):
        """Clean up DHT sensor resources."""
        try:
            if self.dht_device:
                self.dht_device.exit()
            logger.info("DHT11 sensor cleanup completed")
        except Exception as e:
            logger.error(f"Error during DHT11 cleanup: {e}") 