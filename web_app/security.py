"""Password hashing, opaque sessions, CSRF and form validation."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from email_validator import validate_email, EmailNotValidError
from fastapi import HTTPException

COOKIE='laptop_session'
PH=PasswordHasher()


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode('ascii')).hexdigest()


def normalize_email(value: str) -> str:
    try:
        return validate_email(value.strip(),check_deliverability=False,allow_smtputf8=False).normalized.lower()
    except EmailNotValidError as exc:
        raise ValueError('Введите корректный email.') from exc


def validate_password(value: str) -> None:
    if not 12<=len(value)<=128 or not value.strip():
        raise ValueError('Пароль должен содержать от 12 до 128 символов.')


def verify_password(encoded: str,password: str) -> bool:
    try:
        return PH.verify(encoded,password)
    except (VerificationError,InvalidHashError):
        return False


@dataclass
class Context:
    connection: object
    session: dict
    user: dict | None
    cookie_token: str=field(repr=False)


def new_session(connection,user_id: int | None=None) -> tuple[dict,str]:
    token=secrets.token_urlsafe(32)
    csrf=secrets.token_hex(32)
    expires=datetime.now(timezone.utc)+timedelta(hours=8 if user_id is not None else 0.5)
    row=connection.execute('''INSERT INTO web_sessions(token_hash,user_id,csrf_token,expires_at)
        VALUES(%s,%s,%s,%s) RETURNING token_hash,user_id,csrf_token,expires_at''',
        (token_hash(token),user_id,csrf,expires)).fetchone()
    return dict(row),token


def session_context(connection,cookie: str | None) -> Context:
    session=None
    if cookie and len(cookie)<=100 and cookie.isascii():
        session=connection.execute('''SELECT token_hash,user_id,csrf_token,expires_at FROM web_sessions
            WHERE token_hash=%s AND expires_at>now()''',(token_hash(cookie),)).fetchone()
    user=None
    if session and session['user_id'] is not None:
        user=connection.execute('SELECT id,email,name FROM users WHERE id=%s AND is_active',(session['user_id'],)).fetchone()
        if user is None:
            connection.execute('DELETE FROM web_sessions WHERE token_hash=%s',(session['token_hash'],))
            session=None
    if session is None:
        session,cookie=new_session(connection)
    return Context(connection,dict(session),dict(user) if user else None,cookie)


def rotate(context: Context,user: dict | None) -> None:
    context.connection.execute('DELETE FROM web_sessions WHERE token_hash=%s',(context.session['token_hash'],))
    context.session,context.cookie_token=new_session(context.connection,user['id'] if user else None)
    context.user=user


def csrf_check(context: Context,token: str,request) -> None:
    origin=request.headers.get('origin')
    expected=f'{request.url.scheme}://{request.url.netloc}'
    if origin and origin.rstrip('/')!=expected:
        raise HTTPException(403,'Недопустимый источник формы.')
    if not token or not hmac.compare_digest(str(token).encode('utf-8'),context.session['csrf_token'].encode('ascii')):
        raise HTTPException(403,'Форма устарела. Обновите страницу и повторите действие.')


def limit_key(action: str,email: str,ip: str) -> str:
    return hashlib.sha256(f'{action}\0{email.lower()}\0{ip}'.encode('utf-8')).hexdigest()


def is_blocked(connection,key: str) -> bool:
    row=connection.execute('SELECT blocked_until>now() AS blocked FROM web_auth_attempts WHERE key_hash=%s',(key,)).fetchone()
    return bool(row and row['blocked'])


def record_failure(connection,key: str,threshold: int=5) -> None:
    connection.execute('''INSERT INTO web_auth_attempts(key_hash,failures) VALUES(%s,1)
        ON CONFLICT(key_hash) DO UPDATE SET
        failures=CASE WHEN web_auth_attempts.window_started_at<now()-interval '15 minutes' THEN 1 ELSE web_auth_attempts.failures+1 END,
        window_started_at=CASE WHEN web_auth_attempts.window_started_at<now()-interval '15 minutes' THEN now() ELSE web_auth_attempts.window_started_at END,
        blocked_until=CASE WHEN web_auth_attempts.window_started_at>=now()-interval '15 minutes'
              AND web_auth_attempts.failures+1>=%s THEN now()+interval '15 minutes' ELSE NULL END''',(key,threshold))
