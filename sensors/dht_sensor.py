import Adafruit_DHT
import time
from config.settings import DHT_SENSOR_PIN
from utils.logging_setup import logger

class DHTSensor:
    def __init__(self, sensor_type=Adafruit_DHT.DHT11):
        """
        Initialize the DHT sensor.
        Args:
            sensor_type: Type of DHT sensor (default DHT11)
        """
        self.sensor = sensor_type
        self.pin = DHT_SENSOR_PIN
        self.last_temp = None
        self.last_humidity = None
        self.last_read_time = 0
        self.read_interval = 2  # Minimum seconds between reads
        logger.info("DHT sensor initialized")

    def read_sensor(self):
        """
        Read temperature and humidity from DHT sensor.
        Returns:
            tuple: (temperature, humidity) or (None, None) on failure
        """
        current_time = time.time()
        
        # Respect minimum read interval to prevent sensor overload
        if current_time - self.last_read_time < self.read_interval:
            return self.last_temp, self.last_humidity

        try:
            humidity, temperature = Adafruit_DHT.read_retry(self.sensor, self.pin, retries=3)
            
            if humidity is not None and temperature is not None:
                # Round to 1 decimal place for better readability
                temperature = round(temperature, 1)
                humidity = round(humidity, 1)
                
                # Update last known good values
                self.last_temp = temperature
                self.last_humidity = humidity
                self.last_read_time = current_time
                
                logger.debug(f"DHT reading - Temp: {temperature}°C, Humidity: {humidity}%")
                return temperature, humidity
            else:
                logger.warning("Failed to get reading from DHT sensor")
                return self.last_temp, self.last_humidity  # Return last known good values
                
        except Exception as e:
            logger.error(f"Error reading DHT sensor: {e}")
            return self.last_temp, self.last_humidity  # Return last known good values

    def get_temperature(self):
        """Get temperature reading only."""
        temp, _ = self.read_sensor()
        return temp

    def get_humidity(self):
        """Get humidity reading only."""
        _, humidity = self.read_sensor()
        return humidity

    def get_formatted_data(self):
        """
        Get formatted sensor data as a dictionary.
        Returns:
            dict: Formatted sensor data with temperature and humidity
        """
        temp, humidity = self.read_sensor()
        return {
            "temperature": temp if temp is not None else "N/A",
            "humidity": humidity if humidity is not None else "N/A"
        } 