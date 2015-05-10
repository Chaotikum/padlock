#!/usr/bin/env python3

import asyncio
import yaml
import re
import json
from aiohttp import web


class Lock:
    def __init__(self, id, name):
        self.id = id
        self.name = name
        self.locked = None
        self.uncertain = None
        self.info = None
        self.battery_low = None
        self.error = None

    def setState(self, state):
        if state == 1:
            self.locked = False
        else:
            self.locked = True

    def setInfo(self, info):
        self.uncertain = (info & 3) == 3
        self.battery_low = (info & 8) == 8

    def setError(self, error):
        self.error = error

    def getStatus(self, writer, id):
        r = "S6526C70F,00,00000000,01,6526C70F,01B001%s%s010E\r\n" % (id, self.id)
        writer.write(r.encode("UTF-8"))

    def setLocked(self, writer, id, state):
        s = "00" if state else "01"
        r = "SF165F45E,00,00000000,01,F165F45E,03B011%s%s8001%sFF\r\n" % (id, self.id, s)
        writer.write(r.encode("UTF-8"))

    def to_dict(self):
        return { "id": self.id,
                 "name": self.name,
                 "locked": self.locked,
                 "uncertain": self.uncertain,
                 "battery_low": self.battery_low,
                 "error": self.error
               }

    def __repr__(self):
        s = "locked" if self.locked else "unlocked"
        s += " uncertain" if self.uncertain else ""
        s += (" Error: %s" % self.error) if self.error != 0 else ""

        return "Lock(%s, %s, %s)" % (self.id, self.name, s)


class LockManager:
    def __init__(self, id):
        self.writer = None
        self.id = id
        self.locks = {}

    def getLocks(self):
        return self.locks.values()

    def getLock(self, id):
        return self.locks[id]

    @asyncio.coroutine
    def setWriter(self, writer):
        self.writer = writer

        for _, lock in self.locks.items():
            lock.getStatus(writer, self.id)
            yield from asyncio.sleep(1)

    def handle(self, line):
        m = re.match("^E......,....,.*,.*,.*,......(......)(......)0601(..)(.)(.)", line)

        if not m:
            return

        src = m.group(1)
        dst = m.group(2)

        if dst != self.id:
           return

        if not src in self.locks:
           return

        lock = self.locks[src]

        lock.setState(int(m.group(3), 16))
        lock.setInfo(int(m.group(4), 16))
        lock.setError(int(m.group(5), 16))

        print(lock)


    def addLock(self, lock):
        self.locks[lock.id] = lock


@asyncio.coroutine
def hmland(manager, host, port):
    while True:
        reader, writer = yield from asyncio.open_connection(host, port)

        asyncio.async(manager.setWriter(writer))

        while True:
            line = yield from reader.readline()
            if not line:
                break

            manager.handle(line.decode("UTF-8").strip())

        yield from asyncio.sleep(2)

class Webserver:
    def __init__(self, host, port, manager):
        self.host = host
        self.port = port
        self.manager = manager

    @asyncio.coroutine
    def handle_get_locks(self, request):
        locks = list(map(lambda x: x.to_dict(), manager.getLocks()))

        return web.Response(content_type="application/json", body=json.dumps(locks).encode("UTF-8"))


    @asyncio.coroutine
    def handle_get_lock(self, request):
        try:
            response = manager.getLock(request.match_info.get('id')).to_dict()

            return web.Response(content_type="application/json", body=json.dumps(response).encode("UTF-8"))
        except KeyError:
            raise web.HTTPNotFound()

    @asyncio.coroutine
    def handle_put_lock(self, request):
        try:
            lock = manager.getLock(request.match_info.get('id'))

            r = yield from request.text()

            r = r.strip()

            if r == "lock":
                lock.setLocked(manager.writer, manager.id, True)
            elif r == "unlock":
                lock.setLocked(manager.writer, manager.id, False)
            else:
                raise web.HTTPBadRequest()

            return web.Response(body="ACK".encode("UTF-8"))
        except KeyError:
            raise web.HTTPNotFound()

    @asyncio.coroutine
    def start(self, loop):
        app = web.Application(loop=loop)
        app.router.add_route('GET', '/locks', self.handle_get_locks)
        app.router.add_route('GET', '/lock/{id}', self.handle_get_lock)
        app.router.add_route('PUT', '/lock/{id}', self.handle_put_lock)

        srv = yield from loop.create_server(app.make_handler(), self.host, self.port)
        return srv

config = yaml.load(open("config.yaml"))

manager = LockManager(config["id"])

for lock in config["locks"]:
    manager.addLock(Lock(lock["id"], lock["name"]))

webserver = Webserver("localhost", 2500, manager)

loop = asyncio.get_event_loop()
asyncio.async(hmland(manager, config["host"], config["port"]))
loop.run_until_complete(webserver.start(loop))
loop.run_forever()
