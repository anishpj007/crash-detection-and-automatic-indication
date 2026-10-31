"""Twilio SMS delivery for live crash alerts.

Configuration is read from process environment variables or backend/.env. The
local .env file is intended for development only and is excluded from Git.
"""

import base64
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = os.path.join(BASE_DIR, ".env")


class SmsDeliveryError(RuntimeError):
    """A safe-to-display SMS error containing a provider code, not its payload."""

    def __init__(self, message, error_code=None):
        super().__init__(message)
        self.error_code = error_code


def _load_local_env():
    """Load simple KEY=value entries without overriding real environment vars."""
    if not os.path.isfile(DOTENV_PATH):
        return

    with open(DOTENV_PATH, "r", encoding="utf-8") as env_file:
        for line in env_file:
            entry = line.strip()
            if not entry or entry.startswith("#"):
                continue
            if entry.startswith("export "):
                entry = entry[7:].lstrip()
            key, separator, value = entry.partition("=")
            key = key.strip()
            if not separator or not key or key in os.environ:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ[key] = value


_load_local_env()


def _valid_e164(number):
    return bool(re.fullmatch(r"\+[1-9]\d{7,14}", number or ""))


def get_sms_status():
    """Return safe, non-secret configuration details for the dashboard."""
    provider = os.environ.get("SMS_PROVIDER", "twilio").strip().lower()
    gateway_url = os.environ.get("ANDROID_SMS_GATEWAY_URL", "").strip()
    gateway_username = os.environ.get("ANDROID_SMS_GATEWAY_USERNAME", "").strip()
    gateway_password = os.environ.get("ANDROID_SMS_GATEWAY_PASSWORD", "").strip()
    enabled = os.environ.get("SMS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }
    trial_mode = os.environ.get("TWILIO_TRIAL_MODE", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }
    to_number = os.environ.get("SMS_TO_NUMBER", "").strip()

    missing = []
    if provider not in {"twilio", "android_gateway"}:
        missing.append("SMS_PROVIDER (supported values: twilio, android_gateway)")
    elif provider == "twilio":
        if not os.environ.get("TWILIO_ACCOUNT_SID", "").strip():
            missing.append("TWILIO_ACCOUNT_SID")
        if not os.environ.get("TWILIO_AUTH_TOKEN", "").strip():
            missing.append("TWILIO_AUTH_TOKEN")
        if not _valid_e164(os.environ.get("TWILIO_FROM_NUMBER", "").strip()):
            missing.append("TWILIO_FROM_NUMBER (E.164 format, e.g. +15551234567)")
    elif provider == "android_gateway":
        if not gateway_url.startswith(("http://", "https://")):
            missing.append("ANDROID_SMS_GATEWAY_URL (local server URL ending in /message)")
        if not gateway_username:
            missing.append("ANDROID_SMS_GATEWAY_USERNAME")
        if not gateway_password:
            missing.append("ANDROID_SMS_GATEWAY_PASSWORD")
    if not _valid_e164(to_number):
        missing.append("SMS_TO_NUMBER (E.164 format, e.g. +15551234567)")

    ready = enabled and not missing
    if not enabled:
        state = "disabled"
    elif missing:
        state = "not_configured"
    else:
        state = "ready"

    masked_recipient = None
    if to_number:
        masked_recipient = f"••••{to_number[-4:]}"

    return {
        "state": state,
        "enabled": enabled,
        "ready": ready,
        "provider": {
            "twilio": "Twilio",
            "android_gateway": "Android SMS Gateway",
        }.get(provider, provider),
        "provider_key": provider,
        "recipient": masked_recipient,
        "trial_mode": trial_mode if provider == "twilio" else False,
        "missing": missing,
    }


def _send_android_gateway_sms(message):
    """Send through the SMS Gateway for Android local server on the LAN."""
    gateway_url = os.environ["ANDROID_SMS_GATEWAY_URL"].strip()
    username = os.environ["ANDROID_SMS_GATEWAY_USERNAME"].strip()
    password = os.environ["ANDROID_SMS_GATEWAY_PASSWORD"].strip()
    credentials = base64.b64encode(
        f"{username}:{password}".encode("utf-8")
    ).decode("ascii")
    payload = json.dumps({
        "textMessage": {"text": message},
        "phoneNumbers": [os.environ["SMS_TO_NUMBER"].strip()],
    }).encode("utf-8")
    request = Request(
        gateway_url,
        data=payload,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=12) as response:
            response_body = response.read().decode("utf-8")
            if not response_body:
                return None
            try:
                result = json.loads(response_body)
            except ValueError:
                return None
            return result.get("id") or result.get("messageId")
    except HTTPError as exc:
        raise SmsDeliveryError(
            f"Android gateway HTTP {exc.code}", error_code=f"HTTP_{exc.code}"
        ) from None
    except URLError:
        raise SmsDeliveryError(
            "Could not reach the Android SMS gateway", error_code="NETWORK"
        ) from None
    except TimeoutError:
        raise SmsDeliveryError(
            "Android SMS gateway request timed out", error_code="TIMEOUT"
        ) from None


def send_sms(message):
    """Submit one SMS to Twilio and return the provider message SID."""
    status = get_sms_status()
    if not status["ready"]:
        raise RuntimeError("SMS notifications are not configured")

    if status["provider_key"] == "android_gateway":
        return _send_android_gateway_sms(message)

    account_sid = os.environ["TWILIO_ACCOUNT_SID"].strip()
    auth_token = os.environ["TWILIO_AUTH_TOKEN"].strip()
    endpoint = (
        "https://api.twilio.com/2010-04-01/Accounts/"
        f"{account_sid}/Messages.json"
    )
    trial_mode = os.environ.get("TWILIO_TRIAL_MODE", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }
    # Twilio trial accounts reject custom SMS bodies. Their trial API accepts
    # predefined template identifiers in the Body field instead.
    sms_body = "sms_internal_alerts" if trial_mode else message
    body = urlencode({
        "To": os.environ["SMS_TO_NUMBER"].strip(),
        "From": os.environ["TWILIO_FROM_NUMBER"].strip(),
        "Body": sms_body,
    }).encode("utf-8")
    credentials = base64.b64encode(
        f"{account_sid}:{auth_token}".encode("utf-8")
    ).decode("ascii")
    request = Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=12) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result.get("sid")
    except HTTPError as exc:
        try:
            error_body = json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            error_body = {}
        provider_code = error_body.get("code")
        if provider_code:
            raise SmsDeliveryError(
                f"Twilio error {provider_code}", error_code=str(provider_code)
            ) from None
        raise SmsDeliveryError(
            f"Twilio HTTP {exc.code}", error_code=f"HTTP_{exc.code}"
        ) from None
    except URLError:
        raise SmsDeliveryError(
            "Could not reach the Twilio SMS service", error_code="NETWORK"
        ) from None
    except TimeoutError:
        raise SmsDeliveryError("Twilio request timed out", error_code="TIMEOUT") from None
