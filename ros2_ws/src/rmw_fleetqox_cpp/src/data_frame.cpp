#include "rmw_fleetqox_cpp/data_frame.hpp"

#include <algorithm>
#include <cctype>
#include <limits>
#include <stdexcept>
#include <sstream>

namespace rmw_fleetqox_cpp
{
namespace
{

std::string json_escape(const std::string & value)
{
  std::ostringstream out;
  for (const char c : value) {
    if (c == '\\' || c == '"') {
      out << '\\' << c;
    } else if (c == '\n') {
      out << "\\n";
    } else {
      out << c;
    }
  }
  return out.str();
}

std::optional<std::size_t> json_value_start(const std::string & payload, const std::string & key)
{
  const std::string needle = "\"" + key + "\"";
  const auto start = payload.find(needle);
  if (start == std::string::npos) {
    return std::nullopt;
  }
  std::size_t i = start + needle.size();
  while (i < payload.size() && std::isspace(static_cast<unsigned char>(payload[i]))) {
    ++i;
  }
  if (i >= payload.size() || payload[i] != ':') {
    return std::nullopt;
  }
  ++i;
  while (i < payload.size() && std::isspace(static_cast<unsigned char>(payload[i]))) {
    ++i;
  }
  return i;
}

std::optional<std::string> json_string_value(const std::string & payload, const std::string & key)
{
  const auto value_start = json_value_start(payload, key);
  if (!value_start || *value_start >= payload.size() || payload[*value_start] != '"') {
    return std::nullopt;
  }
  std::string value;
  bool escaped = false;
  for (std::size_t i = *value_start + 1; i < payload.size(); ++i) {
    const char c = payload[i];
    if (escaped) {
      value.push_back(c == 'n' ? '\n' : c);
      escaped = false;
      continue;
    }
    if (c == '\\') {
      escaped = true;
      continue;
    }
    if (c == '"') {
      return value;
    }
    value.push_back(c);
  }
  return std::nullopt;
}

std::optional<std::uint64_t> json_uint_value(const std::string & payload, const std::string & key)
{
  const auto value_start = json_value_start(payload, key);
  if (!value_start) {
    return std::nullopt;
  }
  std::size_t i = *value_start;
  while (i < payload.size() && std::isspace(static_cast<unsigned char>(payload[i]))) {
    ++i;
  }
  std::size_t end = i;
  while (end < payload.size() && std::isdigit(static_cast<unsigned char>(payload[end]))) {
    ++end;
  }
  if (end == i) {
    return std::nullopt;
  }
  return static_cast<std::uint64_t>(std::stoull(payload.substr(i, end - i)));
}

std::optional<double> json_double_value(const std::string & payload, const std::string & key)
{
  const auto value_start = json_value_start(payload, key);
  if (!value_start) {
    return std::nullopt;
  }
  std::size_t end = *value_start;
  while (end < payload.size()) {
    const char c = payload[end];
    if (!(std::isdigit(static_cast<unsigned char>(c)) || c == '-' || c == '+' ||
      c == '.' || c == 'e' || c == 'E'))
    {
      break;
    }
    ++end;
  }
  if (end == *value_start) {
    return std::nullopt;
  }
  try {
    return std::stod(payload.substr(*value_start, end - *value_start));
  } catch (const std::exception &) {
    return std::nullopt;
  }
}

bool json_bool_value(const std::string & payload, const std::string & key)
{
  const auto value_start = json_value_start(payload, key);
  return value_start && payload.compare(*value_start, 4, "true") == 0;
}

void encode_graph_qos(std::ostringstream & out, const GraphQosProfile & qos)
{
  out << "\"qos\":{";
  out << "\"history\":" << qos.history << ",";
  out << "\"depth\":" << qos.depth << ",";
  out << "\"reliability\":" << qos.reliability << ",";
  out << "\"durability\":" << qos.durability << ",";
  out << "\"deadline_sec\":" << qos.deadline_sec << ",";
  out << "\"deadline_nsec\":" << qos.deadline_nsec << ",";
  out << "\"lifespan_sec\":" << qos.lifespan_sec << ",";
  out << "\"lifespan_nsec\":" << qos.lifespan_nsec << ",";
  out << "\"liveliness\":" << qos.liveliness << ",";
  out << "\"liveliness_lease_duration_sec\":" << qos.liveliness_lease_duration_sec << ",";
  out << "\"liveliness_lease_duration_nsec\":" << qos.liveliness_lease_duration_nsec << ",";
  out << "\"avoid_ros_namespace_conventions\":" << qos.avoid_ros_namespace_conventions;
  out << "}";
}

GraphQosProfile decode_graph_qos(const std::string & payload)
{
  GraphQosProfile qos{};
  qos.history = json_uint_value(payload, "history").value_or(0);
  qos.depth = json_uint_value(payload, "depth").value_or(0);
  qos.reliability = json_uint_value(payload, "reliability").value_or(0);
  qos.durability = json_uint_value(payload, "durability").value_or(0);
  qos.deadline_sec = json_uint_value(payload, "deadline_sec").value_or(0);
  qos.deadline_nsec = json_uint_value(payload, "deadline_nsec").value_or(0);
  qos.lifespan_sec = json_uint_value(payload, "lifespan_sec").value_or(0);
  qos.lifespan_nsec = json_uint_value(payload, "lifespan_nsec").value_or(0);
  qos.liveliness = json_uint_value(payload, "liveliness").value_or(0);
  qos.liveliness_lease_duration_sec =
    json_uint_value(payload, "liveliness_lease_duration_sec").value_or(0);
  qos.liveliness_lease_duration_nsec =
    json_uint_value(payload, "liveliness_lease_duration_nsec").value_or(0);
  qos.avoid_ros_namespace_conventions =
    json_uint_value(payload, "avoid_ros_namespace_conventions").value_or(0);
  return qos;
}

bool json_has_string_value(
  const std::string & payload,
  const std::string & key,
  const std::string & expected)
{
  std::size_t search_start = 0;
  const std::string needle = "\"" + key + "\"";
  while (search_start < payload.size()) {
    const auto found = payload.find(needle, search_start);
    if (found == std::string::npos) {
      return false;
    }
    const auto value_start = json_value_start(payload.substr(found), key);
    if (value_start) {
      const auto absolute_value_start = found + *value_start;
      if (absolute_value_start < payload.size() && payload[absolute_value_start] == '"') {
        std::string value;
        bool escaped = false;
        for (std::size_t i = absolute_value_start + 1; i < payload.size(); ++i) {
          const char c = payload[i];
          if (escaped) {
            value.push_back(c == 'n' ? '\n' : c);
            escaped = false;
            continue;
          }
          if (c == '\\') {
            escaped = true;
            continue;
          }
          if (c == '"') {
            if (value == expected) {
              return true;
            }
            break;
          }
          value.push_back(c);
        }
      }
    }
    search_start = found + needle.size();
  }
  return false;
}

std::optional<std::string> json_object_value(const std::string & payload, const std::string & key)
{
  const auto value_start = json_value_start(payload, key);
  if (!value_start || *value_start >= payload.size() || payload[*value_start] != '{') {
    return std::nullopt;
  }
  bool in_string = false;
  bool escaped = false;
  int depth = 0;
  for (std::size_t i = *value_start; i < payload.size(); ++i) {
    const char c = payload[i];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (c == '\\') {
      escaped = in_string;
      continue;
    }
    if (c == '"') {
      in_string = !in_string;
      continue;
    }
    if (in_string) {
      continue;
    }
    if (c == '{') {
      ++depth;
      continue;
    }
    if (c == '}') {
      --depth;
      if (depth == 0) {
        return payload.substr(*value_start, i - *value_start + 1);
      }
    }
  }
  return std::nullopt;
}

std::string hex_encode(const std::vector<std::uint8_t> & bytes)
{
  static constexpr char kHex[] = "0123456789abcdef";
  std::string encoded;
  encoded.reserve(bytes.size() * 2);
  for (const std::uint8_t byte : bytes) {
    encoded.push_back(kHex[(byte >> 4) & 0x0F]);
    encoded.push_back(kHex[byte & 0x0F]);
  }
  return encoded;
}

std::optional<std::vector<std::uint8_t>> hex_decode(const std::string & encoded)
{
  if (encoded.size() % 2 != 0) {
    return std::nullopt;
  }
  std::vector<std::uint8_t> decoded;
  decoded.reserve(encoded.size() / 2);
  auto nibble = [](const char c) -> int {
      if (c >= '0' && c <= '9') {
        return c - '0';
      }
      if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
      }
      if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
      }
      return -1;
    };
  for (std::size_t i = 0; i < encoded.size(); i += 2) {
    const int high = nibble(encoded[i]);
    const int low = nibble(encoded[i + 1]);
    if (high < 0 || low < 0) {
      return std::nullopt;
    }
    decoded.push_back(static_cast<std::uint8_t>((high << 4) | low));
  }
  return decoded;
}

// Base64 instead of hex roughly halves the on-wire blowup for a large
// serialized_payload (4/3x vs 2x the raw byte count) -- for a payload that
// needs application-level UDP fragmentation to survive netem rate limiting
// (see FLEETQOX_RMW_LOSS_RESILIENT_FRAGMENT_CHUNK_BYTES), that difference
// directly determines how many concurrent large-payload flows fit through
// a bandwidth-constrained link before fragment loss sets in.
std::string base64_encode(const std::vector<std::uint8_t> & bytes)
{
  static constexpr char kAlphabet[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string encoded;
  encoded.reserve(((bytes.size() + 2) / 3) * 4);
  std::size_t i = 0;
  while (i + 3 <= bytes.size()) {
    const std::uint32_t chunk =
      (static_cast<std::uint32_t>(bytes[i]) << 16) |
      (static_cast<std::uint32_t>(bytes[i + 1]) << 8) |
      static_cast<std::uint32_t>(bytes[i + 2]);
    encoded.push_back(kAlphabet[(chunk >> 18) & 0x3F]);
    encoded.push_back(kAlphabet[(chunk >> 12) & 0x3F]);
    encoded.push_back(kAlphabet[(chunk >> 6) & 0x3F]);
    encoded.push_back(kAlphabet[chunk & 0x3F]);
    i += 3;
  }
  const std::size_t remaining = bytes.size() - i;
  if (remaining == 1) {
    const std::uint32_t chunk = static_cast<std::uint32_t>(bytes[i]) << 16;
    encoded.push_back(kAlphabet[(chunk >> 18) & 0x3F]);
    encoded.push_back(kAlphabet[(chunk >> 12) & 0x3F]);
    encoded.push_back('=');
    encoded.push_back('=');
  } else if (remaining == 2) {
    const std::uint32_t chunk =
      (static_cast<std::uint32_t>(bytes[i]) << 16) |
      (static_cast<std::uint32_t>(bytes[i + 1]) << 8);
    encoded.push_back(kAlphabet[(chunk >> 18) & 0x3F]);
    encoded.push_back(kAlphabet[(chunk >> 12) & 0x3F]);
    encoded.push_back(kAlphabet[(chunk >> 6) & 0x3F]);
    encoded.push_back('=');
  }
  return encoded;
}

std::optional<std::vector<std::uint8_t>> base64_decode(const std::string & encoded)
{
  if (encoded.size() % 4 != 0) {
    return std::nullopt;
  }
  auto sextet = [](const char c) -> int {
      if (c >= 'A' && c <= 'Z') {
        return c - 'A';
      }
      if (c >= 'a' && c <= 'z') {
        return c - 'a' + 26;
      }
      if (c >= '0' && c <= '9') {
        return c - '0' + 52;
      }
      if (c == '+') {
        return 62;
      }
      if (c == '/') {
        return 63;
      }
      return -1;
    };
  std::vector<std::uint8_t> decoded;
  decoded.reserve((encoded.size() / 4) * 3);
  for (std::size_t i = 0; i < encoded.size(); i += 4) {
    const bool pad2 = encoded[i + 2] == '=';
    const bool pad3 = encoded[i + 3] == '=';
    const int s0 = sextet(encoded[i]);
    const int s1 = sextet(encoded[i + 1]);
    const int s2 = pad2 ? 0 : sextet(encoded[i + 2]);
    const int s3 = pad3 ? 0 : sextet(encoded[i + 3]);
    if (s0 < 0 || s1 < 0 || (!pad2 && s2 < 0) || (!pad3 && s3 < 0) ||
      (pad2 && !pad3))
    {
      return std::nullopt;
    }
    const std::uint32_t chunk =
      (static_cast<std::uint32_t>(s0) << 18) |
      (static_cast<std::uint32_t>(s1) << 12) |
      (static_cast<std::uint32_t>(s2) << 6) |
      static_cast<std::uint32_t>(s3);
    decoded.push_back(static_cast<std::uint8_t>((chunk >> 16) & 0xFF));
    if (!pad2) {
      decoded.push_back(static_cast<std::uint8_t>((chunk >> 8) & 0xFF));
    }
    if (!pad3) {
      decoded.push_back(static_cast<std::uint8_t>(chunk & 0xFF));
    }
  }
  return decoded;
}

template<typename T>
std::optional<T> first_present(const std::optional<T> & first, const std::optional<T> & second)
{
  return first ? first : second;
}

std::string strip_padding(const std::string & payload)
{
  std::string stripped = payload;
  while (!stripped.empty() && stripped.back() == ' ') {
    stripped.pop_back();
  }
  return stripped;
}

std::vector<std::uint64_t> uint_values_after(const std::string & payload, const std::string & key)
{
  const auto start = payload.find(key);
  if (start == std::string::npos) {
    return {};
  }
  std::vector<std::uint64_t> values;
  std::string current;
  for (std::size_t i = start + key.size(); i < payload.size(); ++i) {
    const char c = payload[i];
    if (std::isdigit(static_cast<unsigned char>(c))) {
      current.push_back(c);
      continue;
    }
    if (!current.empty()) {
      values.push_back(static_cast<std::uint64_t>(std::stoull(current)));
      current.clear();
    }
    if (c == '}' && payload.find("\"state\"", i) != std::string::npos) {
      break;
    }
  }
  if (!current.empty()) {
    values.push_back(static_cast<std::uint64_t>(std::stoull(current)));
  }
  return values;
}

std::vector<std::string> json_string_array_value(const std::string & payload, const std::string & key)
{
  const auto value_start = json_value_start(payload, key);
  if (!value_start || *value_start >= payload.size() || payload[*value_start] != '[') {
    return {};
  }
  std::vector<std::string> values;
  bool in_string = false;
  bool escaped = false;
  std::string current;
  for (std::size_t i = *value_start + 1; i < payload.size(); ++i) {
    const char c = payload[i];
    if (!in_string) {
      if (c == ']') {
        return values;
      }
      if (c == '"') {
        in_string = true;
        current.clear();
      }
      continue;
    }
    if (escaped) {
      current.push_back(c == 'n' ? '\n' : c);
      escaped = false;
      continue;
    }
    if (c == '\\') {
      escaped = true;
      continue;
    }
    if (c == '"') {
      values.push_back(current);
      in_string = false;
      continue;
    }
    current.push_back(c);
  }
  return {};
}

std::vector<std::pair<std::uint64_t, std::uint64_t>> missing_ranges_from_ack_nack(
  const std::string & payload)
{
  const auto values = uint_values_after(payload, "missing_sequence_ranges");
  std::vector<std::pair<std::uint64_t, std::uint64_t>> ranges;
  for (std::size_t i = 0; i + 1 < values.size(); i += 2) {
    ranges.emplace_back(values[i], values[i + 1]);
  }
  return ranges;
}

std::vector<std::pair<std::uint64_t, std::uint64_t>> json_uint_pair_array_value(
  const std::string & payload,
  const std::string & key)
{
  const auto values = uint_values_after(payload, key);
  std::vector<std::pair<std::uint64_t, std::uint64_t>> ranges;
  ranges.reserve(values.size() / 2);
  for (std::size_t i = 0; i + 1 < values.size(); i += 2) {
    ranges.emplace_back(values[i], values[i + 1]);
  }
  return ranges;
}

}  // namespace

std::string stream_key(const DataFrame & frame)
{
  return frame.robot_id + "|" + frame.topic + "|" + frame.publisher_id;
}

std::string encode_data_frame(const DataFrame & frame)
{
  std::ostringstream out;
  out << kDataFrameMagic;
  out << "{\"schema_version\":\"" << kDataFrameSchemaVersion << "\",";
  out << "\"kind\":\"sidecar_packet_frame\",";
  out << "\"domain_id\":" << frame.domain_id << ",";
  if (!frame.type_name.empty()) {
    out << "\"type_name\":\"" << json_escape(frame.type_name) << "\",";
  }
  out << "\"route\":{\"robot_id\":\"" << json_escape(frame.robot_id) << "\",";
  out << "\"topic\":\"" << json_escape(frame.topic) << "\"";
  if (!frame.flow_class.empty()) {
    out << ",\"flow_class\":\"" << json_escape(frame.flow_class) << "\"";
  }
  out << "},";
  out << "\"sample_envelope\":{";
  out << "\"robot_id\":\"" << json_escape(frame.robot_id) << "\",";
  out << "\"topic\":\"" << json_escape(frame.topic) << "\",";
  out << "\"publisher_id\":\"" << json_escape(frame.publisher_id) << "\",";
  out << "\"source_sequence_number\":" << frame.source_sequence_number << ",";
  out << "\"source_timestamp_ns\":" << frame.source_timestamp_ns;
  out << "}";
  if (!frame.serialized_payload.empty()) {
    out << ",\"serialized_payload\":{";
    out << "\"encoding\":\"base64\",";
    out << "\"size\":" << frame.serialized_payload.size() << ",";
    out << "\"data\":\"" << base64_encode(frame.serialized_payload) << "\"}";
  }
  if (frame.deadline_ms > 0.0) {
    out << ",\"delivery\":{\"deadline_ms\":" << frame.deadline_ms << "}";
  }
  if (frame.qoe_debt > 0.0 || frame.task_criticality > 0.0) {
    out << ",\"qox\":{\"qoe_debt\":" << frame.qoe_debt << ",";
    out << "\"task_criticality\":" << frame.task_criticality << "}";
  }
  if (frame.age_ms > 0.0) {
    out << ",\"timing\":{\"age_ms\":" << frame.age_ms << "}";
  }
  if (frame.repair_requested || frame.prior_repair_attempts > 0) {
    out << ",\"repair\":{\"requested\":" <<
      (frame.repair_requested ? "true" : "false") << ",";
    out << "\"prior_attempts\":" << frame.prior_repair_attempts << "}";
  }
  out << "}";
  return out.str();
}

std::optional<DataFrame> decode_data_frame(const std::string & payload)
{
  const std::string stripped = strip_padding(payload);
  const std::string magic = kDataFrameMagic;
  if (stripped.rfind(magic, 0) != 0) {
    return std::nullopt;
  }
  const std::string body = stripped.substr(magic.size());
  if (!json_has_string_value(body, "schema_version", kDataFrameSchemaVersion)) {
    return std::nullopt;
  }
  const std::string route = json_object_value(body, "route").value_or(body);
  const std::string sample_envelope = json_object_value(body, "sample_envelope").value_or(body);
  const std::string source_metadata = json_object_value(body, "source_metadata").value_or("");
  const std::string serialized_payload = json_object_value(body, "serialized_payload").value_or("");
  const std::string delivery = json_object_value(body, "delivery").value_or("");
  const std::string qox = json_object_value(body, "qox").value_or("");
  const std::string timing = json_object_value(body, "timing").value_or("");
  const std::string repair = json_object_value(body, "repair").value_or("");
  const auto robot_id = first_present(
    json_string_value(route, "robot_id"),
    json_string_value(sample_envelope, "robot_id"));
  const auto topic = first_present(
    json_string_value(route, "topic"),
    json_string_value(sample_envelope, "topic"));
  const auto publisher_id = first_present(
    json_string_value(sample_envelope, "publisher_id"),
    json_string_value(source_metadata, "publisher_id"));
  const auto sequence = first_present(
    json_uint_value(sample_envelope, "source_sequence_number"),
    json_uint_value(source_metadata, "source_sequence_number"));
  const auto timestamp = first_present(
    json_uint_value(sample_envelope, "source_timestamp_ns"),
    json_uint_value(source_metadata, "source_timestamp_ns"));
  if (!robot_id || !topic || !publisher_id || !sequence || !timestamp) {
    return std::nullopt;
  }
  std::vector<std::uint8_t> payload_bytes;
  if (!serialized_payload.empty()) {
    const auto encoding = json_string_value(serialized_payload, "encoding");
    const auto encoded_data = json_string_value(serialized_payload, "data");
    if (!encoding || (*encoding != "hex" && *encoding != "base64") || !encoded_data) {
      return std::nullopt;
    }
    const auto decoded_payload = *encoding == "base64" ?
      base64_decode(*encoded_data) : hex_decode(*encoded_data);
    if (!decoded_payload) {
      return std::nullopt;
    }
    payload_bytes = *decoded_payload;
  }
  return DataFrame{
    *robot_id,
    *topic,
    *publisher_id,
    *sequence,
    static_cast<std::int64_t>(*timestamp),
    payload_bytes,
    json_uint_value(body, "domain_id").value_or(0),
    json_string_value(body, "type_name").value_or(""),
    json_string_value(route, "flow_class").value_or(""),
    json_double_value(delivery, "deadline_ms").value_or(0.0),
    json_double_value(timing, "age_ms").value_or(0.0),
    json_double_value(qox, "qoe_debt").value_or(0.0),
    json_double_value(qox, "task_criticality").value_or(0.0),
    json_bool_value(repair, "requested"),
    json_uint_value(repair, "prior_attempts").value_or(0)};
}

std::string encode_route_advertisement(const RouteAdvertisement & advertisement)
{
  std::ostringstream out;
  out << kDataFrameMagic;
  out << "{\"schema_version\":\"" << kRouteAdvertisementSchemaVersion << "\",";
  out << "\"kind\":\"route_advertisement\",";
  out << "\"domain_id\":" << advertisement.domain_id << ",";
  out << "\"endpoint_id\":\"" << json_escape(advertisement.endpoint_id) << "\",";
  out << "\"role\":\"" << json_escape(advertisement.role) << "\",";
  out << "\"topic\":\"" << json_escape(advertisement.topic) << "\",";
  out << "\"type_name\":\"" << json_escape(advertisement.type_name) << "\",";
  out << "\"lease_ms\":" << advertisement.lease_ms;
  out << "}";
  return out.str();
}

std::optional<RouteAdvertisement> decode_route_advertisement(const std::string & payload)
{
  const std::string stripped = strip_padding(payload);
  const std::string magic = kDataFrameMagic;
  if (stripped.rfind(magic, 0) != 0) {
    return std::nullopt;
  }
  const std::string body = stripped.substr(magic.size());
  if (!json_has_string_value(body, "schema_version", kRouteAdvertisementSchemaVersion)) {
    return std::nullopt;
  }
  const auto topic = json_string_value(body, "topic");
  const auto role = json_string_value(body, "role");
  if (!topic || !role) {
    return std::nullopt;
  }
  RouteAdvertisement advertisement{};
  advertisement.endpoint_id = json_string_value(body, "endpoint_id").value_or("");
  advertisement.role = *role;
  advertisement.topic = *topic;
  advertisement.type_name = json_string_value(body, "type_name").value_or("");
  advertisement.lease_ms = json_uint_value(body, "lease_ms").value_or(0);
  advertisement.domain_id = json_uint_value(body, "domain_id").value_or(0);
  return advertisement;
}

std::string encode_graph_advertisement(const GraphAdvertisement & advertisement)
{
  std::ostringstream out;
  out << kDataFrameMagic;
  out << "{\"schema_version\":\"" << kGraphAdvertisementSchemaVersion << "\",";
  out << "\"kind\":\"graph_advertisement\",";
  out << "\"domain_id\":" << advertisement.domain_id << ",";
  out << "\"endpoint_id\":\"" << json_escape(advertisement.endpoint_id) << "\",";
  out << "\"action\":\"" << json_escape(advertisement.action) << "\",";
  out << "\"entity_kind\":\"" << json_escape(advertisement.entity_kind) << "\",";
  out << "\"node_name\":\"" << json_escape(advertisement.node_name) << "\",";
  out << "\"node_namespace\":\"" << json_escape(advertisement.node_namespace) << "\",";
  out << "\"topic\":\"" << json_escape(advertisement.topic) << "\",";
  out << "\"type_name\":\"" << json_escape(advertisement.type_name) << "\",";
  out << "\"endpoint_gid\":\"" << json_escape(advertisement.endpoint_gid) << "\",";
  encode_graph_qos(out, advertisement.qos);
  out << ",";
  out << "\"lease_ms\":" << advertisement.lease_ms;
  out << "}";
  return out.str();
}

std::optional<GraphAdvertisement> decode_graph_advertisement(const std::string & payload)
{
  const std::string stripped = strip_padding(payload);
  const std::string magic = kDataFrameMagic;
  if (stripped.rfind(magic, 0) != 0) {
    return std::nullopt;
  }
  const std::string body = stripped.substr(magic.size());
  if (!json_has_string_value(body, "schema_version", kGraphAdvertisementSchemaVersion)) {
    return std::nullopt;
  }
  const auto action = json_string_value(body, "action");
  const auto entity_kind = json_string_value(body, "entity_kind");
  if (!action || !entity_kind) {
    return std::nullopt;
  }
  GraphAdvertisement advertisement{};
  advertisement.endpoint_id = json_string_value(body, "endpoint_id").value_or("");
  advertisement.action = *action;
  advertisement.entity_kind = *entity_kind;
  advertisement.node_name = json_string_value(body, "node_name").value_or("");
  advertisement.node_namespace = json_string_value(body, "node_namespace").value_or("");
  advertisement.topic = json_string_value(body, "topic").value_or("");
  advertisement.type_name = json_string_value(body, "type_name").value_or("");
  advertisement.endpoint_gid = json_string_value(body, "endpoint_gid").value_or("");
  const std::string qos = json_object_value(body, "qos").value_or("");
  if (!qos.empty()) {
    advertisement.qos = decode_graph_qos(qos);
  }
  advertisement.lease_ms = json_uint_value(body, "lease_ms").value_or(0);
  advertisement.domain_id = json_uint_value(body, "domain_id").value_or(0);
  return advertisement;
}

std::string encode_service_frame(const ServiceFrame & frame)
{
  std::ostringstream out;
  out << kDataFrameMagic;
  out << "{\"schema_version\":\"" << kServiceFrameSchemaVersion << "\",";
  out << "\"kind\":\"service_frame\",";
  out << "\"domain_id\":" << frame.domain_id << ",";
  out << "\"client_priority\":" << frame.client_priority << ",";
  out << "\"client_weight\":" << frame.client_weight << ",";
  out << "\"request_deadline_ns\":" << frame.request_deadline_ns << ",";
  out << "\"role\":\"" << json_escape(frame.role) << "\",";
  out << "\"service_name\":\"" << json_escape(frame.service_name) << "\",";
  out << "\"type_name\":\"" << json_escape(frame.type_name) << "\",";
  out << "\"client_endpoint_id\":\"" << json_escape(frame.client_endpoint_id) << "\",";
  out << "\"service_endpoint_id\":\"" << json_escape(frame.service_endpoint_id) << "\",";
  out << "\"sequence_id\":" << frame.sequence_id << ",";
  out << "\"source_timestamp_ns\":" << frame.source_timestamp_ns << ",";
  out << "\"lifespan_ns\":" << frame.lifespan_ns;
  if (!frame.serialized_payload.empty()) {
    out << ",\"serialized_payload\":{";
    out << "\"encoding\":\"base64\",";
    out << "\"size\":" << frame.serialized_payload.size() << ",";
    out << "\"data\":\"" << base64_encode(frame.serialized_payload) << "\"}";
  }
  out << "}";
  return out.str();
}

std::optional<ServiceFrame> decode_service_frame(const std::string & payload)
{
  const std::string stripped = strip_padding(payload);
  const std::string magic = kDataFrameMagic;
  if (stripped.rfind(magic, 0) != 0) {
    return std::nullopt;
  }
  const std::string body = stripped.substr(magic.size());
  if (!json_has_string_value(body, "schema_version", kServiceFrameSchemaVersion)) {
    return std::nullopt;
  }
  const auto role = json_string_value(body, "role");
  const auto service_name = json_string_value(body, "service_name");
  const auto type_name = json_string_value(body, "type_name");
  const auto client_endpoint_id = json_string_value(body, "client_endpoint_id");
  const auto sequence_id = json_uint_value(body, "sequence_id");
  const auto source_timestamp_ns = json_uint_value(body, "source_timestamp_ns");
  const auto lifespan_ns = json_uint_value(body, "lifespan_ns").value_or(0);
  if (!role || !service_name || !type_name || !client_endpoint_id || !sequence_id ||
    !source_timestamp_ns)
  {
    return std::nullopt;
  }
  const std::string serialized_payload = json_object_value(body, "serialized_payload").value_or("");
  std::vector<std::uint8_t> payload_bytes;
  if (!serialized_payload.empty()) {
    const auto encoding = json_string_value(serialized_payload, "encoding");
    const auto encoded_data = json_string_value(serialized_payload, "data");
    if (!encoding || (*encoding != "hex" && *encoding != "base64") || !encoded_data) {
      return std::nullopt;
    }
    const auto decoded_payload = *encoding == "base64" ?
      base64_decode(*encoded_data) : hex_decode(*encoded_data);
    if (!decoded_payload) {
      return std::nullopt;
    }
    payload_bytes = *decoded_payload;
  }
  return ServiceFrame{
    *role,
    *service_name,
    *type_name,
    *client_endpoint_id,
    json_string_value(body, "service_endpoint_id").value_or(""),
    static_cast<std::int64_t>(*sequence_id),
    static_cast<std::int64_t>(*source_timestamp_ns),
    static_cast<std::int64_t>(lifespan_ns),
    payload_bytes,
    json_uint_value(body, "domain_id").value_or(0),
    json_uint_value(body, "client_priority").value_or(0),
    0,
    std::max<std::uint64_t>(1, json_uint_value(body, "client_weight").value_or(1)),
    json_uint_value(body, "request_deadline_ns").value_or(0)};
}

bool service_frame_expired(const ServiceFrame & frame, std::int64_t now_ns)
{
  if (frame.lifespan_ns <= 0 || frame.source_timestamp_ns <= 0) {
    return false;
  }
  return now_ns > frame.source_timestamp_ns && now_ns - frame.source_timestamp_ns > frame.lifespan_ns;
}

std::string encode_action_frame(const ActionFrame & frame)
{
  std::ostringstream out;
  out << kDataFrameMagic;
  out << "{\"schema_version\":\"" << kActionFrameSchemaVersion << "\",";
  out << "\"kind\":\"action_frame\",";
  out << "\"domain_id\":" << frame.domain_id << ",";
  out << "\"role\":\"" << json_escape(frame.role) << "\",";
  out << "\"action_name\":\"" << json_escape(frame.action_name) << "\",";
  out << "\"type_name\":\"" << json_escape(frame.type_name) << "\",";
  out << "\"endpoint_id\":\"" << json_escape(frame.endpoint_id) << "\",";
  out << "\"goal_id\":\"" << json_escape(frame.goal_id) << "\",";
  out << "\"sequence_id\":" << frame.sequence_id << ",";
  out << "\"source_timestamp_ns\":" << frame.source_timestamp_ns << ",";
  out << "\"lifespan_ns\":" << frame.lifespan_ns;
  if (!frame.serialized_payload.empty()) {
    out << ",\"serialized_payload\":{";
    out << "\"encoding\":\"base64\",";
    out << "\"size\":" << frame.serialized_payload.size() << ",";
    out << "\"data\":\"" << base64_encode(frame.serialized_payload) << "\"}";
  }
  out << "}";
  return out.str();
}

std::optional<ActionFrame> decode_action_frame(const std::string & payload)
{
  const std::string stripped = strip_padding(payload);
  const std::string magic = kDataFrameMagic;
  if (stripped.rfind(magic, 0) != 0) {
    return std::nullopt;
  }
  const std::string body = stripped.substr(magic.size());
  if (!json_has_string_value(body, "schema_version", kActionFrameSchemaVersion)) {
    return std::nullopt;
  }
  const auto role = json_string_value(body, "role");
  const auto action_name = json_string_value(body, "action_name");
  const auto type_name = json_string_value(body, "type_name");
  const auto endpoint_id = json_string_value(body, "endpoint_id");
  const auto goal_id = json_string_value(body, "goal_id");
  const auto sequence_id = json_uint_value(body, "sequence_id");
  const auto source_timestamp_ns = json_uint_value(body, "source_timestamp_ns");
  const auto lifespan_ns = json_uint_value(body, "lifespan_ns").value_or(0);
  if (!role || !action_name || !type_name || !endpoint_id || !goal_id || !sequence_id ||
    !source_timestamp_ns)
  {
    return std::nullopt;
  }
  const std::string serialized_payload = json_object_value(body, "serialized_payload").value_or("");
  std::vector<std::uint8_t> payload_bytes;
  if (!serialized_payload.empty()) {
    const auto encoding = json_string_value(serialized_payload, "encoding");
    const auto encoded_data = json_string_value(serialized_payload, "data");
    if (!encoding || (*encoding != "hex" && *encoding != "base64") || !encoded_data) {
      return std::nullopt;
    }
    const auto decoded_payload = *encoding == "base64" ?
      base64_decode(*encoded_data) : hex_decode(*encoded_data);
    if (!decoded_payload) {
      return std::nullopt;
    }
    payload_bytes = *decoded_payload;
  }
  return ActionFrame{
    *role,
    *action_name,
    *type_name,
    *endpoint_id,
    *goal_id,
    static_cast<std::int64_t>(*sequence_id),
    static_cast<std::int64_t>(*source_timestamp_ns),
    static_cast<std::int64_t>(lifespan_ns),
    payload_bytes,
    json_uint_value(body, "domain_id").value_or(0)};
}

bool action_frame_expired(const ActionFrame & frame, std::int64_t now_ns)
{
  if (frame.lifespan_ns <= 0 || frame.source_timestamp_ns <= 0) {
    return false;
  }
  return now_ns > frame.source_timestamp_ns && now_ns - frame.source_timestamp_ns > frame.lifespan_ns;
}

AckNackFeedback observe_frame(SequenceState & state, const DataFrame & frame)
{
  AckNackFeedback feedback;
  const auto sequence = frame.source_sequence_number;
  const bool duplicate = state.observed_sequences.find(sequence) != state.observed_sequences.end();
  const bool out_of_order = state.initialized && sequence < state.highest_observed_sequence;
  state.observed_sequences.insert(sequence);
  state.highest_observed_sequence = std::max(state.highest_observed_sequence, sequence);
  if (!state.initialized) {
    state.initialized = true;
  }
  while (state.observed_sequences.find(state.highest_contiguous_sequence + 1) != state.observed_sequences.end()) {
    ++state.highest_contiguous_sequence;
  }
  feedback = feedback_from_sequence_state(state);
  feedback.duplicate = duplicate;
  feedback.out_of_order = out_of_order;
  return feedback;
}

AckNackFeedback establish_reception_sequence_baseline(SequenceState & state)
{
  state.reception_sequence_baseline_initialized = true;
  state.cumulative_ack_floor = state.highest_observed_sequence;
  state.highest_contiguous_sequence = state.highest_observed_sequence;
  state.pending_missing_ranges.clear();
  return feedback_from_sequence_state(state);
}

AckNackFeedback feedback_from_sequence_state(const SequenceState & state)
{
  AckNackFeedback feedback;
  if (!state.observed_sequences.empty()) {
    feedback.lowest_observed_sequence = *state.observed_sequences.begin();
    if (state.cumulative_ack_floor > 0) {
      feedback.lowest_observed_sequence = std::max(
        feedback.lowest_observed_sequence,
        state.cumulative_ack_floor);
    }
  }
  feedback.highest_contiguous_sequence = state.highest_contiguous_sequence;
  feedback.highest_observed_sequence = state.highest_observed_sequence;
  if (state.highest_contiguous_sequence >= state.highest_observed_sequence ||
    state.highest_contiguous_sequence == std::numeric_limits<std::uint64_t>::max())
  {
    return feedback;
  }

  std::uint64_t next_missing_candidate = state.highest_contiguous_sequence + 1;
  bool exhausted_sequence_space = false;
  for (auto observed = state.observed_sequences.lower_bound(next_missing_candidate);
    observed != state.observed_sequences.end() &&
    *observed <= state.highest_observed_sequence;
    ++observed)
  {
    if (*observed < next_missing_candidate) {
      continue;
    }
    if (*observed > next_missing_candidate) {
      feedback.missing_sequence_ranges.emplace_back(
        next_missing_candidate, *observed - 1);
    }
    if (*observed == std::numeric_limits<std::uint64_t>::max()) {
      exhausted_sequence_space = true;
      break;
    }
    next_missing_candidate = *observed + 1;
  }
  if (!exhausted_sequence_space &&
    next_missing_candidate <= state.highest_observed_sequence)
  {
    feedback.missing_sequence_ranges.emplace_back(
      next_missing_candidate, state.highest_observed_sequence);
  }
  return feedback;
}

std::string encode_ack_nack(
  const DataFrame & frame,
  const AckNackFeedback & feedback,
  const std::string & subscriber_id)
{
  std::ostringstream out;
  out << "{\"schema_version\":\"" << kAckNackSchemaVersion << "\",";
  out << "\"kind\":\"source_sequence_ack_nack\",";
  out << "\"domain_id\":" << frame.domain_id << ",";
  out << "\"robot_id\":\"" << json_escape(frame.robot_id) << "\",";
  out << "\"source_topic\":\"" << json_escape(frame.topic) << "\",";
  if (!subscriber_id.empty()) {
    out << "\"subscriber_id\":\"" << json_escape(subscriber_id) << "\",";
  }
  out << "\"stream_key\":[\"source_stream\",\"" << json_escape(frame.robot_id) << "\",";
  out << "\"" << json_escape(frame.topic) << "\",\"" << json_escape(frame.publisher_id) << "\"],";
  out << "\"ack\":{\"source_sequence_number\":" << frame.source_sequence_number << ",";
  out << "\"source_timestamp_ns\":" << frame.source_timestamp_ns << "},";
  out << "\"nack\":{\"missing_sequence_ranges\":[";
  for (std::size_t i = 0; i < feedback.missing_sequence_ranges.size(); ++i) {
    if (i > 0) {
      out << ",";
    }
    out << "[" << feedback.missing_sequence_ranges[i].first << ",";
    out << feedback.missing_sequence_ranges[i].second << "]";
  }
  out << "]},";
  out << "\"state\":{\"lowest_observed_sequence\":" << feedback.lowest_observed_sequence << ",";
  out << "\"highest_contiguous_sequence\":" << feedback.highest_contiguous_sequence << ",";
  out << "\"highest_observed_sequence\":" << feedback.highest_observed_sequence << ",";
  out << "\"duplicate\":" << (feedback.duplicate ? "true" : "false") << ",";
  out << "\"out_of_order\":" << (feedback.out_of_order ? "true" : "false") << "}}";
  return out.str();
}

std::optional<AckNackFrame> decode_ack_nack(const std::string & payload)
{
  const std::string stripped = strip_padding(payload);
  const std::string body = stripped.rfind(kDataFrameMagic, 0) == 0 ?
    stripped.substr(std::string(kDataFrameMagic).size()) : stripped;
  if (!json_has_string_value(body, "schema_version", kAckNackSchemaVersion)) {
    return std::nullopt;
  }
  const auto robot_id = json_string_value(body, "robot_id");
  const auto topic = json_string_value(body, "source_topic");
  const std::string ack = json_object_value(body, "ack").value_or("");
  const std::string state = json_object_value(body, "state").value_or("");
  const auto sequence = json_uint_value(ack, "source_sequence_number");
  const auto timestamp = json_uint_value(ack, "source_timestamp_ns");
  const std::vector<std::string> stream_key = json_string_array_value(body, "stream_key");
  if (!robot_id || !topic || !sequence || !timestamp || stream_key.size() < 4) {
    return std::nullopt;
  }

  AckNackFrame frame{};
  frame.robot_id = *robot_id;
  frame.topic = *topic;
  frame.publisher_id = stream_key[3];
  frame.subscriber_id = json_string_value(body, "subscriber_id").value_or("");
  frame.ack_sequence_number = *sequence;
  frame.source_timestamp_ns = static_cast<std::int64_t>(*timestamp);
  frame.missing_sequence_ranges = missing_ranges_from_ack_nack(body);
  frame.lowest_observed_sequence =
    json_uint_value(state, "lowest_observed_sequence").value_or(0);
  frame.highest_contiguous_sequence =
    json_uint_value(state, "highest_contiguous_sequence").value_or(0);
  frame.highest_observed_sequence =
    json_uint_value(state, "highest_observed_sequence").value_or(0);
  frame.duplicate = body.find("\"duplicate\":true") != std::string::npos;
  frame.out_of_order = body.find("\"out_of_order\":true") != std::string::npos;
  frame.domain_id = json_uint_value(body, "domain_id").value_or(0);
  return frame;
}

bool ack_nack_acknowledges_sequence(
  const AckNackFrame & frame,
  std::uint64_t sequence)
{
  if (sequence == frame.ack_sequence_number) {
    return true;
  }
  return frame.lowest_observed_sequence > 0 &&
         frame.lowest_observed_sequence <= frame.highest_contiguous_sequence &&
         sequence >= frame.lowest_observed_sequence &&
         sequence <= frame.highest_contiguous_sequence;
}

std::string encode_unrecoverable_loss_notice(const UnrecoverableLossNotice & notice)
{
  std::ostringstream out;
  out << "{\"schema_version\":\"" << kUnrecoverableLossNoticeSchemaVersion << "\",";
  out << "\"kind\":\"source_sequence_unrecoverable\",";
  out << "\"domain_id\":" << notice.domain_id << ",";
  out << "\"robot_id\":\"" << json_escape(notice.robot_id) << "\",";
  out << "\"source_topic\":\"" << json_escape(notice.topic) << "\",";
  out << "\"publisher_id\":\"" << json_escape(notice.publisher_id) << "\",";
  out << "\"subscriber_id\":\"" << json_escape(notice.subscriber_id) << "\",";
  out << "\"source_timestamp_ns\":" << notice.source_timestamp_ns << ",";
  out << "\"lost_sequence_ranges\":[";
  for (std::size_t i = 0; i < notice.lost_sequence_ranges.size(); ++i) {
    if (i > 0) {
      out << ",";
    }
    out << "[" << notice.lost_sequence_ranges[i].first << ",";
    out << notice.lost_sequence_ranges[i].second << "]";
  }
  out << "]}";
  return out.str();
}

std::optional<UnrecoverableLossNotice> decode_unrecoverable_loss_notice(
  const std::string & payload)
{
  const std::string stripped = strip_padding(payload);
  const std::string body = stripped.rfind(kDataFrameMagic, 0) == 0 ?
    stripped.substr(std::string(kDataFrameMagic).size()) : stripped;
  if (!json_has_string_value(
      body, "schema_version", kUnrecoverableLossNoticeSchemaVersion) ||
    !json_has_string_value(body, "kind", "source_sequence_unrecoverable"))
  {
    return std::nullopt;
  }
  const auto robot_id = json_string_value(body, "robot_id");
  const auto topic = json_string_value(body, "source_topic");
  const auto publisher_id = json_string_value(body, "publisher_id");
  const auto subscriber_id = json_string_value(body, "subscriber_id");
  const auto timestamp = json_uint_value(body, "source_timestamp_ns");
  const auto ranges = json_uint_pair_array_value(body, "lost_sequence_ranges");
  if (!robot_id || !topic || !publisher_id || !subscriber_id || !timestamp ||
    subscriber_id->empty() || ranges.empty())
  {
    return std::nullopt;
  }
  for (const auto & range : ranges) {
    if (range.first == 0 || range.first > range.second) {
      return std::nullopt;
    }
  }
  return UnrecoverableLossNotice{
    *robot_id,
    *topic,
    *publisher_id,
    *subscriber_id,
    static_cast<std::int64_t>(*timestamp),
    ranges,
    json_uint_value(body, "domain_id").value_or(0)};
}

std::vector<std::uint64_t> missing_sequences_from_ack_nack(const std::string & payload)
{
  std::vector<std::uint64_t> sequences;
  for (const auto & range : missing_ranges_from_ack_nack(payload)) {
    for (std::uint64_t sequence = range.first; sequence <= range.second; ++sequence) {
      sequences.push_back(sequence);
    }
  }
  return sequences;
}

bool ack_nack_reports_out_of_order(const std::string & payload)
{
  return payload.find("\"out_of_order\":true") != std::string::npos;
}

}  // namespace rmw_fleetqox_cpp
