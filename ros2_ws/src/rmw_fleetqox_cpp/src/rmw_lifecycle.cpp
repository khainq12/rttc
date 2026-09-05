#include <algorithm>
#include <new>
#include <cstring>
#include <mutex>
#include <vector>

#include "rcutils/allocator.h"
#include "rcutils/strdup.h"
#include "rmw/allocators.h"
#include "rmw/discovery_options.h"
#include "rmw/domain_id.h"
#include "rmw/error_handling.h"
#include "rmw/init.h"
#include "rmw/init_options.h"
#include "rmw/localhost.h"
#include "rmw/rmw.h"
#include "rmw/security_options.h"
#include "rmw/validate_namespace.h"
#include "rmw/validate_node_name.h"

struct rmw_context_impl_s
{
  bool is_shutdown;
  rcutils_allocator_t allocator;
};

extern "C" int rmw_fleetqox_cpp_sros2_identity_validation_decision(
  const char * enclave);

namespace
{

constexpr const char * kIdentifier = "rmw_fleetqox_cpp";

struct FleetQoxNodeData
{
  rcutils_allocator_t allocator;
  std::size_t domain_id;
  char * name;
  char * namespace_;
  rmw_guard_condition_t * graph_guard_condition;
};

std::mutex g_node_guard_mutex;
std::vector<FleetQoxNodeData *> g_node_guard_data;

bool identifier_matches(const char * identifier)
{
  return identifier != nullptr && std::strcmp(identifier, kIdentifier) == 0;
}

bool allocator_is_valid(const rcutils_allocator_t & allocator)
{
  return rcutils_allocator_is_valid(&allocator);
}

void reset_init_options(rmw_init_options_t * options)
{
  if (options != nullptr) {
    *options = rmw_get_zero_initialized_init_options();
  }
}

void deallocate_string(char * value, rcutils_allocator_t allocator)
{
  if (value != nullptr && allocator.deallocate != nullptr) {
    allocator.deallocate(value, allocator.state);
  }
}

rmw_ret_t require_fleetqox_identifier(const char * identifier)
{
  if (!identifier_matches(identifier)) {
    RMW_SET_ERROR_MSG("rmw_fleetqox_cpp implementation identifier mismatch");
    return RMW_RET_INCORRECT_RMW_IMPLEMENTATION;
  }
  return RMW_RET_OK;
}

bool context_is_valid(const rmw_context_t * context)
{
  return context != nullptr &&
         identifier_matches(context->implementation_identifier) &&
         context->impl != nullptr &&
         !context->impl->is_shutdown;
}

rmw_ret_t validate_node_name_and_namespace(const char * name, const char * namespace_)
{
  if (name == nullptr || namespace_ == nullptr) {
    RMW_SET_ERROR_MSG("node name and namespace must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  int validation_result = RMW_NODE_NAME_VALID;
  size_t invalid_index = 0;
  rmw_ret_t ret = rmw_validate_node_name(name, &validation_result, &invalid_index);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (validation_result != RMW_NODE_NAME_VALID) {
    RMW_SET_ERROR_MSG("invalid rmw_fleetqox_cpp node name");
    return RMW_RET_INVALID_ARGUMENT;
  }
  validation_result = RMW_NAMESPACE_VALID;
  invalid_index = 0;
  ret = rmw_validate_namespace(namespace_, &validation_result, &invalid_index);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (validation_result != RMW_NAMESPACE_VALID) {
    RMW_SET_ERROR_MSG("invalid rmw_fleetqox_cpp node namespace");
    return RMW_RET_INVALID_ARGUMENT;
  }
  return RMW_RET_OK;
}

}  // namespace

extern "C"
{

void rmw_fleetqox_cpp_graph_register_node(
  const char * name, const char * namespace_, std::size_t domain_id);
void rmw_fleetqox_cpp_graph_unregister_node(
  const char * name, const char * namespace_, std::size_t domain_id);
bool rmw_fleetqox_cpp_socket_ensure_started();
const char * rmw_fleetqox_cpp_socket_init_error();
void rmw_fleetqox_cpp_shutdown_pubsub_runtime();
void rmw_fleetqox_cpp_register_live_node(const rmw_node_t * node);
void rmw_fleetqox_cpp_unregister_live_node(const rmw_node_t * node);

void rmw_fleetqox_cpp_trigger_graph_guard_conditions()
{
  std::lock_guard<std::mutex> lock(g_node_guard_mutex);
  for (FleetQoxNodeData * data : g_node_guard_data) {
    if (data != nullptr && data->graph_guard_condition != nullptr) {
      const rmw_ret_t ret = rmw_trigger_guard_condition(data->graph_guard_condition);
      (void)ret;
    }
  }
}

void rmw_fleetqox_cpp_trigger_graph_guard_conditions_for_domain(std::size_t domain_id)
{
  std::lock_guard<std::mutex> lock(g_node_guard_mutex);
  for (FleetQoxNodeData * data : g_node_guard_data) {
    if (data == nullptr || data->domain_id != domain_id ||
      data->graph_guard_condition == nullptr)
    {
      continue;
    }
    const rmw_ret_t ret = rmw_trigger_guard_condition(data->graph_guard_condition);
    (void)ret;
  }
}

rmw_init_options_t rmw_get_zero_initialized_init_options(void)
{
  return rmw_init_options_t{};
}

rmw_ret_t rmw_init_options_init(rmw_init_options_t * init_options, rcutils_allocator_t allocator)
{
  if (init_options == nullptr) {
    RMW_SET_ERROR_MSG("init_options is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (init_options->implementation_identifier != nullptr || init_options->impl != nullptr) {
    RMW_SET_ERROR_MSG("init_options is already initialized");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (!allocator_is_valid(allocator)) {
    RMW_SET_ERROR_MSG("allocator is invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }

  rmw_init_options_t initialized = rmw_get_zero_initialized_init_options();
  initialized.implementation_identifier = kIdentifier;
  initialized.domain_id = RMW_DEFAULT_DOMAIN_ID;
  initialized.security_options = rmw_get_default_security_options();
  initialized.localhost_only = RMW_LOCALHOST_ONLY_DEFAULT;
  initialized.discovery_options = rmw_get_zero_initialized_discovery_options();
  initialized.discovery_options.automatic_discovery_range =
    RMW_AUTOMATIC_DISCOVERY_RANGE_SYSTEM_DEFAULT;
  initialized.discovery_options.allocator = allocator;
  initialized.allocator = allocator;
  initialized.enclave = rcutils_strdup("", allocator);
  if (initialized.enclave == nullptr) {
    RMW_SET_ERROR_MSG("failed to allocate default enclave");
    return RMW_RET_BAD_ALLOC;
  }

  *init_options = initialized;
  return RMW_RET_OK;
}

rmw_ret_t rmw_init_options_copy(const rmw_init_options_t * src, rmw_init_options_t * dst)
{
  if (src == nullptr || dst == nullptr) {
    RMW_SET_ERROR_MSG("init_options copy arguments must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_fleetqox_identifier(src->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (dst->implementation_identifier != nullptr || dst->impl != nullptr) {
    RMW_SET_ERROR_MSG("destination init_options is already initialized");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (!allocator_is_valid(src->allocator)) {
    RMW_SET_ERROR_MSG("source init_options allocator is invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }

  rmw_init_options_t copied = rmw_get_zero_initialized_init_options();
  copied.instance_id = src->instance_id;
  copied.implementation_identifier = kIdentifier;
  copied.domain_id = src->domain_id;
  copied.localhost_only = src->localhost_only;
  copied.allocator = src->allocator;
  copied.impl = nullptr;

  ret = rmw_security_options_copy(&src->security_options, &copied.allocator, &copied.security_options);
  if (ret != RMW_RET_OK) {
    reset_init_options(&copied);
    return ret;
  }
  ret = rmw_discovery_options_copy(&src->discovery_options, &copied.allocator, &copied.discovery_options);
  if (ret != RMW_RET_OK) {
    rmw_security_options_fini(&copied.security_options, &copied.allocator);
    reset_init_options(&copied);
    return ret;
  }
  copied.enclave = rcutils_strdup(src->enclave != nullptr ? src->enclave : "", copied.allocator);
  if (copied.enclave == nullptr) {
    const rmw_ret_t discovery_fini_ret = rmw_discovery_options_fini(&copied.discovery_options);
    const rmw_ret_t security_fini_ret =
      rmw_security_options_fini(&copied.security_options, &copied.allocator);
    (void)discovery_fini_ret;
    (void)security_fini_ret;
    reset_init_options(&copied);
    RMW_SET_ERROR_MSG("failed to copy enclave");
    return RMW_RET_BAD_ALLOC;
  }

  *dst = copied;
  return RMW_RET_OK;
}

rmw_ret_t rmw_init_options_fini(rmw_init_options_t * init_options)
{
  if (init_options == nullptr) {
    RMW_SET_ERROR_MSG("init_options is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_fleetqox_identifier(init_options->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (!allocator_is_valid(init_options->allocator)) {
    RMW_SET_ERROR_MSG("init_options allocator is invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }

  rmw_ret_t final_ret = RMW_RET_OK;
  if (rmw_security_options_fini(&init_options->security_options, &init_options->allocator) != RMW_RET_OK) {
    final_ret = RMW_RET_ERROR;
  }
  if (rmw_discovery_options_fini(&init_options->discovery_options) != RMW_RET_OK) {
    final_ret = RMW_RET_ERROR;
  }
  deallocate_string(init_options->enclave, init_options->allocator);
  reset_init_options(init_options);
  return final_ret;
}

rmw_context_t rmw_get_zero_initialized_context(void)
{
  return rmw_context_t{};
}

rmw_ret_t rmw_init(const rmw_init_options_t * options, rmw_context_t * context)
{
  if (options == nullptr || context == nullptr) {
    RMW_SET_ERROR_MSG("rmw_init options and context must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_fleetqox_identifier(options->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (options->enclave == nullptr) {
    RMW_SET_ERROR_MSG("rmw_init requires initialized options with an enclave");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (context->implementation_identifier != nullptr || context->impl != nullptr) {
    RMW_SET_ERROR_MSG("context is already initialized");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (!allocator_is_valid(options->allocator)) {
    RMW_SET_ERROR_MSG("rmw_init options allocator is invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  const int identity_decision =
    rmw_fleetqox_cpp_sros2_identity_validation_decision(options->enclave);
  if (identity_decision == 2) {
    RMW_SET_ERROR_MSG("SROS2 local identity certificate/key/CA validation failed");
    return RMW_RET_ERROR;
  }
  if (identity_decision == 3) {
    RMW_SET_ERROR_MSG("SROS2 local identity certificate common name does not match enclave");
    return RMW_RET_ERROR;
  }

  void * impl_memory = options->allocator.allocate(sizeof(rmw_context_impl_s), options->allocator.state);
  if (impl_memory == nullptr) {
    RMW_SET_ERROR_MSG("failed to allocate rmw_fleetqox_cpp context impl");
    return RMW_RET_BAD_ALLOC;
  }
  auto * impl = new (impl_memory) rmw_context_impl_s{false, options->allocator};

  rmw_init_options_t options_copy = rmw_get_zero_initialized_init_options();
  ret = rmw_init_options_copy(options, &options_copy);
  if (ret != RMW_RET_OK) {
    impl->~rmw_context_impl_s();
    options->allocator.deallocate(impl_memory, options->allocator.state);
    return ret;
  }

  context->instance_id = options->instance_id;
  context->implementation_identifier = kIdentifier;
  context->options = options_copy;
  context->actual_domain_id = options->domain_id == RMW_DEFAULT_DOMAIN_ID ? 0 : options->domain_id;
  context->impl = impl;
  return RMW_RET_OK;
}

rmw_ret_t rmw_shutdown(rmw_context_t * context)
{
  if (context == nullptr) {
    RMW_SET_ERROR_MSG("context is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_fleetqox_identifier(context->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (context->impl == nullptr) {
    RMW_SET_ERROR_MSG("context impl is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  context->impl->is_shutdown = true;
  return RMW_RET_OK;
}

rmw_ret_t rmw_context_fini(rmw_context_t * context)
{
  if (context == nullptr) {
    RMW_SET_ERROR_MSG("context is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_fleetqox_identifier(context->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (context->impl == nullptr || !context->impl->is_shutdown) {
    RMW_SET_ERROR_MSG("context must be shutdown before fini");
    return RMW_RET_INVALID_ARGUMENT;
  }

  bool no_local_nodes = false;
  {
    std::lock_guard<std::mutex> lock(g_node_guard_mutex);
    no_local_nodes = g_node_guard_data.empty();
  }
  if (no_local_nodes) {
    // Stop the background threads (retransmit, deadline monitor, graph
    // renewal, remote graph lease monitor, service workers) before this
    // context's impl is destructed and freed below, so nothing can be
    // mid-callback against state that's about to be deallocated.
    rmw_fleetqox_cpp_shutdown_pubsub_runtime();
  }

  rmw_context_impl_s * impl = context->impl;
  rcutils_allocator_t allocator = impl->allocator;
  ret = rmw_init_options_fini(&context->options);
  impl->~rmw_context_impl_s();
  allocator.deallocate(impl, allocator.state);
  *context = rmw_get_zero_initialized_context();
  return ret;
}

rmw_node_t * rmw_create_node(
  rmw_context_t * context,
  const char * name,
  const char * namespace_)
{
  if (!context_is_valid(context)) {
    RMW_SET_ERROR_MSG("context is not a valid rmw_fleetqox_cpp context");
    return nullptr;
  }
  if (validate_node_name_and_namespace(name, namespace_) != RMW_RET_OK) {
    return nullptr;
  }

  rcutils_allocator_t allocator = context->options.allocator;
  if (!allocator_is_valid(allocator)) {
    RMW_SET_ERROR_MSG("context allocator is invalid");
    return nullptr;
  }
  if (!rmw_fleetqox_cpp_socket_ensure_started()) {
    const char * init_error = rmw_fleetqox_cpp_socket_init_error();
    RMW_SET_ERROR_MSG(init_error != nullptr && init_error[0] != '\0' ?
      init_error : "socket transport is not ready");
    return nullptr;
  }

  rmw_node_t * node = rmw_node_allocate();
  if (node == nullptr) {
    RMW_SET_ERROR_MSG("failed to allocate rmw node");
    return nullptr;
  }
  void * data_memory = allocator.allocate(sizeof(FleetQoxNodeData), allocator.state);
  if (data_memory == nullptr) {
    rmw_node_free(node);
    RMW_SET_ERROR_MSG("failed to allocate rmw_fleetqox_cpp node data");
    return nullptr;
  }
  auto * data = new (data_memory) FleetQoxNodeData{
    allocator, context->actual_domain_id, nullptr, nullptr, nullptr};
  data->name = rcutils_strdup(name, allocator);
  data->namespace_ = rcutils_strdup(namespace_, allocator);
  if (data->name == nullptr || data->namespace_ == nullptr) {
    deallocate_string(data->name, allocator);
    deallocate_string(data->namespace_, allocator);
    data->~FleetQoxNodeData();
    allocator.deallocate(data_memory, allocator.state);
    rmw_node_free(node);
    RMW_SET_ERROR_MSG("failed to copy rmw_fleetqox_cpp node identity");
    return nullptr;
  }
  data->graph_guard_condition = rmw_create_guard_condition(context);
  if (data->graph_guard_condition == nullptr) {
    deallocate_string(data->name, allocator);
    deallocate_string(data->namespace_, allocator);
    data->~FleetQoxNodeData();
    allocator.deallocate(data_memory, allocator.state);
    rmw_node_free(node);
    RMW_SET_ERROR_MSG("failed to create rmw_fleetqox_cpp graph guard condition");
    return nullptr;
  }

  node->implementation_identifier = kIdentifier;
  node->data = data;
  node->name = data->name;
  node->namespace_ = data->namespace_;
  node->context = context;
  {
    std::lock_guard<std::mutex> lock(g_node_guard_mutex);
    g_node_guard_data.push_back(data);
  }
  rmw_fleetqox_cpp_graph_register_node(
    node->name, node->namespace_, node->context->actual_domain_id);
  rmw_fleetqox_cpp_register_live_node(node);
  return node;
}

rmw_ret_t rmw_destroy_node(rmw_node_t * node)
{
  if (node == nullptr) {
    RMW_SET_ERROR_MSG("node is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_fleetqox_identifier(node->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  // Unregister before any teardown so a caller that still holds this
  // pointer (observed: upstream rcl's global rosout logging fini, invoked
  // well after this node was destroyed) gets a safe "invalid" verdict from
  // node_is_valid() instead of dereferencing memory this function is about
  // to free.
  rmw_fleetqox_cpp_unregister_live_node(node);
  auto * data = static_cast<FleetQoxNodeData *>(node->data);
  if (data != nullptr) {
    rcutils_allocator_t allocator = data->allocator;
    {
      std::lock_guard<std::mutex> lock(g_node_guard_mutex);
      g_node_guard_data.erase(
        std::remove(g_node_guard_data.begin(), g_node_guard_data.end(), data),
        g_node_guard_data.end());
    }
    rmw_fleetqox_cpp_graph_unregister_node(
      data->name, data->namespace_, node->context->actual_domain_id);
    if (data->graph_guard_condition != nullptr) {
      const rmw_ret_t graph_guard_ret = rmw_destroy_guard_condition(data->graph_guard_condition);
      (void)graph_guard_ret;
    }
    deallocate_string(data->name, allocator);
    deallocate_string(data->namespace_, allocator);
    data->~FleetQoxNodeData();
    allocator.deallocate(data, allocator.state);
  }
  rmw_node_free(node);
  return RMW_RET_OK;
}

const rmw_guard_condition_t * rmw_node_get_graph_guard_condition(const rmw_node_t * node)
{
  if (node == nullptr || !identifier_matches(node->implementation_identifier)) {
    RMW_SET_ERROR_MSG("node is not a valid rmw_fleetqox_cpp node");
    return nullptr;
  }
  auto * data = static_cast<FleetQoxNodeData *>(node->data);
  if (data == nullptr || data->graph_guard_condition == nullptr) {
    RMW_SET_ERROR_MSG("node graph guard condition is not available");
    return nullptr;
  }
  return data->graph_guard_condition;
}

}  // extern "C"
