from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from postdesk.api import auth
from postdesk.api.schemas import AuthStatus, Password
from postdesk.db.session import get_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


@router.get("/status", response_model=AuthStatus)
def status(request: Request, session: Session = Depends(get_session)) -> AuthStatus:
    return AuthStatus(configured=auth.password_configured(session),
                      authenticated=auth.is_authenticated(request, session))


@router.post("/setup", response_model=AuthStatus)
def setup(body: Password, request: Request, response: Response, session: Session = Depends(get_session)):
    """The first visit: set the password (only while none is set)."""
    if auth.password_configured(session):
        raise HTTPException(status_code=409, detail="a password is already set; sign in instead")
    try:
        auth.set_password(session, body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    auth.issue_cookie(session, response, secure=_secure(request))
    return AuthStatus(configured=True, authenticated=True)


@router.post("/login", response_model=AuthStatus)
def login(body: Password, request: Request, response: Response, session: Session = Depends(get_session)):
    if not auth.password_configured(session):
        raise HTTPException(status_code=409, detail="no password set yet")
    if not auth.verify_password(session, body.password):
        raise HTTPException(status_code=401, detail="wrong password")
    auth.issue_cookie(session, response, secure=_secure(request))
    return AuthStatus(configured=True, authenticated=True)


@router.post("/logout", response_model=AuthStatus)
def logout(response: Response, session: Session = Depends(get_session)):
    auth.clear_cookie(response)
    return AuthStatus(configured=auth.password_configured(session), authenticated=False)
