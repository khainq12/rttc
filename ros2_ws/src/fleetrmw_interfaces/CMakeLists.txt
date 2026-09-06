cmake_minimum_required(VERSION 3.8)
project(fleetrmw_interfaces)

find_package(ament_cmake REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(sensor_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/SampleIdentity.msg"
  "msg/ProjectionQuality.msg"
  "msg/QualifiedOdometry.msg"
  "msg/QualifiedLaserScan.msg"
  "msg/WStringSample.msg"
  "msg/KeyedInstanceSample.idl"
  "srv/FleetShape.srv"
  "action/NavigateFleet.action"
  "action/DispatchFleetTask.action"
  DEPENDENCIES builtin_interfaces geometry_msgs nav_msgs sensor_msgs
)

add_executable(fleetrmw_bounded_shape_service_probe
  src/bounded_shape_service_probe.cpp
)
target_compile_features(fleetrmw_bounded_shape_service_probe PRIVATE cxx_std_17)
ament_target_dependencies(fleetrmw_bounded_shape_service_probe
  rclcpp
)
rosidl_get_typesupport_target(
  fleetrmw_interfaces_cpp_typesupport
  ${PROJECT_NAME}
  rosidl_typesupport_cpp
)
target_link_libraries(
  fleetrmw_bounded_shape_service_probe
  "${fleetrmw_interfaces_cpp_typesupport}"
)

install(
  TARGETS fleetrmw_bounded_shape_service_probe
  RUNTIME DESTINATION lib/${PROJECT_NAME}
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
