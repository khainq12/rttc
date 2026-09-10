package fleetqox.omnetpp;

import inet.mobility.single.LinearMobility;
import inet.networklayer.configurator.ipv4.Ipv4NetworkConfigurator;
import inet.node.inet.WirelessHost;
import inet.node.wireless.AccessPoint;
import inet.physicallayer.wireless.ieee80211.packetlevel.Ieee80211ScalarRadioMedium;

// Single-AP IEEE 802.11 infrastructure network matching the ns-3 "wifi"
// (non-roaming) topology in external/ns3/fleetqox_trace_replay.cc: every
// unique trace endpoint (fleet_controller, fleet_router, operator_ui, and
// numRobots robot[] stations) is a WirelessHost associated with a single
// AccessPoint. Exact per-node position assignment does not replicate ns-3's
// endpoint-discovery order (that order is a CSV-scan artifact, not part of
// the scenario definition); station density (stationSpacing) and speed
// magnitude (mobilitySpeed) do match. Uses the Simplified mgmt modules
// (fixed AP association, no scan/auth negotiation) so a short trace-replay
// window doesn't depend on beacon/probe timing -- see
// run_omnetpp_docker_wifi_parity.py for the bounded-metric parity
// comparison this backs.
network FleetQoxWifiReplay
{
    parameters:
        int numRobots = default(8);
        string traceFile;
        double wlanBitrate @unit(bps) = default(54Mbps);
        double stationSpacing @unit(m) = default(3m);
        double mobilitySpeed @unit(mps) = default(0mps);
        double startOffset @unit(s) = default(1s);
        int totalStations = default(numRobots + 3);
        int gridWidth = default(int(ceil(sqrt(totalStations))));
        @display("bgb=800,650");
    submodules:
        configurator: Ipv4NetworkConfigurator {
            config = xml("<config><interface hosts='*' address='10.20.x.x' netmask='255.255.0.0'/></config>");
            @display("p=80,60");
        }
        radioMedium: Ieee80211ScalarRadioMedium {
            @display("p=80,160");
        }
        ap: AccessPoint {
            mobility.typename = "StationaryMobility";
            mobility.initialX = parent.gridWidth * parent.stationSpacing / 2;
            mobility.initialY = parent.gridWidth * parent.stationSpacing / 2;
            wlan[*].mgmt.typename = "Ieee80211MgmtApSimplified";
            wlan[*].bitrate = parent.wlanBitrate;
            wlan[*].opMode = "g(erp)";
            @display("p=400,60");
        }
        controller: WirelessHost {
            numApps = 1;
            app[0].typename = "fleetqox.omnetpp.TraceDrivenUdpApp";
            app[0].traceFile = parent.traceFile;
            app[0].endpointName = "fleet_controller";
            app[0].startOffset = parent.startOffset;
            app[0].summaryWriter = true;
            mobility.typename = "LinearMobility";
            mobility.initFromDisplayString = false;
            mobility.initialX = default((0 % parent.gridWidth) * parent.stationSpacing);
            mobility.initialY = default(floor(0 / parent.gridWidth) * parent.stationSpacing);
            mobility.speed = parent.mobilitySpeed;
            mobility.initialMovementHeading = default(0deg);
            wlan[*].bitrate = parent.wlanBitrate;
            wlan[*].opMode = "g(erp)";
            wlan[*].mgmt.typename = "Ieee80211MgmtStaSimplified";
            wlan[*].agent.typename = "";
            @display("p=250,300");
        }
        fleetRouter: WirelessHost {
            numApps = 1;
            app[0].typename = "fleetqox.omnetpp.TraceDrivenUdpApp";
            app[0].traceFile = parent.traceFile;
            app[0].endpointName = "fleet_router";
            app[0].startOffset = parent.startOffset;
            mobility.typename = "LinearMobility";
            mobility.initFromDisplayString = false;
            mobility.initialX = default((1 % parent.gridWidth) * parent.stationSpacing);
            mobility.initialY = default(floor(1 / parent.gridWidth) * parent.stationSpacing);
            mobility.speed = parent.mobilitySpeed;
            mobility.initialMovementHeading = default(180deg);
            wlan[*].bitrate = parent.wlanBitrate;
            wlan[*].opMode = "g(erp)";
            wlan[*].mgmt.typename = "Ieee80211MgmtStaSimplified";
            wlan[*].agent.typename = "";
            @display("p=400,300");
        }
        operatorUi: WirelessHost {
            numApps = 1;
            app[0].typename = "fleetqox.omnetpp.TraceDrivenUdpApp";
            app[0].traceFile = parent.traceFile;
            app[0].endpointName = "operator_ui";
            app[0].startOffset = parent.startOffset;
            mobility.typename = "LinearMobility";
            mobility.initFromDisplayString = false;
            mobility.initialX = default((2 % parent.gridWidth) * parent.stationSpacing);
            mobility.initialY = default(floor(2 / parent.gridWidth) * parent.stationSpacing);
            mobility.speed = parent.mobilitySpeed;
            mobility.initialMovementHeading = default(0deg);
            wlan[*].bitrate = parent.wlanBitrate;
            wlan[*].opMode = "g(erp)";
            wlan[*].mgmt.typename = "Ieee80211MgmtStaSimplified";
            wlan[*].agent.typename = "";
            @display("p=550,300");
        }
        robot[numRobots]: WirelessHost {
            numApps = 1;
            app[0].typename = "fleetqox.omnetpp.TraceDrivenUdpApp";
            app[0].traceFile = parent.traceFile;
            app[0].endpointName = "";
            app[0].startOffset = parent.startOffset;
            mobility.typename = "LinearMobility";
            mobility.initFromDisplayString = false;
            mobility.initialX = default(((index + 3) % parent.gridWidth) * parent.stationSpacing);
            mobility.initialY = default(floor((index + 3) / parent.gridWidth) * parent.stationSpacing);
            mobility.speed = parent.mobilitySpeed;
            mobility.initialMovementHeading = default((index % 2 == 0) ? 0deg : 180deg);
            wlan[*].bitrate = parent.wlanBitrate;
            wlan[*].opMode = "g(erp)";
            wlan[*].mgmt.typename = "Ieee80211MgmtStaSimplified";
            wlan[*].agent.typename = "";
            @display("p=70+index*80,520");
        }
}
