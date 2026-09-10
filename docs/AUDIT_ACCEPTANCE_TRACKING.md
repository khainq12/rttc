# Theo dõi tiến độ nghiệm thu theo báo cáo hành chính

Tài liệu này theo dõi tiến độ khắc phục 06 nhóm tiêu chí nghiệm thu nêu
trong `FleetRMW_FleetQoX_Bao_cao_hanh_chinh.docx` (kiểm tra ngày
07/09/2026, tại commit `c5da4417fe9e7f6a99498fac49c4e1ab98aaa5e6` trên
nhánh `main`). Cập nhật file này mỗi khi một nhóm có tiến triển, để không
phải dò lại toàn bộ lịch sử hội thoại.

Trạng thái tại thời điểm kiểm tra gốc (07/09/2026): **0/6 nhóm đạt**.

## Bảng tổng hợp hiện tại

| # | Nhóm tiêu chí | Trạng thái gốc | Trạng thái hiện tại | Bằng chứng |
|---|---|---|---|---|
| 1 | An toàn bộ nhớ / sanitizer đúng topology (2 tiến trình + netem + 32 KiB) | Chưa đạt | ✅ **Đã đóng** | `scripts/run_heap_soak_asan_probe.py`, kết quả `results_rmw_socket/heap_soak_asan_probe_summary.json`: 40/40 round, 2 tiến trình OS riêng biệt (container publisher/subscriber khác nhau), netem thật (`loss random 15%`), payload 32768 byte, ASan+UBSan 0 lỗi, teardown sạch mọi round. Gắn với task B0 (root-cause crash relay teardown). |
| 2 | Độ tin cậy 32 KiB ở 16/32 robot (delivery + publisher ACK completion) | Chưa đạt | ✅ **Đã đóng** | Root-cause bug `wait_for_all_acked` graph-membership pruning (publisher thoát sớm do ack giả) + 2 lỗi phụ (fragment-NACK backoff quá ngắn, relay executor đơn luồng nghẽn ở quy mô 64 route). Grid 9/9 (8/16/32 robot × seed 7/13/29) pass với tiêu chí đầy đủ (`publisher.ack_wait_complete AND relay.downstream_ack_wait_complete AND min_topic_delivery_ratio==1.0`). Commit `b9790d3`. |
| 3 | QUIC/PKI và HA/fencing (online rotation, live revocation, ma trận phân vùng không split-brain, failover/failback đa host, durable state) | Đạt phần lớn (thiếu đa host) | ✅ **Đã đóng** | HA multi-host (Raft + etcd/PostgreSQL, failover + failback) làm ở phiên trước (2 VM KVM thật). PKI cert/CA rotation multi-host (CRL revocation + CA rotation thật, không phải chỉ thêm CA) làm phiên này: `scripts/run_multihost_kvm_udp_peer_auth_crl_reload_probe.py`, 4/4 round pass. Commit `65b7100`. Lưu ý nhỏ: "ma trận phân vùng" mới test một số kịch bản tiêu biểu, chưa phải toàn bộ tổ hợp. |
| 4 | Ngữ nghĩa RMW (full QoS event, full DDS content-filter dialect, deep preallocation) | Đạt một phần | ✅ **Đã đóng** | Dynamic message, nhiều QoS extension (liveliness, deadline, lifespan, destination_order, ownership, partition, presentation) đã xong. **Task #42 (content-filter dialect) đã đóng**: thêm `LIKE ... ESCAPE`, 3/3 pass (`run_rmw_docker_content_filter_sql_probe.py`), và đã ra quyết định phạm vi chính thức — subset hiện tại là ranh giới cuối cùng. **Task #43 (deep_preallocation_claim) đã đóng phần lớn hơn dự kiến**: thay vì redesign wire-format nhị phân (rủi ro cao, ban đầu định hỏi ý kiến), tìm được cách an toàn hơn — verify `snprintf("%.6g",...)` giống hệt định dạng double của `ostringstream` (400k+ giá trị test), rồi build JSON frame body thẳng vào buffer bền vững (`frame_json_scratch`) thay vì `ostringstream` mới mỗi lần, và pool hoá entry trong retransmit ledger (`g_retired_retransmit_entries`) — không đổi 1 byte nào trên wire, không ảnh hưởng 187 probe khác. Verify bằng A/B rebuild (git stash) xác nhận 2 lỗi flaky có sẵn (`rmw_wait_for_all_acked_probe`, `remote_wait_for_all_acked_probe`) tái hiện y hệt ở cả code cũ và mới → không phải regression. `deep_preallocation_claim` vẫn giữ `false` (đúng): phần message deserialization và ledger hash-map node allocation vẫn chưa pool hoá, và binary wire format vẫn là ranh giới scope có chủ đích, không phải việc treo. |
| 5 | Nav2, Open-RMF, đa host, HIL | Đạt một phần | ❌ **Chưa làm** | Nav2 đã có bằng chứng chạy thực tế (từ trước). Open-RMF chưa phải full upstream stack; chưa có bằng chứng đa host/HIL cho workload tự hành. |
| 6 | Đối sánh mô phỏng (ns-3/OMNeT++), soak dài hạn, bằng chứng phát hành qua CI | Đạt một phần | 🟡 **Đang làm** | Xem mục "Nhóm 6" bên dưới — đã khảo sát hiện trạng, chưa code. |

**Tóm lại: 4/6 nhóm đã đóng (1, 2, 3, 4 — nhóm 4 vẫn còn vài ranh giới
scope có chủ đích, xem bảng, không phải việc treo). Nhóm 6 đang bắt đầu
(đã có CI workflow đầu tiên). Nhóm 5 chưa làm.**

## Việc tiếp theo (theo task list nội bộ)

- **Task #42** (Nhóm 4) — ✅ **Đã đóng**: thêm `LIKE ... ESCAPE` vào
  `ContentFilterExpressionParser`/`content_filter_like()` trong
  `rmw_pubsub.cpp`, test mới trong `content_filter_sql_probe.cpp`
  (`escape_scenario` + `multi_char_escape_rejected`), 3/3 pass qua
  `scripts/run_rmw_docker_content_filter_sql_probe.py`. Quyết định phạm vi
  chính thức đã ghi vào `capabilities.json` (`content_filters` narrative)
  và `docs/STATUS_AND_ROADMAP.md`: subset hiện có là ranh giới **cuối
  cùng**, các hàm DDS SQL tuỳ ý và mở rộng vendor bị loại vĩnh viễn (lý do:
  không có nhu cầu thực tế + mở rộng grammar cho input được parse trên mỗi
  sample nhận về sẽ tăng bề mặt tấn công).
- **Task #43** (Nhóm 4) — ✅ **Đã đóng** (người dùng chọn "làm full redesign"
  khi được hỏi, nhưng tìm được cách đạt cùng mục tiêu với rủi ro thấp hơn
  nhiều so với phương án ban đầu): `encode_data_frame_append` build JSON
  frame body thẳng vào `FleetQoxPublisherData::frame_json_scratch` (buffer
  bền vững, tái dùng capacity) thay vì `std::ostringstream` mới + trả về
  `std::string` cấp phát mới mỗi lần publish; double field dùng
  `snprintf("%.6g",...)` thay cho `operator<<`, đã verify giống hệt qua
  400k+ giá trị test (kể cả edge case) nên byte-on-wire không đổi.
  Retransmit ledger giờ tái dùng entry đã retired (acked/evicted) qua pool
  giới hạn theo publisher (`g_retired_retransmit_entries`, cap 8) thay vì
  luôn cấp phát mới; `reset_pooled_retransmit_entry` set lại rõ ràng từng
  field (kể cả field không có tham số constructor như
  `acknowledgments_observed`, các cờ fragment-repair) để không rò rỉ state
  từ message trước — quan trọng vì đây đúng là subsystem ACK từng gây bug
  B1 hồi trước. Verify: `docker_deep_preallocation_probe` mở rộng (JSON
  scratch capacity ổn định + pool ghi nhận hit nhiều hơn miss qua kịch bản
  reliable QoS 8 lần publish/ACK), cộng A/B rebuild qua `git stash` xác
  nhận 2 lỗi flaky có sẵn không phải do thay đổi này, cộng sweep hồi quy
  qua content-filter SQL/OWNERSHIP/DESTINATION_ORDER/QUIC qoe_debt probe —
  tất cả pass giống kết quả trước khi sửa. `deep_preallocation_claim` vẫn
  đúng là `false`: message deserialization và ledger hash-map node
  allocation chưa pool hoá, và binary wire format vẫn là ranh giới scope
  có chủ đích (đổi sẽ ảnh hưởng ~187 probe khác để đổi lấy lợi ích nhỏ hơn
  so với phần đã đóng).
- **Nhóm 5**: đưa FleetRMW vào path ROS 2 thật của RMF components (không
  chỉ gọi service tương thích kiểu); chạy kịch bản đa host cho Open-RMF;
  cân nhắc HIL nếu phạm vi sản xuất yêu cầu.
- **Nhóm 6** (đang làm, bắt đầu 10/09/2026) — khảo sát hiện trạng 3 phần:
  1. **Đối sánh mô phỏng ns-3/OMNeT++**: phát hiện quan trọng khác với ghi
     chú "Trạng thái gốc" ở trên — phía **ns-3 đã có sẵn** kịch bản wireless
     thật (`run_ns3_docker_wifi_mobility_matrix.py`: 802.11g + mobility,
     `run_ns3_docker_wifi_roaming_matrix.py`: chuyển vùng dual-AP), không
     phải chỉ P2P. Cái thiếu thực sự là phía **OMNeT++ hoàn toàn chưa có
     mô hình wireless nào** — `external/omnetpp/FleetQoxTraceReplay.ned`
     chỉ dùng `DatarateChannel` (kênh point-to-point cấu hình
     datarate/delay/per cố định), không có NIC 802.11/mobility gì cả, nên
     `run_omnetpp_docker_parity.py` (đối sánh ns-3 vs OMNeT++) chỉ đối
     sánh được ở kịch bản P2P. Muốn đóng mục này cần viết mới mạng OMNeT++
     dùng INET wireless (Ieee80211 NIC + access point + mobility model)
     khớp tham số với 1 kịch bản wifi ns-3 đã có, rồi chạy đối sánh như
     `omnetpp_docker_parity` hiện tại — đây là phần nặng nhất trong Nhóm 6
     (viết .ned mới + build lại Docker image omnetpp-inet). TSN/mesh: chưa
     thấy có claim nào cho TSN/mesh trong `capabilities.json`, nên có thể
     không cần làm phần đó (cần xác nhận lại với báo cáo hành chính gốc
     nếu có yêu cầu rõ).

     **Cập nhật (10/09/2026) — đã viết code, CHƯA build/chạy (chờ xác nhận
     người dùng trước khi chạy docker build vì tốn thời gian/tài nguyên):**
     - `external/omnetpp/Dockerfile`: bật thêm feature `Ieee80211` và
       `Mobility` trong INET (trước đó chỉ có Ipv4/Loopback/Ppp/Queueing/Udp
       — không có wireless).
     - `external/omnetpp/FleetQoxWifiReplay.ned`: network mới — 1
       `AccessPoint` (`Ieee80211ScalarRadioMedium`) + `WirelessHost` cho
       từng endpoint duy nhất trong trace (`controller`, `fleetRouter`,
       `operatorUi`, `robot[numRobots]`), dùng `LinearMobility` (tốc độ +
       hướng hằng số, xen kẽ +x/-x giống `ConstantVelocityMobilityModel`
       của ns-3) và mgmt kiểu Simplified (association cố định, không phụ
       thuộc thời điểm beacon/probe trong cửa sổ replay ngắn). Lưu ý về
       phạm vi: thứ tự gán vị trí node theo index không khớp thứ tự phát
       hiện endpoint của ns-3 (thứ tự đó chỉ là hệ quả của cách quét CSV,
       không phải một phần định nghĩa kịch bản) — mật độ trạm
       (`stationSpacing`) và độ lớn tốc độ (`mobilitySpeed`) thì khớp.
     - `external/omnetpp/omnetpp.ini`: thêm `[Config MatchedWifi]`.
     - `scripts/run_omnetpp_docker_wifi_parity.py`: runner đối sánh mới,
       chạy cả 3 kịch bản đã có sẵn trong
       `run_ns3_docker_wifi_mobility_matrix.py` (`stationary_near`,
       `mobile_moderate`, `mobile_edge`) trên cả hai simulator với cùng
       trace/seed/policy/robot-count.
     - **Ngưỡng so sánh (`DEFAULT_THRESHOLDS`) trong script này là giả
       thuyết ban đầu, CHƯA được kiểm chứng bằng dữ liệu chạy thật** — nới
       rộng hơn ngưỡng của phần P2P có chủ ý (vì hai chồng MAC/PHY không
       dây độc lập nhau dự kiến lệch nhau nhiều hơn hai chồng point-to-point
       có dây), cần chạy thật rồi điều chỉnh dựa trên số liệu thực tế, không
       được nới lỏng ngầm nếu chạy fail.
     - Việc còn lại trước khi có thể đóng mục này: `docker build` image
       omnetpp-inet (image chưa tồn tại ở máy này), chạy thử
       `run_omnetpp_docker_wifi_parity.py`, sửa lỗi biên dịch/NED nếu có
       (chưa có công cụ OMNeT++ ở máy host để kiểm tra cú pháp NED tĩnh
       trước, nên nhiều khả năng cần vài vòng sửa lỗi build), rồi mới đánh
       giá lại ngưỡng so sánh dựa trên kết quả thật.
  2. **Soak dài hạn**: đã có `run_heap_soak_asan_probe.py`/
     `run_heap_soak_fleet_asan_probe.py` (lặp nhiều "round" ngắn, không
     phải 1 lần chạy liên tục dài) và
     `run_rmw_docker_quic_gateway_async_burst_soak.py`. Cần: 1 kịch bản
     chạy liên tục thật sự dài (vài giờ trở lên, không phải lặp round
     ngắn), theo dõi leak bộ nhớ/degrade hiệu năng theo thời gian. Cần
     người dùng xác nhận thời lượng mong muốn trước khi chạy thật (tốn
     tài nguyên/thời gian) — cần chạy nền (background).
  3. **Bằng chứng qua CI** — ✅ **Đã có workflow đầu tiên** (commit
     `8baa2b9`): `.github/workflows/ci.yml` build image
     `rmw-netem:jazzy` từ `external/rmw-netem/Dockerfile` có sẵn (dùng GH
     Actions layer cache), rồi chạy 2 probe nhẹ làm smoke gate —
     `deep_preallocation_probe` và `content_filter_sql_probe` (chọn 2 cái
     này vì không cần netem/multi-container/quyền đặc biệt mà runner dùng
     chung không có, khác với phần lớn bằng chứng thật của dự án). Kích
     hoạt khi push/PR vào `main`. Đây là smoke gate nhỏ ban đầu, **chưa
     phải full probe suite** — hầu hết bằng chứng thật (netem matrix đa
     container, KVM đa host) cần hạ tầng/quyền mà runner chia sẻ không có,
     nên việc mở rộng bundle này (nếu muốn) sẽ cần tính riêng. Chưa xác
     minh được lần chạy đầu tiên trên GitHub thành công hay không (không
     có công cụ `gh`/token truy cập API từ môi trường này) — cần người
     dùng tự kiểm tra tab Actions trên GitHub. Branch protection cho
     `main` vẫn **chưa bật** — đó là thay đổi cấu hình repo (không chỉ
     thêm file) nên cần xác nhận riêng trước khi bật.

## Quy ước cập nhật file này

- Mỗi khi một nhóm chuyển trạng thái, sửa dòng tương ứng trong bảng và
  ghi rõ commit/script/kết quả làm bằng chứng (không ghi chung chung).
- Không xoá lịch sử "Trạng thái gốc" — đó là mốc đối chiếu với báo cáo
  hành chính gốc.
- Nếu phát hiện một claim tưởng đã đóng nhưng thực ra chưa đủ bằng chứng,
  hạ trạng thái xuống lại và ghi rõ lý do (đừng tự chấp nhận "coi như
  xong" nếu chưa có replay được bằng chứng cụ thể).
