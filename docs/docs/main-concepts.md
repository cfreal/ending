# A different approach to SQL injection

Most tools work fine for straightforward SQL injections over HTTP. However, when dealing with a complex case, a WAF, or other limitations, you often end up writing a custom script to inject your payloads. This is why **ending** was built.

## In short

### Python-based target definitions

In **ending**, targets are defined using Python design files.

Instead of only configuring a URL and parameters through CLI arguments, you write a small Python method that sends an SQL payload to the target.

This function can be as simple as a single HTTP request — or as complex as needed.

Because it’s Python, you can naturally handle:

- authentication and sessions
- custom headers and encodings
- non-HTTP protocols
- WAF bypass logic
- complex request flows

For simple targets, this is often only a few lines of code. For complex targets, it removes the need to switch tools entirely.

### AST-based SQL generation 

**ending** operates on the SQL Abstract Syntax Tree (AST) level, rather than building payloads as raw strings.

- Injection techniques (UNION, error-based, blind, time-based, etc.) are implemented in a generic and reusable way
- Each DBMS translates the AST into its own SQL dialect

Most of the time, you don't need to get that deep into the tool, as ending does everything itself. But if you face a complex injection, you can change the way AST nodes are converted into SQL syntax, and thus build advanced bypasses, such as:

- To get rid of badchars (for instance, `a < b` can be written as `a BETWEEN 0 AND b`)
- To make function calls less easy to spot for the WAF: `SUBSTR(a, b)` becomes `SUBSTR# comment\n(a,b)`

And it only takes a few lines of code!

## In more details

### Logic vs code: working with the AST

The idea of using an AST-based SQL injection engine is very similar to the ones from [Doctrine](https://www.doctrine-project.org/) or [SQL Alchemy](https://www.sqlalchemy.org/): a unified, object-oriented syntax which can be converted into a DBMS-specific one. This stems from the fact that SQL injections algorithms are independant of the target [Database Management Systems (DBMS)](https://en.wikipedia.org/wiki/Database#Database_management_system): when you're doing a blind SQL injection on MySQL, Oracle, or SQLite, you'll always have the *same algorithm* – only the *syntax changes*.

AST nodes are represented by Python classes:

```python
# Creates a SELECT query on table `users` which takes the 5th `password` row
query = Query("users").columns("password").limit(5, 1)
# Creates a comparison that verifies that the 3rd character of the previous query is `b`
comparison = Substring(query, 3, 1) == 'b'
```

This AST can then be converted into DBMS-specific syntax using compilers:

```python
# MySQL: SUBSTR((SELECT email FROM users LIMIT 5,1), 3, 1)='b'
print(mysql.Compiler(singlequote).compile(comparison))

# PostgreSQL: substring((SELECT email FROM users LIMIT 1 OFFSET 5), 3, 1)='b'
print(postgres.Compiler(singlequote).compile(comparison))

# Microsoft SQL Server: SUBSTRING((SELECT password FROM users ORDER BY 1 OFFSET 5 ROWS FETCH NEXT 1 ROWS ONLY),3,1)='b'
print(mssql.Compiler(singlequote).compile(comparison))
```

This has a lot of positive effects on the code:

- using classes instead of raw SQL strings makes it very **readable** and **pythonic**;
- most of the code is **generic**, SQL injection methods are independant from the DBMS;
- implementing complex syntax changes can be done in seconds, by simply editing how a compiler converts an AST node into code.



### Python-based configuration

Generally, SQL injection tools provide CLI arguments to set the target URL, HTTP method, cookies, *etc.* This works fine in generic cases, but proves tedious when your injection is a little bit off the track: maybe you're attacking websockets (or another protocol entirely), or targeting a post-authentication page (thus needing to log in), or maybe you want to manually throttle the injection speed to avoid getting caught by the WAF.

With **ending**, injections are configured using a python class called a **design**. The design defines how **ending** sends its payloads, all in python. It makes it easy to inject over custom protocols, handle authentication, throttle the interactions...

If the injection requires you to issue 10 HTTP requests before getting the result, it can be easily implemented. If you need to log back in every time your blind SQL injection returns false, it can be implemented. If you want to inject over some protocol and get the results over another, it can be done as well.

This gives you a lot of liberties: you completely control how you inject.

Here is a design file for a simple, HTTP based SQL injection:

```python
#!/usr/bin/env python3
# Ending design file

from __future__ import annotations

from ending import *
from ending.ast import *
from ending.cli.design import HTTPDesign
from ending.util import quoting


from ending.db.mysql import *


class Design(HTTPDesign):
    async def send(self, payload: str="1000") -> bytes:
        """Sends given payload to the target and returns the response as bytes.
        """
        response = await self.session.get(
            "http://target.com/vulnerabilities/sqli/",
            params={
                "id": payload,
                "Submit": "Submit"
            }
        )
        return response.content
```