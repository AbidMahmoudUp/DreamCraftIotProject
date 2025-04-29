import json
import logging
import pika
from typing import Callable, Dict, Any, Optional
from datetime import datetime
from config.settings import (
    RABBITMQ_HOST, RABBITMQ_PORT, RABBITMQ_USER, RABBITMQ_PASS,
    EXCHANGE_NAME, RPI_ID
)
from utils.logging_setup import logger

class MessageHandler:
    def __init__(self, host: str = RABBITMQ_HOST, port: int = RABBITMQ_PORT):
        """Initialize the message handler."""
        self.host = host
        self.port = port
        self.connection = None
        self.channel = None
        self.callbacks: Dict[str, Callable] = {}
        
    def register_callback(self, routing_key: str, callback: Callable) -> None:
        """
        Register a callback function for a specific routing key.
        Args:
            routing_key: The routing key to bind the callback to
            callback: The function to call when a message is received
        """
        self.callbacks[routing_key] = callback
        logger.debug(f"Registered callback for routing key: {routing_key}")
        
    def connect(self) -> bool:
        """Establish connection to RabbitMQ."""
        try:
            credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
            parameters = pika.ConnectionParameters(
                host=self.host,
                port=self.port,
                credentials=credentials,
                heartbeat=600,
                virtual_host='/'
            )
            self.connection = pika.BlockingConnection(parameters)
            self.channel = self.connection.channel()
            
            # Declare exchange
            self.channel.exchange_declare(
                exchange=EXCHANGE_NAME,
                exchange_type='topic',
                durable=True
            )
            
            # Declare queues
            self.declare_queue(f"irrigation_system.{RPI_ID}.commands")
            self.declare_queue(f"irrigation_system.{RPI_ID}.status")
            self.declare_queue('device_registration')
            self.declare_queue('device_heartbeat')
            
            # Bind queues
            self.channel.queue_bind(
                queue=f"irrigation_system.{RPI_ID}.commands",
                exchange=EXCHANGE_NAME,
                routing_key=f"command.{RPI_ID}"
            )
            self.channel.queue_bind(
                queue=f"irrigation_system.{RPI_ID}.status",
                exchange=EXCHANGE_NAME,
                routing_key=f"status.{RPI_ID}"
            )
            
            # Set up message consumption
            self.channel.basic_consume(
                queue=f"irrigation_system.{RPI_ID}.commands",
                on_message_callback=self.handle_message,
                auto_ack=False
            )
            
            logger.info("Connected to RabbitMQ and set up message consumption")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to RabbitMQ: {e}")
            return False
            
    def disconnect(self) -> None:
        """Close the RabbitMQ connection."""
        if self.connection and not self.connection.is_closed:
            self.connection.close()
            logger.info("Disconnected from RabbitMQ")
            
    def declare_queue(self, queue_name: str) -> None:
        """Declare a queue if it doesn't exist."""
        try:
            self.channel.queue_declare(queue=queue_name, durable=True)
            logger.debug(f"Queue declared: {queue_name}")
        except Exception as e:
            logger.error(f"Failed to declare queue {queue_name}: {e}")
            
    def register_device(self) -> bool:
        """Register this device with the system."""
        try:
            message = {
                "event": "device_online",
                "rpi_id": RPI_ID,
                "capabilities": ["soil_moisture", "water_pump", "temperature_sensor"],
                "timestamp": datetime.now().isoformat()
            }
            
            self.channel.basic_publish(
                exchange=EXCHANGE_NAME,
                routing_key="device.registration",
                body=json.dumps(message),
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    content_type='application/json'
                )
            )
            logger.info(f"Device registered: {RPI_ID}")
            return True
        except Exception as e:
            logger.error(f"Error registering device: {e}")
            return False
            
    def send_heartbeat(self) -> bool:
        """Send a heartbeat message."""
        try:
            message = {
                "event": "heartbeat",
                "rpi_id": RPI_ID,
                "timestamp": datetime.now().isoformat()
            }
            
            self.channel.basic_publish(
                exchange=EXCHANGE_NAME,
                routing_key="device.heartbeat",
                body=json.dumps(message),
                properties=pika.BasicProperties(content_type='application/json')
            )
            logger.debug("Heartbeat sent")
            return True
        except Exception as e:
            logger.error(f"Error sending heartbeat: {e}")
            return False
            
    def send_status_update(self, state: Dict[str, Any]) -> bool:
        """Send a status update message."""
        try:
            message = {
                "rpi_id": RPI_ID,
                "timestamp": datetime.now().isoformat(),
                "state": state
            }
            
            self.channel.basic_publish(
                exchange=EXCHANGE_NAME,
                routing_key=f"status.{RPI_ID}",
                body=json.dumps(message),
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    content_type='application/json'
                )
            )
            logger.debug(f"Status update sent: {state}")
            return True
        except Exception as e:
            logger.error(f"Error sending status update: {e}")
            return False
            
    def process_messages(self) -> None:
        """Process any pending messages."""
        try:
            self.channel.process_data_events(time_limit=0)  # Non-blocking
        except Exception as e:
            logger.error(f"Error processing messages: {e}")
            
    def handle_message(self, ch, method, properties, body):
        """Handle incoming messages."""
        try:
            message = json.loads(body)
            logger.debug(f"Received message: {message}")
            
            if method.routing_key in self.callbacks:
                self.callbacks[method.routing_key](message)
            else:
                logger.warning(f"No handler for routing key: {method.routing_key}")
                
            ch.basic_ack(delivery_tag=method.delivery_tag)
        except Exception as e:
            logger.error(f"Error handling message: {e}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False) 