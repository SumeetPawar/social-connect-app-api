from pydantic import BaseModel


class PushSubscriptionRequest(BaseModel):
    endpoint: str
    keys: dict  # Contains p256dh and auth


class PushNotificationRequest(BaseModel):
    title: str
    body: str
    user_id: int | None = None  # If None, send to current user
