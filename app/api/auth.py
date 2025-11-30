from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from ..core.security import create_access_token, get_current_user, hash_password
from ..core.settings import Settings, get_settings
from ..models.user import UserCreate, UserDB, UserLogin, UserOut, read_users, write_users

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=dict, status_code=status.HTTP_201_CREATED)
async def register(payload: UserCreate, settings: Settings = Depends(get_settings)):
  users = read_users()
  if any(user.email.lower() == payload.email.lower() for user in users):
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Un compte existe déjà avec cet email.")
  if len(payload.password) < 8:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Le mot de passe doit contenir au moins 8 caractères.")
  new_user = UserDB(
    id=str(uuid4()),
    name=payload.name,
    email=payload.email.lower(),
    passwordHash=hash_password(payload.password),
    role="user",
    createdAt=None,
  )
  users.append(new_user)
  write_users(users)
  token = create_access_token({"sub": new_user.id, "email": new_user.email, "role": new_user.role}, settings)
  return {"token": token, "user": UserOut.model_validate(new_user).model_dump()}


@router.post("/login", response_model=dict)
async def login(payload: UserLogin, settings: Settings = Depends(get_settings)):
  users = read_users()
  matching = next((user for user in users if user.email.lower() == payload.email.lower()), None)
  if not matching or not matching.passwordHash or not matching.passwordHash:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Identifiants invalides.")
  from ..core.security import verify_password

  if not verify_password(payload.password, matching.passwordHash):
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Identifiants invalides.")
  token = create_access_token({"sub": matching.id, "email": matching.email, "role": matching.role}, settings)
  return {"token": token, "user": UserOut.model_validate(matching).model_dump()}


@router.get("/me", response_model=dict)
async def me(user=Depends(get_current_user)):
  return {"user": {"id": user.get("sub"), "email": user.get("email"), "role": user.get("role")}}
