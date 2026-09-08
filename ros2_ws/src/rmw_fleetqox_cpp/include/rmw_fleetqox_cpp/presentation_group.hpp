// FleetQoX PRESENTATION extension (see qos_extensions.hpp), GROUP access
// scope: begin/end-coherent-changes for a "Publisher" that spans multiple
// topics. Real DDS PRESENTATION at GROUP scope is a property of the DDS
// Publisher/Subscriber entity (which can own many DataWriters/DataReaders
// across different topics); rmw has no equivalent entity above a single
// publisher/subscription, and no rmw_publisher_begin_coherent_changes-style
// API at all -- this is exactly the gap FleetQoxExtendedQosPayload's
// presentation_group_id exists to work around, by grouping publishers and
// subscriptions that share one id regardless of which topic each is on.
//
// These two functions are the FleetQoX-specific control surface an
// application uses to mark a coherent set's boundary: every rmw_publish
// call made (on any publisher sharing this one's presentation_group_id,
// including publishers on other topics) between begin and end is buffered
// rather than sent immediately, then flushed together as one atomic burst
// once end is called. A subscription with matching presentation_group_id
// and presentation_coherent_access=true buffers arriving members of that
// burst and only makes any of them visible to rmw_take once every member
// has arrived -- a subscriber can never observe one topic's new value from
// a coherent set without the others being available too.
//
// Since rmw has no standard hook for this, an application must link
// directly against rmw_fleetqox_cpp (not just dlopen it indirectly via
// RMW_IMPLEMENTATION, the way every other rmw call reaches this library)
// to call these two functions -- see fleetrmw_presentation_probe's
// CMakeLists.txt entry for the target_link_libraries(${PROJECT_NAME})
// precedent (also used by fleetrmw_cpp_typesupport_probe). Because
// rmw_fleetqox_cpp is a SHARED library, that direct link and rclcpp's own
// RMW_IMPLEMENTATION-driven dlopen of the same .so resolve to the same
// loaded instance -- there is only one copy of the library's global state,
// so calls made here observe and affect the exact publishers/subscriptions
// created through the normal rclcpp path.
#ifndef RMW_FLEETQOX_CPP__PRESENTATION_GROUP_HPP_
#define RMW_FLEETQOX_CPP__PRESENTATION_GROUP_HPP_

#include "rmw/rmw.h"
#include "rmw/types.h"
#include "rmw/visibility_control.h"

#ifdef __cplusplus
extern "C"
{
#endif

// Begins buffering for every publisher sharing `publisher`'s
// presentation_group_id (from the FleetQoxExtendedQosPayload it was
// created with). Returns RMW_RET_UNSUPPORTED if `publisher` has no
// presentation_group_id configured.
rmw_ret_t rmw_fleetqox_cpp_begin_coherent_changes(const rmw_publisher_t * publisher);

// Flushes every frame buffered since the matching begin_coherent_changes
// call as one atomic, shared coherent_set_id burst, then resumes immediate
// sends for that group. A no-op (returns RMW_RET_OK) if nothing was
// buffered, e.g. end called without a prior begin.
rmw_ret_t rmw_fleetqox_cpp_end_coherent_changes(const rmw_publisher_t * publisher);

#ifdef __cplusplus
}  // extern "C"
#endif

#endif  // RMW_FLEETQOX_CPP__PRESENTATION_GROUP_HPP_
