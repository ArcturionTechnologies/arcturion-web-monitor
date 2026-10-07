"""Pluggable notifiers. Everything is configured by environment variables.

Built in:
  stdout    print the message (default)
  file      append to the file named by WEBMON_NOTIFY_FILE
  webhook   POST {"text": ...} as JSON to WEBMON_WEBHOOK_URL (Slack and Discord
            compatible incoming webhooks accept this shape)
  telegram  sendMessage with WEBMON_TELEGRAM_BOT_TOKEN to WEBMON_TELEGRAM_CHAT_ID

Add your own with `register_notifier(name, factory)`; a factory takes
(env, transport) and returns an object with `send(text) -> bool`.
"""
import json
import os
import sys
import urllib.parse

from .net import http_post


class NotifierConfigError(ValueError):
    pass


def _need(env, name, notifier):
    value = env.get(name)
    if not value:
        raise NotifierConfigError(f"notifier '{notifier}' needs the {name} environment variable")
    return value


class StdoutNotifier:
    def __init__(self, env=None, transport=None, stream=None):
        self.stream = stream

    def send(self, text):
        print(text, file=self.stream or sys.stdout, flush=True)
        return True


class FileNotifier:
    def __init__(self, env, transport=None):
        self.path = _need(env, "WEBMON_NOTIFY_FILE", "file")

    def send(self, text):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n\n")
        return True


class WebhookNotifier:
    def __init__(self, env, transport=http_post):
        self.url = _need(env, "WEBMON_WEBHOOK_URL", "webhook")
        if not self.url.startswith(("https://", "http://")):
            raise NotifierConfigError("WEBMON_WEBHOOK_URL must be an http(s) URL")
        self.transport = transport

    def send(self, text):
        status, _ = self.transport(self.url, json.dumps({"text": text}).encode(),
                                   {"Content-Type": "application/json"}, 15)
        return 200 <= status < 300


class TelegramNotifier:
    def __init__(self, env, transport=http_post):
        self.token = _need(env, "WEBMON_TELEGRAM_BOT_TOKEN", "telegram")
        self.chat_id = _need(env, "WEBMON_TELEGRAM_CHAT_ID", "telegram")
        self.transport = transport

    def send(self, text):
        data = urllib.parse.urlencode({"chat_id": self.chat_id, "text": text,
                                       "disable_web_page_preview": "true"}).encode()
        status, _ = self.transport(f"https://api.telegram.org/bot{self.token}/sendMessage", data,
                                   {"Content-Type": "application/x-www-form-urlencoded"}, 15)
        return 200 <= status < 300


NOTIFIERS = {
    "stdout": StdoutNotifier,
    "file": FileNotifier,
    "webhook": WebhookNotifier,
    "telegram": TelegramNotifier,
}


def register_notifier(name, factory):
    NOTIFIERS[name] = factory


def build_notifier(name=None, env=None, transport=None):
    env = os.environ if env is None else env
    name = name or env.get("WEBMON_NOTIFIER") or "stdout"
    try:
        factory = NOTIFIERS[name]
    except KeyError:
        raise NotifierConfigError(f"unknown notifier {name!r}; choose from {sorted(NOTIFIERS)}")
    return factory(env, transport) if transport else factory(env)
