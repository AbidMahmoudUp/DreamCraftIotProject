import RPi.GPIO as GPIO
import time
import logging
import threading
from typing import Optional
from config.settings import (
    SOIL_SENSOR_PIN, PUMP_PIN, DHT_PIN, TEMP_MODULE_PIN, TEMP_THRESHOLD, 
    LDR_PIN, LED_PIN, VENTILATOR_PIN, VENTILATOR_TEMP_THRESHOLD,
    VENTILATOR_ON_TIME, VENTILATOR_OFF_TIME
)
from utils.logging_setup import logger

class HardwareManager:
    def __init__(self):
        self.pump_pin = PUMP_PIN  # GPIO21 for pump control
        self.soil_sensor_pin = SOIL_SENSOR_PIN  # GPIO4 for soil moisture sensor
        self.dht_pin = DHT_PIN  # GPIO20 for DHT11
        self.temp_module_pin = TEMP_MODULE_PIN  # GPIO16 for temperature-controlled module
        self.ldr_pin = LDR_PIN  # GPIO12 for LDR (Light Dependent Resistor)
        self.led_pin = LED_PIN  # GPIO24 for LED indicator
        self.ventilator_pin = VENTILATOR_PIN  # GPIO26 for ventilator/fan
        self.pump_pwm = None
        self.initialized = False
        self.temp_module_active = False
        self.last_ldr_value = 0 # Store the last read LDR value
        self.led_active = False # Track LED state
        
        # Ventilator control variables
        self.ventilator_active = False  # Current ventilator state
        self.ventilator_auto_mode = True  # Default to automatic mode
        self.ventilator_cycling = False  # Flag to track if cycling is active
        self.ventilator_cycle_thread = None  # Thread for ventilator cycling
        self.ventilator_timer = None  # Timer for ventilator cycling
        self.ventilator_stop_event = threading.Event()  # Event to stop the cycle
        
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
            
            # IMPORTANT: DHT pin (GPIO20) is managed exclusively by the adafruit_dht library
            # Do NOT configure it with GPIO.setup() as this will cause conflicts with the DHT sensor
            
            # Configure temperature module/ventilator pin (active HIGH)
            # Note: The same pin (16) is used for both temperature module and ventilator functions
            GPIO.setup(self.temp_module_pin, GPIO.OUT, initial=GPIO.LOW)  # Start with module/fan OFF (LOW)
            logger.info(f"Temperature module/ventilator pin {self.temp_module_pin} configured as output")
            
            # Configure LDR pin as input with pull-up resistor (not pull-down)
            # This is important as many LDR circuits output LOW when light is detected
            GPIO.setup(self.ldr_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            logger.info(f"LDR pin {self.ldr_pin} configured with PULL-UP resistor")
            
            # Configure LED pin as output
            GPIO.setup(self.led_pin, GPIO.OUT, initial=GPIO.LOW)  # Start with LED OFF
            
            # Configure ventilator pin as output
            GPIO.setup(self.ventilator_pin, GPIO.OUT, initial=GPIO.LOW)  # Start with ventilator OFF
            logger.info(f"Ventilator pin {self.ventilator_pin} configured as output")
            
            # Ensure pump is OFF at startup using inverted logic
            self.force_pump_state(False)  # Sets GPIO HIGH
            
            # Ensure temperature module/ventilator is OFF at startup
            self.set_temp_module(False)  # Sets GPIO LOW
            
            # Initialize LED to OFF
            self.set_led(False)
            
            # Since ventilator uses the same pin as temp module, we don't need separate initialization
            # But we still track its logical state for ventilator functionality
            self.ventilator_active = False
            
            # Read initial LDR value
            self.read_ldr()
            
            time.sleep(1)  # Wait for system to stabilize
            
            self.initialized = True
            logger.info(f"Hardware initialized successfully (PUMP_PIN={self.pump_pin}, SOIL_PIN={self.soil_sensor_pin}, DHT_PIN={self.dht_pin}, " + 
                       f"TEMP_MODULE_PIN={self.temp_module_pin}, LDR_PIN={self.ldr_pin}, LED_PIN={self.led_pin}, VENTILATOR_PIN={self.ventilator_pin})")
            return True
            
        except Exception as e:
            logger.error(f"Hardware initialization failed: {e}")
            self.cleanup()  # Clean up on failure
            return False
            
    def set_led(self, state_on: bool) -> bool:
        """
        Set the LED state.
        Args:
            state_on (bool): True to turn LED on, False to turn off
        Returns:
            bool: True if successful, False if failed
        """
        if not self.initialized:
            logger.warning("Cannot set LED - hardware not initialized")
            return False
            
        try:
            GPIO.output(self.led_pin, GPIO.HIGH if state_on else GPIO.LOW)
            
            # Verify the state change
            time.sleep(0.1)  # Small delay for stability
            actual_state = GPIO.input(self.led_pin) == GPIO.HIGH
            
            if actual_state == state_on:
                self.led_active = state_on
                logger.debug(f"LED set to {'ON' if state_on else 'OFF'}")
                return True
            else:
                logger.error(f"Failed to set LED - wanted {'ON' if state_on else 'OFF'}, got {'ON' if actual_state else 'OFF'}")
                return False
                
        except Exception as e:
            logger.error(f"Error setting LED state: {e}")
            return False
            
    def read_ldr_and_control_led(self) -> int:
        """
        Read the LDR and automatically control the LED based on light detection.
        INVERTED LOGIC: Turn LED ON when dark, OFF when light detected.
        
        With our fix for the LDR hardware:
        - LDR outputs 0 when light is detected
        - LDR outputs 1 when dark
        - We convert this to logical values where 1=light, 0=dark
        - Then we control the LED with inverted logic (ON when dark)
        
        Returns:
            int: 1 for light detected, 0 for darkness
        """
        # Get the processed LDR value (already inverted in read_ldr)
        # This gives us 1=light, 0=dark as the logical meaning
        ldr_value = self.read_ldr()
        
        # Use clean logic:
        # light_detected is true when ldr_value is 1
        # We want LED ON when NOT light_detected (when it's dark)
        light_detected = ldr_value == 1
        desired_led_state = not light_detected
        
        # Only change LED state if it doesn't match the desired state
        if self.led_active != desired_led_state:
            logger.info(f"Light {'detected' if light_detected else 'not detected'} - turning LED {'OFF' if light_detected else 'ON'}")
            self.set_led(desired_led_state)
            
        return ldr_value
            
    def read_ldr(self) -> int:
        """
        Read the LDR (Light Dependent Resistor) value.
        Returns:
            int: 1 for light detected, 0 for darkness
        """
        if not self.initialized:
            logger.warning("Cannot read LDR - hardware not initialized")
            return self.last_ldr_value
            
        try:
            # LDR debugging - we need to add more details about what's happening
            # Read the raw GPIO value
            raw_value = GPIO.input(self.ldr_pin)
            
            # Log detailed information for troubleshooting
            logger.info(f"LDR RAW GPIO VALUE: {raw_value} on pin {self.ldr_pin}")
            
            # Try to fix the issue - INVERT the reading if it's always true
            # This assumes the LDR is wired with pull-up instead of pull-down
            # (i.e., it outputs HIGH when dark, LOW when light)
            ldr_value = 1 if raw_value == 0 else 0  # Invert the reading
            
            self.last_ldr_value = ldr_value
            logger.info(f"LDR adjusted reading: {ldr_value} ({'light' if ldr_value == 1 else 'dark'})")
            return ldr_value
        except Exception as e:
            logger.error(f"Error reading LDR: {e}")
            return self.last_ldr_value
            
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
                        
                    # Turn off temperature module
                    GPIO.setup(self.temp_module_pin, GPIO.OUT)
                    GPIO.output(self.temp_module_pin, GPIO.LOW)  # LOW = OFF
                    
                    # Turn off LED
                    GPIO.setup(self.led_pin, GPIO.OUT)
                    GPIO.output(self.led_pin, GPIO.LOW)  # Turn off LED
                    
                    # Turn off ventilator and stop cycling
                    if self.ventilator_cycling:
                        self.stop_ventilator_cycling()
                    GPIO.setup(self.ventilator_pin, GPIO.OUT)
                    GPIO.output(self.ventilator_pin, GPIO.LOW)  # Turn off ventilator
                except Exception as e:
                    logger.warning(f"Non-critical error during hardware shutdown: {e}")
            
            # Now clean up GPIO
            try:
                GPIO.cleanup()
            except Exception as e:
                logger.warning(f"Non-critical error during GPIO cleanup: {e}")
            
            # Reset instance variables
            self.pump_pwm = None
            self.initialized = False
            self.temp_module_active = False
            self.led_active = False
            self.ventilator_active = False
            self.ventilator_cycling = False
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
    
    def set_temp_module(self, state_on: bool) -> bool:
        """
        Set the temperature module to a specific state.
        Note: Since temp module uses the same pin as ventilator,
        this method controls the same physical pin but tracks temp module state.
        
        Uses active HIGH logic (HIGH = ON, LOW = OFF).
        
        Args:
            state_on (bool): True to turn on, False to turn off
            
        Returns:
            bool: True if successful, False if failed
        """
        if not self.initialized:
            logger.warning("Cannot set temperature module state - hardware not initialized")
            return False
            
        try:
            # Temperature module uses active HIGH logic (opposite of pump)
            desired_gpio = GPIO.HIGH if state_on else GPIO.LOW
            logger.info(f"Setting temperature module {'ON' if state_on else 'OFF'} (GPIO: {desired_gpio})")
            
            # Set GPIO state
            GPIO.output(self.temp_module_pin, desired_gpio)
            
            # Read back to verify
            time.sleep(0.1)  # Short delay for stability
            read_state = GPIO.input(self.temp_module_pin) == GPIO.HIGH
            
            if read_state == state_on:
                # Update both temp module and ventilator state since they share the same pin
                self.temp_module_active = state_on
                self.ventilator_active = state_on  # Keep states in sync
                logger.info(f"Temperature module successfully set to {'ON' if state_on else 'OFF'}")
                return True
            else:
                logger.error(f"Failed to set temperature module - GPIO read {read_state}, expected {state_on}")
                return False
                
        except Exception as e:
            logger.error(f"Error setting temperature module state: {e}")
            return False
            
    def check_temperature_and_control_module(self, temperature: float) -> bool:
        """
        Check temperature and control the temperature module.
        Activates module when temperature exceeds threshold.
        
        Args:
            temperature (float): Current temperature in °C
            
        Returns:
            bool: True if the module state was changed, False otherwise
        """
        if not self.initialized:
            logger.warning("Cannot control temperature module - hardware not initialized")
            return False
            
        try:
            # Determine if module should be active
            should_be_active = temperature > TEMP_THRESHOLD
            
            # Only change state if needed
            if should_be_active != self.temp_module_active:
                logger.info(f"Temperature {temperature}°C {'exceeds' if should_be_active else 'is below'} threshold ({TEMP_THRESHOLD}°C)")
                result = self.set_temp_module(should_be_active)
                return result
            return False  # No change needed
            
        except Exception as e:
            logger.error(f"Error in temperature module control: {e}")
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
                
    def test_temp_module(self) -> bool:
        """Test the temperature module by cycling it ON and OFF."""
        if not self.initialized:
            logger.warning("Cannot test temperature module - not initialized")
            return False
            
        try:
            logger.info("Starting temperature module test...")
            
            # Turn module on
            logger.info("Testing temperature module ON...")
            if not self.set_temp_module(True):
                logger.error("Failed to turn temperature module ON during test")
                return False
                
            time.sleep(1)  # Keep on for 1 second
            
            # Turn module off
            logger.info("Testing temperature module OFF...")
            if not self.set_temp_module(False):
                logger.error("Failed to turn temperature module OFF during test")
                return False
                
            logger.info("Temperature module test passed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Temperature module test failed with error: {e}")
            return False
        finally:
            # Ensure module is off after test
            if self.initialized:
                GPIO.output(self.temp_module_pin, GPIO.LOW)  # LOW = OFF
            
    def test_ventilator(self) -> bool:
        """Test the ventilator by cycling it ON and OFF."""
        if not self.initialized:
            logger.warning("Cannot test ventilator - not initialized")
            return False
            
        try:
            logger.info("Starting ventilator test...")
            
            # Turn ventilator on
            logger.info("Testing ventilator ON...")
            if not self.set_ventilator(True):
                logger.error("Failed to turn ventilator ON during test")
                return False
                
            time.sleep(2)  # Keep on for 2 seconds
            
            # Turn ventilator off
            logger.info("Testing ventilator OFF...")
            if not self.set_ventilator(False):
                logger.error("Failed to turn ventilator OFF during test")
                return False
                
            logger.info("Ventilator test passed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Ventilator test failed with error: {e}")
            return False
        finally:
            # Ensure ventilator is off after test
            if self.initialized:
                GPIO.output(self.ventilator_pin, GPIO.LOW)
    
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
            
            # Test temperature module
            if not self.test_temp_module():
                logger.error("Temperature module test failed")
                return False
                
            # Test soil moisture sensor
            soil_moisture = self.read_soil_moisture()
            logger.info(f"Soil moisture reading: {'DRY' if soil_moisture else 'WET'}")
            
            # Test LDR sensor
            ldr_value = self.read_ldr()
            logger.info(f"LDR reading: {'light' if ldr_value == 1 else 'dark'}")
            
            # Test LED
            logger.info("Testing LED...")
            self.set_led(True)
            time.sleep(1)
            self.set_led(False)
            logger.info("LED test completed")
            
            # Test ventilator
            logger.info("Testing ventilator...")
            if not self.test_ventilator():
                logger.error("Ventilator test failed")
                return False
            
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

    def set_ventilator(self, state_on: bool) -> bool:
        """
        Set the ventilator state.
        Note: Since ventilator uses the same pin as temperature module,
        this method controls the same physical pin but tracks ventilator state.
        
        Args:
            state_on (bool): True to turn ventilator on, False to turn off
        Returns:
            bool: True if successful, False if failed
        """
        if not self.initialized:
            logger.warning("Cannot set ventilator - hardware not initialized")
            return False
            
        try:
            # Set the pin state (ventilator and temp module share the same pin)
            GPIO.output(self.ventilator_pin, GPIO.HIGH if state_on else GPIO.LOW)
            
            # Verify the state change
            time.sleep(0.1)  # Small delay for stability
            actual_state = GPIO.input(self.ventilator_pin) == GPIO.HIGH
            
            if actual_state == state_on:
                # Update both ventilator and temp module state since they share the same pin
                self.ventilator_active = state_on
                self.temp_module_active = state_on  # Keep states in sync
                logger.info(f"Ventilator (pin {self.ventilator_pin}) set to {'ON' if state_on else 'OFF'}")
                return True
            else:
                logger.error(f"Failed to set ventilator - wanted {'ON' if state_on else 'OFF'}, got {'ON' if actual_state else 'OFF'}")
                return False
                
        except Exception as e:
            logger.error(f"Error setting ventilator state: {e}")
            return False
    
    def set_ventilator_mode(self, auto_mode: bool) -> bool:
        """
        Set the ventilator control mode.
        Args:
            auto_mode (bool): True for automatic mode, False for manual mode
        Returns:
            bool: True if successful, False if failed
        """
        try:
            previous_mode = self.ventilator_auto_mode
            self.ventilator_auto_mode = auto_mode
            
            if previous_mode != auto_mode:
                mode_name = "AUTOMATIC" if auto_mode else "MANUAL"
                logger.info(f"Ventilator mode changed to {mode_name}")
                
                # If changing to manual mode, stop any active cycling
                if not auto_mode and self.ventilator_cycling:
                    self.stop_ventilator_cycling()
            
            return True
        except Exception as e:
            logger.error(f"Error setting ventilator mode: {e}")
            return False
    
    def check_temperature_and_control_ventilator(self, temperature: float) -> bool:
        """
        Check temperature and control the ventilator if in automatic mode.
        Args:
            temperature (float): Current temperature in °C
        Returns:
            bool: True if ventilator state was changed, False otherwise
        """
        if not self.initialized:
            logger.warning("Cannot control ventilator - hardware not initialized")
            return False
            
        if not self.ventilator_auto_mode:
            logger.debug("Ventilator in manual mode - not controlling based on temperature")
            return False
            
        try:
            # Determine if ventilator should be active based on temperature
            should_be_active = temperature >= VENTILATOR_TEMP_THRESHOLD
            
            # If temperature is above threshold and ventilator cycling is not active, start cycling
            if should_be_active and not self.ventilator_cycling:
                logger.info(f"Temperature {temperature}°C exceeds threshold ({VENTILATOR_TEMP_THRESHOLD}°C) - starting ventilator cycling")
                self.start_ventilator_cycling()
                return True
            # If temperature is below threshold and ventilator cycling is active, stop cycling
            elif not should_be_active and self.ventilator_cycling:
                logger.info(f"Temperature {temperature}°C is below threshold ({VENTILATOR_TEMP_THRESHOLD}°C) - stopping ventilator cycling")
                self.stop_ventilator_cycling()
                return True
                
            return False  # No change needed
            
        except Exception as e:
            logger.error(f"Error in ventilator temperature control: {e}")
            return False
    
    def start_ventilator_cycling(self) -> bool:
        """
        Start the ventilator cycling (20 seconds ON, 10 seconds OFF).
        Returns:
            bool: True if cycling started successfully, False if failed
        """
        if self.ventilator_cycling:
            logger.debug("Ventilator cycling already active")
            return True
            
        try:
            # Reset stop event
            self.ventilator_stop_event.clear()
            
            # Start cycling in a separate thread
            self.ventilator_cycle_thread = threading.Thread(
                target=self._ventilator_cycle_worker, 
                daemon=True
            )
            self.ventilator_cycling = True
            self.ventilator_cycle_thread.start()
            logger.info("Ventilator cycling started")
            return True
        except Exception as e:
            logger.error(f"Error starting ventilator cycling: {e}")
            self.ventilator_cycling = False
            return False
    
    def stop_ventilator_cycling(self) -> bool:
        """
        Stop the ventilator cycling and turn ventilator off.
        Returns:
            bool: True if cycling stopped successfully, False if failed
        """
        if not self.ventilator_cycling:
            logger.debug("Ventilator cycling not active")
            return True
            
        try:
            # Signal the cycling thread to stop
            self.ventilator_stop_event.set()
            
            # Turn off ventilator
            self.set_ventilator(False)
            
            # Wait for thread to finish (with timeout)
            if self.ventilator_cycle_thread and self.ventilator_cycle_thread.is_alive():
                self.ventilator_cycle_thread.join(timeout=2.0)
                
            self.ventilator_cycling = False
            logger.info("Ventilator cycling stopped")
            return True
        except Exception as e:
            logger.error(f"Error stopping ventilator cycling: {e}")
            return False
    
    def _ventilator_cycle_worker(self):
        """Worker thread for ventilator cycling."""
        logger.info("Ventilator cycle worker started")
        
        try:
            while not self.ventilator_stop_event.is_set():
                # Turn ventilator ON for 20 seconds
                logger.info("Ventilator cycle: turning ON for 20 seconds")
                self.set_ventilator(True)
                
                # Wait for 20 seconds or until stop event, checking every second
                for _ in range(VENTILATOR_ON_TIME):
                    if self.ventilator_stop_event.is_set():
                        break
                    time.sleep(1)
                
                if self.ventilator_stop_event.is_set():
                    break
                
                # Turn ventilator OFF for 10 seconds
                logger.info("Ventilator cycle: turning OFF for 10 seconds")
                self.set_ventilator(False)
                
                # Wait for 10 seconds or until stop event, checking every second
                for _ in range(VENTILATOR_OFF_TIME):
                    if self.ventilator_stop_event.is_set():
                        break
                    time.sleep(1)
        except Exception as e:
            logger.error(f"Error in ventilator cycle worker: {e}")
        finally:
            # Ensure ventilator is off when thread exits
            try:
                self.set_ventilator(False)
            except:
                pass
            self.ventilator_cycling = False
            logger.info("Ventilator cycle worker finished") 