# Copyright 2014 Orange
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging as python_logging
import time

from oslo_config import fixture as config_fixture
from oslo_log import log as logging
import testtools

from networking_bagpipe.bagpipe_bgp.common import config
from networking_bagpipe.bagpipe_bgp import engine
from networking_bagpipe.bagpipe_bgp.engine import exa
from networking_bagpipe.bagpipe_bgp.engine import exabgp_peer_worker


exabgp_peer_worker.setup_exabgp_env()

WAIT_TIME = 0.05

RT1 = exa.RouteTarget(64512, 10)
RT2 = exa.RouteTarget(64512, 20)
RT3 = exa.RouteTarget(64512, 30)
RT4 = exa.RouteTarget(64512, 40)
RT5 = exa.RouteTarget(64512, 50)


def _rt_to_string(rt):
    assert isinstance(rt, exa.RouteTarget)
    return "{}:{}".format(rt.asn, rt.number)


class TestNLRI:

    def __init__(self, desc):
        self.desc = desc
        self.action = None
        self.afi = exa.AFI(exa.AFI.ipv4)
        self.safi = exa.SAFI(exa.SAFI.mpls_vpn)

    def __repr__(self):
        return self.desc

    def __eq__(self, other):
        return self.desc == other.desc

    def __hash__(self):
        return hash(self.desc)


NLRI1 = TestNLRI("NLRI1")
NLRI2 = TestNLRI("NLRI2")

NH1 = "1.1.1.1"
NH2 = "2.2.2.2"
NH3 = "3.3.3.3"

NBR = "NBR"
BRR = "BRR"

python_logging.basicConfig(level=logging.DEBUG,
                           filename="bagpipe-bgp-testsuite.log",
                           format="%(asctime)s %(threadName)-30s %(name)-30s "
                           "%(levelname)-8s %(message)s")

LOG = logging.getLogger()


class TestCase(testtools.TestCase):

    def setUp(self):
        super().setUp()
        cfg_fixture = self.useFixture(config_fixture.Config())
        cfg_fixture.register_opts(config.bgp_opts, "BGP")
        cfg_fixture.config(group='BGP',
                           local_address='11.22.33.44',
                           my_as=64513
                           )


class FakeNLRI:

    def __init__(self, nlri_desc, afi=exa.AFI.ipv4, safi=exa.SAFI.mpls_vpn):
        self.nlri = nlri_desc
        self.afi = afi
        self.safi = safi

    def __repr__(self):
        return "FakeNLRI %s (%d:%d)" % (self.nlri, self.afi, self.safi)


class BaseTestBagPipeBGP:

    def set_event_target_worker(self, worker):
        self.event_target_worker = worker

    def _fake_nlri(self, fake_nlri_desc, **kwargs):
        return FakeNLRI(fake_nlri_desc, **kwargs)

    def _new_route_event(self, event_type, nlri, rts, source, nh, lp=0,
                         replaced_route_entry=None,
                         afi=exa.AFI(exa.AFI.ipv4),
                         safi=exa.SAFI(exa.SAFI.mpls_vpn),
                         **kwargs):
        attributes = exa.Attributes()
        attributes.add(exa.NextHop(nh))
        attributes.add(exa.LocalPreference(lp))

        if 'rtrecords' in kwargs:
            ecoms = exa.ExtendedCommunities()
            ecoms.communities += kwargs['rtrecords']
            attributes.add(ecoms)

        route_event = engine.RouteEvent(event_type,
                                        engine.RouteEntry(nlri, rts,
                                                          attributes, source),
                                        source)
        route_event.set_replaced_route(replaced_route_entry)

        LOG.info("*** Emitting event to %s: %s",
                 self.event_target_worker, route_event)

        self.event_target_worker._on_event(route_event)

        return route_event

    def _new_flow_event(self, event_type, nlri, to_rts, attract_rts, source,
                        afi=exa.AFI(exa.AFI.ipv4),
                        safi=exa.SAFI(exa.SAFI.flow_vpn),
                        **kwargs):
        attributes = exa.Attributes()

        ecommunities = exa.ExtendedCommunities()
        ecommunities.communities.append(
            exa.TrafficRedirect(exa.ASN(int(to_rts[0].asn)),
                                int(to_rts[0].number))
        )

        attributes.add(ecommunities)

        flow_event = engine.RouteEvent(event_type,
                                       engine.RouteEntry(nlri, attract_rts,
                                                         attributes, source),
                                       source)

        self.event_target_worker._on_event(flow_event)

        return flow_event

    def _revert_event(self, event):
        if event.type == engine.RouteEvent.ADVERTISE:
            type = engine.RouteEvent.WITHDRAW
        else:  # WITHDRAW
            type = engine.RouteEvent.ADVERTISE

        route_event = engine.RouteEvent(type, event.route_entry, event.source)

        self.event_target_worker._on_event(route_event)

    def _wait(self):
        time.sleep(WAIT_TIME)

    def _append_call(self, obj):
        LOG.info("****** %s ******", obj)
        self._calls.append(obj)
