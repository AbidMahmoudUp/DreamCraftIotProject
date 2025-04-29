import pika
import json
import time
import threading
import signal
import sys
from typing import Dict, Any

# Import from API server to update sensor data
from api_server import update_sensor_data

# Import RabbitMQ settings from config
from config.settings import (
    RABBITMQ_HOST, RABBITMQ_PORT, RABBITMQ_USER, RABBITMQ_PASS,
    EXCHANGE_NAME, RPI_ID, STATUS_QUEUE
)
from utils.logging_setup import logger

class SensorDataConsumer:
    def __init__(self):
        self.connection = None
        self.channel = None
        self.running = False
        self.thread = None
        self.reconnect_delay = 0
        self.max_reconnect_delay = 30
        
    def connect(self):
        """Establish connection to RabbitMQ."""
        try:
            logger.info(f"Connecting to RabbitMQ at {RABBITMQ_HOST}:{RABBITMQ_PORT}...")
            credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=RABBITMQ_HOST,
                    port=RABBITMQ_PORT,
                    credentials=credentials,
                    heartbeat=600
                )
            )
            channel = connection.channel()
            
            # Declare exchange
            channel.exchange_declare(
                exchange=EXCHANGE_NAME,
                exchange_type='topic',
                durable=True
            )
            
            # Declare and bind status queue
            channel.queue_declare(queue=STATUS_QUEUE, durable=True)
            channel.queue_bind(
                queue=STATUS_QUEUE,
                exchange=EXCHANGE_NAME,
                routing_key=f"status.{RPI_ID}"
            )
            
            self.connection = connection
            self.channel = channel
            self.reconnect_delay = 0  # Reset reconnect delay on successful connection
            logger.info("Connected to RabbitMQ successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to RabbitMQ: {e}")
            self.reconnect_delay = min(self.reconnect_delay + 1, self.max_reconnect_delay)
            time.sleep(self.reconnect_delay)
            return False
            
    def start_consuming(self):
        """Start consuming messages."""
        if not self.channel:
            logger.warning("Cannot start consuming - channel not available")
            return False
            
        try:
            logger.info(f"Starting to consume messages from {STATUS_QUEUE}...")
            self.channel.basic_consume(
                queue=STATUS_QUEUE,
                on_message_callback=self.process_message,
                auto_ack=True
            )
            self.channel.start_consuming()
            return True
        except Exception as e:
            logger.error(f"Error starting consumer: {e}")
            return False
            
    def process_message(self, _unused_channel, method, _unused_properties, body):
        """Process received message."""
        try:
            message = json.loads(body)
            logger.info(f"Received status update: {message}")
            
            # Extract sensor data from message
            sensor_data = {
                "temperature": message.get("temperature"),
                "humidity": message.get("humidity"),
                "soil_is_dry": message.get("soil_moist", False) == False,  # Convert soil_moist to soil_is_dry
                "pump_active": message.get("pump_on", False),
                "last_updated": message.get("last_update", time.time())
            }
            
            # Update sensor data in API server
            update_sensor_data(sensor_data)
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message as JSON: {e}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            
    def run(self):
        """Run the consumer in a loop."""
        self.running = True
        
        while self.running:
            try:
                if self.connect():
                    self.start_consuming()
            except KeyboardInterrupt:
                self.running = False
                logger.info("Interrupt received, shutting down...")
            except Exception as e:
                logger.error(f"Error in run loop: {e}")
                self.reconnect_delay = min(self.reconnect_delay + 1, self.max_reconnect_delay)
                
            if self.running and self.connection and not self.connection.is_closed:
                try:
                    self.connection.close()
                except Exception:
                    pass
                    
            if self.running:
                logger.info(f"Reconnecting in {self.reconnect_delay} seconds...")
                time.sleep(self.reconnect_delay)
                
    def start(self):
        """Start the consumer in a new thread."""
        if self.thread and self.thread.is_alive():
            logger.warning("Consumer already running")
            return
            
        self.thread = threading.Thread(target=self.run)
        self.thread.daemon = True
        self.thread.start()
        logger.info("Consumer started in background thread")
        
    def stop(self):
        """Stop the consumer."""
        self.running = False
        if self.connection and not self.connection.is_closed:
            try:
                self.connection.close()
            except Exception:
                pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5.0)
        logger.info("Consumer stopped")

def signal_handler(sig, frame):
    """Handle termination signals."""
    logger.info("Termination signal received, shutting down...")
    consumer.stop()
    sys.exit(0)

if __name__ == "__main__":
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Create and start consumer
    consumer = SensorDataConsumer()
    consumer.start()
    
    try:
        # Keep the main thread alive
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        consumer.stop() 