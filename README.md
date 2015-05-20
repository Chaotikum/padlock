# config.yml

    host: localhost
    port: 1000
    id: '123ABC'
    locks:
      - name: Tür 1
        id: '123456'
      - name: Tür 2
        id: 'ABCDEF'

# API

## GET /locks

Returns a JSON list of locks:

    [
      {
        "name": "Tür 1",
        "battery_low": false,
        "id": "123456",
        "uncertain": false,
        "locked": false,
        "error": 0
      },
      {
        "name": "Tür 2",
        "battery_low": false,
        "id": "ABCDEF",
        "uncertain": false,
        "locked": false,
        "error": 0
      }
    ]

Example:

    curl --key my.key --cert my.crt https://$HOST/locks

## GET /lock/{id}

Get JSON of a specific lock.

Example:

    curl --key my.key --cert my.crt https://$HOST/lock/123456

## PUT /lock/{id}

Content: "lock" or "unlock"

Change state of lock. Will return ACK or an HTTP error code.

Example:

    curl --key my.key --cert my.crt https://$HOST/lock/123456 -X PUT --data unlock

## PUT /door

Trigger door opener.

Example:

    curl --key my.key --cert my.crt https://$HOST/door -X PUT

## GET /locks/stream

Returns an event stream. Same format as /locks.

## GET /lock/{id}/stream

Returns an event stream. Same format as /lock/{id}.

