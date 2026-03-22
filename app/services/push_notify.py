from pywebpush import webpush, WebPushException
import json
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

VAPID_PRIVATE_KEY = settings.VAPID_PRIVATE_KEY
VAPID_PUBLIC_KEY = settings.VAPID_PUBLIC_KEY
VAPID_CLAIMS = {
    "sub": "mailto:admin@example.com"
}

class PushResult:
    OK = "ok"            # delivered
    EXPIRED = "expired"  # 404/410 — subscription is dead, delete from DB
    ERROR = "error"      # any other failure


def send_web_push(subscription_info: dict, message: dict) -> str:
    """
    Send a push notification.
    Returns PushResult.OK / PushResult.EXPIRED / PushResult.ERROR.
    Callers should delete the subscription from DB when EXPIRED is returned.
    """
    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(message),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS,
        )
        return PushResult.OK
    except WebPushException as ex:
        status = ex.response.status_code if ex.response is not None else None
        logger.warning(f"WebPushException (HTTP {status}): {ex}")
        # 404 = endpoint gone, 410 = explicitly unsubscribed — both mean: delete this sub
        if status in (404, 410):
            return PushResult.EXPIRED
        return PushResult.ERROR
    except Exception as ex:
        logger.error(f"Unexpected push error: {ex}")
        return PushResult.ERROR
