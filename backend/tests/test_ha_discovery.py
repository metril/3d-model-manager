import json
from pathlib import Path

from app.services import ha_discovery as had

SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "ha_discovery_a1mini.json"


def test_discovery_snapshot():
    messages = had.discovery_messages("homeassistant", "0309DA123456", "A1 Mini", "A1 Mini")
    expected = json.loads(SNAPSHOT_PATH.read_text())
    assert [list(m) for m in messages] == expected
    assert len(messages) == 14


def test_unique_ids_stable_and_unique():
    messages = had.discovery_messages("homeassistant", "0309DA123456", "A1 Mini", "A1 Mini")
    unique_ids = [payload["unique_id"] for _, payload in messages]
    assert len(unique_ids) == len(set(unique_ids))

    messages_again = had.discovery_messages("homeassistant", "0309DA123456", "A1 Mini", "A1 Mini")
    assert [payload["unique_id"] for _, payload in messages_again] == unique_ids


def test_removal_topics_match():
    topics = had.removal_topics("homeassistant", "0309DA123456")
    messages = had.discovery_messages("homeassistant", "0309DA123456", "A1 Mini", "A1 Mini")
    assert topics == [topic for topic, _ in messages]


def test_custom_prefix():
    messages = had.discovery_messages("custom_prefix", "0309DA123456", "A1 Mini", "A1 Mini")
    assert all(topic.startswith("custom_prefix/") for topic, _ in messages)


def test_state_payload_offline_when_none():
    payload = had.state_payload(None, last_result=None, queue_length=0)
    assert payload["state"] == "OFFLINE"
    assert payload["printing"] is False


def test_state_payload_printing_flag():
    payload = had.state_payload(
        {"gcode_state": "RUNNING", "mc_percent": 42}, last_result="SUCCESS", queue_length=2
    )
    assert payload["printing"] is True
    assert payload["state"] == "RUNNING"
    assert payload["progress"] == 42
    assert payload["last_result"] == "SUCCESS"
    assert payload["queue_length"] == 2

    payload_idle = had.state_payload({"gcode_state": "FINISH"}, last_result=None, queue_length=0)
    assert payload_idle["printing"] is False
