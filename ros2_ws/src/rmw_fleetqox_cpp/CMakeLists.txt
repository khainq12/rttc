cmake_minimum_required(VERSION 3.8)
project(rmw_fleetqox_cpp)

include(CTest)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(rcl REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rcutils REQUIRED)
find_package(rmw REQUIRED)
find_package(rmw_dds_common REQUIRED)
find_package(OpenSSL REQUIRED)
find_package(rosidl_runtime_c REQUIRED)
find_package(rosidl_dynamic_typesupport REQUIRED)
find_package(rosidl_typesupport_c REQUIRED)
find_package(rosidl_typesupport_cpp REQUIRED)
find_package(rosidl_typesupport_introspection_c REQUIRED)
find_package(rosidl_typesupport_introspection_cpp REQUIRED)
find_package(std_msgs REQUIRED)
find_package(std_srvs REQUIRED)
find_package(tinyxml2_vendor REQUIRED)
find_package(PkgConfig QUIET)

set(FLEETRMW_OPTIONAL_TARGETS)

add_library(${PROJECT_NAME}_transport
  src/data_frame.cpp
  src/inprocess_quic_client.cpp
  src/quic_gateway_transport.cpp
  src/shared_memory_transport.cpp
)
set_target_properties(${PROJECT_NAME}_transport PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_compile_features(${PROJECT_NAME}_transport PUBLIC cxx_std_17)
target_include_directories(${PROJECT_NAME}_transport PUBLIC
  "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>"
  "$<INSTALL_INTERFACE:include>"
)

add_library(${PROJECT_NAME} SHARED
  src/rmw_identifier.cpp
  src/rmw_graph.cpp
  src/rmw_lifecycle.cpp
  src/rmw_pubsub.cpp
  src/rmw_stubs.cpp
  src/rmw_wait.cpp
)
target_compile_features(${PROJECT_NAME} PUBLIC cxx_std_17)
target_link_libraries(${PROJECT_NAME}
  ${PROJECT_NAME}_transport
  OpenSSL::Crypto
  ${CMAKE_DL_LIBS}
  tinyxml2::tinyxml2
)
ament_target_dependencies(${PROJECT_NAME}
  rcutils
  rmw
  rmw_dds_common
  rosidl_runtime_c
  rosidl_dynamic_typesupport
  rosidl_typesupport_c
  rosidl_typesupport_cpp
  rosidl_typesupport_introspection_c
  rosidl_typesupport_introspection_cpp
)

add_executable(fleetrmw_transport_loop_smoke
  src/transport_loop_smoke.cpp
)
target_compile_features(fleetrmw_transport_loop_smoke PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_transport_loop_smoke
  ${PROJECT_NAME}_transport
)

add_executable(fleetrmw_frame_probe
  src/frame_probe.cpp
)
target_compile_features(fleetrmw_frame_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_frame_probe
  ${PROJECT_NAME}_transport
)

add_executable(fleetrmw_action_frame_probe
  src/action_frame_probe.cpp
)
target_compile_features(fleetrmw_action_frame_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_action_frame_probe
  ${PROJECT_NAME}_transport
)

add_executable(fleetrmw_action_router_probe
  src/action_router_probe.cpp
)
target_compile_features(fleetrmw_action_router_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_action_router_probe
  ${PROJECT_NAME}_transport
)

add_executable(fleetrmw_lifecycle_probe
  src/lifecycle_probe.cpp
)
target_compile_features(fleetrmw_lifecycle_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_lifecycle_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_lifecycle_probe
  rcutils
  rmw
)

add_executable(fleetrmw_serialized_pubsub_probe
  src/serialized_pubsub_probe.cpp
)
target_compile_features(fleetrmw_serialized_pubsub_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_serialized_pubsub_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_serialized_pubsub_probe
  rcutils
  rmw
)

add_executable(fleetrmw_qos_probe
  src/qos_probe.cpp
)
target_compile_features(fleetrmw_qos_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_qos_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_qos_probe
  rcutils
  rmw
)

add_executable(fleetrmw_qos_best_available_probe
  src/qos_best_available_probe.cpp
)
target_compile_features(fleetrmw_qos_best_available_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_qos_best_available_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_qos_best_available_probe
  rcutils
  rmw
)

add_executable(fleetrmw_take_sequence_probe
  src/take_sequence_probe.cpp
)
target_compile_features(fleetrmw_take_sequence_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_take_sequence_probe
  ${PROJECT_NAME}
  std_msgs::std_msgs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_take_sequence_probe
  rcutils
  rmw
  rosidl_runtime_c
  std_msgs
)

add_executable(fleetrmw_qos_event_probe
  src/qos_event_probe.cpp
)
target_compile_features(fleetrmw_qos_event_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_qos_event_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_qos_event_probe
  rcutils
  rmw
)

add_executable(fleetrmw_matched_event_probe
  src/matched_event_probe.cpp
)
target_compile_features(fleetrmw_matched_event_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_matched_event_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_matched_event_probe
  rcutils
  rmw
)

add_executable(fleetrmw_callback_teardown_probe
  src/callback_teardown_probe.cpp
)
target_compile_features(fleetrmw_callback_teardown_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_callback_teardown_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_callback_teardown_probe
  rcutils
  rmw
)

add_executable(fleetrmw_remote_event_probe
  src/remote_event_probe.cpp
)
target_compile_features(fleetrmw_remote_event_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_remote_event_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_remote_event_probe
  rcutils
  rmw
)

add_executable(fleetrmw_qos_incompatible_event_probe
  src/qos_incompatible_event_probe.cpp
)
target_compile_features(fleetrmw_qos_incompatible_event_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_qos_incompatible_event_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_qos_incompatible_event_probe
  rcutils
  rmw
)

add_executable(fleetrmw_qos_deadline_incompatible_event_probe
  src/qos_deadline_incompatible_event_probe.cpp
)
target_compile_features(fleetrmw_qos_deadline_incompatible_event_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_qos_deadline_incompatible_event_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_qos_deadline_incompatible_event_probe
  rcutils
  rmw
)

add_executable(fleetrmw_qos_liveliness_incompatible_event_probe
  src/qos_liveliness_incompatible_event_probe.cpp
)
target_compile_features(fleetrmw_qos_liveliness_incompatible_event_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_qos_liveliness_incompatible_event_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_qos_liveliness_incompatible_event_probe
  rcutils
  rmw
)

add_executable(fleetrmw_type_incompatible_event_probe
  src/type_incompatible_event_probe.cpp
)
target_compile_features(fleetrmw_type_incompatible_event_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_type_incompatible_event_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_type_incompatible_event_probe
  rcutils
  rmw
)

add_executable(fleetrmw_message_lost_event_probe
  src/message_lost_event_probe.cpp
)
target_compile_features(fleetrmw_message_lost_event_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_message_lost_event_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_message_lost_event_probe
  rcutils
  rmw
)

add_executable(fleetrmw_message_lost_interprocess_probe
  src/message_lost_interprocess_probe.cpp
)
target_compile_features(fleetrmw_message_lost_interprocess_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_message_lost_interprocess_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_message_lost_interprocess_probe
  rcutils
  rmw
)

add_executable(fleetrmw_liveliness_event_probe
  src/liveliness_event_probe.cpp
)
target_compile_features(fleetrmw_liveliness_event_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_liveliness_event_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_liveliness_event_probe
  rcutils
  rmw
)

add_executable(fleetrmw_automatic_liveliness_probe
  src/automatic_liveliness_probe.cpp
)
target_compile_features(fleetrmw_automatic_liveliness_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_automatic_liveliness_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_automatic_liveliness_probe
  rcutils
  rmw
)

add_executable(fleetrmw_remote_manual_liveliness_probe
  src/remote_manual_liveliness_probe.cpp
)
target_compile_features(fleetrmw_remote_manual_liveliness_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_remote_manual_liveliness_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_remote_manual_liveliness_probe
  rcutils
  rmw
)

add_executable(fleetrmw_remote_liveliness_multi_endpoint_probe
  src/remote_liveliness_multi_endpoint_probe.cpp
)
target_compile_features(fleetrmw_remote_liveliness_multi_endpoint_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_remote_liveliness_multi_endpoint_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_remote_liveliness_multi_endpoint_probe
  rcutils
  rmw
)

add_executable(fleetrmw_liveliness_scale_probe
  src/liveliness_scale_probe.cpp
)
target_compile_features(fleetrmw_liveliness_scale_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_liveliness_scale_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_liveliness_scale_probe
  rcutils
  rmw
)

add_executable(fleetrmw_remote_liveliness_scale_probe
  src/remote_liveliness_scale_probe.cpp
)
target_compile_features(fleetrmw_remote_liveliness_scale_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_remote_liveliness_scale_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_remote_liveliness_scale_probe
  rcutils
  rmw
)

add_executable(fleetrmw_liveliness_default_lease_probe
  src/liveliness_default_lease_probe.cpp
)
target_compile_features(fleetrmw_liveliness_default_lease_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_liveliness_default_lease_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_liveliness_default_lease_probe
  rcutils
  rmw
)

add_executable(fleetrmw_remote_deadline_event_probe
  src/remote_deadline_event_probe.cpp
)
target_compile_features(fleetrmw_remote_deadline_event_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_remote_deadline_event_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_remote_deadline_event_probe
  rcutils
  rmw
)

add_executable(fleetrmw_content_filter_probe
  src/content_filter_probe.cpp
)
target_compile_features(fleetrmw_content_filter_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_content_filter_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_content_filter_probe
  rcutils
  rmw
)

add_executable(fleetrmw_content_filter_sql_probe
  src/content_filter_sql_probe.cpp
)
target_compile_features(fleetrmw_content_filter_sql_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_content_filter_sql_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_content_filter_sql_probe
  rcutils
  rmw
)

add_executable(fleetrmw_content_filter_typed_probe
  src/content_filter_typed_probe.cpp
)
target_compile_features(fleetrmw_content_filter_typed_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_content_filter_typed_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_content_filter_typed_probe
  geometry_msgs
  rcutils
  rmw
  rosidl_typesupport_c
  rosidl_typesupport_cpp
  std_msgs
)

add_executable(fleetrmw_service_qos_probe
  src/service_qos_probe.cpp
)
target_compile_features(fleetrmw_service_qos_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_service_qos_probe
  ${PROJECT_NAME}
  std_srvs::std_srvs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_service_qos_probe
  rcutils
  rmw
  std_srvs
)

add_executable(fleetrmw_domain_isolation_probe
  src/domain_isolation_probe.cpp
)
target_compile_features(fleetrmw_domain_isolation_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_domain_isolation_probe
  ${PROJECT_NAME}
  std_srvs::std_srvs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_domain_isolation_probe
  rcutils
  rmw
  std_srvs
)

add_executable(fleetrmw_service_error_probe
  src/service_error_probe.cpp
)
target_compile_features(fleetrmw_service_error_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_service_error_probe
  ${PROJECT_NAME}
  std_srvs::std_srvs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_service_error_probe
  rcutils
  rmw
  std_srvs
)

add_executable(fleetrmw_service_resource_limit_probe
  src/service_resource_limit_probe.cpp
)
target_compile_features(fleetrmw_service_resource_limit_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_service_resource_limit_probe
  ${PROJECT_NAME}
  std_srvs::std_srvs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_service_resource_limit_probe
  rcutils
  rmw
  std_srvs
)

add_executable(fleetrmw_service_client_isolation_probe
  src/service_client_isolation_probe.cpp
)
target_compile_features(fleetrmw_service_client_isolation_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_service_client_isolation_probe
  ${PROJECT_NAME}
  std_srvs::std_srvs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_service_client_isolation_probe
  rcutils
  rmw
  std_srvs
)

add_executable(fleetrmw_service_repair_admission_probe
  src/service_repair_admission_probe.cpp
)
target_compile_features(fleetrmw_service_repair_admission_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_service_repair_admission_probe
  ${PROJECT_NAME}
  std_srvs::std_srvs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_service_repair_admission_probe
  rcutils
  rmw
  std_srvs
)

add_executable(fleetrmw_service_priority_probe
  src/service_priority_probe.cpp
)
target_compile_features(fleetrmw_service_priority_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_service_priority_probe
  ${PROJECT_NAME}
  std_srvs::std_srvs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_service_priority_probe
  rcutils
  rmw
  std_srvs
)

add_executable(fleetrmw_service_weighted_fairness_probe
  src/service_weighted_fairness_probe.cpp
)
target_compile_features(fleetrmw_service_weighted_fairness_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_service_weighted_fairness_probe
  ${PROJECT_NAME}
  std_srvs::std_srvs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_service_weighted_fairness_probe
  rcutils
  rmw
  std_srvs
)

add_executable(fleetrmw_service_deadline_scheduler_probe
  src/service_deadline_scheduler_probe.cpp
)
target_compile_features(fleetrmw_service_deadline_scheduler_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_service_deadline_scheduler_probe
  ${PROJECT_NAME}
  std_srvs::std_srvs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_service_deadline_scheduler_probe
  rcutils
  rmw
  std_srvs
)

add_executable(fleetrmw_service_durable_replay_probe
  src/service_durable_replay_probe.cpp
)
target_compile_features(fleetrmw_service_durable_replay_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_service_durable_replay_probe
  ${PROJECT_NAME}
  std_srvs::std_srvs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_service_durable_replay_probe
  rcutils
  rmw
  rosidl_runtime_c
  std_srvs
)

add_executable(fleetrmw_reliability_probe
  src/reliability_probe.cpp
)
target_compile_features(fleetrmw_reliability_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_reliability_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_reliability_probe
  rcutils
  rmw
)

add_executable(fleetrmw_wait_for_all_acked_probe
  src/wait_for_all_acked_probe.cpp
)
target_compile_features(fleetrmw_wait_for_all_acked_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_wait_for_all_acked_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_wait_for_all_acked_probe
  rcutils
  rmw
)

add_executable(fleetrmw_remote_wait_for_all_acked_probe
  src/remote_wait_for_all_acked_probe.cpp
)
target_compile_features(fleetrmw_remote_wait_for_all_acked_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_remote_wait_for_all_acked_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_remote_wait_for_all_acked_probe
  rcutils
  rmw
)

add_executable(fleetrmw_reliable_interprocess_probe
  src/reliable_interprocess_probe.cpp
)
target_compile_features(fleetrmw_reliable_interprocess_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_reliable_interprocess_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_reliable_interprocess_probe
  rcutils
  rmw
)

add_executable(fleetrmw_typed_pubsub_probe
  src/typed_pubsub_probe.cpp
)
target_compile_features(fleetrmw_typed_pubsub_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_typed_pubsub_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_typed_pubsub_probe
  rcutils
  rmw
)

add_executable(fleetrmw_std_msgs_string_probe
  src/std_msgs_string_probe.cpp
)
target_compile_features(fleetrmw_std_msgs_string_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_std_msgs_string_probe
  ${PROJECT_NAME}
  std_msgs::std_msgs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_std_msgs_string_probe
  rcutils
  rmw
  std_msgs
)

add_executable(fleetrmw_quic_gateway_publish_probe
  src/quic_gateway_publish_probe.cpp
)
target_compile_features(fleetrmw_quic_gateway_publish_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_quic_gateway_publish_probe
  ${PROJECT_NAME}
  std_msgs::std_msgs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_quic_gateway_publish_probe
  rcutils
  rmw
  std_msgs
)

add_executable(fleetrmw_quic_gateway_burst_publish_probe
  src/quic_gateway_burst_publish_probe.cpp
)
target_compile_features(fleetrmw_quic_gateway_burst_publish_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_quic_gateway_burst_publish_probe
  ${PROJECT_NAME}
  std_msgs::std_msgs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_quic_gateway_burst_publish_probe
  rcutils
  rmw
  std_msgs
)

add_executable(fleetrmw_quic_gateway_take_probe
  src/quic_gateway_take_probe.cpp
)
target_compile_features(fleetrmw_quic_gateway_take_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_quic_gateway_take_probe
  ${PROJECT_NAME}_transport
)

add_executable(fleetrmw_quic_inprocess_session_probe
  src/quic_inprocess_session_probe.cpp
)
target_compile_features(fleetrmw_quic_inprocess_session_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_quic_inprocess_session_probe
  ${PROJECT_NAME}_transport
)

add_executable(fleetrmw_quic_inprocess_concurrent_stream_probe
  src/quic_inprocess_concurrent_stream_probe.cpp
)
target_compile_features(fleetrmw_quic_inprocess_concurrent_stream_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_quic_inprocess_concurrent_stream_probe
  ${PROJECT_NAME}_transport
)

add_executable(fleetrmw_quic_stateful_gateway_probe
  src/quic_stateful_gateway_probe.cpp
)
target_compile_features(fleetrmw_quic_stateful_gateway_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_quic_stateful_gateway_probe
  ${PROJECT_NAME}_transport
)

add_executable(fleetrmw_quic_stateful_rmw_probe
  src/quic_stateful_rmw_probe.cpp
)
target_compile_features(fleetrmw_quic_stateful_rmw_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_quic_stateful_rmw_probe
  ${PROJECT_NAME}
  std_msgs::std_msgs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_quic_stateful_rmw_probe
  rcutils
  rmw
  std_msgs
)

add_executable(fleetrmw_quic_mtls_probe
  src/quic_mtls_probe.cpp
)
target_compile_features(fleetrmw_quic_mtls_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_quic_mtls_probe
  ${PROJECT_NAME}_transport
)

add_executable(fleetrmw_quic_admission_probe
  src/quic_admission_probe.cpp
)
target_compile_features(fleetrmw_quic_admission_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_quic_admission_probe
  ${PROJECT_NAME}_transport
)

add_executable(fleetrmw_quic_qox_repair_probe
  src/quic_qox_repair_probe.cpp
)
target_compile_features(fleetrmw_quic_qox_repair_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_quic_qox_repair_probe
  ${PROJECT_NAME}_transport
)

add_executable(fleetrmw_quic_feedback_batch_probe
  src/quic_feedback_batch_probe.cpp
)
target_compile_features(fleetrmw_quic_feedback_batch_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_quic_feedback_batch_probe
  ${PROJECT_NAME}_transport
)

add_executable(fleetrmw_quic_application_outcome_probe
  src/quic_application_outcome_probe.cpp
)
target_compile_features(fleetrmw_quic_application_outcome_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_quic_application_outcome_probe
  ${PROJECT_NAME}_transport
)

add_executable(fleetrmw_quic_task_outcome_submit_probe
  src/quic_task_outcome_submit_probe.cpp
)
target_compile_features(fleetrmw_quic_task_outcome_submit_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_quic_task_outcome_submit_probe
  ${PROJECT_NAME}_transport
)

add_executable(fleetrmw_quic_durable_application_outcome_failover_probe
  src/quic_durable_application_outcome_failover_probe.cpp
)
target_compile_features(
  fleetrmw_quic_durable_application_outcome_failover_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_quic_durable_application_outcome_failover_probe
  ${PROJECT_NAME}_transport
)

add_executable(fleetrmw_quic_durable_failover_probe
  src/quic_durable_failover_probe.cpp
)
target_compile_features(fleetrmw_quic_durable_failover_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_quic_durable_failover_probe
  ${PROJECT_NAME}_transport
)

add_executable(fleetrmw_quic_native_path_observation_probe
  src/quic_native_path_observation_probe.cpp
)
target_compile_features(fleetrmw_quic_native_path_observation_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_quic_native_path_observation_probe
  ${PROJECT_NAME}_transport
)

add_executable(fleetrmw_quic_durable_admission_failover_probe
  src/quic_durable_admission_failover_probe.cpp
)
target_compile_features(fleetrmw_quic_durable_admission_failover_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_quic_durable_admission_failover_probe
  ${PROJECT_NAME}_transport
)

add_executable(fleetrmw_quic_inprocess_rmw_bidirectional_probe
  src/quic_inprocess_rmw_bidirectional_probe.cpp
)
target_compile_features(fleetrmw_quic_inprocess_rmw_bidirectional_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_quic_inprocess_rmw_bidirectional_probe
  ${PROJECT_NAME}
  std_msgs::std_msgs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_quic_inprocess_rmw_bidirectional_probe
  rcutils
  rmw
  std_msgs
)

add_executable(fleetrmw_quic_gateway_rmw_take_probe
  src/quic_gateway_rmw_take_probe.cpp
)
target_compile_features(fleetrmw_quic_gateway_rmw_take_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_quic_gateway_rmw_take_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_quic_gateway_rmw_take_probe
  rcutils
  rmw
)

add_executable(fleetrmw_geometry_twist_probe
  src/geometry_twist_probe.cpp
)

add_executable(fleetrmw_cpp_typesupport_probe
  src/cpp_typesupport_probe.cpp
)

add_executable(fleetrmw_loaned_message_probe
  src/loaned_message_probe.cpp
)
target_compile_features(fleetrmw_loaned_message_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_loaned_message_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_loaned_message_probe
  rcutils
  rmw
  rosidl_typesupport_c
  rosidl_typesupport_cpp
  std_msgs
)

add_executable(fleetrmw_allocation_probe
  src/allocation_probe.cpp
)
target_compile_features(fleetrmw_allocation_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_allocation_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_allocation_probe
  rcutils
  rmw
)

add_executable(fleetrmw_security_options_probe
  src/security_options_probe.cpp
)
target_compile_features(fleetrmw_security_options_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_security_options_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_security_options_probe
  rcutils
  rmw
)

add_executable(fleetrmw_security_policy_probe
  src/security_policy_probe.cpp
)
target_compile_features(fleetrmw_security_policy_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_security_policy_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_security_policy_probe
  rcutils
  rmw
)

add_executable(fleetrmw_sros2_permissions_probe
  src/sros2_permissions_probe.cpp
)
target_compile_features(fleetrmw_sros2_permissions_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_sros2_permissions_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_sros2_permissions_probe
  rcutils
  rmw
)

add_executable(fleetrmw_sros2_service_permissions_probe
  src/sros2_service_permissions_probe.cpp
)
target_compile_features(fleetrmw_sros2_service_permissions_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_sros2_service_permissions_probe
  ${PROJECT_NAME}
  std_srvs::std_srvs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_sros2_service_permissions_probe
  rcutils
  rmw
  std_srvs
)

add_executable(fleetrmw_sros2_governance_probe
  src/sros2_governance_probe.cpp
)
target_compile_features(fleetrmw_sros2_governance_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_sros2_governance_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_sros2_governance_probe
  rcutils
  rmw
)

add_executable(fleetrmw_sros2_identity_probe
  src/sros2_identity_probe.cpp
)
target_compile_features(fleetrmw_sros2_identity_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_sros2_identity_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_sros2_identity_probe
  rcutils
  rmw
)

add_executable(fleetrmw_udp_aead_probe
  src/udp_aead_probe.cpp
)
target_compile_features(fleetrmw_udp_aead_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_udp_aead_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_udp_aead_probe
  rcutils
  rmw
)

add_executable(fleetrmw_dynamic_message_probe
  src/dynamic_message_probe.cpp
)
target_compile_features(fleetrmw_dynamic_message_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_dynamic_message_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_dynamic_message_probe
  rcutils
  rmw
  rosidl_dynamic_typesupport
)

add_executable(fleetrmw_rclcpp_interprocess_probe
  src/rclcpp_interprocess_probe.cpp
)
target_compile_features(fleetrmw_rclcpp_interprocess_probe PRIVATE cxx_std_17)
ament_target_dependencies(fleetrmw_rclcpp_interprocess_probe
  geometry_msgs
  nav_msgs
  rclcpp
  std_srvs
)
target_compile_features(fleetrmw_cpp_typesupport_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_cpp_typesupport_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_cpp_typesupport_probe
  geometry_msgs
  rcutils
  rmw
  rosidl_typesupport_c
  rosidl_typesupport_cpp
  std_msgs
)

if(PkgConfig_FOUND)
  pkg_check_modules(NGTCP2_GNUTLS QUIET IMPORTED_TARGET
    libngtcp2
    libngtcp2_crypto_gnutls
  )
  pkg_check_modules(GNUTLS QUIET IMPORTED_TARGET gnutls)
  pkg_check_modules(NGHTTP3 QUIET IMPORTED_TARGET libnghttp3)
endif()
if(NGTCP2_GNUTLS_FOUND AND GNUTLS_FOUND AND NGHTTP3_FOUND)
  target_compile_definitions(${PROJECT_NAME}_transport PRIVATE
    FLEETQOX_HAS_INPROCESS_QUIC=1
  )
  target_link_libraries(${PROJECT_NAME}_transport PRIVATE
    PkgConfig::NGTCP2_GNUTLS
    PkgConfig::GNUTLS
    PkgConfig::NGHTTP3
  )
  add_executable(fleetrmw_quic_dependency_probe
    src/quic_dependency_probe.cpp
  )
  target_compile_features(fleetrmw_quic_dependency_probe PRIVATE cxx_std_17)
  target_link_libraries(fleetrmw_quic_dependency_probe
    PkgConfig::NGTCP2_GNUTLS
    PkgConfig::GNUTLS
    PkgConfig::NGHTTP3
  )
  list(APPEND FLEETRMW_OPTIONAL_TARGETS fleetrmw_quic_dependency_probe)
else()
  message(STATUS "ngtcp2/GnuTLS/nghttp3 development package not found; using subprocess-only QUIC")
endif()
target_compile_features(fleetrmw_geometry_twist_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_geometry_twist_probe
  ${PROJECT_NAME}
  geometry_msgs::geometry_msgs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_geometry_twist_probe
  geometry_msgs
  rcutils
  rmw
)

add_executable(fleetrmw_rcl_string_probe
  src/rcl_string_probe.cpp
)
target_compile_features(fleetrmw_rcl_string_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_rcl_string_probe
  std_msgs::std_msgs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_rcl_string_probe
  rcl
  rcutils
  std_msgs
)

add_executable(fleetrmw_rcl_graph_talker
  src/rcl_graph_talker.cpp
)
target_compile_features(fleetrmw_rcl_graph_talker PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_rcl_graph_talker
  std_msgs::std_msgs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_rcl_graph_talker
  rcl
  rcutils
  std_msgs
)

add_executable(fleetrmw_rcl_service_node
  src/rcl_service_node.cpp
)
target_compile_features(fleetrmw_rcl_service_node PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_rcl_service_node
  ${PROJECT_NAME}
  std_srvs::std_srvs__rosidl_typesupport_introspection_c
)
ament_target_dependencies(fleetrmw_rcl_service_node
  rcl
  rcutils
  std_srvs
)

add_executable(fleetrmw_wait_probe
  src/wait_probe.cpp
)
target_compile_features(fleetrmw_wait_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_wait_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_wait_probe
  rcutils
  rmw
)

add_executable(fleetrmw_graph_probe
  src/graph_probe.cpp
)
target_compile_features(fleetrmw_graph_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_graph_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_graph_probe
  rcutils
  rmw
)

add_executable(fleetrmw_interprocess_pubsub_probe
  src/interprocess_pubsub_probe.cpp
)
target_compile_features(fleetrmw_interprocess_pubsub_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_interprocess_pubsub_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_interprocess_pubsub_probe
  rcutils
  rmw
)

add_executable(fleetrmw_udp_pmtu_discovery_probe
  src/udp_pmtu_discovery_probe.cpp
)
target_compile_features(fleetrmw_udp_pmtu_discovery_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_udp_pmtu_discovery_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_udp_pmtu_discovery_probe
  rcutils
  rmw
)

add_executable(fleetrmw_udp_router_probe
  src/udp_router_probe.cpp
)
target_compile_features(fleetrmw_udp_router_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_udp_router_probe
  ${PROJECT_NAME}_transport
)

add_executable(fleetrmw_generic_serialized_relay_probe
  src/generic_serialized_relay_probe.cpp
)
target_compile_features(fleetrmw_generic_serialized_relay_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_generic_serialized_relay_probe
  ${CMAKE_DL_LIBS}
)
ament_target_dependencies(fleetrmw_generic_serialized_relay_probe
  rclcpp
)

add_executable(fleetrmw_remote_graph_probe
  src/remote_graph_probe.cpp
)
target_compile_features(fleetrmw_remote_graph_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_remote_graph_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_remote_graph_probe
  rcutils
  rmw
)

add_executable(fleetrmw_remote_graph_lease_probe
  src/remote_graph_lease_probe.cpp
)
target_compile_features(fleetrmw_remote_graph_lease_probe PRIVATE cxx_std_17)
target_link_libraries(fleetrmw_remote_graph_lease_probe
  ${PROJECT_NAME}
)
ament_target_dependencies(fleetrmw_remote_graph_lease_probe
  rcutils
  rmw
)

if(BUILD_TESTING)
  add_test(
    NAME fleetrmw_transport_loop_repair_smoke
    COMMAND fleetrmw_transport_loop_smoke
      --robot-count 2 --samples-per-robot 3 --skip-first --json
  )
  add_test(
    NAME fleetrmw_action_frame_roundtrip_smoke
    COMMAND fleetrmw_action_frame_probe
  )
  add_test(
    NAME fleetrmw_take_sequence_ordering_smoke
    COMMAND fleetrmw_take_sequence_probe
  )
  add_test(
    NAME fleetrmw_wait_for_all_acked_smoke
    COMMAND fleetrmw_wait_for_all_acked_probe
  )
  add_test(
    NAME fleetrmw_remote_graph_lease_identity_smoke
    COMMAND fleetrmw_remote_graph_lease_probe
  )
  add_test(
    NAME fleetrmw_service_qos_gid_identity_smoke
    COMMAND fleetrmw_service_qos_probe
  )
  add_test(
    NAME fleetrmw_graph_guard_wait_smoke
    COMMAND fleetrmw_wait_probe
  )
  add_test(
    NAME fleetrmw_domain_isolation_smoke
    COMMAND fleetrmw_domain_isolation_probe
  )
  add_test(
    NAME fleetrmw_message_lost_sequence_gap_smoke
    COMMAND fleetrmw_message_lost_event_probe
  )
  add_test(
    NAME fleetrmw_automatic_liveliness_idle_renewal_smoke
    COMMAND fleetrmw_automatic_liveliness_probe
  )
  add_test(
    NAME fleetrmw_callback_teardown_quiescence_smoke
    COMMAND fleetrmw_callback_teardown_probe
  )
  set_tests_properties(
    fleetrmw_transport_loop_repair_smoke
    fleetrmw_action_frame_roundtrip_smoke
    fleetrmw_take_sequence_ordering_smoke
    fleetrmw_wait_for_all_acked_smoke
    fleetrmw_remote_graph_lease_identity_smoke
    fleetrmw_service_qos_gid_identity_smoke
    fleetrmw_graph_guard_wait_smoke
    fleetrmw_domain_isolation_smoke
    fleetrmw_message_lost_sequence_gap_smoke
    fleetrmw_automatic_liveliness_idle_renewal_smoke
    fleetrmw_callback_teardown_quiescence_smoke
    PROPERTIES TIMEOUT 15
  )
  set_tests_properties(
    fleetrmw_wait_for_all_acked_smoke
    PROPERTIES ENVIRONMENT
      "FLEETQOX_RMW_TEST_ACK_DELAY_SUBSCRIPTION_SUFFIX=-2;FLEETQOX_RMW_TEST_ACK_DELAY_MS=400"
  )
  set_tests_properties(
    fleetrmw_message_lost_sequence_gap_smoke
    PROPERTIES ENVIRONMENT
      "FLEETQOX_RMW_DROP_SOURCE_SEQUENCES=3;FLEETQOX_RMW_MESSAGE_LOST_GAP_GRACE_MS=100"
  )
  if(TARGET fleetrmw_quic_dependency_probe)
    add_test(
      NAME fleetrmw_quic_dependency_link_smoke
      COMMAND fleetrmw_quic_dependency_probe
    )
    set_tests_properties(
      fleetrmw_quic_dependency_link_smoke
      PROPERTIES TIMEOUT 15
    )
  endif()
endif()

install(
  DIRECTORY include/
  DESTINATION include
)
install(
  FILES capabilities.json
  DESTINATION share/${PROJECT_NAME}
)
install(
  TARGETS
    ${PROJECT_NAME}
    ${PROJECT_NAME}_transport
    fleetrmw_transport_loop_smoke
    fleetrmw_frame_probe
    fleetrmw_action_frame_probe
    fleetrmw_action_router_probe
    fleetrmw_lifecycle_probe
    fleetrmw_serialized_pubsub_probe
    fleetrmw_qos_probe
    fleetrmw_qos_best_available_probe
    fleetrmw_take_sequence_probe
    fleetrmw_qos_event_probe
    fleetrmw_matched_event_probe
    fleetrmw_callback_teardown_probe
    fleetrmw_remote_event_probe
    fleetrmw_qos_incompatible_event_probe
    fleetrmw_qos_deadline_incompatible_event_probe
    fleetrmw_qos_liveliness_incompatible_event_probe
    fleetrmw_type_incompatible_event_probe
    fleetrmw_message_lost_event_probe
    fleetrmw_message_lost_interprocess_probe
    fleetrmw_liveliness_event_probe
    fleetrmw_automatic_liveliness_probe
    fleetrmw_remote_manual_liveliness_probe
    fleetrmw_remote_liveliness_multi_endpoint_probe
    fleetrmw_liveliness_scale_probe
    fleetrmw_remote_liveliness_scale_probe
    fleetrmw_liveliness_default_lease_probe
    fleetrmw_remote_deadline_event_probe
    fleetrmw_content_filter_probe
    fleetrmw_content_filter_sql_probe
    fleetrmw_content_filter_typed_probe
    fleetrmw_service_qos_probe
    fleetrmw_domain_isolation_probe
    fleetrmw_udp_pmtu_discovery_probe
    fleetrmw_service_error_probe
    fleetrmw_service_resource_limit_probe
    fleetrmw_service_client_isolation_probe
    fleetrmw_service_repair_admission_probe
    fleetrmw_service_priority_probe
    fleetrmw_service_weighted_fairness_probe
    fleetrmw_service_deadline_scheduler_probe
    fleetrmw_service_durable_replay_probe
    fleetrmw_reliability_probe
    fleetrmw_wait_for_all_acked_probe
    fleetrmw_remote_wait_for_all_acked_probe
    fleetrmw_reliable_interprocess_probe
    fleetrmw_typed_pubsub_probe
    fleetrmw_std_msgs_string_probe
    fleetrmw_quic_gateway_publish_probe
    fleetrmw_quic_gateway_burst_publish_probe
    fleetrmw_quic_gateway_take_probe
    fleetrmw_quic_inprocess_session_probe
    fleetrmw_quic_inprocess_concurrent_stream_probe
    fleetrmw_quic_stateful_gateway_probe
    fleetrmw_quic_stateful_rmw_probe
    fleetrmw_quic_mtls_probe
    fleetrmw_quic_admission_probe
    fleetrmw_quic_qox_repair_probe
    fleetrmw_quic_feedback_batch_probe
    fleetrmw_quic_application_outcome_probe
    fleetrmw_quic_task_outcome_submit_probe
    fleetrmw_quic_durable_application_outcome_failover_probe
    fleetrmw_quic_durable_failover_probe
    fleetrmw_quic_native_path_observation_probe
    fleetrmw_quic_durable_admission_failover_probe
    fleetrmw_quic_inprocess_rmw_bidirectional_probe
    fleetrmw_quic_gateway_rmw_take_probe
    fleetrmw_geometry_twist_probe
    fleetrmw_cpp_typesupport_probe
    fleetrmw_loaned_message_probe
    fleetrmw_allocation_probe
    fleetrmw_security_options_probe
    fleetrmw_security_policy_probe
    fleetrmw_sros2_permissions_probe
    fleetrmw_sros2_service_permissions_probe
    fleetrmw_sros2_governance_probe
    fleetrmw_sros2_identity_probe
    fleetrmw_udp_aead_probe
    fleetrmw_dynamic_message_probe
    fleetrmw_rclcpp_interprocess_probe
    fleetrmw_rcl_string_probe
    fleetrmw_rcl_graph_talker
    fleetrmw_rcl_service_node
    fleetrmw_wait_probe
    fleetrmw_graph_probe
    fleetrmw_interprocess_pubsub_probe
    fleetrmw_udp_router_probe
    fleetrmw_generic_serialized_relay_probe
    fleetrmw_remote_graph_probe
    fleetrmw_remote_graph_lease_probe
    ${FLEETRMW_OPTIONAL_TARGETS}
  EXPORT export_${PROJECT_NAME}
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION lib/${PROJECT_NAME}
)

ament_export_include_directories(include)
ament_export_libraries(${PROJECT_NAME} ${PROJECT_NAME}_transport)
ament_export_targets(export_${PROJECT_NAME})
register_rmw_implementation(
  "c:rosidl_typesupport_introspection_c"
  "cpp:rosidl_typesupport_introspection_cpp"
)
ament_package()
