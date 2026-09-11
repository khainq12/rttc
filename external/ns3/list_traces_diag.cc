// Enumerate every attribute and trace source on the wifi classes we care
// about, using ns-3's own TypeId runtime reflection -- no .cc source or
// NS_LOG needed for this, since TypeId metadata is registered at static
// init time regardless of build config.
#include "ns3/core-module.h"
#include "ns3/wifi-module.h"
#include "ns3/tap-bridge-module.h"
#include <iostream>
using namespace ns3;

void Dump(const std::string& name) {
  TypeId tid;
  if (!TypeId::LookupByNameFailSafe(name, &tid)) {
    std::cout << "MISSING TypeId: " << name << "\n";
    return;
  }
  std::cout << "=== " << name << " (parent: " << tid.GetParent().GetName() << ") ===\n";
  std::cout << "-- trace sources --\n";
  for (uint32_t i = 0; i < tid.GetTraceSourceN(); ++i) {
    auto info = tid.GetTraceSource(i);
    std::cout << "  " << info.name << " [" << info.callback << "]\n";
  }
  std::cout << "-- attributes --\n";
  for (uint32_t i = 0; i < tid.GetAttributeN(); ++i) {
    auto info = tid.GetAttribute(i);
    std::cout << "  " << info.name << "\n";
  }
}

int main() {
  Dump("ns3::ApWifiMac");
  Dump("ns3::StaWifiMac");
  Dump("ns3::WifiMac");
  Dump("ns3::WifiPhy");
  Dump("ns3::WifiRemoteStationManager");
  return 0;
}
