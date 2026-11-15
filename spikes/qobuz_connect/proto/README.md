Copied unchanged from [ciaens/qobuz-connect](https://github.com/ciaens/qobuz-connect)
(MIT, commit `ccc2c2b`), which took them from the Qobuz web player, so field
numbers and enum values match the official apps.

- `qcloud.proto`: the outer frame of the cloud websocket
- `qconnect.proto`: the Qobuz Connect messages carried inside `qcloud.Payload`
