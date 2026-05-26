# Debugging ending

To debug, you generally have two options.

## Logging

**ending** logs everything into `~/ending/ending.log`. Increase the log level to `SQL` to view SQL payloads as they are sent.
To do so:

- CLI: Use the `--debug` flag
- Library: use `logging.set_level("SQL")`

## Interception

You're generally going to use ending to send data over the network. Just fire up wireshark or a proxy (Burp ?) to view it live.