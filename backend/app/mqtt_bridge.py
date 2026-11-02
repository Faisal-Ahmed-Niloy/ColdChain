"""Connects the Ingestor to the Mosquitto broker (paho-mqtt 2.x).

Subscribes:  coldchain/v1/+/telemetry   (signed readings, QoS 1)
             coldchain/v1/+/status      (online/offline last-will, retained)
Publishes:   coldchain/v1/<dev>/cmd     (signed ACKs and commands, QoS 1)
"""
from __future__ import annotations

import logging

import paho.mqtt.client as mqtt

from .config import settings
from .ingest import Ingestor

log = logging.getLogger("coldchain.mqtt")


class MqttBridge:
    def __init__(self, ingestor: Ingestor) -> None:
        self.ingestor = ingestor
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="coldchain-backend",
                                  clean_session=True)
        if settings.mqtt_user:
            self.client.username_pw_set(settings.mqtt_user, settings.mqtt_pass or None)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=10)
        self.connected = False
        ingestor.publisher = self.publish

    def start(self) -> None:
        self.client.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=30)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def publish(self, topic: str, payload: bytes) -> None:
        info = self.client.publish(topic, payload, qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError(f"publish failed rc={info.rc}")

    # -------------------------------------------------- callbacks
    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            log.error("MQTT connect failed: %s", reason_code)
            return
        self.connected = True
        prefix = settings.topic_prefix
        client.subscribe([(f"{prefix}+/telemetry", 1), (f"{prefix}+/status", 1)])
        log.info("MQTT connected to %s:%s", settings.mqtt_host, settings.mqtt_port)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self.connected = False
        log.warning("MQTT disconnected: %s (auto-reconnecting)", reason_code)

    def _on_message(self, client, userdata, msg) -> None:
        try:
            for topic, payload in self.ingestor.handle(msg.topic, msg.payload):
                client.publish(topic, payload, qos=1)
        except Exception:  # never let one bad message kill the network thread
            log.exception("error handling %s", msg.topic)
