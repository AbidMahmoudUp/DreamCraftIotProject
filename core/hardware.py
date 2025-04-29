import RPi.GPIO as GPIO
import time
import logging
from typing import Optional
from config.settings import SOIL_SENSOR_PIN, PUMP_PIN, DHT_PIN
from utils.logging_setup import logger

class HardwareManager:
    def __init__(self):
        self.pump_pin = PUMP_PIN  # GPIO21 for pump control
        self.soil_sensor_pin = SOIL_SENSOR_PIN  # GPIO4 for soil moisture sensor
        self.dht_pin = DHT_PIN  # GPIO20 for DHT11
        self.pump_pwm = None
        self.initialized = False
        
    def initialize(self) -> bool:
        """Initialize GPIO and hardware components."""
        try:
            if self.initialized:
                logger.info("Hardware already initialized")
                return True
                
            # Clean up any existing GPIO setup
            try:
                if self.pump_pwm:
                    self.pump_pwm.stop()
                GPIO.cleanup()
            except:
                pass
                
            # Set up GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            
            # Configure pump pin with PWM and inverted logic (active LOW)
            GPIO.setup(self.pump_pin, GPIO.OUT, initial=GPIO.HIGH)  # Start with pump OFF (HIGH)
            self.pump_pwm = GPIO.PWM(self.pump_pin, 100)  # 100 Hz
            self.pump_pwm.start(100)  # Start with pump OFF (100% duty cycle)
            
            # Configure soil sensor pin with pull-down
            GPIO.setup(self.soil_sensor_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            
            # Configure DHT pin
            GPIO.setup(self.dht_pin, GPIO.OUT)
            
            # Ensure pump is OFF at startup using inverted logic
            self.force_pump_state(False)  # Sets GPIO HIGH
            time.sleep(1)  # Wait for system to stabilize
            
            self.initialized = True
            logger.info(f"Hardware initialized successfully (PUMP_PIN={self.pump_pin}, SOIL_PIN={self.soil_sensor_pin}, DHT_PIN={self.dht_pin})")
            return True
            
        except Exception as e:
            logger.error(f"Hardware initialization failed: {e}")
            self.cleanup()  # Clean up on failure
            return False
            
    def cleanup(self):
        """Clean up GPIO resources."""
        try:
            # First, try to turn off the pump if GPIO is still set up
            if self.initialized:
                try:
                    # Ensure pump pin is still configured as output
                    GPIO.setup(self.pump_pin, GPIO.OUT)
                    # Turn off pump using both GPIO and PWM
                    GPIO.output(self.pump_pin, GPIO.HIGH)  # HIGH = OFF
                    if self.pump_pwm:
                        self.pump_pwm.ChangeDutyCycle(100)  # 100% = OFF
                        time.sleep(0.5)  # Wait for pump to fully stop
                        self.pump_pwm.stop()
                except Exception as e:
                    logger.warning(f"Non-critical error during pump shutdown: {e}")
            
            # Now clean up GPIO
            try:
                GPIO.cleanup()
            except Exception as e:
                logger.warning(f"Non-critical error during GPIO cleanup: {e}")
            
            # Reset instance variables
            self.pump_pwm = None
            self.initialized = False
            logger.info("Hardware cleanup completed")
            
        except Exception as e:
            logger.error(f"Error during hardware cleanup: {e}")
            
    def force_pump_state(self, state_on: bool, max_retries: int = 3) -> bool:
        """Force the pump to a specific state with retries."""
        if not self.initialized:
            logger.warning("Cannot set pump state - hardware not initialized")
            return False
            
        # Use inverted logic: LOW = ON, HIGH = OFF (exactly like in aza.py)
        desired_gpio = GPIO.LOW if state_on else GPIO.HIGH
        logger.info(f"Forcing pump {'ON' if state_on else 'OFF'} (desired GPIO: {desired_gpio})")
        
        for attempt in range(max_retries):
            try:
                # First, ensure PWM is in correct state - FOLLOW EXACT SEQUENCE FROM AZA.PY
                if state_on:
                    self.pump_pwm.ChangeDutyCycle(0)  # 0% = ON
                else:
                    self.pump_pwm.ChangeDutyCycle(100)  # 100% = OFF
                time.sleep(0.2)  # Let PWM stabilize - CRITICAL DELAY
                
                # Then set GPIO state
                logger.info(f"Setting GPIO to {desired_gpio}")
                GPIO.output(self.pump_pin, desired_gpio)
                time.sleep(0.2)  # Let GPIO settle - CRITICAL DELAY
                
                # Read back the state multiple times to ensure stability
                states = []
                for _ in range(3):
                    states.append(GPIO.input(self.pump_pin))
                    time.sleep(0.1)
                
                logger.info(f"Read back states: {states}")
                
                # Check if all readings match desired state
                if all(state == desired_gpio for state in states):
                    logger.info(f"Successfully set pump {'ON' if state_on else 'OFF'} (GPIO: {desired_gpio})")
                    return True
                else:
                    logger.warning(f"Inconsistent GPIO states on attempt {attempt + 1}. "
                             f"Desired: {desired_gpio}, Got: {states}")
                    
            except Exception as e:
                logger.error(f"Error setting pump state on attempt {attempt + 1}: {e}")
                
            # Longer wait before retry
            time.sleep(1.0)
        
        logger.error(f"Failed to set pump state after {max_retries} attempts")
        return False
            
    def test_pump_hardware(self) -> bool:
        """Test the pump hardware by cycling it ON and OFF."""
        if not self.initialized:
            logger.warning("Cannot test pump hardware - not initialized")
            return False
            
        try:
            logger.info("Starting pump hardware test...")
            
            # Initial state verification
            initial_state = GPIO.input(self.pump_pin)
            logger.info(f"Initial GPIO state: {initial_state}")
            
            # Turn pump on
            logger.info("Testing pump ON...")
            if not self.force_pump_state(True):
                logger.error("Failed to turn pump ON during hardware test")
                self.force_pump_state(False)
                return False
                
            time.sleep(1)  # Run for 1 second
            
            # Turn pump off
            logger.info("Testing pump OFF...")
            if not self.force_pump_state(False):
                logger.error("Failed to turn pump OFF during hardware test")
                return False
                
            time.sleep(1)  # Wait for pump to fully stop
            
            # Final state verification
            final_state = GPIO.input(self.pump_pin)
            logger.info(f"Final GPIO state: {final_state}")
            
            if final_state != GPIO.HIGH:  # HIGH = OFF
                logger.error(f"Unexpected final state: {final_state}")
                return False
                
            logger.info("Pump hardware test passed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Pump hardware test failed with error: {e}")
            return False
        finally:
            # Ensure pump is off after test
            if self.initialized:
                if self.pump_pwm:
                    self.pump_pwm.ChangeDutyCycle(100)  # 100% = OFF
                GPIO.output(self.pump_pin, GPIO.HIGH)  # HIGH = OFF
                time.sleep(0.5)  # Wait for pump to fully stop
            
    def test_hardware(self, *args) -> bool:
        """
        Test all hardware components.
        Args:
            *args: Additional arguments that might be passed
        Returns:
            bool: True if all hardware tests pass
        """
        try:
            logger.info("Testing all hardware components...")
            
            # Initialize hardware if not already initialized
            if not self.initialized:
                logger.info("Hardware not initialized, initializing now...")
                if not self.initialize():
                    logger.error("Failed to initialize hardware")
                    return False
            
            # Test pump hardware
            if not self.test_pump_hardware():
                logger.error("Pump hardware test failed")
                return False
                
            # Test soil moisture sensor
            soil_moisture = self.read_soil_moisture()
            logger.info(f"Soil moisture reading: {'DRY' if soil_moisture else 'WET'}")
            
            # DHT sensor is tested separately in the temperature sensor class
            
            logger.info("All hardware tests passed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Hardware test failed: {e}")
            return False
            
    def read_soil_moisture(self) -> bool:
        """Read the soil moisture sensor state."""
        if not self.initialized:
            logger.warning("Cannot read soil moisture - hardware not initialized")
            return False
            
        try:
            return GPIO.input(self.soil_sensor_pin) == GPIO.HIGH
        except Exception as e:
            logger.error(f"Error reading soil moisture: {e}")
            return False
            
    def get_dht_pin(self) -> int:
        """Get the DHT sensor pin number."""
        return self.dht_pin 