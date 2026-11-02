"""Webhook payload → event data."""

from custom_components.yuvomi.event import event_data, task_id_of


def test_task_id_from_tag_and_url():
    assert task_id_of({"tag": "task-42"}) == 42
    assert task_id_of({"tag": "", "url": "/tasks/7"}) == 7
    assert task_id_of({"tag": "tasks_13"}) == 13
    assert task_id_of({"tag": "birthday-3", "url": "/calendar"}) is None


def test_event_data():
    payload = {
        "event": "notification",
        "notification": {"title": "Due", "body": "Take out trash", "url": "/tasks/5", "tag": "x", "priority": "high"},
        "sentAt": "2026-11-02T10:00:00Z",
    }
    assert event_data(payload) == {
        "title": "Due",
        "body": "Take out trash",
        "url": "/tasks/5",
        "tag": "x",
        "priority": "high",
        "task_id": 5,
        "sent_at": "2026-11-02T10:00:00Z",
    }


def test_event_data_plain_text():
    assert event_data({"text": "hello"})["body"] == "hello"
