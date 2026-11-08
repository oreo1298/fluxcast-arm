"""DLNA/Cast start-up against TVs that do not behave like Samsung's.

LG webOS (the C2 among them) answers SetAVTransportURI only once it has
opened the stream, refuses Play with UPnP 701 while still TRANSITIONING,
and picks the stream apart by its protocolInfo. Cast TVs ignore a LOAD sent
to whatever app is in front unless it is the Default Media Receiver (#174).
"""
import importlib.util
import os
import socket
import sys
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import server  # noqa: E402
import session_watch  # noqa: E402

HAS_UPNP = importlib.util.find_spec("upnpclient") is not None
HAS_CAST = importlib.util.find_spec("pychromecast") is not None

TS_URL = "http://192.168.1.20:8080/session-x/progressive.ts"
HLS_URL = "http://192.168.1.20:8080/session-x/stream.m3u8"
LG_SINK = [
    "http-get:*:video/mpeg:DLNA.ORG_PN=AVC_TS_HD_EU_ISO",
    "http-get:*:video/mpeg:DLNA.ORG_PN=MPEG_TS_HD_NA_ISO",
    "http-get:*:video/mp2t:*",
    "http-get:*:video/mp4:*",
]


@unittest.skipUnless(HAS_UPNP, "upnpclient not installed")
class ProtocolInfoTest(unittest.TestCase):
    def setUp(self):
        from dlna import dlna
        self.dlna = dlna

    def test_default_is_kept_when_the_tv_says_nothing(self):
        self.assertEqual(self.dlna.choose_protocol_info(TS_URL, []), self.dlna.TS_PROTOCOL_INFO)

    def test_default_is_kept_when_the_tv_lists_it(self):
        sink = ["http-get:*:video/mpeg:DLNA.ORG_PN=AVC_TS_MP_HD_AAC;DLNA.ORG_OP=01"]
        self.assertEqual(self.dlna.choose_protocol_info(TS_URL, sink),
                         self.dlna.TS_PROTOCOL_INFO)

    def test_video_mpeg_is_preferred_over_another_mime_wildcard(self):
        info = self.dlna.choose_protocol_info(TS_URL, LG_SINK)
        self.assertTrue(info.startswith("http-get:*:video/mpeg:"), info)
        self.assertIn("DLNA.ORG_PN=AVC_TS_HD_EU_ISO;", info)
        self.assertTrue(info.endswith(self.dlna.TS_DLNA_PARAMS))

    def test_a_wildcard_drops_the_profile(self):
        info = self.dlna.choose_protocol_info(TS_URL, ["http-get:*:video/mpeg:*"])
        self.assertEqual(info, f"http-get:*:video/mpeg:{self.dlna.TS_DLNA_PARAMS}")

    def test_mp2t_only_tv_gets_mp2t(self):
        info = self.dlna.choose_protocol_info(TS_URL, ["http-get:*:video/mp2t:*"])
        self.assertTrue(info.startswith("http-get:*:video/mp2t:"))

    def test_hls_support_is_read_from_the_list(self):
        self.assertIs(self.dlna.accepts_hls([]), None)
        self.assertIs(self.dlna.accepts_hls(LG_SINK), False)
        self.assertIs(self.dlna.accepts_hls(
            LG_SINK + ["http-get:*:application/vnd.apple.mpegurl:*"]), True)
        self.assertEqual(
            self.dlna.choose_protocol_info(
                HLS_URL, ["http-get:*:application/vnd.apple.mpegurl:*"]),
            "http-get:*:application/vnd.apple.mpegurl:*")

    def test_http_headers_follow_the_chosen_label(self):
        info = self.dlna.choose_protocol_info(TS_URL, LG_SINK)
        self.assertEqual(self.dlna.protocol_mime(info), "video/mpeg")
        self.assertEqual(self.dlna.content_features(info), info.split(":", 3)[3])


class _FakeAVTransport:
    """Scripted AVTransport: each action pops its next outcome."""

    def __init__(self, set_uri=(), play=(), states=()):
        self.set_uri = list(set_uri)
        self.play = list(play)
        self.states = list(states)
        self.calls = []

    def _next(self, script, name):
        self.calls.append(name)
        outcome = script.pop(0) if script else None
        if isinstance(outcome, Exception):
            raise outcome
        return outcome or {}

    def Stop(self, **kw):
        self.calls.append("Stop")

    def SetAVTransportURI(self, **kw):
        return self._next(self.set_uri, "SetAVTransportURI")

    def Play(self, **kw):
        return self._next(self.play, "Play")

    def GetTransportInfo(self, **kw):
        self.calls.append("GetTransportInfo")
        state = self.states.pop(0) if self.states else "STOPPED"
        return {"CurrentTransportState": state, "CurrentTransportStatus": "OK"}


@unittest.skipUnless(HAS_UPNP, "upnpclient not installed")
class DlnaStartTest(unittest.TestCase):
    def setUp(self):
        import upnpclient
        from dlna import dlna
        self.dlna = dlna
        self.err = upnpclient.soap.SOAPError

    def _device(self, **script):
        device = mock.MagicMock()
        device.friendly_name = "[LG] webOS TV OLED55C2PUA"
        device.AVTransport = _FakeAVTransport(**script)
        return device

    def _start(self, device):
        with mock.patch("builtins.print"):
            return self.dlna.start_cast(device, TS_URL, sleep=lambda s: None)

    def test_play_is_retried_while_the_tv_answers_701(self):
        device = self._device(play=[self.err(701, "Transition not available")] * 2)
        self.assertTrue(self._start(device))
        self.assertEqual(device.AVTransport.calls.count("Play"), 3)

    def test_play_waits_while_the_tv_reports_transitioning(self):
        device = self._device(states=["TRANSITIONING", "TRANSITIONING", "STOPPED"])
        self.assertTrue(self._start(device))
        calls = device.AVTransport.calls
        self.assertEqual(calls.count("GetTransportInfo"), 3)
        self.assertLess(calls.index("SetAVTransportURI"), calls.index("Play"))

    def test_a_tv_that_starts_by_itself_is_not_sent_play(self):
        device = self._device(states=["PLAYING"])
        self.assertTrue(self._start(device))
        self.assertNotIn("Play", device.AVTransport.calls)

    def test_a_refused_url_is_a_failure_not_a_success(self):
        device = self._device(set_uri=[self.err(714, "Illegal MIME-type")])
        with mock.patch("builtins.print") as printed:
            ok = self.dlna.start_cast(device, TS_URL, sleep=lambda s: None)
        self.assertFalse(ok)
        self.assertNotIn("Play", device.AVTransport.calls)
        self.assertIn("UPnP error 714", " ".join(str(c) for c in printed.call_args_list))

    def test_701_forever_gives_up(self):
        device = self._device()
        device.AVTransport.Play = mock.Mock(
            side_effect=self.err(701, "Transition not available"))
        with mock.patch.object(self.dlna, "PLAY_RETRY_SECONDS", 0.05):
            self.assertFalse(self._start(device))
        self.assertGreater(device.AVTransport.Play.call_count, 1)

    def test_play_error_while_the_tv_is_already_playing_is_fine(self):
        device = self._device(play=[self.err(501, "Action failed")],
                              states=["STOPPED", "PLAYING"])
        self.assertTrue(self._start(device))

    def test_errors_are_described(self):
        self.assertIn("still switching", self.dlna.describe_error(self.err(701, "x")))
        self.assertIn("did not answer", self.dlna.describe_error(
            type("ReadTimeout", (Exception,), {})()))


@unittest.skipUnless(HAS_CAST, "pychromecast not installed")
class CastStartTest(unittest.TestCase):
    def setUp(self):
        from cast import cast
        self.cast = cast

    def _device(self, app_id="SPOTIFY", session=True, launches=True):
        device = mock.MagicMock()
        device.app_id = app_id
        device.app_display_name = "Spotify"
        device.cast_info.friendly_name = "LG C2"
        order = []

        def start_app(app, **kw):
            order.append(("start_app", app))
            if launches:
                device.app_id = app

        def play_media(url, ctype, **kw):
            order.append(("play_media", ctype, kw.get("media_info")))

        device.start_app.side_effect = start_app
        mc = device.media_controller
        mc.play_media.side_effect = play_media
        mc.session_active_event.is_set.return_value = session
        return device, order

    def _start(self, device, url=HLS_URL):
        with mock.patch("builtins.print"):
            return self.cast.start_cast(device, url, sleep=lambda s: None)

    def test_media_receiver_is_launched_before_the_load(self):
        device, order = self._device()
        self.assertTrue(self._start(device))
        self.assertEqual(order[0], ("start_app", self.cast.APP_MEDIA_RECEIVER))
        self.assertEqual(order[1][0], "play_media")
        self.assertEqual(order[1][1], "application/x-mpegURL")
        self.assertEqual(order[1][2], {"hlsVideoSegmentFormat": "mpeg2_ts"})

    def test_receiver_already_in_front_is_not_relaunched(self):
        device, order = self._device(app_id=self.cast.APP_MEDIA_RECEIVER)
        self.assertTrue(self._start(device))
        self.assertEqual([o[0] for o in order], ["play_media"])

    def test_no_media_session_is_reported_as_failure(self):
        device, _ = self._device(session=False)
        self.assertFalse(self._start(device))

    def test_unreachable_cast_port_is_reported(self):
        import pychromecast
        device, order = self._device()
        device.wait.side_effect = pychromecast.RequestTimeout("wait", 15)
        self.assertFalse(self._start(device))
        self.assertEqual(order, [])


class DiagnoseTest(unittest.TestCase):
    def _stats(self, **kw):
        base = {"requests": 0, "bytes_sent": 0, "rejected": {}}
        base.update(kw)
        return base

    def test_never_fetched_points_at_reachability(self):
        with mock.patch.object(session_watch, "firewall_hint",
                               return_value="sudo firewall-cmd --add-port=8080/tcp"):
            msg = session_watch.diagnose(self._stats(), tv_ip="192.168.1.50",
                                         host="192.168.1.20", port=8080)
        self.assertIn("never requested", msg)
        self.assertIn("firewall-cmd --add-port=8080/tcp", msg)
        self.assertIn("--host", msg)

    def test_refused_other_address_is_named(self):
        msg = session_watch.diagnose(self._stats(rejected={"192.168.1.51": 3}),
                                     tv_ip="192.168.1.50", host="192.168.1.20", port=8080)
        self.assertIn("192.168.1.51", msg)

    def test_fetched_then_stopped_points_at_the_format(self):
        msg = session_watch.diagnose(self._stats(requests=2, bytes_sent=300_000),
                                     tv_ip="192.168.1.50", host="192.168.1.20", port=8080,
                                     tv_state="STOPPED")
        self.assertIn("did not like", msg)

    def test_healthy_session_says_nothing(self):
        self.assertIsNone(session_watch.diagnose(
            self._stats(requests=5, bytes_sent=50_000_000), tv_ip="192.168.1.50",
            host="192.168.1.20", port=8080, tv_state="PLAYING"))

    def test_local_ip_for_ignores_loopback_and_junk(self):
        self.assertIsNone(session_watch.local_ip_for("127.0.0.1"))
        self.assertIsNone(session_watch.local_ip_for("not-an-ip"))


class StreamServerLabelAndStatsTest(unittest.TestCase):
    """The TS bridge must be labelled the way the DIDL told the TV, and the
    server must remember whether the TV ever asked for anything."""

    def setUp(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.saved = (server.TS_MIME, server.TS_CONTENT_FEATURES)
        self.srv = server.StreamServer(host="127.0.0.1", port=self.port, session_id="s1")
        self.srv.start()

    def tearDown(self):
        self.srv.stop()
        server.set_dlna_ts_profile(*self.saved)

    def _head(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.headers

    def test_progressive_ts_headers_follow_the_chosen_profile(self):
        self.srv.allow_client("127.0.0.1")
        server.set_dlna_ts_profile("video/mp2t", "DLNA.ORG_OP=01;DLNA.ORG_CI=0")
        status, headers = self._head("/s1/progressive.ts")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "video/mp2t")
        self.assertEqual(headers["contentFeatures.dlna.org"], "DLNA.ORG_OP=01;DLNA.ORG_CI=0")
        self.assertEqual(server.request_stats.snapshot()["requests"], 1)

    def test_refused_clients_are_counted(self):
        self.srv.allow_client("10.9.9.9")
        with mock.patch("builtins.print"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self._head("/s1/progressive.ts")
        self.assertEqual(ctx.exception.code, 403)
        snap = server.request_stats.snapshot()
        self.assertEqual(snap["requests"], 0)
        self.assertEqual(snap["rejected"], {"127.0.0.1": 1})


if __name__ == "__main__":
    unittest.main()
