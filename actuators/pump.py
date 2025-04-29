import RPi.GPIO as GPIO
import time
from config.settings import PUMP_PIN, PUMP_REFRESH_INTERVAL
from utils.logging_setup import logger

class WaterPump:
    def __init__(self):
        """Initialize the water pump control with inverted logic (active LOW)."""
        # Ensure GPIO is properly set up
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        
        # Configure pump pin with inverted logic (active LOW)
        GPIO.setup(PUMP_PIN, GPIO.OUT, initial=GPIO.HIGH)  # Start with pump OFF (HIGH)
        
        # Initialize PWM for pump control
        self.pump_pwm = GPIO.PWM(PUMP_PIN, 100)  # 100Hz frequency
        self.pump_pwm.start(100)  # Start with 100% duty cycle (OFF)
        self.last_refresh = 0
        logger.info(f"Water pump initialized on pin {PUMP_PIN} (active LOW)")

    def force_pump_state(self, state_on, max_retries=3):
        """
        Force the pump to a specific state with verification.
        Args:
            state_on (bool): True to turn pump on, False to turn off
            max_retries (int): Maximum number of attempts to set the state
        Returns:
            bool: True if successful, False if failed
        """
        # Use inverted logic: LOW = ON, HIGH = OFF
        desired_gpio = GPIO.LOW if state_on else GPIO.HIGH
        logger.info(f"Forcing pump {'ON' if state_on else 'OFF'} (GPIO: {'LOW' if state_on else 'HIGH'})")

        for attempt in range(max_retries):
            try:
                # Set PWM state first with full power
                if state_on:
                    # If turning ON, use 0% duty cycle (full power)
                    self.pump_pwm.ChangeDutyCycle(0)  # 0% = ON (full power)
                    time.sleep(0.1)  # Let PWM stabilize
                    
                    # Then force GPIO LOW for maximum power
                    GPIO.output(PUMP_PIN, GPIO.LOW)
                    logger.info(f"Pump set to ON - PWM=0%, GPIO=LOW (maximum power)")
                else:
                    # If turning OFF, use 100% duty cycle
                    self.pump_pwm.ChangeDutyCycle(100)  # 100% = OFF
                    time.sleep(0.1)  # Let PWM stabilize
                    
                    # Then force GPIO HIGH
                    GPIO.output(PUMP_PIN, GPIO.HIGH)
                    logger.info(f"Pump set to OFF - PWM=100%, GPIO=HIGH")

                # Verify state multiple times
                time.sleep(0.2)  # Let GPIO settle
                states = [GPIO.input(PUMP_PIN) for _ in range(3)]
                
                if all(state == desired_gpio for state in states):
                    logger.info(f"Pump state verified: {'ON' if state_on else 'OFF'} (GPIO: {desired_gpio})")
                    return True

                logger.warning(f"Inconsistent GPIO states on attempt {attempt + 1}: {states}, desired: {desired_gpio}")
            except Exception as e:
                logger.error(f"Error setting pump state on attempt {attempt + 1}: {e}")

            time.sleep(1.0)  # Wait before retry

        logger.error(f"Failed to set pump state after {max_retries} attempts")
        return False

    def refresh_pump_state(self, is_active):
        """
        Refresh the pump state if it's supposed to be active.
        Args:
            is_active (bool): Whether the pump should be active
        """
        current_time = time.time()
        if is_active and current_time - self.last_refresh >= PUMP_REFRESH_INTERVAL:
            logger.debug("Refreshing pump ON state")
            # Direct GPIO control for maximum power
            GPIO.output(PUMP_PIN, GPIO.LOW)
            # Also set PWM to 0% for maximum power
            self.pump_pwm.ChangeDutyCycle(0)
            self.last_refresh = current_time

    def test_pump(self, run_time=5):
        """
        Test the pump with maximum power for a specified time.
        Args:
            run_time (int): Time in seconds to run the pump
        Returns:
            bool: True if test completed, False if error
        """
        try:
            logger.info(f"Testing pump with maximum power for {run_time} seconds...")
            
            # Turn pump ON with maximum power
            GPIO.output(PUMP_PIN, GPIO.LOW)
            self.pump_pwm.ChangeDutyCycle(0)  # 0% = full power
            logger.info("Pump turned ON with maximum power")
            
            # Run for specified time
            time.sleep(run_time)
            
            # Turn pump OFF
            GPIO.output(PUMP_PIN, GPIO.HIGH)
            self.pump_pwm.ChangeDutyCycle(100)
            logger.info("Pump test completed and turned OFF")
            
            return True
        except Exception as e:
            logger.error(f"Pump test failed: {e}")
            # Ensure pump is OFF after test
            try:
                GPIO.output(PUMP_PIN, GPIO.HIGH)
                self.pump_pwm.ChangeDutyCycle(100)
            except:
                pass
            return False

    def cleanup(self):
        """Clean up pump resources."""
        try:
            # Ensure pump is OFF using inverted logic
            self.force_pump_state(False)  # Sets GPIO HIGH
            time.sleep(0.5)
            self.pump_pwm.stop()
            GPIO.cleanup(PUMP_PIN)
            logger.info("Water pump cleanup completed")
        except Exception as e:
            logger.error(f"Error during water pump cleanup: {e}") 