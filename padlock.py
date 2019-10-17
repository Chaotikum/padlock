#!/usr/bin/env python3

import asyncio
import yaml
import re
import json
import socket
from aiohttp import web
from asyncio import events
from asyncio.streams import StreamReader, StreamReaderProtocol, StreamWriter, _DEFAULT_LIMIT


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
        locked = False if state == 1 else True
        if locked == self.locked:
            return False

        self.locked = locked

        return True

    def setInfo(self, info):
        uncertain = (info & 3) == 3
        battery_low = (info & 8) == 8

        if battery_low == self.battery_low and uncertain == self.uncertain:
            return False

        self.uncertain = uncertain
        self.battery_low = battery_low

        return True

    def setError(self, error):
        if error == self.error:
            return False

        self.error = error

        return True

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
        self.observer = None
        self.writer = None
        self.id = id
        self.locks = {}

        asyncio.async(self.update_locks_task())

    def setObserver(self, observer):
        self.observer = observer

    def getLocks(self):
        return self.locks.values()

    def getLock(self, id):
        return self.locks[id]

    def setWriter(self, writer):
        self.writer = writer

    @asyncio.coroutine
    def update_locks_task(self):
        while True:
            yield from self.update_locks()
            yield from asyncio.sleep(900)

    @asyncio.coroutine
    def update_locks(self):
        if not self.writer:
            return

        for _, lock in self.locks.items():
            lock.getStatus(self.writer, self.id)
            yield from asyncio.sleep(1)

    @asyncio.coroutine
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

        change = False

        change = change or lock.setState(int(m.group(3), 16))
        change = change or lock.setInfo(int(m.group(4), 16))
        change = change or lock.setError(int(m.group(5), 16))

        if change:
            yield from self.announce(lock)

    @asyncio.coroutine
    def announce(self, lock):
        print(lock)

        if self.observer:
            yield from self.observer.update(lock)


    def addLock(self, lock):
        self.locks[lock.id] = lock


@asyncio.coroutine
def hmland(manager, host, port):
    while True:
        print("Connecting to hmland")

        try:
          loop = events.get_event_loop()
          reader, writer = yield from asyncio.open_connection(host, port)

          manager.setWriter(writer)

          asyncio.async(manager.update_locks())
          print("Connected")
          while True:
              line = yield from reader.readline()

              if not line:
                  break

              yield from manager.handle(line.decode("UTF-8").strip())

        except:
          pass

        yield from asyncio.sleep(2)

class Webserver:
    def __init__(self, host, port, manager):
        self.host = host
        self.port = port
        self.manager = manager
        self.manager.setObserver(self)
        self.queues = set()

    def log(self, request, msg):
        try:
            dn = request.headers["SSL_CLIENT_S_DN"]
        except KeyError:
            dn = "(unknown, local request?)"

        try:
            sn = request.headers["SSL_CLIENT_M_SERIAL"]
        except KeyError:
            sn = "(unknown, local request?)"

        print(dn, sn, request.method, request.path, msg)

    @asyncio.coroutine
    def update(self, lock):
        queues = set(self.queues)

        for queue in queues:
            try:
                queue.put_nowait(lock)
            except asyncio.QueueFull:
                self.queues.remove(queue)

    def locksToJson(self):
        locks = list(map(lambda x: x.to_dict(), manager.getLocks()))

        return json.dumps(locks, ensure_ascii=False).encode("UTF-8")

    def lockToJson(self, lock):
        return json.dumps(lock.to_dict(), ensure_ascii=False).encode("UTF-8")

    @asyncio.coroutine
    def handle_get_locks(self, request):
        return web.Response(content_type="application/json", body=self.locksToJson())

    @asyncio.coroutine
    def handle_get_lock(self, request):
        try:
            lock = manager.getLock(request.match_info.get('id'))

            return web.Response(content_type="application/json", body=self.lockToJson(lock))
        except KeyError:
            raise web.HTTPNotFound()

    @asyncio.coroutine
    def handle_get_locks_stream(self, request):
        stream = web.StreamResponse()
        stream.content_type = 'text/event-stream'
        stream.start(request)

        queue = asyncio.Queue(maxsize=1)

        self.queues.add(queue)

        while True:
            stream.write(b"data: " + self.locksToJson() + b"\n\n")
            yield from queue.get()

    @asyncio.coroutine
    def handle_get_lock_stream(self, request):
        try:
            lock = manager.getLock(request.match_info.get('id'))
            lock_id = lock.id

            stream = web.StreamResponse()
            stream.content_type = 'text/event-stream'
            stream.start(request)

            queue = asyncio.Queue(maxsize=1)

            self.queues.add(queue)

            while True:
                if lock.id == lock_id:
                    stream.write(b"data: " + self.lockToJson(lock) + b"\n\n")

                lock = yield from queue.get()

        except KeyError:
            raise web.HTTPNotFound()

    @asyncio.coroutine
    def handle_put_lock(self, request):
        try:
            lock = manager.getLock(request.match_info.get('id'))

            r = yield from request.text()

            r = r.strip()

            if r == "lock":
                self.log(request, "%s locked" % lock)
                lock.setLocked(manager.writer, manager.id, True)
            elif r == "unlock":
                self.log(request, "%s unlocked" % lock)
                lock.setLocked(manager.writer, manager.id, False)
            else:
                raise web.HTTPBadRequest()

            return web.Response(body="ACK".encode("UTF-8"))
        except KeyError:
            raise web.HTTPNotFound()

    @asyncio.coroutine
    def handle_door(self, request):
        reader, writer = yield from asyncio.open_connection("fd20:bdda:5df0:0:bad8:12ff:fe66:fa6", 6004)

        writer.write("open\n".encode("UTF-8"))
        writer.close()

        self.log(request, "Türöffner betätigt")

        return web.Response(body="ACK".encode("UTF-8"))

    @asyncio.coroutine
    def start(self, loop):
        app = web.Application(loop=loop)
        app.router.add_route('GET', '/locks', self.handle_get_locks)
        app.router.add_route('GET', '/locks/stream', self.handle_get_locks_stream)
        app.router.add_route('GET', '/lock/{id}', self.handle_get_lock)
        app.router.add_route('GET', '/lock/{id}/stream', self.handle_get_lock_stream)
        app.router.add_route('PUT', '/lock/{id}', self.handle_put_lock)
        app.router.add_route('PUT', '/door', self.handle_door)

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
