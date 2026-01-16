import logging
import threading
from collections import namedtuple

import backoff
import requests
from simple_salesforce import SalesforceLogin

LOGGER = logging.getLogger(__name__)

OAuthCredentials = namedtuple("OAuthCredentials", ("client_id", "client_secret", "refresh_token"))

PasswordCredentials = namedtuple("PasswordCredentials", ("username", "password", "security_token"))

JWTCredentials = namedtuple("JWTCredentials", ("username", "consumer_key", "private_key"))


def log_backoff_attempt(details):
    LOGGER.info("HTTPError detected, triggering backoff: %d try", details.get("tries"))


def parse_credentials(config):
    # Explicit JWT auth flag takes priority
    use_jwt_auth = config.get("use_jwt_auth")
    if use_jwt_auth is True or (isinstance(use_jwt_auth, str) and use_jwt_auth.lower() in ("true", "1")):
        creds = JWTCredentials(*(config.get(key) for key in JWTCredentials._fields))
        if all(creds):
            return creds
        raise Exception(
            "use_jwt_auth is enabled but missing required JWT credentials: username, consumer_key, private_key"
        )

    # Fall back to existing inference for backward compatibility
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

        if isinstance(credentials, JWTCredentials):
            return SalesforceAuthJWT(credentials, **kwargs)

        raise Exception("Invalid credentials")


class SalesforceAuthOAuth(SalesforceAuth):
    # The minimum expiration setting for SF Refresh Tokens is 15 minutes
    REFRESH_TOKEN_EXPIRATION_PERIOD = 900

    @property
    def _login_body(self):
        return {"grant_type": "refresh_token", **self._credentials._asdict()}

    @property
    def _login_url(self):
        login_url = "https://login.salesforce.com/services/oauth2/token"

        if self.is_sandbox:
            login_url = "https://test.salesforce.com/services/oauth2/token"

        return login_url

    def login(self):
        LOGGER.info("Attempting login via OAuth2")

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
        LOGGER.info("OAuth2 login successful")
        self._access_token = auth["access_token"]
        self._instance_url = auth["instance_url"]
        LOGGER.info("Starting new login timer")
        self.login_timer = threading.Timer(self.REFRESH_TOKEN_EXPIRATION_PERIOD, self.login)
        self.login_timer.start()


class SalesforceAuthPassword(SalesforceAuth):
    def login(self):
        login = SalesforceLogin(sandbox=self.is_sandbox, **self._credentials._asdict())

        self._access_token, host = login
        self._instance_url = "https://" + host


class SalesforceAuthJWT(SalesforceAuth):
    # Refresh before token expiration (Salesforce tokens typically expire after 2 hours)
    TOKEN_REFRESH_PERIOD = 900

    def login(self):
        LOGGER.info("Attempting login via JWT Bearer")

        @backoff.on_exception(
            backoff.expo,
            Exception,
            max_tries=10,
            factor=2,
            on_backoff=log_backoff_attempt,
        )
        def _login():
            domain = "test" if self.is_sandbox else "login"
            return SalesforceLogin(
                username=self._credentials.username,
                consumer_key=self._credentials.consumer_key,
                privatekey=self._credentials.private_key,
                domain=domain,
            )

        access_token, host = _login()
        LOGGER.info("JWT Bearer login successful")
        self._access_token = access_token
        self._instance_url = "https://" + host
        LOGGER.info("Starting new login timer")
        self.login_timer = threading.Timer(self.TOKEN_REFRESH_PERIOD, self.login)
        self.login_timer.start()
