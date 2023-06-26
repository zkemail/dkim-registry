#!/usr/bin/env python3
import asyncio
import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
import re
from tqdm import tqdm
from typing import List
import urllib.request
import zipfile

from fastapi import FastAPI, Request
from sqlalchemy.orm import sessionmaker, declarative_base
import uvicorn

from sqlalchemy import create_engine, Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from fastapi.templating import Jinja2Templates

workdir = Path(__name__).resolve().parent
chunk_size = 10
all_sites = [] # see get_all_sites()

api = FastAPI()
db_in_use = 'not_postgres'
if db_in_use == 'postgres':
    engine = create_engine("postgresql://dkim:changeme@localhost:5432/dkim")
else:
    engine = create_engine(
        f"sqlite:///{workdir}/data/dkim.db",
        connect_args={"check_same_thread": False},
    )
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Domain(Base):
    __tablename__ = "domains"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    dkims = relationship("Dkim", back_populates="domain", cascade='all, delete-orphan')


class Dkim(Base):
    __tablename__ = "dkims"
    id = Column(Integer, primary_key=True, index=True)
    public_key = Column(String, unique=True, index=True)
    selector = Column(String)
    key_type = Column(String)
    domain_id = Column(Integer, ForeignKey('domains.id'))
    domain = relationship("Domain", back_populates="dkims")
    #date_added


Base.metadata.create_all(bind=engine)


app = FastAPI()
app.mount("/api", api)
templates = Jinja2Templates(directory="templates")


@app.get("/")
async def domain_list(request: Request):
    db = SessionLocal()
    domains = db.query(Domain).all()
    @dataclass
    class D:
        name: str
        selectors: str
    items = []
    for domain in domains:
        selectors = list(set([d.selector for d in domain.dkims]))
        items.append(D(domain.name, ', '.join(selectors)))
    return templates.TemplateResponse('index.html', {'request': request, 'domains': items})


@api.get("/dkim/")
async def read_dkims(domain: str = None, id: int = None):
    db = SessionLocal()
    if domain:
        domain = db.query(Domain).filter_by(name=domain).one()
        return domain.dkims
    elif id:
        dkims = db.query(Dkim)
        return dkims.filter(Dkim.id == id).all()


async def query_selectors(domains: List[str]):
    db = SessionLocal()
    domain_ids = db.query(Domain).filter(Domain.name.in_(domains))
    dkims = db.query(Dkim).filter(Dkim.domain_id.in_([d.id for d in domain_ids.all()]))
    return list(set([d.selector for d in dkims]))


async def query_domains(selectors: List[str] = []):
    db = SessionLocal()
    if selectors:
        dkims = db.query(Dkim).filter(Dkim.selector.in_(selectors))
        return list(set([d.domain.name for d in dkims]))
    return [d.name for d in db.query(Domain).all()]


async def create_dkim(domain: str, public_key: str, key_type: str, selector: str):
    db = SessionLocal()
    try:
        domain = db.query(Domain).filter_by(name=domain).one()
    except:
        domain = Domain(name=domain)
    dkim = Dkim(domain=domain, public_key=public_key, key_type=key_type, selector=selector)
    db.add(dkim)
    try:
        db.commit()
    except Exception:
        db.rollback()


def load_sites():
    data = []
    with open(workdir / 'data/top-1m.csv.deprecated') as f:
        reader = csv.reader(f, delimiter=',')
        for i in range(8):
            next(reader) # skip header
        for row in reader:
            data.append(row[1])
    if (workdir / 'additional_sites.txt').is_file():
        with open(workdir / 'additional_sites.txt') as f:
            for row in f.readlines():
                data.append(row.strip())
    return data


def get_all_sites():
    global all_sites
    if all_sites:
        return all_sites
    fname = Path('top-1m.csv.zip')
    if (workdir / fname).is_file():
        all_sites = load_sites()
        return all_sites
    url = f'http://s3.amazonaws.com/alexa-static/{fname}'
    urllib.request.urlretrieve(url, fname)
    with zipfile.ZipFile(fname, 'r') as zipf:
        zipf.extractall(workdir / 'data')
    fname.rename(workdir / f'data/{fname}')
    all_sites = load_sites()
    return all_sites


async def run_command(tag, command):
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    return tag, stdout.decode().strip()


async def chunked_query(sites, selector):
    tasks = []
    for domain in sites:
        tasks.append(asyncio.ensure_future(
            run_command(domain, f'dig {selector}._domainkey.{domain} TXT +short'),
        ))
    data = await asyncio.gather(*tasks)
    results = {}
    for item in data:
        domain, output = item
        output = output.replace('" "', '').replace('"', '')
        match = re.search(r"v=(\w+); k=(\w+); p=(\S+)", output)
        if match:
            record_type = match.group(1)
            key_type = match.group(2)
            public_key = match.group(3)
            if record_type.upper() != 'DKIM1':
                # seen: DKIM1, DKIM, dkim1, DKIM2, DKlM1
                print(f'{domain}: unknown record type: {record_type}')
            results[domain] = (key_type, public_key, selector)
    return results


def get_selectors(args):
    if args.selectors:
        return args.selectors
    with open(workdir / 'selectors.txt') as f:
        selectors = [selector.strip() for selector in f.readlines()]
    return selectors


async def main(args):
    if args.collect:
        return await collector(args)
    elif args.query_domains:
        print(await query_domains(args.query_domains))
        return 0
    elif args.query_selectors:
        print(await query_selectors(args.query_selectors))
        return 0
    return 1


async def collector(args):
    for selector in get_selectors(args):
        if selector not in args.rescan and "all" not in args.rescan:
            # Check if this selector has been scanned before. If it
            # has, then we already know which sites use this selector,
            # and we only query those and not the entire list
            sites = await query_domains([selector])
            if not sites:
                # assume it hasn't been scanned before
                print(f'No sites found in database with selector "{selector}"')
                print('Default to scanning for all sites (slow).')
                sites = get_all_sites()
        else:
            # we are going to scan all sites for this selector (slow)
            sites = get_all_sites()
        print(f'Collecting DKIMs for selector "{selector}"')
        with tqdm(total=len(sites) / chunk_size) as pbar:
            for i in range(0, len(sites), chunk_size):
                chunk = sites[i:i+chunk_size]
                results = await chunked_query(chunk, selector)
                for domain, result in results.items():
                    key_type, public_key, selector = result
                    await create_dkim(
                            domain=domain,
                            public_key=public_key,
                            key_type=key_type,
                            selector=selector
                    )
                pbar.update(1)
    return 0


if __name__ == "__main__":
    formatter = '%(message)s'
    handlers = []

    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('-c', '--collect', action='store_true',
                        help='Run collector (takes a while)')
    parser.add_argument('-qd', '--query_domains', nargs='+', default=[],
                        help='Query domains that are using the give selector(s).'
                        'Example: -qd google dkim default')
    parser.add_argument('-qs', '--query_selectors', nargs='+', default=[],
                        help='Query selectors in use by the given domain(s).'
                        'Example: -qs yahoo.com intel.com')
    parser.add_argument('-s', '--selectors', nargs='+', default=[],
                        help='Use the given selectors, don\'t read selectors.txt')
    parser.add_argument('-r', '--rescan', nargs='+', default=[],
                        help='Rescan the given selectors (or "all"), don\'t optimize for known domains')
    parser.add_argument('-w', '--webpage', action='store_true',
                        help='Serve webpage')
    args = parser.parse_args()
    if args.webpage:
        uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
    elif args.query_domains or args.query_selectors or args.collect:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(main(args))
    else:
        parser.print_help()
