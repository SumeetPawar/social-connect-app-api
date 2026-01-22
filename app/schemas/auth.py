from pydantic import BaseModel, EmailStr, Field

class SignupIn(BaseModel):
    name: str | None = None
    email: EmailStr
    password: str = Field(min_length=6, max_length=72)

class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=72)

class AuthOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class RefreshRequest(BaseModel):
    refresh_token: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    
class RefreshIn(BaseModel):
    refresh_token: str