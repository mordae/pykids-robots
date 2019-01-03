#!/usr/bin/python3 -tt
# -*- coding: utf-8 -*-

import zmq
import json
import time
import random
import logging


# Establish a private logger for this module.
log = logging.getLogger('server')
log.addHandler(logging.NullHandler())


class Server:
    "The PyKids Robots game server."

    def __init__(self, address='tcp://0.0.0.0:4321'):
        "Configure the game server to listen on given 0MQ address."

        self._router = zmq.Context().instance().socket(zmq.ROUTER)
        self._router.setsockopt(zmq.IDENTITY, b'server')
        self._router.bind(address)

    @property
    def active_players(self):
        return {n: p for n, p in self.players.items()
                if p['will'] == 'play'}

    def _send_to(self, recipient, message):
        log.debug('%r <- %r', recipient, message)
        recipient = recipient.encode('utf8')
        message = json.dumps(message).encode('utf8')
        self._router.send_multipart([recipient, message])

    def _send_all(self, message):
        for recipient in self.players:
            self._send_to(recipient, message)

    def _receive(self, timeout=100):
        if self._router.poll(timeout, zmq.POLLIN):
            try:
                sender, message = self._router.recv_multipart()
                sender = sender.decode('utf8')
                message = json.loads(message.decode('utf8'))
            except Exception as e:
                log.exception(e)
                pass
            else:
                log.debug('%r -> %r', sender, message)
                return sender, message

        return None, None

    def _begin_registration(self):
        # We always start with a clear player sheet.
        self.players = {}

        # Enter the phase and take note of the time.
        self.phase = 'registration'
        self.since = time.time()

    def _tick_registration(self):
        # We will start the game after 10s in the lobby,
        # once we have at least 2 players. Otherwise wait.

        if time.time() >= self.since + 10.0:
            if len(self.active_players) >= 2:
                self._begin_game()

    def _on_hello(self, sender, message):
        # By default, join players as active participants.
        if message.get('want') not in ('play', 'spectate'):
            message['want'] = 'play'

        # Make latecomers spectators, though.
        if message['want'] == 'play' and self.phase != 'registration':
            message['want'] = 'spectate'

        # Same when there are too many players already.
        if self.phase == 'registration':
            if self.active_players.get(sender, {}).get('want') != 'play':
                if len(self.active_players) >= len(starts):
                    message['want'] = 'spectate'

        self.players[sender] = {
            'nick': str(message.get('nick'))[:32],
            'will': message['want'],
        }

        self._send_all({
            'type': 'roster',
            'players': self.players,
        })

    def _begin_game(self):
        # Enter the game phase.
        self.phase = 'game'
        self.since = 0
        self.turn  = 0

        # We are going to gather actions of the players.
        self.actions = {}

        # We are also going to keep track of our world.
        self.world = generate_world(self.active_players)

        # Kick off the first turn.
        self._tick_game()

    def _tick_game(self):
        # Turn takes at least a second.
        if time.time() < self.since + 1.0:
            return

        # Apply actions to the world.
        update_world(self.world, self.actions)

        if len(self.world['robots']) <= 1:
            # Someone has managed to win the game.
            self.actions = {}
            return self._begin_results()

        # Advance to the next turn.
        self.actions = {}
        self.turn += 1
        self.since = time.time()

        self._send_all({
            'type': 'sitrep',
            'world': self.world,
            'turn': self.turn,
        })

    def _on_action(self, sender, message):
        # Only players with robots can post their actions.
        if sender not in self.world['robots']:
            return

        # Check that the orders are for the upcoming turn.
        if message.get('turn') != self.turn:
            return

        target = None

        if isinstance(message.get('target'), str):
            if message['target'] != sender:
                if message['target'] in self.world['robots']:
                    target = message['target']

        # TODO: Implement movement.

        self.actions[sender] = {
            'target': target,
        }

    def _begin_results(self):
        # Enter the last phase.
        self.phase = 'results'
        self.since = time.time()

        self._send_all({
            'type': 'results',
            'world': self.world,
            'turn': self.turn,
        })

    def _tick_results(self):
        # Show results for 3 seconds.
        if time.time() < self.since + 3.0:
            return

        # Announce the end of the game.
        self._send_all({'type': 'end'})

        # Then start a new game.
        self._begin_registration()

    def _tick(self):
        return getattr(self, '_tick_{}'.format(self.phase), lambda: None)()

    def run(self):
        "Run the game server indefinitely."

        # When started, begin with the player registration.
        self._begin_registration()

        # Then we dispatch incoming messages indefinitely,
        # performing the system upkeep tasks whenever we wake up.
        while True:
            # Perform maintenance work.
            self._tick()

            # Wait for next message.
            sender, message = self._receive()

            # Ignore timeout results. We emit them to make it
            # possible to progress without receiving any messages.
            if sender is None:
                continue

            # Make sure the message has the correct structure.
            # We need this to be able to dispatch it.

            if not isinstance(message, dict):
                log.warning('Invalid message from %r.', sender)
                continue

            if not isinstance(message.get('type'), str):
                log.warning('Message missing type from %r.', sender)
                continue

            # Determine message handler method name from the message type.
            handler = '_on_{}'.format(message['type'])

            if not hasattr(self, handler):
                log.warning('No handler for type=%r.', message['type'])
                continue

            # Dispatch the message.
            getattr(self, handler)(sender, message)


# Generate all possible starting positions on the edges of the map.
starts = []

for x in range(50, 1550, 50):
    starts.append((x + 25,  25))
    starts.append((x + 25, 875))

for y in range(50, 850, 50):
    starts.append((  25, y + 25))
    starts.append((1575, y + 25))


def generate_world(players):
    positions = list(random.sample(starts, len(players)))
    robots = {}

    for player in players:
        x, y = positions.pop()
        robots[player] = {
            'move_from': {'x': x, 'y': y},
            'move_to': {'x': x, 'y': y},
            'damage': 10,
            'target': None,
            'hp': 100,
        }

    return {
        'arena': {
            'width': 1600,
            'height': 900,
        },
        'robots': robots,
        'wrecks': {},
    }


def update_world(world, actions):
    for robot in world['robots'].values():
        # Move the robot to the destination.
        robot['move_from'] = robot['move_to']

        # If a target was named and exists, apply the damage.
        if robot['target'] in world['robots']:
            world['robots'][robot['target']]['hp'] -= robot['damage']

        robot['target'] = None

    for name, robot in list(world['robots'].items()):
        if robot['hp'] <= 0:
            world['wrecks'][name] = world['robots'].pop(name)

    for name, action in actions.items():
        robot = world['robots'][name]
        robot['move_to'] = action.get('move_to', robot['move_from'])
        robot['target'] = action.get('target')


if __name__ == '__main__':
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    logging.root.addHandler(handler)
    logging.root.setLevel(logging.DEBUG)

    server = Server()
    server.run()


# vim:set sw=4 ts=4 et:
