# AST compilers

Compilers convert an AST into a DBMS-specific SQL syntax.
Each database has its own compiler, available as the `Compiler` class.

```python
from ending.db import postgres

compiler = postgres.Compiler(quote=quoting.singlequote)
```


## Compiling a node

To compile an AST `Node`, use `compile()`.

```python
>>> query = Query("users").columns("username", "password")
>>> compiler.compile(query)
'SELECT username,password FROM users'
```

In order to be used with f-strings or `str()`, nodes can be wrapped with a compiler:

```
>>> compiler.wrap(query)
>>> f"A' UNION {query} -- -"
"A' UNION SELECT username,password FROM users -- -"
```

## String encoding

The `quote` argument defines how strings should be encoded by the compiler:

```python
>>> condition = Identifier("role") == 'admin'
>>> compiler = mysql.Compiler(quote=quoting.singlequote)
>>> compiler.compile(condition)
"role='admin'"
>>> compiler = mysql.Compiler(quote=quoting.hexadecimal)
>>> compiler.compile(condition)
"role=0x61646d696e"
```

!!! notes

    Refer to `ending.util.quoting` to check out every string quoting function.

## Development

### Node compilation

Compilers have a `compile_X()` method to compile each node type `X`. Simply override the method to change the resulting SQL syntax.

To convert the standard `SUBSTRING(a, b, c)` to `SUBSTRING(a FROM b FOR c)` (supported by MySQL, for instance), override `compile_Substring()`:

```python
class NoCommaCompiler(mysql.Compiler):
    def compile_Substring(self, substring: Substring, s):
        if substring.length is not None:
            return f"SUBSTRING({substring.string} FROM {substring.start+1} FOR {substring.length})"
        else:
            return f"SUBSTRING({substring.string} FROM {substring.start+1})"
```

```python
>>> compiler = NoCommaCompiler(quote=quoting.singlequote)
>>> compiler.compile(Substring(Identifier("password"), 10, 1))
'SUBSTRING(password FROM 11 FOR 1)'
```

### Recursivity

In addition to returning a string directly, `compile_X()` methods can return another node, which will in its turn get compiled.

As an example, if a target automatically encodes `<` characters, we can get rid of them by reversing the comparison: `a < b` becomes `b > a`. This is handled by the `Comparison` node. Instead of returning a string directly, we can return another `Comparison`, which will in turn get compiled.

```python
class NoLessThanCompiler(mysql.Compiler):
    def compile_Comparison(self, comparison: Comparison, s: str) -> str:
        # Avoid the use of "<" by reversing the comparison
        # a < b --> b > a
        if comparison.operator == "<":
            return Comparison(comparison.right, ">", comparison.left)
        return super().compile_Comparison(comparison, s)
```

```python
>>> compiler = NoLessThanCompiler(quote=quoting.singlequote)
>>> compiler.compile(Identifier("id") < 100)
'100>id'
```