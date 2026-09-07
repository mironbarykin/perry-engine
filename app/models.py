from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    # A communicator is responsible for deriving these from its authenticated
    # sender (for example, Telegram's update.from.id), never from chat text.
    user_id: str | None = Field(default=None, max_length=64)
    user_name: str | None = Field(default=None, max_length=120)
    telegram_id: int | None = Field(default=None, gt=0)
    message: str

    @model_validator(mode="after")
    def has_sender_identity(self) -> "ChatRequest":
        if not any((self.user_id, self.user_name, self.telegram_id)):
            raise ValueError("One of user_id, user_name, or telegram_id is required")
        return self


class ChatResponse(BaseModel):
    reply: str
    pending_confirmations: list["PendingConfirmation"] = Field(default_factory=list)


class PendingConfirmation(BaseModel):
    id: str
    action_type: str | None = None
    expires_in_minutes: int | None = None


class SenderIdentity(BaseModel):
    user_id: str | None = Field(default=None, max_length=64)
    user_name: str | None = Field(default=None, max_length=120)
    telegram_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def has_sender_identity(self) -> "SenderIdentity":
        if not any((self.user_id, self.user_name, self.telegram_id)):
            raise ValueError("One of user_id, user_name, or telegram_id is required")
        return self
