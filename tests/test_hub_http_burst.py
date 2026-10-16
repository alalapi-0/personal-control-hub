"""Real loopback admission under a full, unchanged eight-worker bound."""
import http.client
import socket
import threading
import time
import unittest

from pch_functional_fixture import FunctionalFixture


class HubHTTPBurstTests(unittest.TestCase):
    def occupy(self, fixture):
        clients = []
        try:
            for _ in range(8):
                clients.append(socket.create_connection(('127.0.0.1', fixture.server.server_port), timeout=2))
            deadline = time.monotonic() + 2
            while fixture.server._slots._value and time.monotonic() < deadline:
                time.sleep(.001)
            self.assertEqual(fixture.server._slots._value, 0)
            return clients
        except BaseException:
            for client in clients:
                client.close()
            raise

    def test_short_asset_burst_waits_for_slot_without_adding_worker(self):
        with FunctionalFixture() as fixture:
            clients = self.occupy(fixture)
            # An idle browser connection closes during the bounded handoff.
            release = threading.Timer(.03, clients[0].close)
            connection = http.client.HTTPConnection('127.0.0.1', fixture.server.server_port, timeout=2)
            try:
                release.start()
                connection.request('GET', '/assets/connection_view.mjs')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                body = response.read()
                self.assertIn(b'export ', body)
                self.assertEqual(len(body), int(response.getheader('Content-Length')))
                # Seven held sockets still own slots; the asset used the freed
                # eighth slot rather than increasing the concurrency limit.
                self.assertLessEqual(fixture.server._slots._value, 1)
                self.assertEqual(fixture.server.request_queue_size, 8)
            finally:
                connection.close()
                release.cancel()
                release.join(2)
                self.assertFalse(release.is_alive())
                for client in clients:
                    client.close()

    def test_sustained_full_bound_rejects_and_recovers_after_clients_close(self):
        with FunctionalFixture() as fixture:
            clients = self.occupy(fixture)
            connection = http.client.HTTPConnection('127.0.0.1', fixture.server.server_port, timeout=2)
            try:
                started = time.monotonic()
                connection.request('GET', '/assets/connection_view.mjs')
                with self.assertRaises(http.client.RemoteDisconnected):
                    connection.getresponse()
                elapsed = time.monotonic() - started
                self.assertGreaterEqual(elapsed, .075)
                self.assertLess(elapsed, 1)
                self.assertEqual(fixture.server._slots._value, 0)
            finally:
                connection.close()
                for client in clients:
                    client.close()
            deadline = time.monotonic() + 2
            while fixture.server._slots._value != 8 and time.monotonic() < deadline:
                time.sleep(.001)
            self.assertEqual(fixture.server._slots._value, 8)
            status, _, body = fixture.call(path='/api/health')
            self.assertEqual(status, 200)
            self.assertEqual(body['data']['service'], 'hub-local')
            self.assertEqual(fixture.effects, [])


if __name__ == '__main__':
    unittest.main()
