// LD_PRELOAD shim: forces Fast DDS's own internal logging to Warning
// verbosity with a stdout consumer, since Info-level EPROSIMA_LOG_INFO
// call sites are compiled OUT of this prebuilt Release package (no
// FASTDDS_ENFORCE_LOG_INFO define) -- confirmed by reading
// fastdds/dds/log/Log.hpp directly. Warning/Error level logging IS
// compiled in and controllable at runtime via the public Log API.
// Diagnostic only -- does not change any middleware behavior/QoS,
// only where its own pre-existing log messages are printed.
#include <fastdds/dds/log/Log.hpp>
#include <fastdds/dds/log/StdoutConsumer.hpp>
#include <memory>

using namespace eprosima::fastdds::dds;

__attribute__((constructor))
static void force_fastdds_log_verbosity() {
    Log::SetVerbosity(Log::Kind::Warning);
    Log::RegisterConsumer(std::make_unique<StdoutConsumer>());
}
