# AST nodes internals

**ending** builds its payloads as an SQL AST ([Abstract Syntax Tree](https://en.wikipedia.org/wiki/Abstract_syntax_tree)). They are then compiled into valid SQL syntax for a DBMS using a compiler.
The logic of the framework is built on these AST nodes, allowing for generic code.

Import AST nodes using:

```python
from ending.ast import *
```

## Why AST nodes

**ending** builds SQL payloads as an Abstract Syntax Tree rather than raw strings. The tree is then compiled into valid SQL for a specific DBMS by a `Compiler`.

This gives three concrete benefits:

**DBMS agnosticism.** The same node tree compiles into different SQL dialects without changing the node definition.

```python
node = Substring(Query("users").columns("password"), 0, 1)

mysql.Compiler().compile(node)   # SUBSTR((SELECT password FROM users),1,1)
mssql.Compiler().compile(node)   # SUBSTRING((SELECT password FROM users),1,1)
```

**Type propagation.** Every node carries SQL type information (`TextType`, `IntType`, etc.), derived automatically from its children. The framework uses this to properly perform SQL injections, using, for instance, statistics.

**Composability.** Because nodes are plain Python objects, complex payloads are assembled from smaller pieces using operators (`==`, `&`, `|`) and node constructors, without string manipulation.

---

## Instantiating nodes

Nodes are instantiated like any other Python class:

```python
from ending.ast import *

Identifier("username")
Value(42)
Value("admin")
```

### Auto-casting of raw values

Most AST nodes accept raw Python values and cast them automatically to the most logical node. You rarely need to wrap a value explicitly.

```python
# These two are identical, because comparison casts its operands as Value
Comparison(Identifier("role"), "=", Value("admin"))
Comparison(Identifier("role"), "=", "admin")       # "admin" → Value("admin")
```

Another example: `Alias` nodes expects both its column and alias to be identifiers, so the next two lines are equivalent:

```python
# SQL equivalent: username AS u
alias = Alias(Identifier("username"), Identifier("u"))
alias = Alias("username", "u") # "username" → Identifier("username"), same for "u"
```

### Operator shortcuts

`Node` overloads comparison and logical operators, so conditions read naturally:

```python
id_ = Identifier("id")

id_ == 1          # Comparison(id_, "=", Value(1))
id_ != 0          # Comparison(id_, "!=", Value(0))
id_ > 10          # Comparison(id_, ">", Value(10))

(id_ == 1) & (id_ != 0)   # LogicalOperation(..., "AND", ...)
(id_ == 1) | (id_ == 2)   # LogicalOperation(..., "OR",  ...)
```

### Immutability

Nodes are immutable. Attributes cannot be changed after creation:

```python
v = Value(42)
v.value = 99   # AttributeError: Cannot set attribute 'value', object is frozen
```

To produce a modified copy, use `dataclasses.replace` or the `with_metadata` helper described below.

---

## Node metadata

Every node exposes a `metadata` attribute with three properties:

| Property   | Type       | Meaning                                          |
|------------|------------|--------------------------------------------------|
| `type`     | `NodeType` | SQL type of the expression result                |
| `single`   | `bool`     | Whether the node returns a single row            |
| `nullable` | `bool`     | Whether the result can be `NULL`                 |

These are computed on first access from the node's structure and cached. 

For instance, a `Value` node as an adequate type :

```python
string = Value("some string")
string.metadata.type  # TextType()

string = Value(123)
string.metadata.type  # IntType()
```

And its `single` and `nullable` values are set to `True` and `False` respectively.

On the other hand, by default, an `Identifier` has an `UnknownType` type, is not single, and is nullable.

More complex nodes derive their metadata from their subnodes. For instance:

```python
# Concatenates "some string", "a", "b"
concatenation = Concatenation(string, "a", "b")
concatenation.metadata.single  # true, as every subnode is single

# Concatenates `username`, "a", "b"
concatenation = Concatenation(Identifier("username"), "a", "b")
concatenation.metadata.single  # false, as one of the subnodes is not single
```

If you wish to set part of the metadata manually, add them to the constructor:

```python
username = Identifier("username", type=TextType(), nullable=False)
```

Or create a copy of a node with `Node.with_metadata()`:

```python
username = Identifier("username")
username = username.with_metadata(type=TextType(), nullable=False)
```

### Typing

Typing has its own section [here](typing.md).

## Common nodes

### Queries

The most used AST node is `Query`, which represents a `SELECT` query.

```python
# SQL: SELECT username, password, email FROM users
query = Query("users").columns("username", "password", "email")
```

Adding conditions:

```python
# SQL: SELECT username, password, email FROM users WHERE role='admin'
query = query.where(Identifier("role") == Value("admin"))
```

Setting bounds:

```python
# SQL: SELECT username, password, email FROM users WHERE role='admin' LIMIT 2, 3
query = query.limit(2, 3)
```

It's easy to reformat the query to your needs. We can compute the `COUNT(*)` for the previous query:

```python
# SQL: SELECT COUNT(*) FROM users WHERE role='admin'
count_query = query.limit(None).columns(Count())
```

### Identifiers

Table names, column names, etc. are called *identifiers*. Use them to create conditions or to set the column type.

```python
role = Identifier("role")

# SQL: role='admin'
condition = (role == 'admin')
```

In addition to `==`, other operators are supported: `!=`, `>`, `<=`, etc.

```python
user_id = Identifier("user_id")
date_activation = Identifier("date_activation")

# SQL: date_activation > 1636717727 AND user_id <> 0
condition = (date_activation > 1636717727) & (user_id != 0)
```

### Values

Your AST might contain raw SQL values, for instance in this statement:

```python
role = Identifier("role")
value_admin = Value("admin")

# SQL: role='admin'
condition = (role == value_admin)
```

!!! notes

    In the section above, we don't need to create Value() nodes to build the comparisons: ending converts raw data into values automatically.


### Function calls

To call SQL functions, use the `Function` node, which provides syntaxic sugar:

```python
# SQL: ExtractValue('a', 'b', 'c')
Function["ExtractValue"]("a", "b", "c")
```

Raw values are automatically cast into `Value` nodes, but you can input other node types:

```python
# SQL: LOCATE('a', user)
Function["LOCATE"]("a", Identifier("user"))
```

## Metanodes

Some standard SQL functions are available under different names over different DBMS. For instance, to get part of a string, you'd call `SUBSTRING()` on SQL Server and PostgreSQL, but you'd call `SUBSTR()` on Oracle. As such, metanodes are nodes that are available on all DBMS but under a different syntax or name. The compiler is tasked to convert them into valid syntax. Metanodes include, but are not limited to:

- `Substring()`: Takes part of a string
- `Length()`: Computes the length of a string
- `Ord()`: Returns the ordinal value for a character or byte

## Creating new nodes

### Minimal example

Every new node class needs `@dataclass(eq=False)` and `@node`:

```python
from dataclasses import dataclass
from ending.ast import node, Node, Value, NodeType, IntType

@node
@dataclass(eq=False)
class Absolute(Node):
    """ABS(<value>)"""

    value: Value

    def _compute_type(self) -> NodeType:
        return IntType(min=0)

    def _compute_single(self) -> bool:
        return self.value.metadata.single

    def _compute_nullable(self) -> bool:
        return self.value.metadata.nullable
```

Because `Value` is a `CastableNode`, raw values are auto-cast: `Absolute(42)` works and wraps `42` in a `Value`.

### Field types and casting behaviour

`@node` reads the field type annotations and determines how each field is handled at instantiation:

| Field type                 | Behaviour at instantiation            |
|----------------------------|---------------------------------------|
| Subclass of `CastableNode` | Raw values are cast automatically     |
| Subclass of `Node`         | Must already be a node; no casting    |
| `str`, `int`, `bool`       | Type-checked, no casting              |
| `NodeType` or `Any`        | Not checked                           |

For fields with complex semantics that cannot be expressed as a type annotation (e.g. a tuple of pairs), set `__node_attrs__` manually and handle the field in `_set_fields`:

```python
@node
@dataclass(eq=False)
class MyNode(Node):
    condition: Node
    pairs: tuple  # list of (Value, Value)

    __node_attrs__: ClassVar[NodeAttributes] = NodeAttributes(
        nodes=[("condition", Node)],
        names={"condition"},
    )

    def _set_fields(self) -> None:
        super()._set_fields()
        self.pairs = tuple(
            (Value.from_value(a), Value.from_value(b)) for a, b in self.pairs
        )
```

### Using `CastableNode` as the base

If your node should itself be auto-castable from a raw value (so it can be used as a field type in other nodes), inherit from `CastableNode` instead of `Node`:

```python
@node
@dataclass(eq=False)
class Identifier(CastableNode):
    name: str
```

Any node field typed `Identifier` then accepts a plain string.

### Compiler hook

For a new node to render as SQL, add a `compile_<ClassName>` method to the relevant compiler:

```python
class MyCompiler(mysql.Compiler):
    def compile_Absolute(self, node: Absolute, spec: str) -> str:
        return f"ABS({node.value})"
```

`{node.value}` compiles the child node through the same compiler recursively.


## Examples

### Blind SQL injection

Compare the first character of the admin password to a list of characters.

Setup initial query.

```python
# SQL: SELECT password FROM users
password_query = Query('users').columns('password')
```

Refine query: add the role condition, and ask for the first row only:

```python
# SQL: SELECT password FROM users WHERE role='admin' LIMIT 1
admin_query = password_query.where(Identifier('role') == 'admin').limit(1)
```

Take the first character of the password:


```python
# SQL: SUBSTRING((SELECT password FROM users WHERE role='admin' LIMIT 1), 1, 1)
password_substring = Substring(admin_query, 0, 1)
```

And finally, compare it to the first 8 hexadecimal chars `0..7`:

```python
# SQL: SUBSTRING((...), 1, 1) IN ('0', '1', '2', ..., '7')
password_substring_is_in = IsIn(password_substring, ['0','1','2','3','4','5','6','7'])
```