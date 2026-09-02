# tap-salesforce

[![CircleCI Build Status](https://circleci.com/gh/singer-io/tap-salesforce.png)](https://circleci.com/gh/singer-io/tap-salesforce.png)


[Singer](https://www.singer.io/) tap that extracts data from a [Salesforce](https://www.salesforce.com/) Account and produces JSON-formatted data following the [Singer spec](https://github.com/singer-io/getting-started/blob/master/SPEC.md).

This is a forked version of [tap-salesforce (v1.4.24)](https://github.com/singer-io/tap-salesforce) that maintained by the Meltano team.

Main differences from the original version:

- Support for `username/password/security_token` authentication
- Support for concurrent execution (8 threads by default) when accessing different API endpoints to speed up the extraction process
- Support for much faster discovery

# Quickstart

## Install the tap

This version of `tap-salesforce` is not available on PyPi, so you have to fetch it directly from the Meltano maintained project:

```bash
python3 -m venv venv
source venv/bin/activate
pip install git+https://github.com/MeltanoLabs/tap-salesforce.git
```

## Create a Config file

**Required**
```
{
  "api_type": "BULK2",
  "select_fields_by_default": true,
}
```

**Required for OAuth based authentication**
```
{
  "client_id": "secret_client_id",
  "client_secret": "secret_client_secret",
  "refresh_token": "abc123",
}
```

**Required for username/password based authentication**
```
{
  "username": "Account Email",
  "password": "Account Password",
  "security_token": "Security Token",
}
```

**Required for OAuth JWT bearer authentication**
```
{
  "use_jwt_auth": true,
  "jwt_client_id": "connected_app_consumer_key",
  "jwt_username": "integration.user@example.com",
  "jwt_private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----",
}
```

**Optional**
```
{
  "start_date": "2017-11-02T00:00:00Z",
  "state_message_threshold": 1000,
  "max_workers": 8,
  "streams_to_discover": ["Lead", "LeadHistory"]
}
```

The `client_id` and `client_secret` keys are your OAuth Salesforce App secrets. The `refresh_token` is a secret created during the OAuth flow. For more info on the Salesforce OAuth flow, visit the [Salesforce documentation](https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/intro_understanding_web_server_oauth_flow.htm).

When `use_jwt_auth` is `true` (or the string `"true"`), the tap ignores the other credential keys and uses the [OAuth 2.0 JWT bearer flow](https://help.salesforce.com/s/articleView?id=sf.remoteaccess_oauth_jwt_flow.htm): it signs an RS256 assertion with `jwt_private_key` (the PEM private key whose certificate is uploaded to the connected app identified by `jwt_client_id`) for the user `jwt_username`, and exchanges it for an access token. No client secret or refresh token is involved, so concurrent tap runs cannot invalidate each other's tokens. `jwt_private_key` may carry literal `\n` sequences in place of newlines (as a key pasted into a single-line environment variable does); the tap normalizes them and fails at startup if the key does not parse. When the flag is absent or false, the existing OAuth/password inference applies unchanged.

The `start_date` is used by the tap as a bound on SOQL queries when searching for records.  This should be an [RFC3339](https://www.ietf.org/rfc/rfc3339.txt) formatted date-time, like "2018-01-08T00:00:00Z". For more details, see the [Singer best practices for dates](https://github.com/singer-io/getting-started/blob/master/BEST_PRACTICES.md#dates).

The `api_type` is used to switch the behavior of the tap between using Salesforce's "REST", "BULK" and "BULK 2.0" APIs (each using the `queryAll` operation to include deleted and archived records). When new fields are discovered in Salesforce objects, the `select_fields_by_default` key describes whether or not the tap will select those fields by default.

The `state_message_threshold` is used to throttle how often STATE messages are generated when the tap is using the "REST" API. This is a balance between not slowing down execution due to too many STATE messages produced and how many records must be fetched again if a tap fails unexpectedly. Defaults to 1000 (generate a STATE message every 1000 records).

The `max_workers` value is used to set the maximum number of threads used in order to concurrently extract data for streams. Defaults to 8 (extract data for 8 streams in paralel).

The `streams_to_discover` value may contain a list of Salesforce streams (each ending up in a target table) for which the discovery is handled.
By default, discovery is handled for all existing streams, which can take several minutes. With just several entities which users typically need it is running few seconds.
The disadvantage is that you have to keep this list in sync with the `select` section, where you specify all properties(each ending up in a table column).

## Run Discovery

To run discovery mode, execute the tap with the config file.

```
tap-salesforce --config config.json --discover > properties.json
```

## Sync Data

To sync data, select fields in the `properties.json` output and run the tap.

```
tap-salesforce --config config.json --properties properties.json [--state state.json]
```

Copyright &copy; 2017 Stitch
