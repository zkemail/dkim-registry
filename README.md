## To use with PostgreSQL (debian-based OS), run these commands first to prep the database:

Set up PostgreSQL
```
$ sudo apt update
$ sudo apt install postgresql postgresql-contrib
$ sudo systemctl start postgresql.service
$ sudo -u postgres psql
postgres=# CREATE USER dkim password 'changeme'; # please use a better password
CREATE ROLE
postgres=#
$ sudo -u postgres createdb dkim
```


To set up python environment, run this:
```
$ python3 -m venv venv
$ source ./venv/bin/activate
$ pip install -r requirements.txt
```


To begin (or resume) collection of data, run this:
```
(venv) $ ./main.py --collect # collect the data (may take some time)
```

To browse the data, run this command, and view it from browser
```
(venv) $ ./main.py --webpage # launches web server on port 8001
```

You can run the server in one process while leaving the collector running
in a separate process.

To add additional sites to serve beyond the ones from alexa, add them
to the file "additional_sites.txt".

To add more selectors to query on, add them to the `selectors.txt` file. Each
additional selector added will double the total number of queries made, so
the number of queries grows geometrically. But it will be faster in subsequent runs unless you specify the `rescan` flag for particular selectors.


## Knobs (see `main.py`)
Depending on resources available on the host running the collection, you
may choose to increase or decrease the number of simultaneous queries by
changing `chunk_size`.


## Webpage
Browse to here to see the list of domains for which DKIM(s) were found in DNS:
http://localhost:8001/


## API
To query all records of a specific domain, e.g., arca.live:

http://localhost:8001/api/dkim/?domain=arca.live


To query ALL the data (might be slow):

http://localhost:8001/api/dkim/

