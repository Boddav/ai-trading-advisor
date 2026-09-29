#pragma once
// Copy to builtin_creds.h (NOT committed, see .gitignore) to build the plugin with a
// built-in cTrader Open API app. Without it, put Client ID / Secret in accounts.csv
// (User / Pass columns) - the plugin reads them from there or from oauth_token.json.
#define CTRADER_BUILTIN_CLIENT_ID     "your_client_id"
#define CTRADER_BUILTIN_CLIENT_SECRET "your_client_secret"
