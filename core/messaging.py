import json
import time
import pika
from pika.adapters.select_connection import SelectConnection
from config.settings import (
    RABBITMQ_HOST, RABBITMQ_PORT, RABBITMQ_USER, RABBITMQ_PASS,
    EXCHANGE_NAME, COMMAND_QUEUE, STATUS_QUEUE, RPI_ID
)
from utils.logging_setup import logger

class MessageHandler:
    def __init__(self, state_manager, pump_controller):
        """Initialize the message handler."""
        self.state_manager = state_manager
        self.pump_controller = pump_controller
        self.connection = None
        self.channel = None
        self.should_reconnect = False
        self.reconnect_delay = 0
        self.was_consuming = False
        self.closing = False
        self.consumer_tag = None

    def connect(self):
        """Connect to RabbitMQ."""
        logger.info(f'Connecting to {RABBITMQ_HOST}')
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        parameters = pika.ConnectionParameters(
            host=RABBITMQ_HOST,
            port=RABBITMQ_PORT,
            credentials=credentials,
            heartbeat=60
        )
        return SelectConnection(
            parameters=parameters,
            on_open_callback=self.on_connection_open,
            on_open_error_callback=self.on_connection_open_error,
            on_close_callback=self.on_connection_closed
        )

    def on_connection_open(self, connection):
        """Called when the connection is established."""
        logger.info('Connection opened')
        self.connection = connection
        self.open_channel()

    def on_connection_open_error(self, _unused_connection, err):
        """Called if the connection can't be established."""
        logger.error(f'Connection open failed: {err}')
        self.reconnect()

    def on_connection_closed(self, _unused_connection, reason):
        """Called when the connection is closed."""
        self.channel = None
        if self.closing:
            self.connection.ioloop.stop()
        else:
            logger.warning(f'Connection closed, reconnect necessary: {reason}')
            self.reconnect()

    def reconnect(self):
        """Reconnect to RabbitMQ."""
        self.should_reconnect = True
        if self.connection:
            self.connection.ioloop.stop()

    def open_channel(self):
        """Open a new channel with RabbitMQ."""
        logger.info('Creating a new channel')
        self.connection.channel(on_open_callback=self.on_channel_open)

    def on_channel_open(self, channel):
        """Called when the channel is opened."""
        logger.info('Channel opened')
        self.channel = channel
        self.channel.add_on_close_callback(self.on_channel_closed)
        self.setup_exchange()

    def on_channel_closed(self, channel, reason):
        """Called when RabbitMQ closes the channel."""
        logger.warning(f'Channel {channel} was closed: {reason}')
        self.connection.close()

    def setup_exchange(self):
        """Setup the exchange on RabbitMQ."""
        logger.info(f'Declaring exchange: {EXCHANGE_NAME}')
        self.channel.exchange_declare(
            exchange=EXCHANGE_NAME,
            exchange_type='topic',
            durable=True,
            callback=self.on_exchange_declareok
        )

    def on_exchange_declareok(self, _unused_frame):
        """Called when the exchange is declared."""
        logger.info(f'Exchange declared: {EXCHANGE_NAME}')
        self.setup_queues()

    def setup_queues(self):
        """Setup all required queues."""
        # Command queue
        self.channel.queue_declare(
            queue=COMMAND_QUEUE,
            durable=True,
            callback=self.on_command_queue_declareok
        )

        # Status queue
        self.channel.queue_declare(
            queue=STATUS_QUEUE,
            durable=True,
            callback=self.on_status_queue_declareok
        )

    def on_command_queue_declareok(self, _unused_frame):
        """Called when the command queue is declared."""
        logger.info('Command queue declared')
        self.channel.queue_bind(
            queue=COMMAND_QUEUE,
            exchange=EXCHANGE_NAME,
            routing_key=f"command.{RPI_ID}",
            callback=self.on_command_bindok
        )

    def on_status_queue_declareok(self, _unused_frame):
        """Called when the status queue is declared."""
        logger.info('Status queue declared')
        self.channel.queue_bind(
            queue=STATUS_QUEUE,
            exchange=EXCHANGE_NAME,
            routing_key=f"status.{RPI_ID}",
            callback=lambda _: logger.info('Status queue bound')
        )

    def on_command_bindok(self, _unused_frame):
        """Called when the command queue is bound."""
        logger.info('Command queue bound')
        self.start_consuming()

    def start_consuming(self):
        """Start consuming messages from RabbitMQ."""
        logger.info(f'Starting consumption on queue: {COMMAND_QUEUE}')
        self.channel.add_on_cancel_callback(self.on_consumer_cancelled)
        self.consumer_tag = self.channel.basic_consume(
            queue=COMMAND_QUEUE,
            on_message_callback=self.on_message
        )
        self.was_consuming = True

    def on_consumer_cancelled(self, method_frame):
        """Called when RabbitMQ cancels the consumer."""
        logger.info(f'Consumer was cancelled remotely: {method_frame}')
        if self.channel:
            self.channel.close()

    def on_message(self, _unused_channel, basic_deliver, properties, body):
        """Called when a message is received."""
        delivery_tag = basic_deliver.delivery_tag
        try:
            command = json.loads(body)
            logger.info(f"Received command: {command}")

            # Handle mode changes first
            if "mode" in command:
                mode = command["mode"].upper()
                logger.info(f"Setting operation mode to {mode}")
                
                if mode == "AUTOMATIC":
                    if self.state_manager.set_automatic_mode(True):
                        logger.info("Successfully switched to AUTOMATIC mode - soil sensor will control pump")
                    else:
                        logger.error("Failed to switch to AUTOMATIC mode")
                elif mode == "MANUAL":
                    if self.state_manager.set_automatic_mode(False):
                        logger.info("Successfully switched to MANUAL mode - soil sensor readings will be ignored")
                    else:
                        logger.error("Failed to switch to MANUAL mode")
                        
                # Always send status update after mode change
                self.send_status_update()

            # Handle pump control commands
            if "pump_control" in command:
                state = command["pump_control"] == "ON"
                logger.info(f"Processing pump control command: {'ON' if state else 'OFF'}")
                
                # If turning pump ON and force_manual_mode flag is set, switch to manual mode first
                if state and command.get("force_manual_mode", False):
                    logger.info("Auto-switching to MANUAL mode due to force_manual_mode flag")
                    if self.state_manager.set_automatic_mode(False):
                        logger.info("Successfully switched to MANUAL mode")
                    else:
                        logger.error("Failed to switch to MANUAL mode")
                
                if self.pump_controller.force_pump_state(state):
                    self.state_manager.update_pump_state(state)
                    logger.info(f"Pump turned {'ON' if state else 'OFF'} successfully in current mode")
                    self.send_status_update()
                else:
                    logger.error(f"Failed to set pump to {'ON' if state else 'OFF'}")
            elif "state" in command:
                state = bool(command["state"])
                logger.info(f"Setting pump state to {'ON' if state else 'OFF'}")
                
                # If turning pump ON and force_manual_mode flag is set, switch to manual mode first
                if state and command.get("force_manual_mode", False):
                    logger.info("Auto-switching to MANUAL mode due to force_manual_mode flag")
                    if self.state_manager.set_automatic_mode(False):
                        logger.info("Successfully switched to MANUAL mode")
                    else:
                        logger.error("Failed to switch to MANUAL mode")
                
                if self.pump_controller.force_pump_state(state):
                    self.state_manager.update_pump_state(state)
                    logger.info(f"Pump turned {'ON' if state else 'OFF'} successfully")
                    self.send_status_update()
                else:
                    logger.error(f"Failed to set pump to {'ON' if state else 'OFF'}")

            self.acknowledge_message(delivery_tag)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message as JSON: {e}")
            self.channel.basic_nack(delivery_tag=delivery_tag, requeue=False)
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            self.channel.basic_nack(delivery_tag=delivery_tag, requeue=False)

    def acknowledge_message(self, delivery_tag):
        """Acknowledge the message delivery."""
        logger.info(f'Acknowledging message {delivery_tag}')
        if self.channel:
            self.channel.basic_ack(delivery_tag)

    def send_status_update(self):
        """Send the current status to RabbitMQ."""
        if not self.channel:
            logger.warning("Cannot send status update - channel not available")
            return

        try:
            message = self.state_manager.get_status_message()
            self.channel.basic_publish(
                exchange=EXCHANGE_NAME,
                routing_key=f"status.{RPI_ID}",
                body=json.dumps(message),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # make message persistent
                    content_type='application/json'
                )
            )
            logger.info("Status update sent")
        except Exception as e:
            logger.error(f"Error sending status update: {e}")

    def stop(self):
        """Stop the message handler."""
        logger.info('Stopping message handler...')
        self.closing = True
        if self.was_consuming:
            self.stop_consuming()
        else:
            if self.connection:
                self.connection.ioloop.stop()
        logger.info('Message handler stopped')

    def stop_consuming(self):
        """Stop consuming messages."""
        if self.channel:
            logger.info('Sending a Basic.Cancel')
            self.channel.basic_cancel(self.consumer_tag, self.on_cancel_ok)

    def on_cancel_ok(self, _unused_frame):
        """Called when RabbitMQ acknowledges the cancellation."""
        logger.info('Basic.Cancel ok')
        self.was_consuming = False
        self.close_channel()

    def close_channel(self):
        """Close the channel."""
        logger.info('Closing the channel')
        if self.channel:
            self.channel.close()

    def run(self):
        """Run the message handler."""
        while not self.closing:
            self.connection = None
            self.channel = None

            try:
                self.connection = self.connect()
                self.connection.ioloop.start()
            except Exception as error:
                logger.error(f'Error: {error}')
                self.reconnect_delay = min(self.reconnect_delay + 1, 30)
                time.sleep(self.reconnect_delay)

            if self.should_reconnect:
                self.should_reconnect = False
                time.sleep(self.reconnect_delay)
            else:
                break 