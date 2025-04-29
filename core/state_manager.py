import time
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
from utils.logging_setup import logger

@dataclass
class SystemState:
    """Data class representing the system state."""
    pump_on: bool = False
    soil_moist: bool = False
    temperature: float = 0.0
    humidity: float = 0.0
    last_watering: Optional[float] = None
    last_check: Optional[float] = None
    mode: str = "auto"  # "auto" or "manual"
    mode_set_by_user: bool = False  # Track if mode was explicitly set by user command
    error: Optional[str] = None
    last_update: Optional[float] = None

class StateManager:
    def __init__(self):
        """Initialize the state manager."""
        self._state = SystemState()
        self._state_lock = False
        
    def update_state(self, **kwargs) -> bool:
        """
        Update the system state with new values.
        Args:
            **kwargs: Key-value pairs of state attributes to update
        Returns:
            bool: True if update was successful, False otherwise
        """
        try:
            if self._state_lock:
                logger.warning("State update attempted while locked")
                return False
                
            # Update only valid attributes
            valid_attrs = {k: v for k, v in kwargs.items() if hasattr(self._state, k)}
            for key, value in valid_attrs.items():
                setattr(self._state, key, value)
                
            self._state.last_update = time.time()
            logger.debug(f"State updated: {valid_attrs}")
            return True
            
        except Exception as e:
            logger.error(f"Error updating state: {e}")
            return False
            
    def get_state(self) -> Dict[str, Any]:
        """
        Get the current system state.
        Returns:
            Dict[str, Any]: Current state as a dictionary
        """
        return asdict(self._state)
        
    def lock_state(self) -> None:
        """Lock the state to prevent updates."""
        self._state_lock = True
        logger.debug("State locked")
        
    def unlock_state(self) -> None:
        """Unlock the state to allow updates."""
        self._state_lock = False
        logger.debug("State unlocked")
        
    def set_error(self, error_msg: str) -> None:
        """
        Set an error state.
        Args:
            error_msg: Description of the error
        """
        self.update_state(error=error_msg)
        logger.error(f"System error: {error_msg}")
        
    def clear_error(self) -> None:
        """Clear any error state."""
        self.update_state(error=None)
        logger.info("Error state cleared")
        
    def set_mode(self, mode: str) -> bool:
        """
        Set the system mode (auto/manual).
        Args:
            mode: "auto" or "manual"
        Returns:
            bool: True if mode was set successfully
        """
        if mode not in ["auto", "manual"]:
            logger.error(f"Invalid mode: {mode}")
            return False
            
        return self.update_state(mode=mode, mode_set_by_user=True)
        
    def update_sensor_data(self, soil_moist: bool, temperature: float, humidity: float) -> None:
        """
        Update sensor readings in the state.
        Args:
            soil_moist: Soil moisture reading
            temperature: Temperature reading
            humidity: Humidity reading
        """
        self.update_state(
            soil_moist=soil_moist,
            temperature=temperature,
            humidity=humidity,
            last_check=time.time()
        )
        
    def record_watering(self) -> None:
        """Record that watering has occurred."""
        self.update_state(
            last_watering=time.time(),
            pump_on=False
        )
        
    def is_watering_allowed(self, min_interval: int = 3600) -> bool:
        """
        Check if watering is allowed based on timing.
        Args:
            min_interval: Minimum seconds between waterings
        Returns:
            bool: True if watering is allowed
        """
        if self._state.last_watering is None:
            return True
            
        time_since_last = time.time() - self._state.last_watering
        return time_since_last >= min_interval 
        
    def set_automatic_mode(self, automatic: bool) -> bool:
        """
        Set the system to automatic or manual mode.
        Args:
            automatic: True for automatic mode, False for manual mode
        Returns:
            bool: True if mode was set successfully
        """
        mode = "auto" if automatic else "manual"
        result = self.set_mode(mode)
        if result:
            logger.info(f"System set to {'AUTOMATIC' if automatic else 'MANUAL'} mode")
        return result
        
    def update_pump_state(self, pump_on: bool) -> bool:
        """
        Update the pump state.
        Args:
            pump_on: True to turn pump on, False to turn pump off
        Returns:
            bool: True if pump state was updated successfully
        """
        result = self.update_state(pump_on=pump_on)
        if result:
            logger.info(f"Pump state set to {'ON' if pump_on else 'OFF'}")
            if pump_on:
                # Record watering start time if turning on
                self.update_state(last_watering=time.time())
        return result
        
    def get_status_message(self) -> Dict[str, Any]:
        """
        Get a formatted status message for sending via messaging system.
        Returns:
            Dict[str, Any]: Status information
        """
        state = self.get_state()
        return {
            "rpi_id": "irrigation_system_1",  # Should be from config
            "timestamp": time.time(),
            "soil_is_dry": not state.get("soil_moist", False),
            "pump_active": state.get("pump_on", False),
            "automatic_mode": state.get("mode", "auto") == "auto",
            "last_watered": state.get("last_watering"),
            "temperature": state.get("temperature")
        } 