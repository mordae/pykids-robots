#!/usr/bin/python3 -tt
# -*- coding: utf-8 -*-

import zmq
import json
import uuid
import logging


# Establish a private logger for this module.
log = logging.getLogger('client')
log.addHandler(logging.NullHandler())


class Client:
    "The PyKids Robots game client."

    def __init__(self, ai,
                 address='tcp://127.0.0.1:4321',
                 nick='', want='play'):
        "Configure the game client to connect to the given 0MQ address."

        assert isinstance(nick, str), 'nick must be a string'
        assert want in ('play', 'spectate'), \
                'you may want to either play or spectate'

        self.identity = uuid.uuid4().hex[:16]

        if not nick:
            nick = self.identity

        self.nick = nick[:32]
        self.want = want
        self.phase = None
        self.ai = ai

        self._router = zmq.Context().instance().socket(zmq.ROUTER)
        self._router.setsockopt(zmq.IDENTITY, self.identity.encode('utf8'))
        self._router.connect(address)

    def _send(self, message):
        log.debug('server <- %r', message)
        message = json.dumps(message).encode('utf8')
        self._router.send_multipart([b'server', message])

    def _receive(self, timeout=100):
        if self._router.poll(timeout, zmq.POLLIN):
            try:
                _sender, message = self._router.recv_multipart()
                message = json.loads(message.decode('utf8'))
            except Exception as e:
                log.exception(e)
                pass
            else:
                log.debug('server -> %r', message)
                return message

        return None

    def _begin_registration(self):
        # Enter the phase.
        self.phase = 'registration'
        self.players = {}

        # Keep sending player info until we get the roster.
        self._tick_registration()

    def _tick_registration(self):
        if not self.players:
            self._send({
                'type': 'hello',
                'want': self.want,
                'nick': self.nick,
            })

    def _on_roster(self, message):
        self.players = message['players']

    def _on_sitrep(self, message):
        self.phase = 'game'
        self.turn = message['turn']
        self.world = message['world']

        # TODO: Draw what happened or something...

        action = self.ai(self.identity, self.world) or {}
        assert isinstance(action, dict), 'Action must be a dict'
        action['type'] = 'action'
        action['turn'] = self.turn

        self._send(action)

    def _on_results(self, message):
        self.phase = 'results'

        winners = [self.players[n]['nick'] for n in message['world']['robots']]
        losers  = [self.players[n]['nick'] for n in message['world']['wrecks']]

        log.info('Winners: %r', winners)
        log.info('Losers: %r', losers)

    def _on_end(self, message):
        self._begin_registration()

    def _tick(self):
        return getattr(self, '_tick_{}'.format(self.phase), lambda: None)()

    def run(self):
        "Run the game client indefinitely."

        # When started, begin with the player registration.
        self._begin_registration()

        # Then we dispatch incoming messages indefinitely,
        # performing the system upkeep tasks whenever we wake up.
        while True:
            # Perform maintenance work.
            self._tick()

            # Wait for next message.
            message = self._receive()

            # Ignore timeout results. We emit them to make it
            # possible to progress without receiving any messages.
            if message is None:
                continue

            # Make sure the message has the correct structure.
            # We need this to be able to dispatch it.

            if not isinstance(message, dict):
                log.warning('Invalid message.')
                continue

            if not isinstance(message.get('type'), str):
                log.warning('Message missing type')
                continue

            # Determine message handler method name from the message type.
            handler = '_on_{}'.format(message['type'])

            if not hasattr(self, handler):
                log.warning('No handler for type=%r.', message['type'])
                continue

            # Dispatch the message.
            getattr(self, handler)(message)


if __name__ == '__main__':
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    logging.root.addHandler(handler)
    logging.root.setLevel(logging.DEBUG)

    def think(me, world):
        for name, robot in world['robots'].items():
            if name != me:
                return {'target': name}

    client = Client(think)
    client.run()


# vim:set sw=4 ts=4 et:
