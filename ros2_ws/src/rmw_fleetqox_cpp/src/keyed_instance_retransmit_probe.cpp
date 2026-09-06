#include <cstdint>
#include <iostream>
#include <string>

#include "fleetrmw_interfaces/msg/detail/keyed_instance_sample__struct.hpp"
#include "fleetrmw_interfaces/msg/detail/keyed_instance_sample__type_support.hpp"
#include "fleetrmw_interfaces/msg/detail/w_string_sample__struct.hpp"
#include "fleetrmw_interfaces/msg/detail/w_string_sample__type_support.hpp"
#include "fleetrmw_interfaces/msg/keyed_instance_sample.hpp"
#include "fleetrmw_interfaces/msg/w_string_sample.hpp"
#include "rcutils/allocator.h"
#include "rmw/init.h"
#include "rmw/init_options.h"
#include "rmw/publisher_options.h"
#include "rmw/qos_profiles.h"
#include "rmw/rmw.h"
#include "rmw/subscription_options.h"
#include "rosidl_typesupport_interface/macros.h"

// Probes the retransmit ledger's per-@key-instance history bound.
//
// rosidl_typesupport_introspection_{c,cpp}::MessageMember::is_key_ is a real,
// documented field (tracks the OMG IDL4 @key annotation), but no stock
// ROS 2 message uses it -- the legacy .msg grammar cannot express @key at
// all, only a hand-authored .idl can (see KeyedInstanceSample.idl). Before
// this probe, FleetRMW's reliable-retransmit ledger bounded history purely
// per-publisher (via qos.depth for KEEP_LAST, else a fixed 4096-entry cap),
// with no notion of DDS-style per-instance bounding at all.
//
// This probe publishes RELIABLE/KEEP_LAST(depth=3) samples for two distinct
// key_id instances, interleaved, 5 samples each (10 total, well over the
// depth-3 bound), to a publisher with no matched subscription. With no
// subscriber to ever acknowledge them, every entry's eviction is driven
// purely by the depth bound (see the "truly subscriber-less reliable
// publisher" comment beside ReliableRetransmitEntry's construction in
// rmw_pubsub.cpp) -- giving this probe a deterministic, race-free way to
// observe the bound. With per-instance bounding active, each instance keeps
// its own most recent 3 samples independently -- 6 entries total, 2 distinct
// instances, 3 max per instance. Without it (the old, purely-per-publisher
// behavior), the two instances would share one depth-3 budget -- at most 3
// entries total, not 6.
extern "C" size_t rmw_fleetqox_cpp_test_retransmit_ledger_total_entries_for_publisher(
  const rmw_publisher_t * publisher);
extern "C" size_t rmw_fleetqox_cpp_test_retransmit_ledger_distinct_instances_for_publisher(
  const rmw_publisher_t * publisher);
extern "C" size_t
rmw_fleetqox_cpp_test_retransmit_ledger_max_entries_per_instance_for_publisher(
  const rmw_publisher_t * publisher);

int main()
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options = rmw_get_zero_initialized_init_options();
  rmw_context_t context = rmw_get_zero_initialized_context();
  if (rmw_init_options_init(&options, allocator) != RMW_RET_OK ||
    rmw_init(&options, &context) != RMW_RET_OK)
  {
    std::cout << "{\"status\":\"context_init_failed\"}" << std::endl;
    return 1;
  }
  rmw_node_t * node = rmw_create_node(&context, "keyed_instance_retransmit_probe", "/fleetqox");
  const auto * type_support =
    ROSIDL_TYPESUPPORT_INTERFACE__MESSAGE_SYMBOL_NAME(
    rosidl_typesupport_cpp, fleetrmw_interfaces, msg, KeyedInstanceSample)();

  rmw_qos_profile_t qos = rmw_qos_profile_default;
  qos.reliability = RMW_QOS_POLICY_RELIABILITY_RELIABLE;
  qos.history = RMW_QOS_POLICY_HISTORY_KEEP_LAST;
  qos.depth = 3;

  const rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  // Deliberately no matched subscription: with nothing to ever acknowledge
  // an entry, eviction is driven purely by the depth bound, which is what
  // this probe needs to observe deterministically (see the comment above).
  rmw_publisher_t * publisher = node == nullptr ? nullptr : rmw_create_publisher(
    node, type_support, "/fleetqox/keyed_instance_retransmit_probe", &qos, &publisher_options);
  if (publisher == nullptr) {
    std::cout << "{\"status\":\"endpoint_create_failed\"}" << std::endl;
    return 1;
  }

  constexpr std::uint32_t kSamplesPerInstance = 5;
  constexpr std::uint32_t kDepth = 3;
  bool publish_ok = true;
  for (std::uint32_t round = 0; round < kSamplesPerInstance; ++round) {
    for (std::uint32_t key_id : {1u, 2u}) {
      fleetrmw_interfaces::msg::KeyedInstanceSample sample;
      sample.key_id = key_id;
      sample.sequence = round;
      sample.tag = "instance-" + std::to_string(key_id);
      publish_ok = publish_ok && rmw_publish(publisher, &sample, nullptr) == RMW_RET_OK;
    }
  }

  const size_t total_entries =
    rmw_fleetqox_cpp_test_retransmit_ledger_total_entries_for_publisher(publisher);
  const size_t distinct_instances =
    rmw_fleetqox_cpp_test_retransmit_ledger_distinct_instances_for_publisher(publisher);
  const size_t max_per_instance =
    rmw_fleetqox_cpp_test_retransmit_ledger_max_entries_per_instance_for_publisher(publisher);

  const bool per_instance_bounding_observed =
    distinct_instances == 2 &&
    max_per_instance == kDepth &&
    total_entries == kDepth * 2;

  // Regression check: a message type with no @key field (every stock ROS 2
  // message today) must keep the exact old behavior -- one shared depth-3
  // budget for the whole publisher, not per-instance. Same interleave shape,
  // just a "key_id"-shaped field that carries no @key annotation.
  const auto * wstring_type_support =
    ROSIDL_TYPESUPPORT_INTERFACE__MESSAGE_SYMBOL_NAME(
    rosidl_typesupport_cpp, fleetrmw_interfaces, msg, WStringSample)();
  rmw_publisher_t * keyless_publisher = node == nullptr ? nullptr : rmw_create_publisher(
    node, wstring_type_support, "/fleetqox/keyed_instance_retransmit_probe_keyless",
    &qos, &publisher_options);
  bool keyless_publish_ok = keyless_publisher != nullptr;
  for (std::uint32_t round = 0; round < kSamplesPerInstance; ++round) {
    for (std::uint32_t key_id : {1u, 2u}) {
      fleetrmw_interfaces::msg::WStringSample sample;
      sample.tag = "instance-" + std::to_string(key_id) + "-" + std::to_string(round);
      keyless_publish_ok = keyless_publish_ok &&
        rmw_publish(keyless_publisher, &sample, nullptr) == RMW_RET_OK;
    }
  }
  const size_t keyless_total_entries = keyless_publisher == nullptr ? 0 :
    rmw_fleetqox_cpp_test_retransmit_ledger_total_entries_for_publisher(keyless_publisher);
  const size_t keyless_distinct_instances = keyless_publisher == nullptr ? 0 :
    rmw_fleetqox_cpp_test_retransmit_ledger_distinct_instances_for_publisher(keyless_publisher);
  const bool keyless_behavior_unchanged =
    keyless_distinct_instances == 1 && keyless_total_entries == kDepth;

  const bool destroy_ok =
    rmw_destroy_publisher(node, publisher) == RMW_RET_OK &&
    (keyless_publisher == nullptr ||
    rmw_destroy_publisher(node, keyless_publisher) == RMW_RET_OK) &&
    rmw_destroy_node(node) == RMW_RET_OK;

  const bool ok = publish_ok && per_instance_bounding_observed &&
    keyless_publish_ok && keyless_behavior_unchanged && destroy_ok;

  std::cout << "{\"schema_version\":\"fleetrmw.keyed_instance_retransmit_probe.v1\",";
  std::cout << "\"status\":\"" << (ok ? "ok" : "failed") << "\",";
  std::cout << "\"publish_ok\":" << (publish_ok ? "true" : "false") << ",";
  std::cout << "\"total_entries\":" << total_entries << ",";
  std::cout << "\"distinct_instances\":" << distinct_instances << ",";
  std::cout << "\"max_entries_per_instance\":" << max_per_instance << ",";
  std::cout << "\"per_instance_bounding_observed\":" <<
    (per_instance_bounding_observed ? "true" : "false") << ",";
  std::cout << "\"keyless_publish_ok\":" << (keyless_publish_ok ? "true" : "false") << ",";
  std::cout << "\"keyless_total_entries\":" << keyless_total_entries << ",";
  std::cout << "\"keyless_distinct_instances\":" << keyless_distinct_instances << ",";
  std::cout << "\"keyless_behavior_unchanged\":" <<
    (keyless_behavior_unchanged ? "true" : "false") << "}" << std::endl;

  const rmw_ret_t shutdown_ret = rmw_shutdown(&context);
  const rmw_ret_t context_fini_ret = rmw_context_fini(&context);
  const rmw_ret_t options_fini_ret = rmw_init_options_fini(&options);
  (void)shutdown_ret;
  (void)context_fini_ret;
  (void)options_fini_ret;
  return ok ? 0 : 1;
}
