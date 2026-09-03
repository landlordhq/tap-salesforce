import logging
import threading
from collections import namedtuple
from datetime import datetime, timedelta, timezone

import backoff
import jwt
import requests
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from simple_salesforce import SalesforceLogin

LOGGER = logging.getLogger(__name__)

OAuthCredentials = namedtuple("OAuthCredentials", ("client_id", "client_secret", "refresh_token"))

PasswordCredentials = namedtuple("PasswordCredentials", ("username", "password", "security_token"))

JwtCredentials = namedtuple("JwtCredentials", ("jwt_client_id", "jwt_username", "jwt_private_key"))

def log_backoff_attempt(details):
    LOGGER.info("HTTPError detected, triggering backoff: %d try", details.get("tries"))

def use_jwt_auth(config):
    flag = config.get("use_jwt_auth")
    return flag is True or (isinstance(flag, str) and flag.lower() == "true")

def parse_credentials(config):
    if use_jwt_auth(config):
        creds = JwtCredentials(*(config.get(key) for key in JwtCredentials._fields))
        if all(creds):
            return creds
        raise Exception(f"use_jwt_auth is enabled but config is missing one of {JwtCredentials._fields}")

    for cls in reversed((OAuthCredentials, PasswordCredentials)):
        creds = cls(*(config.get(key) for key in cls._fields))
        if all(creds):
            return creds

    raise Exception("Cannot create credentials from config.")


class SalesforceAuth:
    def __init__(self, credentials, is_sandbox=False):
        self.is_sandbox = is_sandbox
        self._credentials = credentials
        self._access_token = None
        self._instance_url = None
        self._auth_header = None
        self.login_timer = None

    def login(self):
        """Attempt to login and set the `instance_url` and `access_token` on success."""

    @property
    def rest_headers(self):
        return {"Authorization": f"Bearer {self._access_token}"}

    @property
    def bulk_headers(self):
        return {
            "X-SFDC-Session": self._access_token,
            "Content-Type": "application/json",
        }

    @property
    def instance_url(self):
        return self._instance_url

    @classmethod
    def from_credentials(cls, credentials, **kwargs):
        if isinstance(credentials, OAuthCredentials):
            return SalesforceAuthOAuth(credentials, **kwargs)

        if isinstance(credentials, PasswordCredentials):
            return SalesforceAuthPassword(credentials, **kwargs)

        if isinstance(credentials, JwtCredentials):
            return SalesforceAuthJwt(credentials, **kwargs)

        raise Exception("Invalid credentials")


class SalesforceAuthOAuth(SalesforceAuth):
    # The minimum expiration setting for SF Refresh Tokens is 15 minutes
    REFRESH_TOKEN_EXPIRATION_PERIOD = 900
    LOGIN_METHOD = "OAuth2"

    @property
    def _login_body(self):
        return {"grant_type": "refresh_token", **self._credentials._asdict()}

    @property
    def _login_host(self):
        if self.is_sandbox:
            return "https://test.salesforce.com"
        return "https://login.salesforce.com"

    @property
    def _login_url(self):
        return f"{self._login_host}/services/oauth2/token"

    def login(self):
        LOGGER.info("Attempting login via %s", self.LOGIN_METHOD)

        @backoff.on_exception(
            backoff.expo,
            Exception,
            max_tries=10,
            factor=2,
            on_backoff=log_backoff_attempt,
        )
        def _login():
            resp = None
            try:
                resp = requests.post(
                    self._login_url,
                    data=self._login_body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=30,
                )

                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if resp:
                    LOGGER.error(f"Response from Salesforce: {resp.text}")
                raise e

        auth = _login()
        LOGGER.info("%s login successful", self.LOGIN_METHOD)
        self._access_token = auth["access_token"]
        self._instance_url = auth["instance_url"]
        LOGGER.info("Starting new login timer")
        self.login_timer = threading.Timer(self.REFRESH_TOKEN_EXPIRATION_PERIOD, self.login)
        self.login_timer.start()


class SalesforceAuthJwt(SalesforceAuthOAuth):
    # Salesforce rejects assertions whose exp is more than 3 minutes out
    ASSERTION_LIFETIME = timedelta(minutes=3)
    LOGIN_METHOD = "OAuth2 JWT bearer"

    def __init__(self, credentials, **kwargs):
        super().__init__(credentials, **kwargs)
        pem = credentials.jwt_private_key.replace("\\n", "\n").encode()
        self._private_key = load_pem_private_key(pem, password=None)

    @property
    def _login_body(self):
        claims = {
            "iss": self._credentials.jwt_client_id,
            "sub": self._credentials.jwt_username,
            "aud": self._login_host,
            "exp": datetime.now(timezone.utc) + self.ASSERTION_LIFETIME,
        }
        return {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt.encode(claims, self._private_key, algorithm="RS256"),
        }


class SalesforceAuthPassword(SalesforceAuth):
    def login(self):
        login = SalesforceLogin(sandbox=self.is_sandbox, **self._credentials._asdict())

        self._access_token, host = login
        self._instance_url = "https://" + host
