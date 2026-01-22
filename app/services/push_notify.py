from pywebpush import webpush, WebPushException
import json

from sqlalchemy import null
from app.core.config import settings

VAPID_PRIVATE_KEY = "VbZrKrsZGzTkKGfPGHhMDVGZ_7ZkwICWmReBxAEywb0"
VAPID_PUBLIC_KEY = "BMmVTo0GaTfa9QJSmxlmXrE3ukC6wfZKBRgxxkjBBpvEfBK8-9iNOSGxH04kZPaKCuRccatRgPGlrxnGDIr0O0Y"
VAPID_CLAIMS = {
    "sub": "mailto:admin@example.com"
}

def send_web_push(subscription_info, message):
    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(message),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS
        )
        print("Push sent!")
    except WebPushException as ex:
        print(f"Web push failed: {ex}")
        if ex.response and ex.response.json():
            print(f"Response: {ex.response.json()}")

# Example usage:
if __name__ == "__main__":
    # Replace with a real subscription object from your frontend console
    subscription = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/ddyHseBsoPg:APA91bFjf-LVa4Ab2aHZC_4HG5zv4RlSenwu7nHcdEjMUcD50PH6fPcpwistxUvmB1LRHJbjzuSnYcMN1x-iQrUxRRxKD8UnXPEdhKUTsgX1cGRpFAlvdDEa5n77DOhXBqRvxdywWuoB",
        "expirationTime": null,
        "keys": {
        "p256dh": "BPL7PJRegJl0TLQsFaFFYcH5lsVYewsnZkzQCsrt5l1ISS3EkfnxBihPiJme1JUmN3EQqo6v6WbGwIPluf-g7mI",
        "auth": "DZ4SyL8MmteJxp-M7F8KuQ"
    }
    }
    # send_web_push(subscription, {"title": "Test", "body": "Hello from backend!"})   

    send_web_push(subscription, {
    "title": "Daily Reminder",
    "body": "Log your steps today!",
    "url": "/steps"  # Will open http://localhost:3000/steps
})