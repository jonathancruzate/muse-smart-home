# MacroDroid 6.0 Schema Essentials

Condensed reference of the exact JSON serialization structures required to build the Universal Webhook Macro, Mode Poll Backstop, Notification Push, and Low Battery Alert macros in MacroDroid 6.0.

---

## 1. Root Export Wrapper & Macro Object

Every `.macro` file must be valid JSON with double-quoted keys and no trailing commas:

```json
{
  "macroExportVersion": 1,
  "macro": {
    "m_name": "Universal Webhook",
    "m_enabled": true,
    "m_completed": true,
    "m_GUID": 0,
    "aiGenerated": 1,
    "m_description": "Single-quoted strings inside description to avoid escaping issues.",
    "localVariables": [
      {
        "m_name": "qp",
        "m_type": 4,
        "isLocalVar": true
      },
      {
        "m_name": "req_body",
        "m_type": 2,
        "m_stringValue": "",
        "isLocalVar": true
      }
    ],
    "m_triggerList": [],
    "m_actionList": [],
    "m_constraintList": []
  },
  "globalVariables": [
    {
      "m_name": "muse_last_mode",
      "m_type": 2,
      "m_stringValue": "",
      "isLocalVar": false
    }
  ],
  "userIcons": null,
  "aiFeedback": "Notes for post-import steps."
}
```

### Variable Types (`m_type`)
- `0`: Boolean (`m_booleanValue`)
- `1`: Integer (`m_intValue`)
- `2`: String (`m_stringValue`)
- `3`: Decimal (`m_decimalValue`)
- `4`: Dictionary (`m_dictionary`)
- `5`: Array
- `6`: Color

Always set `"isLocalVar": true` on `localVariables` and `"isLocalVar": false` on `globalVariables` (never `isLocal`).

### GUID Rule
- `macro.m_GUID` is always `0`.
- Every trigger, action, and constraint requires `"m_SIGUID": 0` (or `112233445566771`, `112233445566772`, ... when referenced by another item).

---

## 2. Magic Text Reference

- **Variables:**
  - Global variable: `{v=var_name}`
  - Local variable: `{lv=var_name}`
  - Dictionary entry (key **inside** braces): `{lv=qp[do]}`, `{lv=qp[m]}`, `{v=dict[key]}`
- **System & Device:**
  - Battery & Power: `{battery}`, `{battery_int}`, `{power}` (charging state)
  - Network: `{ssid}`, `{ip}`, `{wifi_strength}`
  - Time: `{datetime}`, `{system_time}`, `{hour}`, `{minute}`
- **Notification Trigger (`NotificationTrigger`):**
  - `{not_app_package}`, `{not_app_name}`, `{not_title}`, `{notification}`, `{not_id}`
- **Note on Ringer & Screen State:**
  - MacroDroid 6.0 has no built-in magic text for ringer mode or screen-on state; leave clearly marked placeholders (e.g. `<MAP_RINGER_MODE_IN_UI>`, `<MAP_SCREEN_ON_IN_UI>`) in the status POST body or populate them from a helper variable in the UI.

---

## 3. Triggers (`m_triggerList`)

### `WebHookTrigger`
Fires when `https://trigger.macrodroid.com/<device_id>/<identifier>` is requested.
```json
{
  "m_classType": "WebHookTrigger",
  "m_SIGUID": 0,
  "identifier": "muse",
  "saveBodyVariableName": "req_body",
  "queryParamsDictionaryName": "qp"
}
```

### `RegularIntervalTrigger`
Fires on a repeating interval in seconds (e.g. `300` = every 5 minutes).
```json
{
  "m_classType": "RegularIntervalTrigger",
  "m_SIGUID": 0,
  "m_seconds": 300
}
```

### `NotificationTrigger`
Fires when a notification arrives. Use `matchOptionMessage` (`0` = Any, `1` = Matches, `2` = Contains, `3` = Excludes) — never `m_textFilter`.
```json
{
  "m_classType": "NotificationTrigger",
  "m_SIGUID": 0,
  "m_option": 0,
  "matchOptionMessage": 0,
  "messageContent": "",
  "separateTitleAndMessage": false,
  "ignoreCase": true
}
```

### `BatteryLevelTrigger`
Fires when battery crosses a threshold (`m_decreasesTo: true` for falling to threshold).
```json
{
  "m_classType": "BatteryLevelTrigger",
  "m_SIGUID": 0,
  "m_decreasesTo": true,
  "m_batteryLevel": 20,
  "m_option": 0
}
```

---

## 4. Actions (`m_actionList`)

### Conditional Flow: `IfConditionAction` / `ElseIfConditionAction` / `ElseAction` / `EndIfAction`
All conditional markers and inner actions sit in the **flat** `m_actionList` array. Every `IfConditionAction` chain must close with **one** `EndIfAction`.

```json
{
  "m_classType": "IfConditionAction",
  "m_SIGUID": 0,
  "m_constraintList": [
    {
      "m_classType": "MacroDroidVariableConstraint",
      "m_SIGUID": 0,
      "m_variable": {
        "m_name": "qp",
        "m_type": 4,
        "isLocalVar": true
      },
      "dictionaryKeys": {
        "keys": ["do"]
      },
      "m_stringComparisonType": 0,
      "m_stringEqual": true,
      "m_stringValue": "speak"
    }
  ]
}
```
Followed by the branch's actions, optional `ElseIfConditionAction` blocks, and a single closing:
```json
{
  "m_classType": "EndIfAction",
  "m_SIGUID": 0
}
```

### `HttpRequestAction`
Always use the nested `requestConfig` object (`requestType`: `0` = GET, `1` = POST, `2` = PUT, `3` = DELETE). Never use flat `urlToPerform` or `httpMethod` fields.

**Example POST (status / ack / notification push):**
```json
{
  "m_classType": "HttpRequestAction",
  "m_SIGUID": 0,
  "requestConfig": {
    "requestType": 1,
    "urlToOpen": "https://your-worker.example.com/st-webhook/ingress?secret=<your-secret>",
    "contentType": "text/plain",
    "contentBodySource": 0,
    "contentBodyText": "phone-status\ncom.macrodroid\nDevice Status\nbattery={battery_int}\ncharging={power}\nwifi_ssid={ssid}\nringer_mode=<MAP_IN_UI>\nscreen_on=<MAP_IN_UI>",
    "blockNextAction": true,
    "saveResponseType": 0,
    "headerParams": [],
    "queryParams": []
  }
}
```

**Example GET saving response to a local variable (`mode-desired` poll):**
```json
{
  "m_classType": "HttpRequestAction",
  "m_SIGUID": 0,
  "requestConfig": {
    "requestType": 0,
    "urlToOpen": "https://your-worker.example.com/st-webhook/mode-desired?secret=<your-secret>",
    "blockNextAction": true,
    "saveResponseType": 1,
    "responseVariableName": "desired_raw",
    "headerParams": [],
    "queryParams": []
  }
}
```

### `JsonParseAction`
Parses a JSON string variable into a dictionary variable.
```json
{
  "m_classType": "JsonParseAction",
  "m_SIGUID": 0,
  "stringVarName": "desired_raw",
  "dictionaryVarName": "desired_dict",
  "parseColors": false
}
```

### `SetVariableAction`
Sets a variable using the nested `m_variable` object and `m_newStringValue` (never `m_variableName` or `m_value`).
```json
{
  "m_classType": "SetVariableAction",
  "m_SIGUID": 0,
  "m_variable": {
    "m_name": "desired_mode",
    "m_type": 2,
    "isLocalVar": true
  },
  "m_newStringValue": "{lv=desired_dict[mode]}"
}
```

### `SpeakTextAction`
Speaks text aloud via TTS.
```json
{
  "m_classType": "SpeakTextAction",
  "m_SIGUID": 0,
  "m_textToSay": "{lv=req_body}",
  "m_pitch": 1.0,
  "m_speed": 1.0,
  "m_queue": true,
  "m_audioStream": 5,
  "m_waitToFinish": true
}
```

### `SamsungRoutinesAction`
Invokes a Samsung Modes & Routines manual routine. On modern One UI versions with shortcut picker support, select the routine interactively in the MacroDroid UI after importing the macro so its device-local `routineUuid` is captured.
```json
{
  "m_classType": "SamsungRoutinesAction",
  "m_SIGUID": 0,
  "routineName": "Switch Sleep mode",
  "actionMode": 0
}
```

---

## 5. Constraints (`m_constraintList`)

### `MacroDroidVariableConstraint`
Used inside `IfConditionAction.m_constraintList` (or macro/trigger/action `m_constraintList`) to compare variables.

**Compare string variable against a literal:**
```json
{
  "m_classType": "MacroDroidVariableConstraint",
  "m_SIGUID": 0,
  "m_variable": {
    "m_name": "desired_mode",
    "m_type": 2,
    "isLocalVar": true
  },
  "m_stringComparisonType": 0,
  "m_stringEqual": true,
  "m_stringValue": "night"
}
```

**Compare string variable against another variable (not-equals check for Mode Poll):**
```json
{
  "m_classType": "MacroDroidVariableConstraint",
  "m_SIGUID": 0,
  "m_variable": {
    "m_name": "desired_mode",
    "m_type": 2,
    "isLocalVar": true
  },
  "m_intCompareVariable": true,
  "m_otherValueToCompare": {
    "m_name": "muse_last_mode",
    "m_type": 2,
    "isLocalVar": false
  },
  "m_stringComparisonType": 0,
  "m_stringEqual": false
}
```
