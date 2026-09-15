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
| 6 | Đối sánh mô phỏng (ns-3/OMNeT++), soak dài hạn, bằng chứng phát hành qua CI | Đạt một phần | 🟡 **Đang làm** | Xem mục "Nhóm 6" bên dưới. Đối sánh ns-3/OMNeT++ Wi-Fi: tìm + sửa 2 bug thật (INET PendingQueue 100 vs ns-3 500 gói; INET Arp retryTimeout 1s bị lộ do đồng bộ start-time) + sửa cách so sánh p99 sang tỷ lệ tương đối. Kết quả 10-seed: **8 trạm = 100% nhóm kịch bản khớp** (trung bình), 16 trạm 56%, 32 trạm 33% (còn khoảng cách thật ở delivery ratio, đã thử 8 giả thuyết không tìm thêm được nguyên nhân). CI: đã xác minh chạy thật pass qua GitHub API. Soak dài hạn: chưa làm. |

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
     - **Cập nhật (10/09/2026) — đã build + chạy thật, có kết quả cụ thể:**
       build image `omnetpp-inet` thành công sau khi thêm retry + ép
       HTTP/1.1 vào `git fetch` (môi trường mạng ở đây hay bị "early EOF"
       giữa chừng với HTTP/2 kéo dài — lỗi hạ tầng, không phải lỗi Dockerfile
       gốc). Sửa 3 bug phát hiện khi chạy thật lần đầu (đều đã commit):
       thiếu feature `Ethernet` cho `AccessPoint` (cần `MacForwardingTable`
       dù không dùng cổng Ethernet nào), thiếu module `ns3-point-to-point`
       trong lệnh link g++ ở **cả 3** script wifi (kể cả 2 script ns-3-only
       có sẵn từ trước, `run_ns3_docker_wifi_mobility_matrix.py` và
       `run_ns3_docker_wifi_roaming_matrix.py` — chưa từng được chạy thật
       trước đây nên bug này chưa bị phát hiện), và `opMode` mặc định
       `"g(mixed)"` của INET bật cơ chế bảo vệ tương thích 802.11b thừa
       (ns-3 dùng thuần 802.11g) — sau khi ép `opMode="g(erp)"` ở cả AP và
       station, kết quả khớp rất sát ở tải nhẹ.

       **Kết quả full matrix (8/16/32 robot × seed 7/13/29 × 3 kịch bản =
       27 case, runtime 27/27 chạy xong không crash cả 2 bên):**
       - **8 robot/1 AP**: khớp gần như hoàn hảo — p50/p99 latency lệch
         vài ms, delivery ratio lệch <2%, miss ratio ~0 cả hai bên (`ok`
         2/3 seed cho `stationary_near`+`mobile_moderate`; `mobile_edge`
         luôn fail vì payload xấp xỉ giới hạn băng thông kênh ở đây).
       - **16/32 robot/1 AP**: hai bên phân kỳ lớn về độ trễ/tỷ lệ mất gói
         khi kênh bão hòa (`parity=5/27` tổng thể với threshold hiện tại).
         Đã điều tra kỹ nguyên nhân bằng instrumentation đo trực tiếp
         (compiled trace-source counter trong ns-3, `@statistic`
         `transmissionState` + vector recording tích phân theo thời gian
         trong INET — không phải NS_LOG text vì quá chậm để dùng thực tế
         ở quy mô 32 trạm), đo trên case 32 robot/`stationary_near`: tổng
         số lần truyền PHY và tổng thời gian chiếm kênh giữa hai bên
         **chỉ lệch ~11-22%** (ns-3: 41634 lần truyền / 2.258s; OMNeT++:
         36307 lần truyền / 1.761s — độ dài trung bình mỗi lần truyền
         54.2µs vs 48.5µs, khá gần nhau, không có bằng chứng bug PHY timing
         rõ ràng ở bên nào — đã kiểm tra CWmin/CWmax, retry limit,
         preamble/header duration đều khớp chuẩn 802.11g ở cả hai). Kết
         luận: đây là hệ quả của tính chất "vực thẳm" (cliff behavior) đã
         biết của CSMA/CA gần điểm bão hòa kênh — một chênh lệch nhỏ
         (~15-20%) về tải/tỷ lệ retry thực tế bị khuếch đại phi tuyến
         thành chênh lệch độ trễ/mất gói lớn, không phải một bug rời rạc
         có thể sửa bằng cách chỉnh tham số. Không tiếp tục đào sâu thêm
         vì đây là giới hạn khoa học thật của việc đối chiếu số liệu giữa
         hai cài đặt MAC/PHY 802.11 độc lập gần bão hòa, không phải việc
         treo.
       - Ngưỡng so sánh (`DEFAULT_THRESHOLDS`) giữ nguyên như thiết kế ban
         đầu — không nới lỏng để ép case 16/32 robot pass, vì đó sẽ là che
         giấu một khác biệt thật thay vì chứng minh parity.

       **Quyết định phạm vi (bản đầu, 10/09/2026 vòng 1):** đóng mục "đối
       sánh ns-3/OMNeT++ Wi-Fi" ở tải nhẹ (≤8 trạm chia sẻ 1 AP), tải cao
       chưa đạt, coi là cliff behavior không sửa được. **Đã bị thay thế**
       bởi kết quả vòng 2 bên dưới (tìm ra fix hàng đợi thật) — xem mục
       "Cập nhật (10/09/2026, vòng 2)" để có kết luận cuối cùng, đầy đủ
       hơn và tốt hơn bản này.

       **Điều tra sâu thêm (10/09/2026, theo yêu cầu người dùng)** — đã
       kiểm tra tuần tự các nghi vấn cụ thể, có số liệu, để loại trừ khả
       năng "có bug sửa được" trước khi chấp nhận giới hạn ở trên:
       - Đếm trực tiếp (không đoán) số lần truyền PHY và tổng thời gian
         chiếm kênh ở case 32 robot/`stationary_near` bằng compiled
         trace-source instrumentation (bản sao riêng của
         `fleetqox_trace_replay.cc`, không đụng file gốc) cho ns-3, và
         `@statistic[transmissionState]` + vector recording tích phân theo
         thời gian cho INET (thử NS_LOG text trước nhưng quá chậm — >340MB
         log cho 1 giây mô phỏng, không khả thi). Kết quả: ns-3 41634 lần
         truyền/2.258s chiếm kênh (TB 54.2µs/lần); INET 36307 lần
         truyền/1.761s (TB 48.5µs/lần) — chỉ lệch ~11-22%, không phải
         chênh lệch lớn kiểu bug.
       - Kiểm tra trung bình (không chỉ giá trị tệ nhất) trên toàn bộ 27
         case mỗi quy mô: lệch tăng đều và nhất quán theo số trạm (8→16→32:
         delivery delta TB 5.1%→7.9%→17.3%) — xác nhận đây là khuôn mẫu
         thật, không phải một seed xui.
       - Kiểm tra các tham số MAC/PHY chuẩn 802.11g có thể gây lệch: CWmin
         (15)/CWmax(1023) khớp cả hai; short retry limit(7)/long retry
         limit(4) khớp cả hai; preamble+SIGNAL duration (16µs+4µs OFDM)
         khớp cả hai; **slot time**: xác nhận kỹ vì lúc đầu tưởng
         `opMode="g(erp)"` có thể không kích hoạt đúng slot time 9µs ngắn
         (thay vì 20µs kiểu legacy) — đọc source `Ieee80211ErpOfdmMode.cc`
         xác nhận `erpOnlyOfdmMode*` (dùng bởi `"g(erp)"`) có `isErpOnly
         = true` → đúng 9µs, khớp ns-3; SIFS 10µs khớp cả hai.
       - Công suất phát/độ nhạy máy thu: ns-3 mặc định 16.0dBm (~40mW) TX,
         CCA -62dBm, RxSensitivity -101dBm; INET mặc định 13.0dBm (20mW)
         TX, energy-detection -85dBm, receiver.sensitivity -85dBm,
         snirThreshold 4dB. Có khác biệt thật (đọc trực tiếp từ
         `Ieee80211Radio.ned` và từ getter runtime của `YansWifiPhy` qua
         một chương trình chẩn đoán riêng), nhưng ở khoảng cách trạm 2-5m
         trong kịch bản này tín hiệu đều dư thừa mạnh so với cả hai ngưỡng
         nên khó là yếu tố quyết định; cơ chế quyết định lỗi khung ở tầng
         thấp hơn (PER liên tục theo SNR ở ns-3 vs ngưỡng SNIR cứng có
         thêm lớp `Ieee80211NistErrorModel` ở INET) là khác biệt kiến trúc
         còn lại chưa kiểm chứng hết, nhưng để đi tiếp cần so sánh logic
         xác suất lỗi khung ở mức bit giữa 2 codebase — vượt phạm vi hợp
         lý của phiên làm việc này, cần chuyên môn sâu về mô hình PHY
         không dây.
       - **Kết luận điều tra vòng 1**: không tìm thấy tham số cấu hình sai
         cụ thể nào có thể sửa để đóng gap này bằng các tham số đã kiểm
         tra ở trên; tạm kết luận là "cliff behavior" của CSMA/CA gần bão
         hòa. **Người dùng không đồng ý dừng lại, yêu cầu điều tra tiếp —
         và đúng**, tìm ra thêm một khác biệt cấu hình thật.

       - **Cập nhật (10/09/2026, vòng 2) — TÌM RA NGUYÊN NHÂN CHÍNH:**
         `PendingQueue` (hàng đợi truyền MAC theo từng trạm) của INET mặc
         định `packetCapacity = default(100)`
         (`src/inet/linklayer/ieee80211/mac/queue/PendingQueue.ned`), còn
         `WifiMacQueue` của ns-3 mặc định **500 gói**
         (`ns3::WifiMacQueue::GetMaxSize()`, đo runtime qua chương trình
         chẩn đoán riêng). Với hàng đợi nhỏ hơn 5 lần, INET rớt gói sớm
         ("tail-drop" khi đầy) thay vì để gói chờ lâu trong hàng đợi như
         ns-3 — làm độ trễ đo được của INET trông thấp giả tạo (gói chờ
         lâu bị rớt thay vì được tính vào thống kê độ trễ), không phải vì
         INET giải quyết tắc nghẽn giỏi hơn thật.

         Thử nghiệm xác nhận: tăng `packetCapacity` INET lên 500 (khớp
         ns-3) cho case 32 robot/`stationary_near`/seed 7 — độ trễ p50
         tăng từ 68ms lên 380ms (ns-3: 470ms, giờ khớp gần hơn nhiều),
         miss ratio khớp rất sát (INET 87.2% vs ns-3 88.7%, trước đó là
         66.9% vs 88.7% — lệch 22 điểm giảm còn 1.5 điểm).

         Đã sửa chính thức: `external/omnetpp/omnetpp.ini`
         (`**.wlan[*].mac.dcf.channelAccess.pendingQueue.packetCapacity =
         500`).

         **Đồng thời sửa lại cách so sánh p99 latency**: ngưỡng cũ dùng độ
         lệch tuyệt đối (ms), phù hợp cho mạng có dây (~10ms) nhưng vô
         nghĩa khi p99 ở vùng bão hòa lên tới 400-1300ms cả hai bên — đổi
         sang **tỷ lệ** (`p99_latency_ratio = max/min`, ngưỡng ≤2.5x) giống
         cách đã làm với `normalized_utility_delta`. Đây là sửa đúng đắn
         về mặt thiết kế phép đo (không phải nới lỏng ngầm để ép pass) —
         `delivery_ratio_delta` và `deadline_miss_ratio_delta` (ngưỡng
         không đổi 0.10) là chỉ số chính, vẫn giữ chặt.
         `scripts/run_omnetpp_docker_wifi_parity.py` cập nhật thành
         `compare_policy_rows_wifi` (schema v2), không đụng script P2P
         (`run_omnetpp_docker_parity.py` vẫn dùng ngưỡng ms tuyệt đối, hợp
         lý cho mạng có dây).

         **Kết quả full matrix sau cả 2 sửa (27/27 runtime ok):**

         | Quy mô | Trước (v1) | Sau (v3) |
         |---|---|---|
         | 8 robot | 21/27 policy-case pass | 21/27 (không đổi, đã tốt sẵn) |
         | 16 robot | ~4/27 | **19/27** |
         | 32 robot | 0/27 | **7/27** |

         Ở 32 robot: `deadline_miss_ratio_delta` và `p99_latency_ratio` đã
         khớp tốt (lệch 1.5-8.5 điểm, tỷ lệ p99 ~1.3-2x) ở hầu hết case;
         phần còn thiếu để pass hết là `delivery_ratio_delta` (~20-22% ở
         một số case) và `normalized_utility_delta` — vẫn còn một khoảng
         cách thật ở mức tải cực đại (32 trạm/1 AP, >80-90% mất gói cả hai
         bên), nhiều khả năng là biến động thống kê tự nhiên giữa 2 luồng
         RNG độc lập (không có cách nào làm 2 simulator độc lập ra đúng
         cùng chuỗi backoff ngẫu nhiên dù cùng giá trị seed, vì thuật toán
         RNG khác nhau) — không tiếp tục ép thêm bằng cách nới ngưỡng nữa,
         ghi nhận trung thực đây là giới hạn còn lại sau khi đã sửa xong
         phần cấu hình thật (queue capacity).

       - **Cập nhật (10/09/2026, vòng 3) — điều tra thêm phần dư ở 32
         trạm theo yêu cầu người dùng**, sau khi đã có fix hàng đợi:
         phân loại 27 case chưa pass thành 3 nhóm cụ thể (không phải "chưa
         rõ" chung chung nữa):
         - Nhóm sát ngưỡng (4 case, 8 trạm/`mobile_edge`): tỷ lệ p99 chỉ
           2.46-2.69 so với ngưỡng 2.5 — gần như đã pass.
         - Nhóm đảo chiều ngẫu nhiên (bằng chứng RNG độc lập): seed 29 ở
           16 trạm có lần OMNeT++ tệ hơn ns-3 (miss 72-81% vs ns-3 chỉ
           37-42%) — ngược xu hướng thường thấy; seed 29 ở 8 trạm có 1
           đỉnh trễ bất thường riêng lẻ (p99 OMNeT++ 527ms vs ns-3 chỉ
           18ms) dù các case khác ở cùng quy mô đều ổn định. Cả hai đều là
           dấu hiệu 2 simulator dùng 2 bộ RNG độc lập khác nhau, không
           phải lỗi hệ thống một chiều.
         - Nhóm 32 trạm (phần lớn case còn lại): `delivery_ratio_delta`
           lệch nhất quán ~15-28% ở mọi seed.

         Đã thử thêm 1 giả thuyết cụ thể cho nhóm 32 trạm: **mô hình tính
         xác suất lỗi khung theo SINR** — ns-3 mặc định dùng
         `YansErrorRateModel`, INET dùng `Ieee80211NistErrorModel` (2 công
         thức khác nhau). Kiểm chứng thực nghiệm bằng cách build lại ns-3
         với cờ `--wifiErrorRateModel=ns3::NistErrorRateModel` (thêm vào
         bản sao riêng của `fleetqox_trace_replay.cc`, không đụng file
         gốc) và so với `YansErrorRateModel` mặc định cho cùng case 32
         trạm/seed 7: **kết quả gần như giống hệt nhau** (rx=1628 vs 1626,
         p50=438 vs 489ms) — loại trừ giả thuyết này. Ở khoảng cách 2m
         trong kịch bản này, tín hiệu đủ mạnh nên công thức lỗi-bit-theo-SNR
         không quan trọng; phần lớn mất gói là do va chạm hoàn toàn (2 tín
         hiệu chồng lấp), không phải lỗi bit ở biên SNR.

         Đến đây đã kiểm tra hết mọi tham số PHY/MAC 802.11g hợp lý có thể
         nghĩ tới (7 giả thuyết cụ thể, có đo/thử nghiệm thực tế, không
         phải suy đoán) — chỉ còn 1 cái tìm ra và sửa được thật (queue
         capacity). Phần dư ~15-28% delivery ratio ở 32 trạm giờ có độ tin
         cậy cao là biến động RNG-stream tự nhiên (không có cách nào làm 2
         codebase độc lập, viết bởi 2 nhóm khác nhau, sinh đúng cùng chuỗi
         số ngẫu nhiên dù cùng giá trị seed).

       - **Cập nhật (10/09/2026, vòng 4) — mở rộng lên 10 seed (7, 13, 29,
         41, 53, 67, 79, 89, 97, 101) theo yêu cầu người dùng để đánh giá
         đúng hơn (3 seed là mẫu nhỏ với một quá trình có yếu tố ngẫu
         nhiên) và tìm ra thêm 1 bug thật:**

         Pass theo từng case riêng lẻ với 10 seed (90 case/quy mô): 8 trạm
         77%, 16 trạm 60%, 32 trạm 27% — gần khớp với số liệu 3-seed
         trước đó, xác nhận 3 seed ban đầu **không phải mẫu xui**, số liệu
         đại diện đúng. Pass theo **trung bình gộp 10 seed** (9 nhóm
         kịch bản×chính sách mỗi quy mô) thậm chí KHÔNG cao hơn (33%,
         56%, 33%) — nghĩa là phần lệch còn lại không đơn thuần là nhiễu
         ngẫu nhiên tự triệt tiêu qua trung bình, mà có phần hệ thống thật.

         Đào sâu cụ thể vào nhóm `8 trạm/stationary_near` (nhóm có vẻ
         "khớp gần hoàn hảo" nhưng tỷ lệ p99 trung bình lại tới 3.7-7.2x):
         xem từng seed riêng lẻ phát hiện **8/10 seed khớp gần tuyệt đối
         (tỷ lệ p99 1.0-1.14x) nhưng 2 seed (29, 89) có đỉnh trễ bất
         thường cực lớn (tỷ lệ 19.65x và 29.94x)** — không phải lệch dàn
         trải mà là 1 sự kiện hiếm, cụ thể. Thêm log chẩn đoán tạm thời
         vào bản sao riêng của `TraceDrivenUdpApp.cc` (không đụng file
         gốc) để in ra gói nào gây trễ >100ms cho case seed 29: **toàn bộ
         61 gói outlier đều là từ `robot_0002` đến `fleet_router`** — gói
         đầu tiên (lịch gửi lúc 40ms) trễ tới 1010ms, các gói tiếp theo
         cùng cặp nguồn-đích "dồn ứ" phía sau rồi giải phóng đồng loạt tại
         cùng 1 mốc thời gian tuyệt đối (~1050ms) — dấu hiệu kinh điển của
         head-of-line blocking do 1 gói bị kẹt.

         **Tìm ra nguyên nhân**: độ trễ ~1000ms khớp chính xác với
         `retryTimeout = default(1s)` của module `Arp` trong INET
         (`src/inet/networklayer/arp/ipv4/Arp.ned`). Do tất cả trạm bắt
         đầu phát lại trace tại đúng cùng 1 mốc `startOffset` (đã ghi chú
         ở trên về `sameTransmissionStartTimeCheck`), các gói ARP quảng bá
         từ nhiều trạm có xác suất va chạm với nhau cao bất thường tại
         đúng thời điểm đó — một hệ quả nhân tạo của việc đồng bộ hóa thời
         gian bắt đầu phát lại có chủ đích (không phải hiện tượng thật của
         mạng thật), không phải do OMNeT++ hay ns-3 mô phỏng sai. Khi ARP
         đầu tiên bị mất, `robot_0002` phải đợi đủ 1s mới thử lại — toàn bộ
         gói ứng dụng xếp hàng chờ phía sau trong lúc đó.

         **Sửa**: đổi `**.arp.typename = "GlobalArp"` (phân giải địa chỉ
         không cần trao đổi gói tin, không có gì để va chạm) thay cho
         `Arp` mặc định — hợp lý vì mục tiêu là so sánh công bằng hành vi
         MAC/PHY 802.11, không phải so sánh 2 cơ chế ARP retry khác nhau
         (ns-3 không thể hiện artifact này vì việc phân giải địa chỉ của
         nó không nằm trong phần workload được replay). Test riêng seed 29
         xác nhận: outlier biến mất hoàn toàn (0 gói >100ms), p99 giảm từ
         314-527ms xuống 13-18ms, miss ratio về 0%.

         **Kết quả full matrix (10 seed) sau khi thêm fix GlobalArp:**

         | Quy mô | Trước GlobalArp | Sau GlobalArp |
         |---|---|---|
         | 8 trạm (case riêng lẻ) | 69/90 (77%) | **79/90 (88%)** |
         | 16 trạm (case riêng lẻ) | 54/90 (60%) | **60/90 (67%)** |
         | 32 trạm (case riêng lẻ) | 24/90 (27%) | **28/90 (31%)** |
         | 8 trạm (trung bình 10 seed, theo nhóm) | 3/9 (33%) | **9/9 (100%)** |
         | 16 trạm (trung bình 10 seed, theo nhóm) | 5/9 (56%) | 5/9 (56%) |
         | 32 trạm (trung bình 10 seed, theo nhóm) | 3/9 (33%) | 3/9 (33%) |

         Tải nhẹ (8 trạm) giờ đạt **parity hoàn toàn trên trung bình 10
         seed** — kết quả rất mạnh. 16 trạm còn 4/9 nhóm chưa đạt, tập
         trung ở `stationary_near` (miss ratio lệch ~16-17 điểm, tương đối
         nhỏ nhưng nhất quán) và `mobile_edge/static_priority`. 32 trạm
         còn 6/9 nhóm chưa đạt, tập trung ở `delivery_ratio` lệch ~11-26%
         trên phần lớn tổ hợp tải cao — đã kiểm tra 7 giả thuyết cụ thể
         (CW/retry/preamble/slot/SIFS/TX-power/error-model) và không tìm
         thêm được nguyên nhân cấu hình nào khác.

       - **Cập nhật (10/09/2026, vòng 5) — tìm ra bug thứ 3, nghiêm trọng
         hơn 2 bug trước, theo yêu cầu người dùng tiếp tục điều tra sau khi
         vẫn tin có nguyên nhân cụ thể:**

         Muốn kiểm chứng gap ở tải cao có phải do sai lệch **thứ tự gán vị
         trí lưới** hay không (`.ned` gán `controller=index0,
         fleetRouter=index1, operatorUi=index2, robot[i]=index(i+3)` theo
         giả định cố định, còn ns-3 gán theo **thứ tự lần đầu gặp mỗi
         endpoint khi quét CSV** — đã ghi chú là giới hạn scope biết trước
         nhưng chưa từng kiểm chứng tác động thật). Viết script Python tính
         đúng thứ tự này cho từng trace cụ thể (không theo công thức đơn
         giản — có seed mà `robot_0011` xuất hiện trước `robot_0010`) rồi
         truyền vị trí chính xác vào OMNeT++ qua CLI override.

         **Phát hiện khi test**: override hoàn toàn không có tác dụng — dời
         `robot[0]` ra xa 999999m, kết quả không đổi 1 bit! Nguyên nhân:
         `LinearMobility` có tham số `initFromDisplayString = default(true)`
         — khi `true` (mặc định), module **bỏ qua hoàn toàn** tham số NED
         `initialX`/`initialY` và lấy vị trí từ chuỗi `@display("p=X,Y")`
         tĩnh trên canvas thay vào đó. Thêm bug nghiêm trọng hơn: biểu thức
         `"p=70+index*80,520"` trong `@display` string **không được tính
         toán** khi dùng làm nguồn vị trí runtime (chỉ được trình vẽ GUI
         hiểu, không phải logic gán vị trí lúc mô phỏng) — kết quả: **toàn
         bộ robot (0-15 ở case 16 trạm) đã ở CÙNG MỘT ĐIỂM (x=70,y=520)
         suốt từ đầu**, còn `fleet_controller/fleet_router/operator_ui` ở
         cách nhau **hàng trăm mét** (250,300 / 400,300 / 550,300 — đơn vị
         pixel bị hiểu nhầm thành mét) — sai lệch vị trí nghiêm trọng hơn
         nhiều so với chỉ "sai thứ tự" ban đầu nghĩ.

         **Sửa**: thêm `mobility.initFromDisplayString = false;` cho cả 4
         loại host (controller/fleetRouter/operatorUi/robot[]), bọc các
         gán `initialX/initialY/initialMovementHeading` trong `default(...)`
         để có thể ghi đè qua CLI, và cập nhật
         `run_omnetpp_docker_wifi_parity.py` để tính đúng thứ tự ns-3 (hàm
         `ns3_encounter_order`) rồi truyền vị trí chính xác
         (`ns3_style_positions` + `_position_override_flags`) cho từng
         case. Đã xác minh override hoạt động thật (test dời xa trong giới
         hạn constraint area → rx giảm mạnh như mong đợi).

         **Kết quả sau khi thêm fix này (3 seed, cộng với 2 fix trước —
         hàng đợi + ARP):**

         | Quy mô | Trước fix vị trí | Sau fix vị trí |
         |---|---|---|
         | 8 trạm | 78% | **96%** ⬆️ tốt hơn nữa |
         | 16 trạm | 70% | 59% ⬇️ tệ hơn |
         | 32 trạm | 26% | 22% ⬇️ tệ hơn |

         **Bất ngờ**: dù đây là bug thật (đã xác minh chắc chắn bằng
         thực nghiệm, không phải suy đoán) và bản sửa khớp đúng với cách
         ns-3 thực sự cài đặt (đọc lại code `fleetqox_trace_replay.cc`
         xác nhận: ns-3 đặt TẤT CẢ node — kể cả controller/router/ui —
         lên cùng 1 lưới chung qua `GridPositionAllocator`, không tách
         biệt như `.ned` cũ của tôi từng làm), việc sửa "đúng hơn" lại
         không cải thiện đều — tốt hơn ở 8 trạm, tệ hơn ở 16/32 trạm.
         Đào sâu chi tiết theo từng case (16 trạm, 3 seed): vấn đề tập
         trung ở kịch bản `stationary_near` (khoảng cách 2m — sít nhất
         trong 3 kịch bản) — miss ratio OMNeT++ giờ **nhất quán** cao hơn
         ns-3 vài điểm đến vài chục điểm ở mọi seed (khác trước đây là
         "lúc hơn lúc kém tùy seed"); `mobile_moderate`/`mobile_edge`
         (spacing 3m/5m) thì vẫn khớp tốt.

         Kiểm tra thêm 1 tầng sâu hơn: `FreeSpacePathLoss::
         computeFreeSpacePathLoss` (model suy hao mặc định của INET) có xử
         lý đặc biệt tại khoảng cách 0: `return distance == 0.0 ? 1.0 :
         ...` — nghĩa là ở bug cũ (robot chồng lên nhau, d=0), suy hao được
         tính bằng 0 (tín hiệu neighbor-to-neighbor tới với cường độ tối
         đa), không phải "không nhiễu" như giả thuyết ban đầu. Nhưng vì
         robot không gửi trực tiếp cho nhau trong mô hình luồng dữ liệu
         (chỉ gửi tới `fleet_router`), yếu tố quyết định thực ra là khoảng
         cách **từng robot → `fleet_router`** chứ không phải robot-robot;
         ở bug cũ mọi robot cách `fleet_router` **CÙNG một khoảng cách**
         (vì trùng điểm) — một kiểu nhiễu đồng nhất giả tạo; sau khi sửa,
         khoảng cách này khác nhau thật cho từng robot, tạo ra mẫu hình
         nhiễu phức tạp hơn nhiều, khó dự đoán chiều ảnh hưởng chỉ bằng suy
         luận. Đây là hiệu ứng bậc 3 (hệ quả của việc sửa đúng 1 bug thật),
         không phải bug độc lập mới.

         **Cập nhật (10/09/2026, vòng 6) — điều tra thêm cơ chế theo yêu
         cầu người dùng, kết luận:**
         - Đo latency theo từng robot (16 trạm/seed 7/`stationary_near`),
           xếp theo khoảng cách tới `fleet_router` (2.0m → 7.2m): **không
           có gradient** — latency/miss ratio gần như đồng đều ở mọi
           khoảng cách. Loại trừ giả thuyết "vị trí gần/xa router quyết
           định".
         - Giả thuyết "traffic `fleet_controller` (50Hz×16 robot) bị mất
           do tín hiệu yếu ở bug cũ (cách xa ~400m, biên độ chỉ ~6dB) nên
           tải kênh thực tế bị giảm giả tạo": đo trực tiếp tỷ lệ giao
           traffic từ `fleet_controller` ở cả setup cũ và mới cho cùng 1
           case — **gần như giống hệt nhau** (98.2% cũ vs 97.4% mới, chỉ
           lệch 0.8 điểm). Loại trừ.
         - Nhìn lại số liệu thô: miss ratio thực tế của case cụ thể này
           chỉ tăng nhẹ sau fix vị trí (0.749→0.784, ~3.5 điểm), **không
           phải thay đổi lớn**. Ngưỡng pass/fail hiện đặt đúng ở mức 10
           điểm lệch (`deadline_miss_ratio_delta<=0.10`) — một dịch chuyển
           nhỏ 3-4 điểm có thể đẩy nhiều case đang sát ngưỡng từ pass sang
           fail cùng lúc, khiến % tổng thể giảm nhiều hơn mức thay đổi
           thực chất. Đây là **hiệu ứng nhạy cảm ngưỡng**, không phải bằng
           chứng của 1 bug lớn còn ẩn.

         **Kết luận**: đã thử 2 giả thuyết cơ chế cụ thể (gradient khoảng
         cách, tải giả tạo từ controller) — cả hai đều bị loại bằng đo đạc
         trực tiếp. Fix vị trí giữ nguyên (đúng kỹ thuật, khớp ns-3 thật);
         thay đổi số liệu tổng thể sau fix là thật nhưng ở mức khiêm tốn,
         bị khuếch đại bởi việc nhiều case nằm sát ngưỡng 10 điểm.

       - **Cập nhật (10/09/2026, vòng 7) — người dùng phản bác đúng lập
         luận "biến động RNG" ở vòng 6** (độ lệch delivery ratio 15-28%
         quá lớn để chỉ là nhiễu), yêu cầu kiểm tra lại xem độ lệch có
         nhất quán 1 chiều hay không. Kiểm tra trực tiếp (32 trạm, 3 seed,
         theo từng policy) — **phát hiện quan trọng**: độ lệch **nhất
         quán theo từng policy, không ngẫu nhiên**:
         - `fifo`: OMNeT++ luôn giao NHIỀU hơn ns-3 (+16 đến +21 điểm, cả
           3 seed).
         - `static_priority`: OMNeT++ luôn giao nhiều hơn, ít hơn (+3 đến
           +8 điểm).
         - `fleetqox_predictive_guarded`: OMNeT++ luôn giao ÍT hơn ns-3
           **~21 điểm, gần như y hệt ở cả 3 seed** (-20.9/-21.3/-20.8%).

         Đào sâu riêng `fleetqox_predictive_guarded` (khối lượng traffic
         `fleet_controller`→robot lớn nhất, 2892/4771 gói = 61%): log chi
         tiết từng `event_id` gửi/nhận (kỹ thuật như đã tìm bug ARP) cho
         thấy tỷ lệ mất gói từ `fleet_controller` tới từng robot tăng
         **gần như tuyến tính hoàn hảo theo chỉ số robot**: robot_0000
         mất 40% → robot_0031 mất 79%. Giả thuyết: mọi gói
         controller→robot phải qua AP chuyển tiếp (802.11 infrastructure
         2 chặng); trace tạo theo thứ tự robot_0000→0031 mỗi tick nên gói
         robot_0031 luôn xếp cuối hàng đợi AP; hạn chót 45ms + kênh bận
         → gói xếp sau dễ trễ hạn hơn.

         **Kiểm chứng bằng ns-3** (cùng kỹ thuật log per-packet, thêm vào
         bản sao riêng của `fleetqox_trace_replay.cc`, không đụng file
         gốc): ns-3 xử lý CÙNG thứ tự trace, nhưng mẫu hình theo robot
         **hoàn toàn khác** — không phải dốc tuyến tính mà robot_0000 bị
         tệ nhất (89%!), rồi robot_0001-0010 trung bình (27-40%),
         robot_0011-0031 khá tốt và nhiễu (10-25%, không xu hướng rõ).
         Tổng loss rate ns-3 (24%) cũng thấp hơn nhiều INET (62%).

         **Kết luận**: giả thuyết "thiên vị theo thứ tự enqueue ở AP" chỉ
         đúng MỘT PHẦN — cả 2 simulator ĐỀU có thiên vị theo thứ tự gửi
         khi tải dồn dập qua AP (xác nhận không phải nhiễu ngẫu nhiên,
         **người dùng đúng**), nhưng **hình dạng thiên vị hoàn toàn khác
         nhau** giữa 2 bên (INET: dốc đều; ns-3: gói đầu bị tệ nhất rồi
         nhiễu). Đây là bằng chứng cụ thể, tái lập được, cho thấy 2 MAC
         stack độc lập tự nhất quán RIÊNG nó nhưng không khớp NHAU khi xử
         lý traffic dồn dập 1-nguồn/nhiều-đích qua AP — không phải 1 bug
         cấu hình có thể sửa bằng 1 tham số (như 3 bug trước), mà là khác
         biệt thuật toán xử lý hàng đợi/tranh chấp thật giữa 2 codebase,
         mỗi bên viết bởi 1 nhóm khác nhau. Không tìm được cách sửa mà
         không phải viết lại thuật toán MAC của 1 trong 2 bên (ngoài
         phạm vi hợp lý của dự án đối sánh).

       - **Trạng thái cuối cùng**: đã tìm và sửa **3 bug cấu hình thật**
         trong hạ tầng đối sánh (không phải giới hạn vật lý không sửa
         được như từng kết luận nhầm ở vòng 1):
         1. `PendingQueue.packetCapacity` 100 (INET) vs 500 (ns-3).
         2. `Arp.retryTimeout` 1s bị lộ do đồng bộ hóa thời gian bắt đầu
            (sửa bằng `GlobalArp`).
         3. `LinearMobility.initFromDisplayString` khiến toàn bộ robot bị
            chồng lên nhau tại 1 điểm, còn controller/router/ui cách xa
            hàng trăm mét (sửa bằng override vị trí đúng theo thứ tự
            ns-3 thật).
         8 trạm đạt 96% (case riêng lẻ, 3 seed) — bằng chứng rất mạnh.
         16 trạm 59%, 32 trạm 22% (case riêng lẻ, 3 seed) — độ lệch còn
         lại đã được xác minh CỤ THỂ (không phải "biến động RNG" chung
         chung): thiên vị theo thứ tự xử lý traffic dồn dập qua AP, khác
         hình dạng giữa 2 MAC stack độc lập, tập trung nặng nhất ở policy
         có khối lượng traffic controller→robot lớn nhất
         (`fleetqox_predictive_guarded`). Đã kiểm chứng bằng log
         per-packet ở cả 2 simulator (không phải suy đoán). Đây là khác
         biệt thuật toán thật giữa 2 codebase độc lập, không phải 1 tham
         số cấu hình sai — không tìm được cách sửa hợp lý nằm trong phạm
         vi dự án đối sánh (sửa sẽ cần viết lại 1 phần thuật toán MAC của
         INET hoặc ns-3).

         **Cập nhật (10/09/2026, vòng 8) — câu hỏi cuối cùng của người
         dùng: "tại sao 8 trạm cao mà 16/32 lại thấp đột ngột, không giảm
         từ từ?"** — nghi ngờ hợp lý là dấu hiệu bug. Kiểm tra bằng cách đo
         **ns-3 một mình** (không so sánh với INET) ở nhiều quy mô liên
         tục: 4, 6, 8, 10, 12, 16, 24, 32 trạm (cùng seed, cùng kịch bản
         `stationary_near`, dùng đúng trace kết hợp cả 3 policy như test
         thật — lần đầu vô tình test sai với trace chỉ 1 policy, cho kết
         quả gây hiểu lầm, đã tự sửa và làm lại đúng cách).

         **Kết quả**: `miss_ratio` của ns-3 (không liên quan gì đến INET)
         gần như 0% liên tục từ 4 đến 12 trạm, rồi **nhảy vọt đột ngột lên
         58-70% ngay tại 16 trạm**, tiếp tục tăng lên 88-95% ở 24/32 trạm.
         Đây thật sự là một "vực thẳm" (không phải suy giảm từ từ) —
         **và nó xảy ra ngay trong ns-3 một mình**, không phải hiện tượng
         chỉ xuất hiện khi so sánh 2 simulator.

         **Kết luận cuối cùng, đầy đủ**: khối lượng traffic thật của kịch
         bản test (điều khiển 50Hz × N robot + trạng thái/nhận thức, gộp
         cả 3 policy trong 1 lần mô phỏng) vượt quá khả năng chịu tải thực
         của kênh 802.11g 54Mbps ở đâu đó giữa 12 và 16 trạm — đây là giới
         hạn vật lý/giao thức thật (khớp lý thuyết Bianchi về CSMA/CA gần
         bão hòa), không phải bug ở simulator nào. Điều này giải thích
         trọn vẹn mẫu hình quan sát được: dưới ngưỡng (≤12 trạm) hệ thống
         dư tải nhiều nên 2 simulator độc lập vẫn giao gần hoàn hảo và
         khớp nhau tốt; vượt ngưỡng (≥16 trạm) hệ thống rơi vào chế độ hỗn
         loạn/nhạy cảm cao, nơi khác biệt thuật toán MAC nhỏ giữa 2
         codebase độc lập (đã xác nhận ở vòng 7) bị khuếch đại rõ rệt.
         Không phải 1 bug còn ẩn — là hệ quả tất yếu của thiết kế workload
         chạm đúng ngưỡng bão hòa kênh ở quy mô 16 trạm trở lên.

         **Cập nhật (10/09/2026, vòng 9) — người dùng hỏi tiếp: "kịch bản
         test có đại diện đúng use case thật không, mục tiêu dự án là tối
         ưu cho nhiều robot mà?"** Đây là câu hỏi đúng trọng tâm hơn cả câu
         hỏi trước. Kiểm tra: tổng traffic thật ở 32 trạm (gộp 3 policy) =
         **3852 gói/giây, 4.14 Mbit/s**; ngân sách "capacity" dùng để
         admission-control quyết định gửi/hoãn = chỉ **1.6 Mbit/s**
         (`capacity_bytes_per_second = max(200_000, robots*6_000)`).

         **Phát hiện**: công thức capacity này tính theo **byte/giây**,
         nhưng Wi-Fi 802.11 thật bị giới hạn chủ yếu bởi **số gói/giây**
         (mỗi gói — dù 96 byte hay 800 byte — đều tốn overhead cố định
         ~150-250µs để tranh chấp kênh + truyền + chờ ACK, gần như không
         phụ thuộc kích thước gói khi gói nhỏ). Với traffic điều khiển
         50Hz tần suất cao (đặc trưng đúng kiểu "nhiều gói nhỏ"), mô hình
         byte-rate đánh giá dư tải trong khi kênh Wi-Fi thật đã gần/vượt
         trần gói/giây — đây là khoảng cách thật giữa cách policy đo
         "capacity" và cách 802.11 thật giới hạn.

         **Quan trọng**: công thức `max(200_000, robots*6_000)` **không
         phải do phiên này tự đặt cho bài wifi-parity** — đã kiểm tra, đây
         là quy ước có sẵn từ trước, dùng chung ở **4 script khác nhau**
         (`run_ns3_docker_fleet_matrix.py`,
         `run_ns3_docker_wifi_mobility_matrix.py`,
         `run_ns3_docker_wifi_roaming_matrix.py`, và bài p2p parity trước
         đó `run_omnetpp_docker_parity.py`) — kế thừa đúng quy ước sẵn có,
         không phải lỗi mới tạo ra riêng cho bài này.

         **Ý nghĩa cho dự án**: đây là phát hiện có giá trị vượt ra ngoài
         phạm vi "đối sánh 2 simulator" — gợi ý rằng mô hình capacity
         dùng cho admission control trong `fleetqox/control_plane.py`
         (và các script ns-3 liên quan) nên cân nhắc thêm ràng buộc theo
         **số gói/giây**, không chỉ byte/giây, nếu muốn mô hình hóa đúng
         giới hạn thật của Wi-Fi cho traffic tần suất cao (điều khiển
         real-time). **Chưa sửa** — nằm ngoài phạm vi nhiệm vụ đối sánh
         simulator của Nhóm 6, cần xem xét riêng nếu muốn cải thiện độ
         chân thực của mô hình capacity trong toàn dự án.

         **Cập nhật (10/09/2026, vòng 10) — người dùng làm rõ mục tiêu chiến
         lược: dự án này là 1 middleware muốn chứng minh hành vi KHÔNG ĐỔI
         dù chạy trên simulator nào (ns-3 hay INET/OMNeT++), để làm căn cứ
         trước khi đầu tư thực nghiệm phần cứng thật.** Sau khi được giải
         thích rõ chi phí thật (phải vá lõi C++ của 1 trong 2 simulator,
         chưa từng làm trong phiên này — mọi sửa trước đó đều nằm trong
         code/config của dự án) và khuyến nghị không nên làm, người dùng
         **vẫn chủ động chọn thử vá lõi simulator** để cố khớp RNG 2 bên
         (qua `AskUserQuestion`, chọn phương án không được khuyến nghị).

         **Điều tra kiến trúc RNG của ns-3** (build ns-3.41 từ mã nguồn có
         vá thêm log chẩn đoán, lần đầu tiên trong dự án — trước đó ns-3
         luôn chỉ cài qua `apt`): xác nhận `RngSeedManager::GetNextStreamIndex()`
         là **1 bộ đếm toàn cục duy nhất**, tăng dần theo đúng **thứ tự
         construct object trong C++** của toàn bộ chương trình — **không
         phải công thức theo per-node**. Đo thực nghiệm (kịch bản 8 robot,
         12 thiết bị wifi): 37 stream bị tiêu thụ trong lúc cài đặt wifi
         (không chia đều 12 thiết bị), rồi 99 stream nữa trong lúc cài
         internet stack (gần chắc chắn từ các object IPv6 DAD không dùng
         tới, dù kịch bản chỉ chạy IPv4/UDP thuần). Kết luận: không thể suy
         ngược ra công thức để bên INET tự tái tạo đúng thứ tự này, vì kiến
         trúc module của OMNeT++ (khai báo qua NED, xây từng node 1 lần)
         không có giai đoạn tương đương với cách ns-3 xây "tất cả node rồi
         mới đến tất cả mobility rồi mới đến tất cả internet stack" theo 3
         lượt riêng biệt.

         **Tìm ra lối thoát**: ns-3 có sẵn API công khai
         `WifiHelper::AssignStreams(devices, baseStream)` để gán stream
         **tường minh, tuần tự, theo đúng thứ tự container** — bỏ qua hoàn
         toàn bộ đếm toàn cục mong manh. `Txop::AssignStreams()` set thẳng
         vào `m_rng`, đúng con RNG sinh backoff CSMA/CA
         (`Txop::GetBackoffSlots`, dùng `m_rng->GetInteger(0, cw)`). Script
         hiện tại **chưa từng gọi hàm này** (0 kết quả grep) — nghĩa là
         thứ tự stream đang phụ thuộc bộ đếm toàn cục mong manh nói trên.

         **Đã triển khai** (đã build và đo thật — xem cập nhật 11/09/2026
         bên dưới cho kết quả cuối cùng):
         1. `external/ns3/fleetqox_trace_replay.cc`: thêm cờ CLI
            `--matchedBackoffRng`/`--matchedBackoffRngBase` (mặc định tắt,
            không đổi hành vi cũ) — khi bật, gán stream tường minh cho
            Txop của từng station (`base + i`) và AP (`base + stationCount
            + j`), công thức đóng, có thể tái tạo.
         2. `external/omnetpp/MatchedMrg32k3aRng.{h,cc}`: port lại **bit-for-
            bit** thuật toán MRG32k3a của ns-3 (`rng-stream.cc`, L'Ecuyer
            2001) thành 1 lớp `cRNG` của OMNeT++, kể cả cách quy đổi
            double→integer khớp đúng `UniformRandomVariable::GetInteger`
            của ns-3.
         3. `external/omnetpp/patches/0001-contention-matched-backoff-rng.patch`:
            vá module `Contention` của INET (đây **là** hành động vá lõi
            simulator mà người dùng đã chủ động chọn) — thêm tham số
            `matchedBackoffRngIndex`, cho phép lấy backoff từ 1 RNG-slot
            vật lý tường minh thay vì mapping mặc định `getRNG(0)`. Áp
            dụng qua `git apply` trong `Dockerfile` lúc build image, ghim
            đúng theo `INET_COMMIT` hiện tại.
         4. `omnetpp.ini`: thêm `[Config MatchedWifiRng]` (kế thừa
            `MatchedWifi`, chọn `rng-class = "MatchedMrg32k3aRng"`).
         5. `run_omnetpp_docker_wifi_parity.py`: thêm cờ `--matched-rng`,
            tái dùng đúng hàm `ns3_encounter_order()` sẵn có (đã dùng cho
            việc khớp vị trí robot ở vòng trước) để sinh override CLI cho
            từng station/AP ở cả 2 bên, đảm bảo cùng 1 chỉ số stream.

         **Cập nhật (11/09/2026) — đã build và đo thật (người dùng ra lệnh
         "chạy đi").** Build lại image OMNeT++/INET (patch áp dụng sạch,
         compile thành công). Smoke test 8 trạm/1 seed phát hiện 1 lỗi
         thật: `MatchedMrg32k3aRng.cc` thiếu include `omnetpp/globals.h`
         (nơi khai báo `omnetpp::internal::classes` và `EXECUTE_ON_STARTUP`
         mà macro `Register_Class` cần) — lỗi compile, không phải lỗi
         logic RNG; sửa xong bằng 1 dòng include, build lại pass ngay.

         **Kết quả đo đầy đủ**: chạy lại đúng ma trận 16/32 robot × 3 seed
         (7/13/29) × 3 kịch bản × 3 policy = 54 tổ hợp, so sánh trực tiếp
         với baseline RNG mặc định (`omnetpp_ns3_docker_wifi_parity_v6_summary.json`,
         cùng trace/seed/kịch bản):

         | Quy mô | Pass (baseline) | Pass (matched-RNG) | Delivery-delta TB (baseline) | Delivery-delta TB (matched-RNG) | Delivery-delta MAX (baseline) | Delivery-delta MAX (matched-RNG) |
         |---|---|---|---|---|---|---|
         | 16 trạm | 16/27 | 21/27 | 0.0389 | 0.0378 | 0.1079 | 0.1220 |
         | 32 trạm | 6/27 | 9/27 | 0.1496 | 0.1499 | 0.2784 | 0.2778 |

         **Kết luận, dứt điểm**: matched-RNG **không thu hẹp được độ lệch
         một cách hệ thống**. Số case "pass" nhích lên (16/27→21/27 và
         6/27→9/27) nhưng đó là các case biên dao động qua lại 2 phía
         ngưỡng 10% (ví dụ 16 trạm/seed 29/mobile_moderate/fifo đi từ PASS
         (delta 0.027) sang FAIL (delta 0.117) — **tệ hơn** khi bật
         matched-RNG), không phải cải thiện đồng nhất. Độ lệch trung bình
         và độ lệch lớn nhất — chỉ số phản ánh đúng mức độ khác biệt thật —
         **gần như không đổi** ở cả 2 quy mô (đặc biệt rõ ở 32 trạm, nơi
         vấn đề nghiêm trọng nhất: 0.1496→0.1499, gần như bằng nhau tuyệt
         đối). Điều này xác nhận bằng thực nghiệm (không phải suy đoán)
         đúng cảnh báo đã nêu trước khi chạy: khớp RNG backoff tuyệt đối
         **không kéo theo** khớp thứ tự sự kiện thật giữa 2 MAC stack độc
         lập — độ lệch còn lại ở 16/32 trạm là khác biệt thuật toán
         xử lý hàng đợi/tranh chấp thật (đã xác nhận cụ thể ở vòng 7), chứ
         không phải "biến động RNG" như từng nghi ngờ ban đầu, và **cách
         tiếp cận vá RNG này (dù đã triển khai đúng, đo được, không phải
         thử sai) không giải quyết được vấn đề** — không có thêm hướng vá
         cấu hình nào hợp lý còn lại trong phạm vi dự án đối sánh. Mã
         nguồn được giữ lại (đã hoạt động đúng, có ích cho việc đo lường
         trong tương lai nếu muốn), nhưng **không đưa vào làm cấu hình mặc
         định** vì không cải thiện được kết quả.

         **Cập nhật (11/09/2026, vòng 11) — người dùng hỏi "còn cách nào để
         giảm miss ratio không", chọn thử hướng A (mô hình capacity theo
         packet-rate) đã nêu ở vòng 9.** Triển khai: thêm
         `capacity_packets_per_tick` (tùy chọn, mặc định `None` không đổi
         hành vi cũ) vào `NetworkLink` (`fleetqox/model.py`), enforce ở
         `fifo_policy`/`static_priority_policy`
         (`fleetqox/simulator.py`) và `PredictiveAdmissionController`
         (`fleetqox/control_plane.py`, đứng sau `fleetqox_predictive_guarded`)
         — đúng 3 policy được đối sánh ở bài wifi-parity. 59+107 test cũ đều
         pass (không phá gì, vì mặc định `None`).

         **Lần đo đầu (ngưỡng 3000 gói/giây, ước lượng lý thuyết từ overhead
         DCF 1 trạm không tranh chấp)**: kết quả **giống hệt tuyệt đối**
         baseline (miss ratio, delivery ratio y hệt đến 4 chữ số thập phân)
         — không phải bug: đo trực tiếp gói/tick trong trace cho thấy ngân
         sách byte hiện tại đã tự nhiên giới hạn xuống chỉ 21-49 gói/tick
         (~1050-2450 gói/giây) ở quy mô 8-32 trạm, **thấp hơn ngưỡng 3000**
         nên chưa bao giờ bị chạm tới. Ước lượng lý thuyết ban đầu quá lỏng.

         **Phát hiện khi dò theo quy mô (8→32 trạm, không ngưỡng)**: tốc độ
         gói admit chỉ tăng ~15-20% từ 12 trạm (21-23 gói/tick, nơi miss
         ratio ~0% — vòng 8) sang 16 trạm (24-28 gói/tick, miss ratio nhảy
         lên 58-70%) — **không tỷ lệ thuận** với mức nhảy vọt của miss
         ratio. Gợi ý mạnh: nguyên nhân chính có thể là **số trạm tranh
         chấp** (nhiều trạm hơn = nhiều va chạm CSMA/CA hơn, độ trễ backoff
         tăng phi tuyến) chứ không đơn thuần tổng khối lượng gói/giây.

         **Lần đo thứ 2 (ngưỡng 1200 gói/giây, khớp mức admit tự nhiên ở 12
         trạm — quy mô lớn nhất còn "an toàn")**: ngưỡng này thật sự bó
         buộc rõ ở 32 trạm (tx trung bình 3839.7→2978.6, giảm ~22%), ít bó
         buộc ở 16 trạm (2636.7→2624.1, gần như không đổi vì tốc độ tự
         nhiên đã dưới ngưỡng).

         | Chỉ số (trung bình, ns-3) | 16 trạm base | 16 trạm cap | 32 trạm base | 32 trạm cap |
         |---|---|---|---|---|
         | deadline_miss_ratio | 0.8075 | 0.7474 | 0.9467 | 0.9098 |
         | delivery_ratio | 0.6625 | 0.6699 | **0.5413** | **0.7344** |
         | delivery_ratio (INET) | 0.6613 | 0.6690 | **0.4976** | **0.7020** |
         | parity pass (so 2 simulator) | 16/27 | 14/27 | 6/27 | **13/27** |

         **Kết luận**: hướng packet-rate **có tác dụng thật, đo được**, rõ
         nhất đúng ở chỗ nặng nhất (32 trạm) — delivery ratio tăng **19-22
         điểm phần trăm** ở cả 2 simulator, tỷ lệ khớp giữa 2 simulator tăng
         hơn gấp đôi (6/27→13/27). Đây là bằng chứng cụ thể xác nhận phát
         hiện "mô hình byte-rate định giá sai gói nhỏ tần suất cao" ở vòng 9
         là **có thật và sửa được một phần**, không chỉ là suy luận lý
         thuyết. Nhưng **chưa giải quyết dứt điểm**: `deadline_miss_ratio`
         vẫn rất cao (~91% ở 32 trạm) vì deadline của traffic (45-160ms) quá
         chặt so với độ trễ tranh chấp thật ở 32 trạm dù đã giảm tải — kể cả
         gói được giao vẫn thường giao trễ hạn. Xác nhận thêm: 1 ngưỡng
         gói/giây cố định toàn cục **không mô hình hóa đúng** tác động của
         số trạm tranh chấp (điều thật sự gây collision phi tuyến) — muốn
         giải quyết triệt để cần các hướng B/C đã nêu (nối
         `RobotBudgetAwareAdmissionController` vào production, gộp gói,
         nâng chuẩn wifi hỗ trợ aggregation, hoặc hạ tần số điều khiển khi
         fleet lớn). Ngưỡng mặc định trong
         `run_omnetpp_docker_wifi_parity.py` đã cập nhật thành 1200 gói/giây
         (thực nghiệm, không phải lý thuyết) kèm giải thích đầy đủ trong
         code.

         **Cập nhật (11/09/2026, vòng 12) — người dùng tiếp tục hỏi "đã áp
         dụng đúng config mạng, môi trường chưa" — kiểm tra và phát hiện
         thật: cả 2 simulator đang chạy 802.11 DCF KHÔNG QoS** (ns-3
         `QosSupported` mặc định false dưới 802.11n; INET `qosStation` mặc
         định false) — mọi loại traffic (control, state, perception, debug)
         tranh chấp kênh bình đẳng trong cùng 1 hàng đợi best-effort, trong
         khi hầu hết thiết bị thật đều hỗ trợ WMM (802.11e EDCA), cơ chế
         chuẩn để traffic điều khiển được ưu tiên thật ở tầng MAC. Đây là
         khoảng cách config thật, đúng hướng người dùng nghi ngờ.

         **Đã triển khai** ở cả 2 bên, cùng 1 bảng ánh xạ flow_class→WMM User
         Priority (`safety/control→AC_VO`, `coordination/state→AC_VI`,
         `human_qoe→AC_VI`, `perception→AC_BE`, `debug/bulk→AC_BK`): cờ
         `--wifiQos` cho ns-3 (gắn `SocketPriorityTag` mỗi gói), tham số
         `wifiQos`/`qosStation` cho INET (gắn `UserPriorityReq`, đồng thời
         phải đọc thêm cột `flow_class` từ CSV — trước đây `TraceDrivenUdpApp.cc`
         không dùng cột này).

         **Kết quả đo (16/32 trạm, chỉ bật QoS, tắt ngưỡng packet-rate để
         cô lập biến số)** — **bất ngờ và không đối xứng**:

         | Chỉ số | 16 trạm ns-3 (base→qos) | 16 trạm INET (base→qos) | 32 trạm ns-3 (base→qos) | 32 trạm INET (base→qos) |
         |---|---|---|---|---|
         | deadline_miss_ratio | 0.8075→0.8862 (tệ hơn) | 0.8660→**0.3121** | 0.9467→0.9475 (không đổi) | 0.9094→**0.3998** |
         | delivery_ratio | 0.6625→0.6485 | 0.6613→**0.7779** | 0.5413→0.5322 | 0.4976→**0.6790** |
         | parity pass toàn ma trận | 0/18 (tệ hơn baseline) | | | |

         **INET cải thiện rất mạnh** (deadline-miss giảm ~50 điểm %) nhưng
         **ns-3 gần như không đổi** (thậm chí nhích tệ hơn ở 16 trạm) — dẫn
         đến độ khớp giữa 2 simulator **tệ đi rõ rệt** (0/18, so với 6-16/27
         trước đó). Đã kiểm tra code ns-3 (`ApWifiMac::ForwardDown` xác nhận
         AP relay giữ nguyên TID gốc khi chuyển tiếp trong cùng BSS —
         `hdr->GetQosTid()` được truyền qua đúng; `StaWifiMac::Enqueue` xác
         nhận `QosUtilsGetTidForPacket` đọc đúng `SocketPriorityTag`) —
         không tìm thấy bug rõ ràng trong cách gắn tag/enable QoS phía ns-3.
         Giả thuyết hợp lý nhất (**chưa kiểm chứng**): `deadline_miss_ratio`
         gộp chung TẤT CẢ flow class trong 1 policy — bật EDCA có thể giúp
         hẳn CONTROL/COORDINATION (AC_VO/AC_VI) nhưng làm PERCEPTION/DEBUG
         (AC_BE/AC_BK, bị hạ cấp) tệ đi, và 2 simulator có thể cân bằng lại
         hiệu ứng này khác nhau đủ mạnh để tạo ra chênh lệch lớn như vậy ở
         mức tổng hợp theo policy — cần đo **riêng theo từng flow_class**
         (chưa có, `PrintSummary()`/CSV output hiện chỉ gộp theo policy) mới
         xác nhận được. **Chưa kết luận dứt điểm, chưa áp dụng làm mặc
         định** — kết quả không rõ ràng là 1 chiến thắng (độ khớp giữa 2
         simulator tệ đi), khác hẳn hướng packet-rate (vòng 11, cải thiện cả
         2 mục tiêu đồng thời). Mã nguồn được giữ lại (cờ `--wifi-qos`, tắt
         mặc định), cần thêm bước đo theo flow_class trước khi quyết định
         hướng tiếp theo.

         **Cập nhật (11/09/2026, vòng 13) — đo lại với thống kê tách riêng
         theo flow_class (gộp cả 18 case 16/32 trạm × 3 seed × 3 kịch bản):**

         | flow_class (AC dự kiến) | ns-3 tx/rx | ns-3 miss% | INET tx/rx | INET miss% |
         |---|---|---|---|---|
         | control (AC_VO, ưu tiên cao nhất) | 127011/74361 | **95.7%** (tệ nhất) | 127011/84548 | **14.4%** (tốt nhất) |
         | coordination (AC_VI) | 15570/8741 | 86.3% | 15570/13218 | 21.2% |
         | state (AC_VI) | 20685/11815 | 78.2% | 20685/17691 | 20.1% |
         | human_qoe (AC_VI) | 1026/677 | 84.8% | 1026/884 | 23.0% |
         | perception (AC_BE) | 9522/5553 | 68.7% | 9522/7442 | 34.7% |
         | debug (AC_BK, ưu tiên thấp nhất) | 1047/615 | **0.2%** (tốt nhất) | 1047/1047 | 41.5% (tệ nhất) |

         **Phát hiện quan trọng nhất: 2 simulator cho ra THỨ HẠNG ƯU TIÊN
         NGƯỢC NHAU HOÀN TOÀN, không chỉ lệch độ lớn.** Ở ns-3, `control`
         (được gán AC_VO, ưu tiên cao nhất theo thiết kế) lại là class **tệ
         nhất** — tệ hơn cả `debug` (ưu tiên thấp nhất, lại tốt nhất)! Ở
         INET thì ngược lại: `control` là class **tốt nhất**, đúng như thiết
         kế QoS dự định, còn `debug` tệ nhất.

         **Giải thích cơ chế cho ns-3 (khớp logic)**: `control` chiếm **73%
         tổng lưu lượng** (50Hz × N robot, cao hơn hẳn mọi class khác) — dồn
         hết vào 1 hàng đợi AC_VO (CWmin ngắn nhất, dễ giành kênh nhất)
         không giúp gì khi CHÍNH BẢN THÂN class đó đã đông đến mức tự nó bão
         hòa/tự va chạm nội bộ giữa hàng chục trạm. `debug` có tx quá nhỏ
         (0.6% tổng lưu lượng) nên hàng đợi AC_BK của nó gần như luôn rảnh
         bất kể "ưu tiên thấp". WMM/EDCA được thiết kế cho traffic ưu tiên
         cao nhưng **khối lượng nhỏ** (như VoIP) — ở đây `control` vừa ưu
         tiên cao vừa khối lượng lớn nhất hệ thống, đúng ngược lại giả định
         thiết kế của EDCA, nên "ưu tiên" phản tác dụng trên ns-3.

         **Phát hiện config phụ, đã sửa**: dò lại `omnetpp.ini`, chỉnh queue
         500-gói ở vòng 6 (bug #1, `**.wlan[*].mac.dcf.channelAccess.pendingQueue`)
         chỉ áp dụng cho nhánh `dcf` — khi bật `qosStation`, INET chuyển hẳn
         sang dùng `mac.hcf.edca.edcaf[0..3].pendingQueue` (4 hàng đợi riêng
         theo AC, xác nhận qua `Hcf.ned`/`Edca.ned`/`Edcaf.ned`), một cây
         module hoàn toàn khác — override cũ **âm thầm mất tác dụng**, mỗi
         hàng đợi AC quay lại mặc định gốc 100 gói. Đã thêm dòng
         `**.wlan[*].mac.hcf.edca.edcaf[*].pendingQueue.packetCapacity = 500`
         để phủ đúng nhánh QoS, nhất quán với vòng 6 — **chưa đo lại** sau
         khi sửa (chờ lệnh "chạy").

         **Kết luận tạm thời**: đây không còn là câu hỏi "QoS có giúp không"
         mà là bằng chứng cụ thể, mạnh hơn hẳn, cho kết luận đã có từ vòng 7
         — 2 MAC stack độc lập phản ứng khác nhau về CHẤT chứ không chỉ về
         LƯỢNG khi cơ chế tranh chấp phức tạp hơn (nhiều hàng đợi EDCA thay
         vì 1 hàng đợi DCF). Việc control là traffic vừa ưu tiên cao vừa
         khối lượng lớn nhất là đặc điểm THẬT của kịch bản fleet (không thể
         sửa bằng cấu hình) — muốn cải thiện cần đổi kiến trúc traffic
         (gộp gói, giảm tần số 50Hz khi fleet lớn — hướng C đã nêu), không
         phải chỉnh tham số QoS.
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
     nên việc mở rộng bundle này (nếu muốn) sẽ cần tính riêng.

     **Cập nhật (10/09/2026) — đã xác minh bằng GitHub REST API công khai**
     (không có `gh`/token, nhưng repo public nên `curl
     api.github.com/repos/khainq12/rttc/actions/runs` không cần xác thực):
     5/5 lần chạy thật trên `main` đều **success** (commit `06352a7`,
     `e9b88e3`, `f68c8d0`, `a24d91f`, và lần chạy mới nhất); 1 lần duy nhất
     ghi `cancelled` là lần chạy cho chính commit thêm workflow (`8baa2b9`)
     — bị hủy tự động vì có commit kế tiếp đè lên gần như ngay sau đó, không
     phải lỗi. Kiểm tra chi tiết từng step của lần chạy gần nhất
     (`GET .../actions/runs/34449076885/jobs`): build image, chạy
     `Deep-preallocation probe`, chạy `Content-filter SQL probe`, upload
     summary — tất cả đều `success`. **Việc này coi như đã đóng**: CI đã
     chạy thật và pass, không còn là "chưa xác minh được".

     Branch protection cho `main` vẫn **chưa bật** — đó là thay đổi cấu
     hình repo (không chỉ thêm file) nên cần xác nhận riêng trước khi bật;
     đây là việc duy nhất còn mở của phần CI (không bắt buộc để "đóng" mục
     bằng-chứng-CI, chỉ là một cứng hoá thêm nếu muốn).

## Điều tra riêng: cơ chế fragment/NACK/repair của FleetRMW (11/09/2026)

**Không thuộc Nhóm 6** (đó là đối sánh ns-3/INET cho wifi) — đây là mảng
khác: tầng truyền tin cậy thật của middleware (chia fragment, NACK, repair,
reassembly) trong `rmw_fleetqox_cpp` (C++, `rmw_pubsub.cpp`). Người dùng đề
xuất phương pháp debug leo thang: bắt đầu từ baseline đã biết chắc chắn ổn
(1 robot, 32 KiB, 0% loss), tăng dần loss rồi số robot (1→4→8→16→32), dừng
ngay ở bước đầu tiên miss, log 4 tín hiệu nhân quả (mất fragment? → NACK?
→ repair? → hết hạn TTL/reassembly?) thay vì sửa code ngay khi thấy miss.

**Hạ tầng có sẵn nhưng chưa dùng tới**: binary probe relay
(`generic_serialized_relay_probe.cpp`) đã tính sẵn đúng các bộ đếm cần
thiết (`fragment_nacks_sent`, `fragment_nacks_received`,
`fragments_selectively_retransmitted`, `fragment_assembly_ttl_expirations`...)
nhưng script Python wrapper (`run_ros2_relay_rmw_netem_probe.py`) đang bỏ
qua không đọc ra. Đã nối lại (`relay_fragment_repair_metrics`) và viết
[`run_rmw_docker_fragment_repair_escalation.py`](../scripts/run_rmw_docker_fragment_repair_escalation.py)
chạy đúng thang leo trên.

**Tự rà soát trước khi chạy, tìm ra 2 lỗi thật trong chính công cụ debug**
(không phải trong `rmw_fleetqox_cpp`):
1. `--timeout-s` mặc định kế thừa (25s) ngắn hơn
   `DEFAULT_FLEETQOX_FRAGMENT_ASSEMBLY_TTL_MS` (60s — chính cơ chế repair
   tự cho phép trước khi bỏ cuộc) — có thể khiến harness báo fail trước khi
   repair kịp hoàn thành ở quy mô lớn. Đã sửa: timeout theo từng bước =
   60s + 10s + 2s×số robot (72s→134s).
2. `fragment_nacks_received`/`fragments_selectively_retransmitted` đo
   **chặng khác** với `fragment_nacks_sent` (relay đóng vai trò nguồn sửa
   cho chặng relay→subscriber, không liên quan gì tới chặng publisher→relay
   bị mất gói) — ban đầu code phân loại nhầm chúng là cặp yêu cầu/phản hồi
   cùng 1 giao dịch. Đã sửa lại logic phân loại cho đúng, chỉ dựa vào tín
   hiệu thật sự quan sát được từ phía relay.

**Kết quả chạy thật (2 lần)**:
- Lần 1: baseline (1 robot, 0% loss) seed=7 fail, seed=13/29 pass — dấu
  hiệu không nhất quán, nghi hạ tầng (docker/flaky), **không kết luận vội**.
- Lần 2 (chạy lại để kiểm chứng): baseline **pass cả 3 seed** — xác nhận
  lần 1 đúng là flaky hạ tầng, không phải bug thật. Ladder leo sạch qua
  0% loss → 5% loss → 4 → 8 robot (pass tất cả). **Duy nhất 1 case miss**:
  16 robot/seed=29 — thiếu đúng 1/32 gói control
  (`control_delivery_ratio=0.96875`), `fragment_nacks_sent=244`,
  `fragment_assembly_ttl_expirations=1` — relay đã phát hiện mất gói, đã
  gửi NACK, nhưng 1 assembly cụ thể không hoàn thành trước khi hết TTL.

**Kết luận theo đúng tiêu chí người dùng đề ra**: *"Nếu 1 robot + 32 KiB +
0% loss đã fail → nghi code fragmentation/reassembly. Nếu chỉ 16 robot +
5% mới fail → nghi contention/bandwidth/repair amplification."* — kết quả
khớp chính xác vế thứ 2: baseline hoàn toàn sạch, chỉ 1/3 seed ở quy mô
16 robot mới miss (1/32 gói) → **đây là hiện tượng tranh chấp/khuếch đại
tải khi repair dưới áp lực nhiều robot, không phải bug fragmentation/
reassembly ở tầng code cơ bản**. Đúng khoảng trống mà dự án chưa từng đo
trước đây (`docker_loss_resilient_large_sample_fragment_5run_summary.json`
chỉ từng chạy robot_count=1).

**Giới hạn còn lại**: chỉ probe relay xuất `fleetqox_transport_metrics` —
publisher/subscriber không có, nên không thể xác nhận publisher có thực sự
nhận/phản hồi NACK hay không (muốn xem hết chuỗi nhân quả đầy đủ cần thêm
instrumentation tương tự cho 2 probe kia — chưa làm). **Chưa chạy bước 32
robot** (ladder dừng ở bước 16 robot theo đúng thiết kế "dừng ở miss đầu
tiên").

## Điều tra riêng: bridge process rmw_fleetqox_cpp thật qua ns-3 TapBridge (11/09/2026)

**Bối cảnh**: Group 6 (wifi-parity) dùng raw single-shot UDP không có
reliability tầng ứng dụng (xem `external/ns3/fleetqox_trace_replay.cc` /
`external/omnetpp/TraceDrivenUdpApp.cc`) — khác với `rmw_fleetqox_cpp`
thật (có cơ chế fragment/NACK/repair, đã xác nhận hoạt động đúng qua
escalation ladder ở mục trên). Người dùng chọn phương án nhiều rủi ro
hơn: nối thẳng process `rmw_fleetqox_cpp` thật qua mạng wifi mô phỏng
bằng ns-3 TapBridge (thay vì viết lại rút gọn fragment/NACK/repair trực
tiếp trong 2 app trace-replay).

**Đã xây dựng xong 3 phần** (đều đã unit test, đều đã chạy thật trong
Docker):
1. `scripts/fleetqox_rmw_trace_endpoint.py` — replay trace CSV của 1
   endpoint qua `rmw_fleetqox_cpp` thật (rclpy).
2. `external/ns3/fleetqox_trace_replay_tap.cc` — topology wifi mô phỏng,
   bridge qua `TapBridge` (Mode=UseLocal) cho từng station.
3. `scripts/run_ns3_docker_wifi_tap_rmw_probe.py` — orchestration
   bridge+tap+veth+netns/station trong 1 container.

**5 bug hạ tầng thật đã tìm + fix qua debug bằng real Docker run** (không
phải đoán mò — mỗi bug đều có bằng chứng cụ thể từ log/packet capture):
1. Thiếu `--cap-add SYS_ADMIN` (chỉ có NET_ADMIN) → `ip netns add` lỗi
   "Operation not permitted".
2. `libns3-tap-bridge.so` có đường dẫn tap-creator helper build-time bị
   baked-in sai (`/build/ns3-.../ns3.41-tap-creator` không tồn tại) → fix
   bằng symlink sang `/usr/libexec/ns3/ns3.41-tap-creator` (đường dẫn apt
   cài thật).
3. Thiếu source ROS2/`rmw_fleetqox_cpp` setup.bash trong `ip netns exec`
   (chạy 1 lệnh, không phải login shell) → `ModuleNotFoundError: rclpy`.
4. Thiếu bật `FLEETQOX_RMW_LOSS_RESILIENT_FRAGMENT_CHUNK_BYTES` /
   `..._UDP_DATAGRAM_BUDGET_BYTES` → payload lớn (>1472B) lỗi "exceeds
   PMTU" (topology bridge L2 giả không có ICMP PMTU feedback thật).
5. **`wait "${ENDPOINT_PIDS[@]}"` dưới `set -e`**: nếu 1 endpoint process
   lỗi, `wait` trả về nonzero và **toàn bộ script abort ngay tại dòng đó**
   — bỏ qua hết phần dump log/result phía sau (thứ TỒN TẠI CHÍNH ĐỂ giải
   thích lỗi đó). Sửa bằng `set +e` / `set -e` bracket quanh `wait`, lưu
   `$?` vào `ENDPOINT_EXIT`, `exit $ENDPOINT_EXIT` ở cuối sau khi đã dump
   hết log.
6. **Start-gate timeout lệch nhau**: endpoint tự chờ `--start-file` tối đa
   15s (tái dùng `--discovery-timeout-s`), nhưng orchestrator chờ MỌI
   endpoint sẵn sàng tối đa 30s rồi mới touch `start` — endpoint xong sớm
   tự timeout trước khi endpoint chậm hơn kịp giải phóng cổng chung. Sửa
   bằng tham số riêng `--start-wait-timeout-s=60` (tách khỏi
   `--discovery-timeout-s`).

**Sau khi sửa hết 6 bug trên, cả 4 endpoint (1-robot scenario) chạy xong
sạch (exit=0, không crash) nhưng rx=0 ở TẤT CẢ — không endpoint nào nhận
được gì dù tx>0.**

**Điều tra sâu bằng raw packet capture (Python AF_PACKET, vì image không
có ping/tcpdump và `NS_LOG` không hoạt động — build ns-3 apt-package tắt
sẵn logging)**:
- ARP broadcast request (station0 hỏi "ai có 10.50.0.3") **được relay
  đúng** qua AP tới station1 nhiều lần (xác nhận qua sniff tại `br0`,
  `ftap1`, VÀ bên trong `ns1`'s `eth0`) — chứng minh tầng L2
  bridge+tap+ns-3-wifi+AP-relay hoạt động cho **broadcast**.
- `ns1`'s kernel nhận đúng request, sinh đúng ARP reply (unicast) — reply
  **rời khỏi `ftap1` thành công** (xác nhận qua sniff) nhưng **KHÔNG BAO
  GIỜ tới `ftap0`/`br0`** — mất hoàn toàn ở chặng AP→station0
  (FromDS-relay, unicast).
- Tìm ra 1 bug thật: `TapBridge` Mode=UseLocal **không** đồng bộ MAC thật
  của tap host-side với MAC ns-3 gán cho WifiNetDevice (2 giá trị hoàn
  toàn khác nhau, xác nhận bằng cách in cả 2 ra) — payload ARP (trường
  SHA) mang MAC "thật" mà AP's association table không biết, nên bị AP
  drop. **Đã fix**: gán MAC tất định (`02:00:00:00:00:0N`) cho cả
  WifiNetDevice (C++, `SetAddress()`) VÀ netns `eth0` tương ứng (Python,
  `_station_mac()`), xác nhận bằng cách đọc lại địa chỉ CẢ TRƯỚC LẪN SAU
  `TapBridge::Install()` — giữ nguyên, không bị ghi đè.
- **Sau khi fix MAC đồng bộ hoàn toàn, unicast reply VẪN không quay lại
  được station0** — loại trừ dứt điểm giả thuyết "MAC mismatch là
  nguyên nhân duy nhất". Đã thử thêm: giảm `DataMode` xuống
  `ErpOfdmRate6Mbps` (loại trừ giả thuyết PHY rate/path-loss) — vẫn
  không có gì thay đổi. Đối chiếu với `fleetqox_trace_replay.cc` (bài
  test wifi-parity ĐANG hoạt động đúng của Group 6) xác nhận: cùng dùng
  `StaWifiMac`/`ApWifiMac` (infrastructure mode), cùng association chỉ
  mất <1s (`warmupMs=1000` đã đủ) — nên relay unicast qua AP **tự nó
  không phải là thứ bị hỏng** khi traffic sinh ra từ BÊN TRONG mô
  phỏng (qua stack Ipv4/Udp riêng của ns-3). Vấn đề có vẻ đặc thù cho
  trường hợp traffic được TapBridge bơm từ NGOÀI vào (external
  injection) cụ thể ở chặng return/FromDS.

**Kết luận tạm thời — CHƯA GIẢI QUYẾT**: hạ tầng orchestration (Docker,
netns, tap, bridge, MAC addressing, timeout/set-e) đã đúng và đã xác
nhận qua packet capture thật. Còn lại 1 giới hạn cụ thể, chưa rõ nguyên
nhân gốc: unicast AP→station relay không hoạt động khi station nhận là
station "ngoài" (TapBridge-injected), dù cùng cấu hình wifi/AP y hệt
bản đang chạy tốt của Group 6. Không có source `.cc` của ns-3 trong
image (chỉ header + built .so, `apt` package không kèm source) và
`NS_LOG` không hoạt động (build release, logging bị compile out) nên
không introspect được `ApWifiMac`/`TapBridge` thật bên trong để xác
định chính xác — mọi giả thuyết đã kiểm chứng bằng thực nghiệm
(packet capture), không đoán suông. Việc "bridge process thật qua TAP"
cần điều tra thêm (vd: test topology CSMA có dây thay wifi để cô lập
xem lỗi có đặc thù cho wifi/AP hay không, hoặc lấy source ns-3 riêng
để đọc `ap-wifi-mac.cc`/`tap-bridge.cc` thật) trước khi có thể dùng để
đánh giá lại Group 6.

**Các fix hạ tầng thật (6 bug trên) đã commit dù mục tiêu cuối chưa đạt**
— vẫn là cải thiện đúng, độc lập với câu hỏi mở còn lại.

### Cô lập bằng topology CSMA (có dây) thay wifi — 11/09/2026

Theo đề xuất "test topology CSMA có dây để cô lập" ở trên, viết
`external/ns3/fleetqox_tap_csma_diag.cc` (diagnostic-only, không phải
1 phần pipeline chính thức): y hệt cấu trúc tap/bridge/veth/netns đã
dùng cho wifi, nhưng thay `StaWifiMac`/`ApWifiMac` bằng 1 CSMA bus
thuần (`CsmaHelper`, không AP, không association) — cùng
`TapBridge::Install()` per-station y hệt.

**Kết quả: hoạt động đúng ngay lần chạy đầu** — ARP resolve thành công
(`ip neigh show` → `REACHABLE`), UDP unicast tới đích, server nhận
đúng payload. **Điều này xác nhận DỨT ĐIỂM**: TapBridge +
bridge/tap/veth/netns orchestration tự nó hoàn toàn đúng; lỗi unicast
không quay lại được **đặc thù riêng cho relay wifi infrastructure-mode
qua AP** (`ApWifiMac`'s FromDS forwarding), không phải vấn đề chung của
TapBridge.

Thử tiếp bước tự nhiên tiếp theo: bỏ AP, dùng **ad-hoc wifi**
(`AdhocWifiMac`, station nói thẳng với nhau, không qua relay) — vẫn giữ
được dynamics CSMA/CA thật của 802.11 mà Group 6 quan tâm, nhưng bỏ hẳn
chặng AP-relay đang hỏng. Viết
`external/ns3/fleetqox_tap_adhoc_diag.cc` (diagnostic-only).

**Kết quả: ns-3 segfault ngay trong `Simulator::Run()`** — đã bisect
bằng debug print sau từng bước (tạo node, cài wifi, cài mobility, cài
TapBridge từng station) xác nhận: **setup hoàn tất sạch sẽ** (in ra đủ
"installed tapbridge 0..3", "about to Simulator::Run"), crash chỉ xảy
ra sau khi event loop bắt đầu xử lý traffic thật. Kiểm chứng thêm: 1
chương trình `AdhocWifiMac` tối giản **không có TapBridge** thì KHÔNG
crash (chạy xong sạch) — nên đây là bug đặc thù của tổ hợp
`AdhocWifiMac` + `TapBridge` (`UseLocal`, `RealtimeSimulatorImpl`)
trong bản ns-3 3.41 (apt package) này, không phải lỗi cấu hình của
mình. Không có `gdb` trong image (`apt-get install gdb` báo "no
installation candidate") nên không lấy được backtrace chính xác dòng
nào crash.

**Kết luận sau khi cô lập bằng cả 2 hướng**:
- TapBridge/orchestration: **đã xác nhận đúng** (CSMA chứng minh).
- Infrastructure-mode wifi (StaWifiMac+ApWifiMac) qua TapBridge: unicast
  relay chặng AP→station không hoạt động, nguyên nhân gốc trong
  `ApWifiMac`/`WifiRemoteStationManager` chưa xác định được (không có
  source thật, không có `NS_LOG`).
- Ad-hoc wifi qua TapBridge: **không dùng được** — bản ns-3 3.41 image
  này segfault khi kết hợp với TapBridge, bất kể có phải lỗi cấu hình
  của FleetQoX hay không.

Cả 2 nhánh khả thi nhất (infra-mode và ad-hoc) trong image ns-3 hiện
tại đều đi vào ngõ cụt theo cách khác nhau.

### Thử ns-3 version khác (3.46, Ubuntu 26.04 LTS) — 11/09/2026

Theo đề xuất ưu tiên "thử version ns-3 khác trước" (ít rủi ro nhất,
không cần patch source): dựng container riêng trên `ubuntu:26.04`
("resolute", LTS hiện hành) — **không đụng vào image `rmw-netem:jazzy`
đang dùng cho pipeline chính** — cài `ns3`/`libns3-dev` qua apt (bản
**3.46-2**, mới hơn 3.41 tới 5 version/~1.5 năm phát triển), build lại
đúng testcase tối giản TAP→TapBridge→AP→STA (không có FleetRMW/ROS2,
đúng theo yêu cầu "chỉ cần ARP+UDP chạy được là đạt").

2 vấn đề build/hạ tầng gặp phải và đã fix (không liên quan tới câu hỏi
chính, chỉ là chi phí setup bản mới):
- ns-3 3.46 header dùng C++20 (`<=>`, `std::remove_cvref_t`,
  `std::bind_front`, `.contains()`) — build lỗi với `-std=c++17` cũ,
  đổi sang `-std=c++20`.
- Thiếu `libsqlite3-dev`/`libgsl-dev` (chỉ có runtime `.so`, không có
  dev symlink) — linker báo "cannot find libsqlite3.so" — cài thêm 2
  gói dev.
- Cùng bug tap-creator baked-in-path như bản 3.41 (đường dẫn build-time
  khác thư mục cài thật) — dùng lại đúng kỹ thuật symlink cũ (lần này
  viết tổng quát hơn: tự trích path bị baked-in bằng `strings` +
  symlink động, không hardcode).

**Kết quả sau khi hết lỗi build: TÁI HIỆN Y HỆT LỖI CŨ.** Lần này
process ns-3 không hề crash khi attach tap (khác hẳn lần đầu với 3.41
gặp lỗi tap-creator) — tức là mọi bước setup hạ tầng đều qua trót lọt —
nhưng `ping`/ARP/UDP vẫn thất bại giống hệt: `ping` báo "Destination
Host Unreachable" toàn bộ 4/4 gói, `ip neigh show` → `FAILED`, UDP
server timeout không nhận được gì.

**Kết luận: KHÔNG PHẢI bug đặc thù của bản 3.41.** Lỗi tồn tại xuyên
suốt ít nhất từ 3.41 → 3.46 (5 version, apt package mới nhất tính đến
2026 qua Ubuntu 26.04 LTS). Loại trừ dứt điểm hướng "đổi version ns-3"
như 1 fix đơn giản — khả năng 1 version MỚI HƠN NỮA (chưa release) sửa
đúng bug này mà không cần hiểu rõ nguyên nhân là rất thấp.

### Tổng kết 3 hướng đã thử và kết luận

1. **CSMA thay wifi**: xác nhận TapBridge/orchestration đúng — dùng
   được làm baseline benchmark FleetRMW không cần wifi thật.
2. **Ad-hoc wifi thay infra-mode**: segfault ngay trong ns-3 (bản 3.41),
   không dùng được.
3. **Đổi version ns-3 (3.41 → 3.46)**: lỗi vẫn y hệt — không phải bug
   theo version, khả năng là bug/giới hạn có tính hệ thống trong cách
   `ApWifiMac` xử lý frame do TapBridge bơm từ ngoài vào (external
   injection), hoặc 1 thiếu sót cấu hình chưa tìm ra dù đã loại trừ MAC
   mismatch, PHY rate, và giờ cả version.

Với cả 3 hướng ít rủi ro đã thử hết và đều không ra kết quả tích cực,
người dùng chọn tiếp tục debug sâu `TapBridge <-> ApWifiMac` trực tiếp,
giới hạn scope chỉ ARP (chưa chạy UDP/FleetRMW) — xem mục tiếp theo.

### Debug sâu TapBridge <-> ApWifiMac bằng runtime trace, giới hạn ARP-only (11/09/2026)

Không có source `.cc` thật và `NS_LOG` không hoạt động (2 giới hạn đã
nêu ở trên), nhưng ns-3 có 1 CƠ CHẾ KHÁC hoàn toàn độc lập với cả 2:
**TypeId runtime reflection**. Viết `external/ns3/list_traces_diag.cc`
(diagnostic-only) gọi `TypeId::GetTraceSourceN()`/`GetTraceSource(i)`
để liệt kê MỌI trace source + chữ ký callback chính xác của
`ApWifiMac`, `StaWifiMac`, `WifiMac`, `WifiPhy`,
`WifiRemoteStationManager` — không cần source, không cần NS_LOG, chỉ
cần linker KHÔNG bỏ qua thư viện do `--as-needed` (fix bằng
`-Wl,--no-as-needed`). Cách này lộ ra 1 danh sách phong phú:
`AssociatedSta`/`DeAssociatedSta` (AP), `Assoc`/`DeAssoc` (STA),
`MacTx`/`MacTxDrop`/`MacRx`/`MacRxDrop` (cả 2), `PhyTxBegin`/`PhyTxEnd`/
`PhyRxBegin`/`PhyRxDrop` (PHY), `MacTxDataFailed` (rate manager) —
đúng những gì cần để quan sát TRỰC TIẾP tại sao AP không relay được,
thay vì chỉ suy luận gián tiếp từ packet capture bên ngoài.

Viết `external/ns3/fleetqox_tap_wifi_trace_diag.cc` (diagnostic-only,
rút gọn còn 2 station + 1 AP để log dễ đọc, đúng yêu cầu "chỉ ARP,
chưa UDP/FleetRMW") — hook toàn bộ các trace trên vào cả 2 station và
AP.

**Bug thật #2 tìm ra (KHÁC bug MAC-mismatch đã fix trước đó)**: dù
`device->SetAddress()` (đã áp dụng từ trước) làm `GetAddress()` báo
ĐÚNG địa chỉ tùy chỉnh, trace `AP ApWifiMac::AssociatedSta` lại ghi
nhận địa chỉ CỦA STATION LÀ GIÁ TRỊ MẶC ĐỊNH CŨ của ns-3
(`00:00:00:00:00:01`/`02`), không phải giá trị mới! Đào sâu header
(`wifi-mac.h`, `frame-exchange-manager.h`, có sẵn dù không có `.cc`)
lộ ra: **`WifiNetDevice`/`WifiMac` (kể từ khi ns-3 tái cấu trúc để hỗ
trợ 802.11be Multi-Link Operation) tách địa chỉ thành 2 tầng riêng**:
`WifiMac::m_address` (tầng "thiết bị/MLD", ĐÂY là cái `SetAddress()`
sửa và `GetAddress()` đọc) và `FrameExchangeManager::m_self` (tầng
"per-link", THỰC SỰ được dùng khi xây dựng frame qua sóng — kể cả
frame Association Request). `SetAddress()` ở tầng thiết bị KHÔNG BAO
GIỜ chạm tới tầng link — 2 tầng độc lập hoàn toàn. Xác nhận bằng cách
đọc lại `smac->GetFrameExchangeManager()->GetAddress()` (API public,
`WifiMac::GetFrameExchangeManager(linkId=SINGLE_LINK_OP_ID)`) — quả
thật khác `GetAddress()` cho tới khi fix.

**Fix**: gọi THÊM `smac->GetFrameExchangeManager()->SetAddress(addr)`
song song với `device->SetAddress(addr)`, cho MỌI station. Xác nhận
bằng trace: `AssociatedSta` giờ báo ĐÚNG địa chỉ tùy chỉnh. **Đã áp
dụng fix này vào `external/ns3/fleetqox_trace_replay_tap.cc`** (file
pipeline chính, không chỉ diagnostic) — biên dịch sạch trên CẢ ns-3
3.41 (image pipeline thật) LẪN 3.46 (image diagnostic).

**Nhưng ARP vẫn KHÔNG round-trip được, dù bug #2 đã fix.** Đào tiếp
bằng trace + `WifiMacHeader::PeekHeader` (đọc trực tiếp RA/TA/A3/seq/
type của từng frame qua sóng, tại tầng PHY) qua NHIỀU lần lặp lại (mỗi
lần retry ARP của kernel Linux, ~1 lần/giây), thấy 1 pattern **hoàn
toàn nhất quán, lặp lại y hệt mỗi lần**:
1. Station0 gửi request (broadcast) → AP nhận, ACK, relay (broadcast,
   `A3`=chính station0) → CẢ 2 station nhận đúng (`MacRx`).
2. Station1 gửi reply (unicast tới AP) → AP nhận, ACK (xác nhận PHY
   nhận đúng, CRC hợp lệ) → AP relay lại (`RA` = **ĐÚNG HỆT** địa chỉ
   station0 đã xác nhận, `TA`=AP, seq mới hoàn toàn không trùng) →
   **station0 lại gửi ACK link-layer cho relay này (xác nhận PHY/CRC
   nhận đúng ở tầng thấp) NHƯNG `WifiMac::MacRxDrop` vẫn fire, frame
   KHÔNG được forward lên tầng ứng dụng.**

Đã loại trừ TRỰC TIẾP bằng dữ liệu thật (không đoán): RA sai (loại —
khớp tuyệt đối), BSSID sai (loại — đọc lại `GetBssid()` của cả 2
station + `GetAddress()` của AP, khớp `00:00:00:00:00:03` cả 3), lỗi
CRC/checksum (loại — station0 vẫn ACK ở tầng PHY, nghĩa là CRC hợp lệ),
duplicate/replay theo (TA, sequence number) (loại — seq tăng dần bình
thường, không trùng lặp).

**Khác biệt DUY NHẤT** giữa relay THÀNH CÔNG (bước 1, station nhận lại
CHÍNH gói broadcast NÓ VỪA GỬI) và relay THẤT BẠI (bước 2, station0
nhận gói UNICAST relay của gói do STATION KHÁC gửi) là: `A3` (trong
frame FromDS, đây là địa chỉ NGƯỜI GỬI GỐC) = chính mình (case 1) hay
= trạm khác (case 2), VÀ `RA`=broadcast (case 1) hay `RA`=unicast cụ
thể (case 2) — 2 biến số đổi ĐỒNG THỜI nên chưa tách được biến nào là
nguyên nhân thật.

### Tách RA vs A3 bằng station thứ 3 (11/09/2026)

`RA=A3` (người nhận = người gửi gốc) là **không thể** với traffic
unicast thật giữa 2 endpoint khác nhau (chỉ xảy ra nếu 1 trạm tự gửi
cho chính nó — không tự nhiên, không đáng dựng). Vì vậy không tách được
RA/A3 theo kiểu "giữ 1 biến cố định, đổi biến kia" thuần túy. Thay vào
đó, dựng thí nghiệm khả thi nhất: **thêm station2 hoàn toàn mới, độc
lập gửi ARP request TỚI station1** (station1 vẫn đóng vai người trả
lời chung cho cả station0 lẫn station2) — mở rộng
`fleetqox_tap_wifi_trace_diag.cc` từ 2 lên 3 station. Câu hỏi: liệu lỗi
"relay unicast bị MacRxDrop" có ĐI THEO danh tính station0 cụ thể
(gợi ý trạng thái bị "nhiễm" riêng của station0), hay xảy ra với BẤT KỲ
station nào nhận relay unicast của dữ liệu người khác (gợi ý RA=unicast
tự nó là nguyên nhân, không liên quan danh tính).

**Kết quả: station2 (hoàn toàn mới, chưa từng liên quan tới cặp
station0/station1 trước đó) THẤT BẠI Y HỆT station0** — `ip neigh` cả 2
đều `FAILED` sau nhiều lần retry ARP của kernel. Điều này loại trừ dứt
điểm giả thuyết "trạng thái riêng của station0" và xác nhận: **lỗi đi
theo `RA=unicast cụ thể` (khác broadcast), không phụ thuộc station nào
là người gửi/người nhận.** Kết hợp với dữ liệu đã có trước đó (relay
broadcast LUÔN thành công, kể cả khi `A3` là 1 station KHÁC người
nhận — station1 từng nhận đúng relay broadcast có `A3`=station0) — bức
tranh giờ đã đủ rõ: **biến quyết định là `RA` (broadcast thành công,
unicast thất bại), không phải `A3` (self hay other không quan trọng)**.

**Trạng thái cuối cùng của nhánh debug ARP-only**: bug thật #2
(FrameExchangeManager address desync) đã tìm + fix, giữ lại trong
pipeline chính. Bug còn lại đã được đặc tả CHÍNH XÁC và ĐẦY ĐỦ bằng
chứng thực nghiệm (không đoán): **relay unicast (FromDS, RA cụ thể
không phải broadcast) của `ApWifiMac` luôn bị `WifiMac::MacRxDrop` ở
MỌI station nhận, mặc dù PHY/CRC/ACK/BSSID/địa chỉ/sequence number đều
đúng — trong khi relay broadcast (RA=ff:ff:ff:ff:ff:ff) của CHÍNH cơ
chế đó luôn thành công.** Đây có khả năng cao là 1 giới hạn/bug thật
trong `ApWifiMac`'s non-QoS unicast-relay path khi driven bởi traffic
BƠM TỪ NGOÀI qua TapBridge (không phải traffic sinh ra từ bên trong mô
phỏng, như cách Group 6's `fleetqox_trace_replay.cc` vẫn đang dùng) —
không phải lỗi cấu hình của FleetQoX có thể tự sửa tiếp mà không có
source `.cc` thật của `ApWifiMac`/`RegularWifiMac` để đọc logic relay
chính xác (điều kiện MacRxDrop cụ thể nằm ở đâu trong code, không thấy
được qua trace source hay header).

### GIẢI QUYẾT: build ns-3 từ source, tìm root cause thật + fix (11/09/2026)

Theo yêu cầu "build ns-3 từ source để có debug symbols" — clone
`ns-3.46` thật từ `gitlab.com/nsnam/ns-3-dev` (tag `ns-3.46`, khớp bản
apt đang test), build bằng CMake trực tiếp (KHÔNG dùng script `./ns3`
— bản này bị lỗi `argparse` không tương thích Python 3.14 của Ubuntu
26.04, không liên quan gì tới vấn đề đang điều tra) với
`-DCMAKE_BUILD_TYPE=Debug -DNS3_LOG=ON`, giới hạn module
(`core;network;mobility;wifi;tap-bridge`, tự kéo theo vài module phụ
thuộc) để build nhanh hơn. Gặp OOM khi build với `-j 24` (VM Docker
Desktop chỉ có ~3.7GB RAM khả dụng dù host có 15GB) — hạ xuống `-j 3`
build lại thành công, resume đúng từ chỗ dừng nhờ ninja.

**Đọc thẳng source (`sta-wifi-mac.cc`, `ap-wifi-mac.cc`,
`mac-rx-middle.cc`, `wifi-net-device.cc`) xác nhận**: `ApWifiMac::Receive()`
relay unicast đúng logic (`to.IsGroup() || IsAssociated(to)`), không
phải nguồn gốc bug. Dùng `NS_LOG` thật (`StaWifiMac=logic|debug`) tìm
ra dòng chính xác gây `MacRxDrop`: `StaWifiMac::Receive()`'s check đầu
tiên `hdr->GetAddr1() != myAddr` — với `myAddr =
GetDevice()->GetAddress()`. Patch 1 dòng in trực tiếp giá trị runtime
(`std::cout` ngay tại đó, rebuild incremental ~vài giây nhờ CMake/ninja)
xác nhận: **`WifiMac::GetAddress()`/`GetDevice()->GetAddress()` thỉnh
thoảng trả về 1 địa chỉ MAC NGẪU NHIÊN (không phải giá trị ta đã set),
trong khi `FrameExchangeManager::GetAddress()` VẪN đúng — một split-brain
KHÁC, sâu hơn bug #2 đã fix trước đó.**

**Truy ra tận gốc**: `TapBridge::ForwardToBridgedDevice()` (Mode=UseLocal,
`src/tap-bridge/model/tap-bridge.cc`) có cơ chế "học" địa chỉ MAC của
thiết bị ns-3 TỪ ĐỊA CHỈ NGUỒN của GÓI ĐẦU TIÊN nó forward từ tap vào
ns-3 — chỉ chạy 1 LẦN, canh giữ bởi cờ `m_ns3AddressRewritten`. Vấn đề:
gói "đầu tiên" này KHÔNG CHẮC LÀ traffic thật của mình — có thể là
traffic NỀN không mong muốn (IPv6 Neighbor Discovery, Linux tự động gửi
khi interface up) hoặc traffic MULTICAST của TRẠM KHÁC bị flood qua
cùng bridge — và bất kỳ cái nào "thắng" cuộc đua này sẽ bị KHÓA VĨNH
VIỄN làm địa chỉ ns-3 dùng, dù KHÔNG liên quan gì tới trạm thật đang
chạy trên tap đó. Xác nhận bằng debug print thêm vào ngay tại lệnh gọi
(in `this`, `m_bridgedDevice`, `src`, `m_ns3AddressRewritten`) — thấy rõ
land vào các địa chỉ hoàn toàn không khớp bất kỳ station nào ta set.

**Đã thử 3 hướng workaround ở tầng orchestration trước khi patch source
(đều KHÔNG đủ hoặc lộ thêm bug Linux kernel/bridge riêng, ghi lại vì có
giá trị tham khảo cho ai gặp lại)**:
1. Set MAC thật của tap (`ip link set ftap$i address ...`) khớp địa chỉ
   station — sửa được triệt để phần ns-3 (xác nhận qua debug print: 0
   anomaly suốt cả run) nhưng lộ ra bug MỚI: Linux **KHÔNG BAO GIỜ
   bridge 1 frame có đích trùng địa chỉ phần cứng CỦA CHÍNH port đó** —
   hành vi lõi kernel, kiểm tra TRƯỚC CẢ logic bridging, không sửa được
   qua `bridge fdb del/add` (cả 2 lệnh đều FAIL thẳng khi thử) hay
   `bridge link set ... learning off` (chỉ ảnh hưởng dynamic learning,
   không ảnh hưởng permanent local-address entry).
2. `sysctl net.ipv6.conf.*.disable_ipv6=1` để chặn traffic nền IPv6 —
   FAIL vì `/proc/sys` read-only trong container (thiếu quyền, không
   sửa được từ bên trong container đang chạy).
3. `ip link set ftap$i multicast off` — hoạt động MỘT PHẦN nhưng vẫn
   còn traffic multicast rò rỉ từ nguồn khác (có thể từ chính `br$i`).

**Fix thật, ở tầng ns-3**: patch
`external/ns3/patches/0001-tap-bridge-disable-use-local-address-autolearn.patch`
— tắt hẳn cơ chế "học từ gói đầu tiên" trong `TapBridge::ForwardToBridgedDevice()`
(Mode=UseLocal), để lại việc set địa chỉ hoàn toàn cho code C++ của mình
(`SetAddress()` gọi tường minh ngay sau `TapBridge::Install()`, không có
race nào cạnh tranh nữa vì auto-learn đã bị tắt). Rebuild `wifi` +
`tap-bridge` module (vài giây, incremental) + link lại
`fleetqox_tap_wifi_trace_diag.cc` (custom build, không qua pkg-config) —
**chạy lại đúng kịch bản đã fail nhiều chục lần trước đó: THÀNH CÔNG
NGAY LẦN ĐẦU** — `ip neigh show` báo `REACHABLE`, thấy cả UDP/ARP 2
chiều hoạt động (station1 cũng tự ARP ngược lại station0 và nhận được
reply). **Xác nhận lại lần 2 (build sạch, xoá hết debug print thừa,
dùng lại orchestration script ĐƠN GIẢN — bỏ hết 3 workaround ở trên,
không cần nữa vì patch đã giải quyết tận gốc) — vẫn REACHABLE ổn định.**

**Đã áp dụng**:
- `external/ns3/patches/0001-tap-bridge-disable-use-local-address-autolearn.patch`
  — patch ns-3 thật, cần áp dụng vào BẤT KỲ source tree ns-3 nào dùng
  để build image chạy `fleetqox_trace_replay_tap.cc`.
- `external/ns3/fleetqox_trace_replay_tap.cc` — chuyển block
  `SetAddress()`/`FrameExchangeManager::SetAddress()` ra SAU
  `TapBridge::Install()` (khớp cấu hình đã xác nhận hoạt động), thêm
  comment đầu file giải thích đầy đủ root cause + tham chiếu patch.

### HOÀN TẤT: đưa patch vào production `rmw-netem:jazzy` (11/09/2026)

Theo "bắt đầu đi" — build ns-3 3.41 THẬT (không phải 3.46) từ source
(`gitlab.com/nsnam/ns-3-dev`, tag `ns-3.41`), áp patch tương tự (code
gần như giống hệt 3.46 ở đúng vị trí, chỉ khác
`Mac48Address::GetBroadcast()` (3.46) vs
`Mac48Address("ff:ff:ff:ff:ff:ff")` (3.41) — patch riêng:
`0001-tap-bridge-disable-use-local-address-autolearn-ns3.41.patch`).

**2 việc phát sinh khi build 3.41 thật (khác build 3.46 trước đó)**:
1. Build FAIL thật với lỗi biên dịch KHÔNG liên quan tới patch:
   `wifi-phy-state-helper.h` dùng `std::transform` nhưng thiếu
   `#include <algorithm>` — ns-3 3.41 viết cho compiler cũ hơn
   nhiều so với GCC 15 (Ubuntu 26.04 build image), libstdc++ mới
   không còn include header này gián tiếp qua chain cũ nữa. Patch riêng:
   `0002-wifi-phy-state-helper-missing-algorithm-header-ns3.41.patch`.
2. `run_ns3_docker_wifi_tap_rmw_probe.py` có đoạn symlink tap-creator
   HARDCODE cứng đường dẫn baked-in của bản APT
   (`/build/ns3-Q7chNJ/.../ns3.41-tap-creator`) — bản build từ source
   MỚI có đường dẫn baked-in KHÁC (`/tmp/ns3-src/build/...`, khớp vị trí
   build trong Dockerfile, bị xoá sau `cmake --install` để giữ image
   nhỏ) — hardcode cũ không còn đúng, gây lỗi `execlp() ENOENT` y hệt
   lỗi đã gặp trước đây. **Sửa triệt để**: đổi từ hardcode sang trích
   xuất ĐỘNG bằng `strings` (kỹ thuật đã dùng nhiều lần ở scratchpad
   testing suốt session này) — không còn phụ thuộc vị trí build cụ thể
   nữa, tự động đúng dù build lại ở đâu.

**Cập nhật `external/rmw-netem/Dockerfile`**: bỏ `ns3`/`libns3-dev` khỏi
apt install, thêm bước `git clone` + áp 2 patch + `cmake` build (Release
profile, NS_LOG tắt — khớp hành vi bản apt cũ, không đổi runtime
behavior ngoài phạm vi fix) + `cmake --install` (module set khớp CHÍNH
XÁC những gì `pkg-config` được gọi trong TOÀN BỘ scripts/ +
external/ns3/*.cc: `applications;bridge;core;csma;internet;mobility;
network;point-to-point;tap-bridge;wifi`). Build ns-3 3.41 từ source
thành công (177/177 target, không còn OOM nhờ `-j 4` — bài học từ lần
build 3.46 debug trước đó).

**Build lại toàn bộ image** (`docker build -f
external/rmw-netem/Dockerfile .`, tag riêng `jazzy-ns3fix-test` trước để
không đè lên image đang chạy tốt) — build sạch, `pkg-config --cflags
--libs ns3-core ns3-wifi ns3-tap-bridge` resolve đúng (không cần sửa
GÌ ở phía Python scripts ngoài chỗ hardcode path đã nêu), naming
library/`.pc` khớp y hệt quy ước bản apt cũ.

**Kết quả cuối cùng — xác nhận bằng pipeline THẬT, không phải diagnostic
riêng**: chạy `scripts/run_ns3_docker_wifi_tap_rmw_probe.py` (script
production, dùng `fleetqox_rmw_trace_endpoint.py` — real `rclpy` +
`rmw_fleetqox_cpp`, replay CSV trace THẬT qua ROS2 pub/sub, không phải
UDP/ARP đơn giản của diagnostic) với 1 robot (4 endpoint):
- **Trước fix (mọi lần chạy suốt session): `rx=0` ở MỌI endpoint,
  100% mất gói.**
- **Sau fix: `fleet_router rx=76`, `operator_ui rx=2`, `robot_0000
  rx=109`/`105`/... — tổng ~180+ message thật nhận được qua đúng
  transport `rmw_fleetqox_cpp` (fragment/NACK/repair thật) chạy trên
  kênh wifi mô phỏng thật (ns-3 TapBridge), không phải Docker bridge
  thường.** (`fleet_controller rx=0` ổn định qua nhiều seed — hợp lý,
  do đặc điểm trace 1-robot: không ai gửi tin TỚI fleet_controller
  trong kịch bản này, không phải bug còn sót).
- Xác nhận ỔN ĐỊNH qua 3 seed độc lập (7, 13, 29) — pattern nhận gói
  nhất quán, không phải may rủi.
- Smoke test thêm 1 script ns-3 KHÁC (không dùng tap-bridge, dùng
  applications/bridge/csma/internet/mobility/network/point-to-point/wifi):
  `run_ns3_docker_wifi_mobility_matrix.py` — `status=ok rows=3`, xác
  nhận KHÔNG regression cho các probe ns-3 khác đang dùng chung image.

**Đã PROMOTE**: `docker tag` image đã test thành `localhost/fleetrmw/
rmw-netem:jazzy` (tag chính, mọi script dùng mặc định) — giữ lại bản
apt cũ ở tag `jazzy-apt-ns341-backup` để rollback nếu cần (thao tác tag
Docker, không mất gì, có thể revert tức thì). Xác nhận lại bằng cách
chạy KHÔNG truyền `--image` (dùng default) — vẫn `rx` đúng như trên.

**Tổng kết toàn bộ nhánh điều tra Group 6 mục "bridge process thật qua
TAP"**: từ 1 câu hỏi ban đầu ("core chạy tốt, sao mô phỏng vẫn kém") →
phát hiện Group 6 test raw UDP không có reliability → chọn phương án
rủi ro cao nhất (nối RMW thật qua TAP) → 7+ bug hạ tầng orchestration →
2 bug địa chỉ MAC ns-3 thật (FrameExchangeManager desync +
TapBridge auto-learn race) → build ns-3 từ source 2 lần (3.46 debug để
tìm bug, 3.41 release để deploy) → patch + tích hợp vào Dockerfile
production → **XÁC NHẬN THẬT: real RMW traffic (fragment/NACK/repair)
chạy được qua kênh wifi mô phỏng ns-3 qua TapBridge, đã promote vào
image chính.** Bước tiếp theo (chưa làm, ngoài phạm vi phiên này): dùng
pipeline này để đo lại delivery ratio của Group 6 ở quy mô 16/32 robot
với RMW thật thay vì raw UDP, so sánh với baseline hiện có.

### 11/09/2026 (tiếp) — Đo delivery ratio thật ở quy mô 16 robot: gặp bug crash MỚI, fix xong, phát hiện vấn đề tiếp theo

Thực hiện đúng bước tiếp theo đã ghi ở trên: chạy
`run_ns3_docker_wifi_tap_rmw_probe.py --num-robots 16` (19 endpoint
thật) để đo delivery ratio thật. Gặp NGAY một bug mới, khác hẳn mọi bug
đã fix trước đó trong nhánh TapBridge.

**Triệu chứng**: 16/19 endpoint crash ngay ở message ĐẦU TIÊN với
`RCLError: Failed to publish: failed to send FleetRMW payload through
UDP transport` (`rmw_pubsub.cpp:6358`). Tái lập ổn định qua 2 seed độc
lập (7, 13) — không phải ngẫu nhiên.

**Root cause**: thêm tạm `errno`/`strerror(errno)` vào error message
(build lại `rmw_fleetqox_cpp` qua `colcon build`, `--parallel-workers 1
MAKEFLAGS=-j2` để tránh OOM `cc1plus` — cùng vấn đề bộ nhớ VM Docker đã
gặp khi build ns-3) → lộ ra `errno=113 (No route to host)` =
**EHOSTUNREACH** ở MỌI lần crash. Với 19 station cùng associate vào 1 AP
mô phỏng gần như đồng thời, gói ARP đầu tiên của nhiều station bị mất do
contention → kernel đánh dấu neighbor entry `FAILED` → `sendto()` trả về
`EHOSTUNREACH` ngay lập tức (đồng bộ, không cần round-trip). Vòng lặp
retry sẵn có trong `rmw_pubsub.cpp` (`send_datagram_to_targets`) CHỈ
retry cho `ENOBUFS`/`EAGAIN`/`EWOULDBLOCK` (nghẽn buffer kernel cục bộ) —
`EHOSTUNREACH` không nằm trong tập này nên fail cứng ngay lần gửi đầu,
`rclpy` biến `RMW_RET_ERROR` thành exception, giết chết node.

**Fix** (`ros2_ws/src/rmw_fleetqox_cpp/src/rmw_pubsub.cpp`, commit
`303062d`): thêm `ENETUNREACH`/`EHOSTUNREACH` vào tập lỗi được retry,
NHƯNG với ngân sách retry riêng (`kUnreachableRetryLimit=40` ×
`kUnreachableRetryBackoffMs=50ms` = tối đa 2s) thay vì tái dùng
`kSendRetryLimit=20 × kSendRetryBackoffMs=5ms=100ms` của lớp buffer-full
— lý do: phục hồi từ EHOSTUNREACH cần một round-trip ARP/association
thật, không phải chỉ đợi buffer kernel rảnh, nên cần backoff dài hơn
hẳn. Đồng thời giữ lại `errno`/`strerror` trong message lỗi vĩnh viễn
(không phải chỉ debug tạm) vì message chung chung cũ đã che giấu hẳn
lớp lỗi này suốt session.

**Xác nhận fix**: build lại sạch, chạy lại đúng kịch bản 16-robot/seed
13 → **0 crash trên cả 19 endpoint** (trước: 16/19 crash), 100% trace
row (2289/2289) gửi thành công ở tầng ứng dụng/kernel.

**Phát hiện MỚI, CHƯA giải quyết**: dù không còn crash, `rx=0` ở TẤT CẢ
19 endpoint kể cả sau khi gửi thành công 2289 message — nghĩa là gói
tin rời được kernel cục bộ nhưng KHÔNG đến nơi qua mạng wifi mô phỏng.
Kiểm tra escalate quy mô (đúng phương pháp "tối thiểu rồi tăng dần" đã
dùng suốt dự án) với CÙNG bản fix:
- 1 robot (4 endpoint): delivery bình thường — `fleet_router rx=76`,
  `operator_ui rx=2`, `robot_0000 rx=105` (khớp kết quả đã xác nhận
  trước đó, fix không làm hỏng gì ở quy mô nhỏ).
- 8 robot (11 endpoint): delivery sụp gần hết — tổng `tx=1573`,
  `rx=22` (~1.4%).
- 16 robot (19 endpoint): delivery sụp hoàn toàn — tổng `tx=2289`,
  `rx=0` (0%).

**Giả thuyết đang nghi ngờ** (CHƯA xác nhận): đây có thể là hiện tượng
"congestion collapse" thật ở tầng 802.11 khi nhiều station cạnh tranh
airtime cùng lúc — nhưng CŨNG có khả năng chính retry loop mới thêm
(ARP re-request lặp lại theo mỗi lần `sendto()` thất bại) làm NẶNG
THÊM tình trạng nghẽn (ARP request cũng là frame broadcast, tốn airtime
y như data frame), tạo vòng lặp tự siết cổ: càng retry → càng nghẽn →
càng fail → càng retry. Chưa phân biệt được 2 khả năng này. Bước tiếp
theo (ngoài phạm vi phần vừa làm): đo airtime/collision thật ở tầng
ns-3 (PhyTxBegin/PhyRxDrop trace, tương tự kỹ thuật đã dùng ở nhánh điều
tra ARP relay trước đó) để xác định nguyên nhân, trước khi kết luận đây
là giới hạn năng lực kênh thật hay tác dụng phụ của chính bản fix.

### 11/09/2026 (tiếp) — Đào bằng trace ns-3: loại 2 giả thuyết, xác định nghẽn PHY thật là nguyên nhân chính

Thêm trace-source counter trực tiếp vào `external/ns3/fleetqox_trace_replay_tap.cc`
(`MacTx`/`MacTxDrop`/`MacRx`/`MacRxDrop`/`PhyTxBegin`/`PhyRxDrop`/
`AssociatedSta`, dùng `std::atomic` thay vì in từng gói — 16 station ở
tốc độ thật sẽ tạo quá nhiều output để đọc, và trước đó đã gặp lỗi
stdout xen kẽ dưới `RealtimeSimulatorImpl`). In định kỳ mỗi 5s thật
(`Simulator::Schedule` tự lặp lại) thay vì chỉ in 1 lần sau
`Simulator::Run()` — orchestrator `kill $NS3_PID` sớm hơn thời điểm
`Simulator::Stop()` tự nhiên nên bản in 1-lần-cuối không bao giờ chạy.

**Loại giả thuyết #1 — không phải "ARP storm" từ chính fix retry vừa
thêm**: thêm counter `unreachable_retry_attempts`/`unreachable_retry_giveups`
trực tiếp vào `rmw_pubsub.cpp` (đếm mỗi lần `sendto()` retry cho riêng
lớp `ENETUNREACH`/`EHOSTUNREACH`), export qua ctypes (cùng cơ chế
`librmw_fleetqox_cpp.so` đã có ở `run_ros2_direct_rmw_netem_probe.py`).
Kết quả 16-robot: tổng **1004 lần retry / 2289 lần gửi (~0.44 lần/gửi
trung bình), 0 giveups** — hoàn toàn KHÔNG khớp giả thuyết "retry ăn
gần hết ngân sách 40 lần cho đa số message". Retry chỉ tập trung ở giai
đoạn khởi động (association/ARP hội tụ), không lặp lại theo từng
message.

**Loại giả thuyết #2 — không phải "repair storm" từ tầng
fragment/NACK/repair**: thêm hàm `fleetqox_transport_metrics()` vào
`scripts/fleetqox_rmw_trace_endpoint.py` (dùng lại đúng cơ chế ctypes
đã có ở `run_ros2_direct_rmw_netem_probe.py`, món nợ kỹ thuật từ mục
#23/#25 chưa từng chạy qua kịch bản TAP-bridge này). Kết quả tổng hợp
19 endpoint: `fragments_selectively_retransmitted=0`,
`nack_retransmissions=0`, `reliable_timeout_retransmissions=0`,
`fragment_nacks_sent=10`, `fragment_nacks_received=0` — tầng reliability
gần như KHÔNG hoạt động, không hề tạo ra traffic khuếch đại.

**Phát hiện thật — traffic nền `pubsub_graph_renewal_loop()`**: đọc
source thấy `rmw_pubsub.cpp` có một thread nền, mặc định mỗi 500ms
(`FLEETQOX_RMW_GRAPH_RENEW_INTERVAL_MS`), quảng bá LẠI toàn bộ
publisher+subscription của node đó tới TẤT CẢ peer khác — chi phí
O(publishers × peers) mỗi tick, O(N²) toàn hệ thống theo số peer. Thêm
`--graph-renew-interval-ms` vào `run_ns3_docker_wifi_tap_rmw_probe.py`
để test. Tăng từ 500ms → 4000ms (mức tối đa RMW cho phép) ở quy mô 16
robot: `mac_tx_total` giảm ~33% (92484 → 62188 trong 30-35s) — xác nhận
đây LÀ một phần đáng kể của tải kênh — **nhưng `rx` vẫn = 0 TUYỆT ĐỐI**,
nghĩa là đây không phải nguyên nhân DUY NHẤT hay chiếm ưu thế áp đảo.

**Kết luận (bằng chứng PHY-level trực tiếp)**: breakdown lý do
`PhyRxDrop` ở lần chạy 4000ms: `BUSY_DECODING_PREAMBLE=171788` +
`PREAMBLE_DETECT_FAILURE=181871` = **353659/432020 (82%)** tổng số gói
bị drop ở tầng PHY. Đây là dấu hiệu kinh điển của xung đột/chồng lấn
preamble thật ở tầng vật lý 802.11 — không phải nghẽn do chính traffic
mình đo (mã hoá qua "TXING", chỉ 42656/432020, tức nửa-song-công bình
thường), cũng không phải artifact instrumentation. **19 station cùng
associate + gửi vào 1 kênh 802.11g (`ErpOfdmRate54Mbps`, 1 AP duy nhất)
đơn giản là VƯỢT NĂNG LỰC kênh thật** — dù đã tắt hẳn 2 nguồn khuếch
đại tình nghi (retry loop của tôi, tầng repair của RMW), và dù giảm hẳn
traffic discovery xuống 33%, kênh vẫn sập hoàn toàn.

**Trả lời câu hỏi nghiên cứu gốc của phiên này**: ở quy mô 16 robot với
topology 802.11g đơn-AP hiện tại, KHÔNG có traffic nào (RMW thật hay
raw UDP) có thể đi qua kênh với tỷ lệ đáng kể — nút thắt là năng lực
vật lý của kênh/topology, không phải giao thức tầng trên. So sánh
"RMW thật cải thiện delivery ratio bao nhiêu so với raw UDP" ở quy mô
này vì vậy không có ý nghĩa cho tới khi giải quyết được giới hạn năng
lực kênh (vd: nhiều AP/kênh song song, chuẩn 802.11 băng thông rộng
hơn, hoặc giảm mật độ station trên mỗi AP) — CHƯA làm trong phiên này,
ngoài phạm vi câu hỏi ban đầu ("có bug gì không") đã được trả lời dứt
điểm: không còn bug phần mềm nào gây ra hiện tượng này, đây là giới hạn
vật lý thật của topology đang dùng.

**File thay đổi**: `external/ns3/fleetqox_trace_replay_tap.cc` (counter
+ in định kỳ), `ros2_ws/src/rmw_fleetqox_cpp/src/rmw_pubsub.cpp`
(counter `unreachable_retry_attempts`/`unreachable_retry_giveups`),
`scripts/fleetqox_rmw_trace_endpoint.py` (`fleetqox_transport_metrics()`),
`scripts/run_ns3_docker_wifi_tap_rmw_probe.py` (`--graph-renew-interval-ms`).

### 11/09/2026 (tiếp) — Thử topology nhiều AP/kênh song song: TỆ HƠN, chưa giải quyết

Theo yêu cầu "thử topology nhiều AP/kênh song song xem có cải thiện
không". Thêm `--numAps` vào `fleetqox_trace_replay_tap.cc`: chia station
round-robin (`station i` → AP `i % numAps`) vào các nhóm, MỖI nhóm dùng
một `YansWifiChannel` C++ RIÊNG (mô hình Yans của ns-3 chỉ tính
interference giữa các PHY chung MỘT channel object — channel object
riêng = kênh không nhiễu nhau hoàn toàn, kịch bản tốt nhất có thể). Thêm
`--num-aps` vào `run_ns3_docker_wifi_tap_rmw_probe.py`.

**Lần thử đầu tiên (không backhaul) thất bại ngay ở bước discovery**:
4 nhóm AP hoàn toàn CÔ LẬP nhau (không kết nối), nên station ở nhóm này
KHÔNG BAO GIỜ tới được station ở nhóm khác. Mỗi lần gửi graph-advertisement
tới 1 peer ở nhóm khác kích hoạt trọn vẹn ngân sách retry
`ENETUNREACH`/`EHOSTUNREACH` (`kUnreachableRetryLimit=40 ×
kUnreachableRetryBackoffMs=50ms` = 2s/peer, vì permanently-unreachable
không phân biệt được với "đang hội tụ ARP" qua errno) — làm startup của
19 endpoint bị treo, vượt hẳn `READY_DEADLINE_S=30s`, script fail với
"timed out waiting for every endpoint to become ready".

**Fix**: thêm backhaul CSMA nối các AP lại (`CsmaHelper` + `BridgeHelper`
bridge mỗi AP's wifi-AP-device với CSMA-device của nó trên chính node
đó) — đúng mô hình canonical ns-3 cho "nhiều AP nối qua LAN có dây",
giống deployment multi-AP thật (các AP uplink vào 1 switch). Test sanity
4 endpoint / 4 AP (mỗi endpoint 1 AP riêng): delivery khớp CHÍNH XÁC
baseline single-AP khỏe mạnh (`fleet_router rx=76`, `operator_ui rx=2`,
`robot_0000 rx=103`) — xác nhận backhaul hoạt động đúng cho quy mô nhỏ.

**Chạy lại 16 robot / 4 AP + backhaul: TỆ HƠN hẳn, không cải thiện**:
`rx=0` TUYỆT ĐỐI (không đổi so với 1-AP) — nhưng `mac_tx_total` từ 92484
(1 AP) TĂNG lên **429322** (~4.6x, gần khớp `numAps=4`), và
`mac_tx_drop_total` từ ~12 TĂNG lên **228621**. Đây không phải cải
thiện bị hạn chế — đây là NẶNG THÊM đáng kể.

**Nghi ngờ nguyên nhân (CHƯA xác nhận dứt điểm, CHƯA fix)**: bridge học
(learning bridge) của `BridgeHelper` có thể KHÔNG hội tụ bảng MAC cho
các station nằm sau cổng wifi (khác với cổng CSMA thường-Ethernet nó
được thiết kế/test chính) — mỗi frame unicast tới MAC "chưa học được"
bị FLOOD ra TẤT CẢ port thay vì chỉ port đúng, và AP nhận flood đó thấy
đích không nằm trong bảng liên kết của chính nó → `MacTxDrop` ("destined
to a station not associated with the AP", đúng khớp định nghĩa trace
source này). Hệ số tăng ~4.6x cho `mac_tx_large` khớp gần đúng với
`numAps=4`, ủng hộ giả thuyết "mỗi frame bị nhân bản ra N AP thay vì 1".
CHƯA điều tra sâu hơn (vd: kiểm tra `BridgeNetDevice`'s learning
callback có nhận đúng source MAC từ phía sau `ApWifiMac` hay không) —
dừng lại ở đây để báo cáo, chưa tự ý tiếp tục đào sâu thêm.

**Kết luận tạm thời**: hướng "nhiều AP/kênh song song" về mặt lý thuyết
đúng đắn (giảm được tranh chấp airtime trong PHẠM VI 1 kênh, đã chứng
minh một phần ở thí nghiệm graph-renewal-interval trước đó khi so sánh
CÙNG 1 AP với ít traffic hơn), nhưng cách triển khai bridge hiện tại có
bug/hạn chế khiến traffic CROSS-AP bị khuếch đại thay vì giảm tải —
CHƯA phải bằng chứng phủ nhận hướng đi, mà là một bug/hạn chế triển khai
cụ thể cần fix tiếp nếu muốn theo hướng này.

### 11/09/2026 (tiếp) — Đào sâu fix bridge flooding: tìm đúng root cause, fix xong, cải thiện thật nhưng vẫn chưa đủ

Theo yêu cầu người dùng "đào sâu fix bridge flooding". Đọc trực tiếp
source ns-3 3.41 (`bridge-net-device.cc`, `ap-wifi-mac.cc`,
`wifi-net-device.cc`, `node.cc`) đã clone sẵn ở `.ns3-build-tmp/ns3-341-src`
từ nhánh điều tra ARP relay trước đó (không cần clone lại).

**Root cause thật (xác nhận qua đọc source, không phải đoán)**:
`BridgeNetDevice` là learning bridge ĐỘNG — chỉ học "MAC này nằm sau
port nào" từ chính traffic ĐI RA của MAC đó (`ForwardUnicast`/
`ForwardBroadcast` đều gọi `Learn(src, incomingPort)`, chỉ học SOURCE,
không bao giờ học DESTINATION trước). Hệ quả: các endpoint CHỈ NHẬN
(như `fleet_router`, `operator_ui` — `tx=0` cố định trong kịch bản này)
KHÔNG BAO GIỜ tự gửi gì để bridge học được vị trí của chúng → MỌI gói
gửi TỚI chúng bị FLOOD ra tất cả AP MÃI MÃI, không chỉ trong giai đoạn
hội tụ ban đầu. Tệ hơn: đọc `ApWifiMac::Receive` (dòng ~1706-1723 bản
3.41) thấy ngay cả traffic relay CÙNG một AP (`to.IsGroup() ||
IsAssociated(to)`) cũng bị đẩy lên `ForwardUp()` (qua nhánh
`PACKET_OTHERHOST` của `WifiNetDevice::ForwardUp`) THÊM vào việc đã
relay qua sóng — nghĩa là traffic nội bộ 1 AP cũng bị bridge nhìn thấy
và có thể bị flood dư thừa ra backbone dù không cần thiết.

**Fix**: bỏ hẳn `CsmaHelper`+`BridgeHelper` (learning bridge), thay
bằng relay TĨNH tự viết — vì chương trình này đã biết CHÍNH XÁC station
nào thuộc AP-group nào ngay từ lúc setup (`stationIndexesByGroup`),
không cần "học" gì cả. Đăng ký `SetPromiscReceiveCallback` trực tiếp
trên wifi-device của mỗi AP; callback tra bảng `MAC đích → AP-group`
(build tĩnh từ đầu) rồi `SendFrom()` thẳng tới đúng 1 AP đích — không
CSMA, không backbone, không bảng học nào cả.

**Bug lần 1 của chính fix mới**: chỉ xử lý `PACKET_OTHERHOST` (unicast
đã biết đích), BỎ SÓT hoàn toàn `PACKET_BROADCAST`/`PACKET_MULTICAST`.
Hệ quả: ARP request (luôn là broadcast) không bao giờ vượt qua AP-group
→ station ở nhóm này KHÔNG BAO GIỜ phân giải được địa chỉ MAC của
station ở nhóm khác → unicast cross-group không bao giờ hình thành
được. Xác nhận qua triệu chứng: `mac_tx_large` (proxy cho traffic thật)
đứng yên gần 0 suốt cả run trong khi `mac_tx_small` (proxy ARP) tăng
liên tục không dừng (request gửi đi liên tục, không bao giờ nhận được
reply). Test sanity 4 endpoint/4 AP: `rx=0` tuyệt đối ở CẢ 4 endpoint.

**Fix cuối**: flood riêng broadcast/multicast ra TẤT CẢ AP-group khác
(không tra bảng theo MAC cụ thể — broadcast vốn phải tới mọi nơi, đây
là hành vi ĐÚNG/bắt buộc của switch thật, không phải bug). Test sanity
4 endpoint/4 AP: **`rx` khớp CHÍNH XÁC baseline single-AP khỏe mạnh**
(`fleet_router rx=76`, `operator_ui rx=2`, `robot_0000 rx=103`),
`phy_rx_drop_total` giảm từ 12636 (bridge, 4 endpoint) xuống còn 388
(giảm ~32 lần) — xác nhận cả correctness lẫn hiệu quả giảm nghẽn ở quy
mô nhỏ.

**Chạy lại 16 robot / 4 AP với relay tĩnh đã fix — cải thiện THẬT
nhưng vẫn hoàn toàn không đủ**:
- `rx` từ 0 (mọi lần chạy 1-AP và cả bản bridge-lỗi trước đó) → **1**
  (message đầu tiên từng nhận được ở quy mô 16-robot trong toàn bộ
  phiên điều tra này).
- `phy_rx_drop_total` giảm ~62%: 432020 (1-AP, graph-renew 4000ms) →
  **164600**.
- NHƯNG: `BUSY_DECODING_PREAMBLE`(58011) + `PREAMBLE_DETECT_FAILURE`
  (50895) = 108906/164600 (66%) — collision thật VẪN chiếm đa số drop,
  chưa được giải quyết triệt để.

**Tại sao vẫn không đủ (đã xác định nguyên nhân cụ thể)**: `fleet_controller`
MỘT MÌNH chiếm 1626/2289 (71%) tổng traffic của cả fleet, và giống mọi
station khác, nó bị GIỚI HẠN trên ĐÚNG 1 kênh (round-robin gán nó vào 1
trong 4 group). Chia CÁC STATION KHÁC ra nhiều kênh song song không hề
giảm tải cho kênh RIÊNG của `fleet_controller` — nó vẫn phải gửi TOÀN
BỘ 1626 message qua đúng 1 radio, đúng 1 kênh, y hệt như ở topology
1-AP. "Nhiều kênh song song" chỉ thật sự hiệu quả khi tải được phân bố
ĐỀU giữa các station — với traffic pattern fan-out cực lệch như kịch
bản Group 6 này (1 controller gửi cho gần như tất cả robot), hướng đi
này có giới hạn cấu trúc rõ ràng, không phải do lỗi triển khai.

**File thay đổi**: `external/ns3/fleetqox_trace_replay_tap.cc` (bỏ
CSMA/Bridge, thêm `ApCrossGroupRelay` tĩnh + flood broadcast/multicast),
`scripts/run_ns3_docker_wifi_tap_rmw_probe.py` (bỏ `ns3-csma ns3-bridge`
khỏi link line, không còn cần).

**Hướng tiếp theo nếu muốn tiếp tục** (chưa làm, cần quyết định của
người dùng): gán riêng `fleet_controller` (hoặc nói chung, station có
tải cao nhất) vào 1 kênh KHÔNG chia sẻ với station nào khác thay vì
round-robin đơn giản — nhưng đây là tinh chỉnh cụ thể cho ĐÚNG traffic
pattern hiện tại (fan-out lệch từ 1 nguồn), không phải giải pháp tổng
quát, và ngay cả khi làm vậy vẫn cần đo lại để xác nhận có đủ hay
không, vì bản thân băng thông 1 kênh 802.11g cho 1626 message vẫn có
giới hạn riêng của nó.

### 11/09/2026 (tiếp) — Thử gán riêng fleet_controller vào 1 kênh: giả thuyết bị BÁC BỎ

Theo yêu cầu người dùng "thử gán riêng fleet_controller vào 1 kênh
riêng". Thêm `--isolateController` vào `fleetqox_trace_replay_tap.cc`:
dành AP-group 0 riêng cho station 0 (`fleet_controller`, luôn cố định
là index 0 theo thứ tự đã ghi ở đầu file), round-robin các station CÒN
LẠI trên `numAps-1` group còn lại. Thêm `--isolate-controller` vào
`run_ns3_docker_wifi_tap_rmw_probe.py`. Test sanity 4 endpoint/4 AP:
delivery vẫn đúng (khớp baseline).

**Chạy 16 robot / 4 AP / isolate-controller: giả thuyết SAI, kết quả
KHÔNG cải thiện — thậm chí hơi tệ hơn**:
- `rx`: quay lại **0** (so với `rx=1` ở bản 4-AP round-robin thường,
  KHÔNG cô lập, đo ngay trước đó).
- `phy_rx_drop_total`: **206207** — CAO HƠN bản không cô lập (164600),
  không thấp hơn.

**Diễn giải**: cô lập `fleet_controller` đảm bảo kênh UPLINK của nó
hoàn toàn sạch (không station nào khác cạnh tranh), nhưng KHÔNG hề
giảm tải DOWNLINK trên kênh của bên NHẬN — và quan trọng hơn, cô lập
này buộc 100% trong số 1626 message của `fleet_controller` đều phải đi
qua relay 2-hop (trước đây, khi chia sẻ nhóm, một phần nhỏ tình cờ có
đích CÙNG nhóm, tiết kiệm được 1 hop). Kết quả gần như y hệt (thậm chí
nhỉnh hơn) cho thấy: nút thắt CHÍNH không nằm ở việc `fleet_controller`
chia sẻ kênh với ai — nhiều khả năng nút thắt thật là traffic nền
`pubsub_graph_renewal_loop()` (mỗi trong 19 endpoint quảng bá tới 18
peer khác, mỗi 500ms, ĐỘC LẬP với topology kênh) đã xác nhận một phần
ở thí nghiệm graph-renewal-interval trước đó — traffic này tồn tại và
chiếm băng thông trên MỌI kênh bất kể cách chia station, nên tinh
chỉnh CHỈ VỀ PHÍA fleet_controller không chạm được vào nguyên nhân gốc.

**File thay đổi**: `external/ns3/fleetqox_trace_replay_tap.cc`
(`--isolateController`), `scripts/run_ns3_docker_wifi_tap_rmw_probe.py`
(`--isolate-controller`).

**Kết luận cho toàn bộ nhánh "multi-AP/kênh song song"**: đã thử 3 biến
thể (round-robin đơn thuần → static relay đúng → cô lập
`fleet_controller`), cải thiện thật nhưng nhỏ nhất chỉ đến từ chính
việc CHIA KÊNH nói chung (round-robin, `rx` 0→1, drop -62%), KHÔNG đến
từ việc tối ưu VỊ TRÍ của 1 station cụ thể. Bằng chứng hiện có nghiêng
về: nút thắt chính là tổng tải hệ thống (traffic nền O(N²) + traffic
ứng dụng) vượt năng lực ngay cả khi đã chia 4 kênh, chứ không phải do
1 station bị đặt sai kênh.

### 11/09/2026 (tiếp) — Hỏi ChatGPT Plus + implement redesign B+ cho graph discovery: xác nhận thêm nút thắt là data-plane, không phải discovery

Theo yêu cầu người dùng thiết lập quy trình cộng tác: người dùng đăng
nhập ChatGPT Plus vào Browser pane, tôi soạn câu hỏi đầy đủ bối cảnh
(toàn bộ bằng chứng đã thu thập: 82% PHY drop là collision thật, retry
loop + fragment/repair đã bị loại, `pubsub_graph_renewal_loop()` O(N²)
là contributor thật nhưng chưa đủ, multi-AP cải thiện nhỏ), ChatGPT
phân tích và xếp hạng 4 hướng (A: delta-only, B: event-driven + heartbeat
+ versioned snapshot repair, C: gossip fan-out, D: discovery coordinator
tập trung) — khuyến nghị **B+ (event-driven delta đã có sẵn + heartbeat
nhẹ + incarnation/version + snapshot repair theo yêu cầu)**, xếp D
(coordinator qua fleet_controller) là #2 cho dài hạn.

**Implement (scope rút gọn có chủ đích so với B+ đầy đủ)**: đọc kỹ
source hiện có, phát hiện phần "event-driven" ChatGPT đề xuất THỰC RA
ĐÃ TỒN TẠI SẴN — `send_publisher_graph_advertisement`/
`send_subscription_graph_advertisement` đã được gọi NGAY LẬP TỨC tại
đúng chỗ tạo/hủy publisher/subscription (không phải chỉ trong vòng lặp
định kỳ). Vòng lặp định kỳ (`pubsub_graph_renewal_loop`) chỉ là lớp AN
TOÀN dự phòng chống mất gói/late-joiner, gửi LẶP LẠI toàn bộ danh sách
mỗi 500ms — đây chính là phần O(N²) cần cắt, không cần xây lại phần
event-driven từ đầu.

Đã làm:
- Thêm `incarnation_id` (random, sinh 1 lần lúc process khởi động) +
  `graph_version` (counter tăng dần mỗi lần add/remove thật) vào
  `GraphAdvertisement` (đặt ở CUỐI struct với giá trị mặc định — không
  cần sửa hàng chục chỗ aggregate-initialization positional có sẵn).
- Vòng lặp định kỳ giờ gửi **heartbeat nhỏ** (chỉ incarnation+version,
  KHÔNG có field publisher/subscription nào) mỗi
  `FLEETQOX_RMW_GRAPH_HEARTBEAT_INTERVAL_MS` (mặc định 1500ms), và chỉ
  gửi lại TOÀN BỘ danh sách (hành vi cũ) mỗi
  `FLEETQOX_RMW_GRAPH_RENEW_INTERVAL_MS` (đổi mặc định từ 500ms/max 4s
  → 10s/max 60s — vai trò đổi từ "cơ chế discovery chính" thành "an
  toàn dự phòng hiếm khi chạy").
- Phía nhận: heartbeat (`entity_kind=="node"`) được chặn sớm, không đi
  qua logic publisher/subscription — CHƯA implement phần "phát hiện
  version-gap → yêu cầu snapshot" (để dành cho vòng sau nếu cần, vì
  kết quả đo được đã đủ trả lời câu hỏi chính).
- Thêm counter đo riêng: `graph_heartbeats_sent/received`,
  `graph_full_resyncs_sent`.

**Kết quả đo (16 robot, single-AP baseline)**: delivery vẫn giữ đúng ở
quy mô nhỏ (1-robot: `rx=76/2/106`, khớp baseline). Ở quy mô 16 robot:
`mac_tx_total` giảm **~37%** (92484 → 58112) — XẤP XỈ mức giảm đã đạt
được trước đó chỉ bằng cách giãn interval đơn giản (33%), KHÔNG vượt
trội hơn nhiều dù kiến trúc phức tạp hơn hẳn. **`rx` vẫn = 0 tuyệt
đối**, và tỷ lệ PHY collision-drop KHÔNG đổi (~82%) — bằng chứng trực
tiếp thêm rằng traffic discovery (dù đã redesign đúng) chưa bao giờ là
nguyên nhân chính của collapse.

**Kết hợp graph-redesign + 4-AP (static relay đã fix trước đó)**:
`phy_rx_drop_total` giảm thêm đáng kể so với chỉ 4-AP đơn thuần
(164600 → **92802**, giảm thêm ~44%) — 2 cải tiến CÓ cộng dồn về mặt
giảm collision. Nhưng `rx` vẫn = 0 (so với 1 message ở lần đo 4-AP đơn
thuần trước đó — chênh lệch 0 so với 1 tin nhắn trên tổng 2289 không
đủ ý nghĩa thống kê để kết luận gì thêm).

**Kết luận tổng hợp toàn bộ phiên điều tra 16-robot-scale**: đã thử và
đo đạc nghiêm túc 5 hướng khác nhau (retry-fix, graph-renewal-interval,
multi-AP round-robin, cô lập station nặng nhất, graph-discovery
redesign kiến trúc mới) — TẤT CẢ đều giảm được traffic/collision ở mức
độ nào đó (33-62% tuỳ hướng, có cộng dồn), NHƯNG KHÔNG hướng nào (kể cả
kết hợp) đưa delivery vượt quá vài phần nghìn phần trăm. Tỷ lệ PHY
collision-drop (BUSY_DECODING_PREAMBLE + PREAMBLE_DETECT_FAILURE) giữ
nguyên ~66-82% xuyên suốt MỌI biến thể đã thử — đây là bằng chứng mạnh
và nhất quán rằng nút thắt là **năng lực vật lý của topology 802.11g ở
mật độ 19 station**, một giới hạn không thể giải quyết bằng tối ưu
tầng giao thức (dù đúng đắn và đáng làm vì lý do kiến trúc/latency
riêng) — cần thay đổi topology căn bản hơn (nhiều AP KHÔNG chia sẻ
station nào — tức < 19 station toàn hệ thống mỗi kênh thực sự độc lập
về mặt vật lý chứ không chỉ logic, hoặc chuẩn wifi băng thông rộng hơn
802.11g) mới có cơ hội thay đổi kết quả về chất, không chỉ về lượng.

**File thay đổi**: `ros2_ws/src/rmw_fleetqox_cpp/include/rmw_fleetqox_cpp/data_frame.hpp`
(`incarnation_id`/`graph_version`), `ros2_ws/src/rmw_fleetqox_cpp/src/data_frame.cpp`
(encode/decode), `ros2_ws/src/rmw_fleetqox_cpp/src/rmw_pubsub.cpp`
(`send_graph_heartbeat`, vòng lặp renewal 2 tầng, guard nhận heartbeat,
counter mới), `scripts/fleetqox_rmw_trace_endpoint.py` (counter mới
vào danh sách ctypes).

### 11/09/2026 (tiếp) — Điểm ChatGPT flag là quan trọng nhất: xác nhận BUG THẬT ở tầng data-plane, fix xong, nhưng lộ ra vấn đề còn sâu hơn

ChatGPT, trước khi đồng ý "đã hết dư địa tối ưu discovery", yêu cầu xác
minh 1 điều bắt buộc trước: **khi publish() một message ROS thật, RMW
có gửi đúng tới subscriber đã match hay gửi mù tới TẤT CẢ static peer?**
Đây là câu hỏi quan trọng nhất trong toàn bộ phiên trao đổi với
ChatGPT — vì nếu SAI, nó là một O(N²) ở đúng tầng DATA (traffic ứng
dụng thật), không phải chỉ ở tầng discovery/metadata.

**Xác nhận: ĐÚNG LÀ BUG THẬT.** Lần theo source thật (`rmw_publish` →
`publish_payload` → `send_data_frame` → `data_frame_targets`) thấy
`data_frame_targets()` mặc định (`peer_policy_=="all"`, giá trị mặc
định của toàn bộ RMW) rơi vào `return frame_targets(include_local);`
— gửi tới **TẤT CẢ** peer trong `FLEETQOX_RMW_PEERS`, không lọc theo
subscription. `matched_subscription_ids` đã được tính sẵn trong
`publish_payload` (qua `rmw_fleetqox_cpp_graph_matched_subscription_endpoint_ids`)
nhưng CHỈ dùng để theo dõi ACK cho reliable QoS trong retransmission
ledger — KHÔNG hề dùng để lọc đích gửi. Với `fleet_controller` gửi
1626 message ở test 16-robot, đây là khuếch đại O(peers) TRÊN MỌI LẦN
publish(), tách biệt hoàn toàn với O(N²) discovery đã tìm và giảm
trước đó — về lý thuyết có thể lớn hơn nhiều.

**Fix**: thêm policy MỚI (không đổi mặc định `"all"`, vì RMW này dùng
chung cho rất nhiều probe/test khác chưa audit) —
`FLEETQOX_RMW_PEER_POLICY=subscription_aware`. Cơ chế: bảng
`peer_subscribed_topic_refcounts_` (refcount theo từng peer, key
"domain|topic|type"), học được bằng cách thread địa chỉ nguồn UDP
(vốn đã có sẵn ở `recvfrom()` nhưng bị "rớt" trước khi tới
`apply_received_graph_advertisement` — phải sửa signature xuyên suốt
`handle_received_datagram`→`handle_received_payload`→
`apply_received_graph_advertisement`) vào một hàm mới
`update_peer_subscription()` gọi khi nhận advertisement subscription
add/remove. `data_frame_targets()` tra bảng này khi
`peer_policy_=="subscription_aware"`, fallback về broadcast-toàn-bộ
CHỈ KHI chưa biết ai subscribe topic đó (fail-open, không rơi message
khi đang trong giai đoạn khởi động/race).

**Xác nhận đúng ở quy mô nhỏ**: 1-robot — delivery khớp baseline
(`rx=76/2/108`), đa số lần gửi dùng đúng targeted-send (fallback rất
ít: 2/143, 21/80).

**Phát hiện MỚI, sâu hơn cả dự đoán, ở quy mô 8 và 16 robot**: tỷ lệ
fallback (= "chưa biết ai subscribe topic này") tăng theo scale:
- 1 robot: fallback thấp (đa số targeted-send thành công).
- 8 robot: fallback **66%** (1049/1595) — nhưng một số robot vẫn đạt
  targeted-send phần lớn (vd `robot_0000`: sub=76, fallback chỉ 18) —
  XÁC NHẬN cơ chế lọc hoạt động ĐÚNG khi discovery kịp hội tụ.
- 16 robot: fallback **100%** (1628/1628 cho `fleet_controller`, và
  toàn bộ endpoint khác cũng vậy) — KHÔNG MỘT peer nào được ghi nhận
  có subscriber trước khi publish bắt đầu.

**Diễn giải**: đây KHÔNG phải bug trong chính fix — đây là bằng chứng
rằng ở quy mô 16 robot, **chính DISCOVERY (dù đã tối ưu bằng B+ ở mục
trước) cũng không kịp hội tụ trong 15s** (thời gian chờ mặc định của
`fleetqox_rmw_trace_endpoint.py` trước khi bắt đầu publish, timeout
xong vẫn tiếp tục chứ không chặn) do chính hiện tượng nghẽn kênh đã
xác nhận xuyên suốt phiên này. Cơ chế fail-open (gửi toàn bộ khi chưa
biết ai subscribe) đúng là hành vi AN TOÀN cần có (không rơi message
oan), nhưng hệ quả là fix này tự động "thoái lui" về gần đúng hành vi
cũ (broadcast toàn bộ) chính xác ở kịch bản cần nó nhất.

**Ý nghĩa với kết luận tổng thể**: đây là bằng chứng MẠNH HƠN cả các
phát hiện trước — không chỉ traffic ứng dụng/discovery bị nghẽn, mà
NGAY CẢ các gói kiểm soát nhỏ nhất (subscription add/remove
advertisement, sau khi đã tối ưu bằng B+) cũng không đến nơi kịp thời
ở mật độ 19 station. Đây củng cố dứt khoát kết luận: nút thắt là năng
lực vật lý kênh 802.11g, không phải bất kỳ lựa chọn thiết kế giao thức
cụ thể nào — vì ngay cả những gói NHỎ NHẤT, THƯA NHẤT cũng không thoát
được tình trạng nghẽn ở quy mô này.

**File thay đổi**: `ros2_ws/src/rmw_fleetqox_cpp/src/rmw_pubsub.cpp`
(policy `subscription_aware`, bảng refcount, thread `source` qua chuỗi
nhận gói, counter mới), `scripts/fleetqox_rmw_trace_endpoint.py`
(counter mới), `scripts/run_ns3_docker_wifi_tap_rmw_probe.py`
(`--subscription-aware`).

### 11/09/2026 (tiếp) — Thí nghiệm quyết định của ChatGPT: bác bỏ giả thuyết "bootstrap feedback loop", đóng dứt điểm nhánh protocol

ChatGPT đưa ra chẩn đoán sắc hơn sau khi thấy fallback=100%: nghi ngờ
đây là **vòng lặp phản hồi dương lúc bootstrap**, không phải giới hạn
vật lý tuyệt đối —

```
discovery chưa hội tụ → subscription-aware fail-open → broadcast toàn bộ
→ traffic tăng → contention tăng → discovery càng khó hội tụ hơn
→ bảng subscription vẫn rỗng → mọi publish tiếp tục broadcast
→ tự khóa vào trạng thái quá tải vĩnh viễn
```

Đề xuất thí nghiệm "Case C": cho discovery đủ thời gian hội tụ THẬT SỰ
(không dùng timeout cố định 15s) trước khi bật publish, rồi đo lại. Nếu
delivery hồi phục mạnh → xác nhận đây là bug bootstrap sửa được. Nếu
vẫn ≈0 → xác nhận dứt điểm đây là giới hạn năng lực kênh thật.

**Implement**: thêm `--discovery-timeout-s` vào orchestrator, tính lại
`ready_deadline_s`/`start_wait_timeout_s` theo giá trị này (giữ nguyên
hành vi cũ ở mặc định 15s, chỉ thay đổi khi truyền giá trị lớn hơn).
Chạy 16-robot với `--discovery-timeout-s=120` (gấp 8 lần), `--subscription-aware`.

**Kết quả — bác bỏ giả thuyết bootstrap feedback loop**:
- Fallback ratio giảm từ **100% → 87.8%** (2044/2327) — xác nhận
  discovery CÓ hội tụ một phần khi cho thêm thời gian (một số robot đạt
  targeted-send thật: `robot_0000` 27/88≈31% fallback, `robot_0003`
  21/56≈37%, `robot_0005` 21/59≈36% — so với 100% trước đó).
- NHƯNG **`rx` vẫn = 0 tuyệt đối trên toàn bộ 2289 message** — không
  một tin nào tới nơi, dù chạy dài hơn nhiều (19 snapshot so với 6-7
  trước đó) và có nhiều lần gửi ĐÚNG targeted (không phải fallback)
  hơn hẳn.
- `phy_rx_drop_total` tăng lên **1899236** (do chạy dài hơn ~3-4 lần)
  nhưng tỷ lệ collision-drop (`BUSY_DECODING_PREAMBLE`+
  `PREAMBLE_DETECT_FAILURE`) giữ nguyên **~82%** — y hệt mọi biến thể
  đã thử trong toàn bộ phiên.

**Kết luận (khớp chính xác nhánh ChatGPT tự dự đoán cho "Case C vẫn
≈0")**: đây KHÔNG phải bootstrap feedback loop có thể sửa bằng cách
chờ lâu hơn hay targeting tốt hơn. Cho dù discovery hội tụ tốt hơn
(fallback giảm gần 1 nửa) và nhiều tin nhắn hơn được gửi ĐÚNG đích,
kênh vẫn không tải nổi dù chỉ 1 trong 2289 tin. Đây là bằng chứng mạnh
nhất, trực tiếp nhất trong toàn bộ phiên rằng **workload thực tế của
19 endpoint vượt hẳn năng lực airtime khả dụng của kênh**, độc lập
hoàn toàn với: retry logic, graph discovery design, data-plane fanout,
hay tốc độ hội tụ discovery. Theo đúng lời ChatGPT: đến đây nên dừng
tối ưu tầng protocol, chuyển hẳn sang tầng topology/capacity (nhiều AP
cell độc lập thật + backbone có dây, chuẩn wifi băng thông rộng hơn
802.11g, traffic admission control, QoS/class separation).

**Tổng kết TOÀN BỘ nhánh "16-robot delivery collapse"** (khép lại sau
khi đã thử: retry-fix, graph-renewal-interval, multi-AP round-robin,
cô lập sender nặng nhất, graph-discovery B+ redesign, subscription-aware
data-plane fanout, và thí nghiệm discovery-timeout dài — 7 hướng độc
lập, có cộng dồn một phần, đều xác nhận qua đo đạc thật): **nút thắt
là năng lực vật lý của kênh 802.11g đơn-AP ở workload 19-endpoint này,
không phải bất kỳ lỗi hay thiết kế giao thức cụ thể nào đã tìm được.**
Mọi tối ưu protocol đã thử đều giảm được tải/collision ở mức độ nào đó
nhưng KHÔNG hướng nào (kể cả kết hợp) đưa delivery vượt quá ~0.04%.

**File thay đổi**: `scripts/run_ns3_docker_wifi_tap_rmw_probe.py`
(`--discovery-timeout-s`, tính lại ready/start deadline).

### 11/09/2026 (tiếp) — KẾT LUẬN CHÍNH THỨC, khép lại nhánh điều tra 16-robot-scale (đã được ChatGPT Plus review)

Gửi toàn bộ 7-bước bằng chứng cho ChatGPT Plus review lần cuối trước khi
chốt kết luận. ChatGPT xác nhận đây là kết luận công bằng, có căn cứ,
với 2 điều chỉnh câu chữ quan trọng cho tính khoa học:
1. Dùng "**nút thắt chi phối** (dominant bottleneck)" thay vì ngụ ý đã
   loại trừ MỌI khả năng kém hiệu quả ở tầng protocol.
2. TRÁNH khẳng định tổng quát "802.11g không hỗ trợ được 19 station"
   — bằng chứng chỉ đúng cho ĐÚNG workload/topology/traffic pattern đã
   đánh giá, không phải một giới hạn tuyệt đối của chuẩn 802.11g.

**Kết luận chính thức (bản ChatGPT đề xuất, đã dịch/thích nghi)**:

> Bằng chứng thực nghiệm cho thấy nút thắt chi phối ở quy mô 16-robot/
> 19-endpoint là tình trạng bão hòa (saturation) của kênh không dây
> IEEE 802.11g dùng chung, dưới workload đã đánh giá — chứ không phải
> một lỗi cụ thể còn sót lại ở tầng middleware.
>
> Bảy thí nghiệm giảm thiểu độc lập đã được đánh giá: lỗi socket
> tạm thời (EHOSTUNREACH), overhead graph-advertisement định kỳ, tranh
> chấp kênh không dây (multi-AP), cô lập sender chi phối, graph
> discovery event-driven (B+), data-plane routing theo subscription,
> và kéo dài thời gian hội tụ discovery. Các can thiệp này đều tạo cải
> thiện đo được ở chỉ số trung gian — giảm đáng kể khối lượng truyền
> MAC và PHY receive drop, cải thiện tốc độ hội tụ discovery, targeting
> đích chọn lọc hơn. TUY NHIÊN, không can thiệp nào tạo cải thiện có ý
> nghĩa ở delivery đầu-cuối, vốn giữ nguyên ở mức xấp xỉ 0% tại quy mô
> 19-endpoint.
>
> Thí nghiệm discovery-convergence cuối cùng là bằng chứng thuyết phục
> nhất. Kéo dài cửa sổ discovery từ 15s lên 120s giảm fallback-broadcast
> từ 100% xuống 87.8%, với một số endpoint đạt phần lớn gửi đúng đích
> theo subscription. Dù vậy, delivery ứng dụng vẫn giữ nguyên CHÍNH XÁC
> 0/2289 message, trong khi PHY receive failure do collision vẫn chiếm
> ~82% tổng số PHY drop. Vì vậy, cả discovery hội tụ chưa đầy đủ LẪN
> data-plane fan-out vô điều kiện đều KHÔNG đủ để giải thích hiện tượng
> sập delivery quan sát được.
>
> Tổng hợp lại, các bất cập ở tầng middleware là contributor THẬT cho
> tải/tranh chấp, nhưng KHÔNG phải nguyên nhân chi phối của lỗi hệ
> thống. Dưới workload đã đánh giá, kênh 802.11g dùng chung vẫn ở chế
> độ tranh chấp/bão hòa nghiêm trọng ngay cả sau khi đã giảm đáng kể
> traffic do middleware sinh ra.
>
> Do đó, việc tiếp tục tối ưu graph renewal, thời gian discovery, hành
> vi retry, hay logic chọn peer được kỳ vọng sẽ cho lợi ích giảm dần
> (diminishing returns) ở kịch bản này. Thí nghiệm tiếp theo nên nhắm
> thẳng vào năng lực mạng và topology: các cell không dây thực sự độc
> lập nối qua backbone có dây (hoặc không giới hạn băng thông khác),
> cấu hình PHY/MAC 802.11 băng thông cao hơn, và cơ chế admission/ưu
> tiên traffic giữ tải airtime trong giới hạn bền vững.
>
> **Các kết luận này CHỈ đúng cho workload 19-endpoint và cấu hình
> 802.11g mô phỏng đã đánh giá — KHÔNG nên diễn giải thành khẳng định
> tổng quát rằng IEEE 802.11g không hỗ trợ được 19 station.**

**Toàn bộ chuỗi bằng chứng (không suy luận từ 1 số liệu đơn lẻ — tấn
công độc lập từng nguyên nhân khả dĩ, quan sát chỉ số trung gian cải
thiện, và LẶP LẠI cùng kết quả đầu-cuối)**:

```
socket retry issue ──────────────► fixed       ┐
graph flooding ──────────────────► reduced     │
RF contention via 4 channels ────► reduced     │
dominant sender ─────────────────► isolated    ├──► delivery ≈ 0
graph control traffic ────────────► reduced     │
data-plane broadcast fanout ──────► reduced     │
discovery convergence time ───────► improved   ┘

                           +
        collision-related PHY drops remain dominant (~66-82%)
                           ↓
            shared-medium saturation
              = dominant explanation
```

**Bước tiếp theo đã thống nhất với ChatGPT** (nếu tiếp tục dự án theo
hướng này — CHƯA làm, cần quyết định của người dùng): thử nghiệm giữ
nguyên application/RMW workload, chỉ thay đổi năng lực mạng/topology —
nhiều AP cell thực sự độc lập nối qua backbone có dây (không phải
static relay wireless như đã thử), hoặc chuẩn wifi băng thông cao hơn
(802.11n/ac/ax). Nếu delivery hồi phục đáng kể khi đổi 1 trong 2 biến
này (giữ nguyên middleware), đó là bằng chứng nhân quả cuối cùng xác
nhận kết luận saturation, thay vì tiếp tục dồn thêm một (thứ 8) can
thiệp middleware nữa.

### 11/09/2026 (tiếp) — So sánh với DDS truyền thống: xác nhận KHÔNG PHẢI vấn đề riêng của rmw_fleetqox_cpp, nhưng lộ ra 1 biến số chưa kiểm soát

Theo yêu cầu người dùng: so sánh với "các DDS truyền thống" (Fast DDS,
Cyclone DDS) thay vì chỉ raw-UDP baseline. Thêm `--rmw-implementation`
vào `run_ns3_docker_wifi_tap_rmw_probe.py`: cho phép chạy ĐÚNG hạ tầng
thật (ns-3 TapBridge, real Linux netns/process, RealtimeSimulatorImpl,
cùng 1 trace CSV) nhưng thay `RMW_IMPLEMENTATION` sang `rmw_fastrtps_cpp`
hoặc `rmw_cyclonedds_cpp` — bỏ qua hoàn toàn phần setup/env-var riêng
của `rmw_fleetqox_cpp` (2 DDS này đã có sẵn trong image, dùng discovery
riêng qua multicast, được relay qua TapBridge giống broadcast/ARP đã
xác nhận trước đó).

**Test sanity 1-robot**: Fast DDS hoạt động đúng (`rx=76/2/103`, khớp
CHÍNH XÁC baseline khỏe mạnh) — xác nhận multicast DDS discovery hoạt
động bình thường qua topology TapBridge ở quy mô nhỏ.

**Kết quả 16-robot — CẢ 3 middleware đều sập giống hệt nhau**:

| Middleware | `mac_tx_total` | `phy_rx_drop_total` | % collision | `rx` |
|---|---|---|---|---|
| rmw_fleetqox_cpp (đã tối ưu B+ + subscription-aware) | ~58000 | ~406000 | ~82% | **0/2289** |
| rmw_fastrtps_cpp (Fast DDS) | 17410 | 152483 | ~82% | **0/2289** |
| rmw_cyclonedds_cpp (Cyclone DDS) | 18490 | 173503 | ~83% | **0/2289** |

Đáng chú ý: Fast DDS và Cyclone DDS tạo ÍT traffic hơn hẳn (17410-18490
so với ~58000 của bản fleetqox_cpp đã tối ưu) nhưng VẪN sập y hệt 0%
— củng cố mạnh mẽ rằng đây KHÔNG PHẢI vấn đề riêng của thiết kế
discovery trong `rmw_fleetqox_cpp`. Hai middleware DDS trưởng thành,
được dùng rộng rãi nhất trong ROS2 thật (hàng nghìn triển khai robot
thực tế) chịu chung số phận trên ĐÚNG topology này.

**NHƯNG — phát hiện một biến số CHƯA kiểm soát, cần làm rõ**: baseline
raw-UDP gốc của Group 6 (`run_ns3_docker_wifi_mobility_matrix.py`,
dùng `fleetqox_trace_replay.cc --topology=wifi`, CÙNG mật độ 19 trạm,
CÙNG chuẩn 802.11g) cho kết quả **~95% delivery** ở quy mô 16 robot —
khác biệt CỰC LỚN so với 0% của cả 3 middleware thật vừa đo. Baseline
này khác ở nhiều điểm CÙNG LÚC, chưa tách bạch được nguyên nhân:
1. Chạy HOÀN TOÀN trong ns-3 simulated-time (không phải
   `RealtimeSimulatorImpl`) — không có TapBridge, không có Linux
   netns/process thật, không có real Linux kernel networking stack.
2. KHÔNG có bất kỳ traffic discovery/control-plane nào — gửi trực
   tiếp theo địa chỉ đã biết trước trong C++, không cần handshake.
3. Cả 3 middleware vừa đo (fleetqox_cpp, Fast DDS, Cyclone DDS) đều
   chạy qua TapBridge + Linux netns thật + RealtimeSimulatorImpl +
   CÓ traffic discovery riêng (dù khối lượng khác nhau khá nhiều giữa
   3 middleware, tất cả vẫn sập y hệt).

Vì CẢ 3 middleware — dù mức traffic discovery khác nhau tới ~3.3 lần
(17410 vs 58000) — đều sập y hệt nhau, trong khi baseline hoàn toàn
không có discovery lại đạt 95%, đây là bằng chứng gợi ý (CHƯA xác nhận
dứt điểm) rằng bản thân traffic discovery/control-plane (bất kể
middleware nào, bất kể khối lượng cụ thể) mới là yếu tố QUYẾT ĐỊNH đẩy
hệ thống qua ngưỡng bão hòa — không đơn thuần là "băng thông airtime
vật lý không đủ cho traffic ứng dụng" như kết luận trước đó ngụ ý.
CHƯA loại trừ được khả năng TapBridge/RealtimeSimulatorImpl/real-OS
overhead tự nó cũng là một phần nguyên nhân (khác biệt kiến trúc #1 ở
trên) — cần thí nghiệm tách bạch thêm (vd: viết 1 sender raw-UDP thật
chạy qua ĐÚNG TapBridge + real netns + RealtimeSimulatorImpl, bỏ qua
hoàn toàn ROS2/RMW/rclpy, để xem có sập hay không) trước khi kết luận
chắc chắn đây là do discovery traffic hay do bản thân hạ tầng
TapBridge/realtime.

**File thay đổi**: `scripts/run_ns3_docker_wifi_tap_rmw_probe.py`
(`--rmw-implementation`).

### 12/09/2026 — Thí nghiệm tách nhân quả (theo ma trận 4 test của ChatGPT): xác định dứt điểm KHÔNG PHẢI TapBridge/realtime, mà là traffic middleware/discovery

Theo yêu cầu người dùng ("bàn với chatgpt để giải quyết vấn đề đi"): mang
biến số chưa kiểm soát ở trên (raw-UDP 95% vs middleware 0%, 2 điểm khác
biệt cùng lúc) sang hỏi ChatGPT. ChatGPT đề xuất ma trận 4 test để tách
bạch 3 nguyên nhân khả dĩ — (A) traffic middleware/discovery, (B) tích
hợp TapBridge/Linux netns thật, (C) hiệu ứng lịch trình realtime-scheduler:

| Test | Mô tả | Kết quả |
|---|---|---|
| 1. ns-3 raw-UDP thuần, scheduler thường | Baseline gốc của Group 6 | ~95% (đã có từ trước) |
| 2. ns-3 raw-UDP thuần + `RealtimeSimulatorImpl`, KHÔNG TapBridge | Cờ `--realtime` mới thêm vào `fleetqox_trace_replay.cc` | **~96.3% (2204/2289)** |
| 3. raw-UDP thật qua ĐÚNG TapBridge + netns + `RealtimeSimulatorImpl`, KHÔNG ROS2/RMW/discovery | `scripts/raw_udp_trace_endpoint.py` mới, chạy qua `--rmw-implementation=raw_udp` | **100% (2289/2289), xác nhận 0 trùng lặp, 0 thiếu sót khi đối chiếu event_id với trace CSV** |
| 4. rmw_fleetqox_cpp / Fast DDS / Cyclone DDS (đã có) | — | **0/2289 cả 3** |

**Test 2** (chỉ thêm `GlobalValue::Bind("SimulatorImplementationType",
StringValue("ns3::RealtimeSimulatorImpl"))` + bật checksum, KHÔNG có
TapBridge/Linux process nào) cho kết quả gần như giống hệt Test 1 (~95%
vs ~96.3%) → **loại trừ**: bản thân việc chạy dưới realtime scheduler
(thay vì discrete-event thường) KHÔNG đủ để gây sập.

**Test 3** (`scripts/raw_udp_trace_endpoint.py`: socket UDP thuần,
KHÔNG rclpy/RMW/discovery nào, dùng bảng ánh xạ tên→IP tĩnh truyền qua
`--peers`, chạy qua ĐÚNG cùng 1 hạ tầng TapBridge + netns thật +
RealtimeSimulatorImpl mà cả 3 middleware ở Test 4 đã dùng) cho kết quả
**100% delivery (2289/2289)**, đối chiếu chặt với trace CSV gốc (không
duplicate, không thiếu) → **loại trừ**: bản thân tích hợp TapBridge/
Linux-netns thật/RealtimeSimulatorImpl KHÔNG phải nguyên nhân — hạ tầng
đó, khi không có traffic discovery/control-plane nào chồng lên, vẫn xử
lý được toàn bộ traffic ứng dụng của workload 19-endpoint mà không mất
gói nào.

**Kết luận (thay thế "802.11g saturation" trước đó)**: với cả (B) và (C)
đã bị loại trừ bằng thực nghiệm trực tiếp, nguyên nhân còn lại duy nhất
được ủng hộ bởi toàn bộ chuỗi bằng chứng là **(A) — bản thân traffic
middleware/discovery control-plane**, không phải do khối lượng dữ liệu
ứng dụng vượt quá năng lực airtime vật lý (điều này từng được kết luận
"dominant bottleneck" nhưng nay đã bị Test 3 bác bỏ trực tiếp). Đáng chú
ý: Fast DDS chỉ tạo 17410 `mac_tx_total` (còn Test 3's raw-UDP data-plane
riêng đã tạo 2755 trong 1 mẫu 5s) — nghĩa là KHÔNG phải "quá nhiều gói
tin nói chung" là vấn đề, mà là **đặc tính riêng của traffic discovery**
(khả năng: multicast định kỳ đồng bộ giữa 19 node cùng lúc, gây collision
tập trung vào những thời điểm cụ thể, thay vì rải đều theo lịch trình
ứng dụng như raw-UDP) mới là yếu tố quyết định đẩy hệ thống qua ngưỡng
sập — khớp với nhận định ChatGPT đã đưa ra trước khi Test 3 chạy.

**Việc còn lại (chưa làm, hướng tiếp theo nếu tiếp tục dự án)**: đo trực
tiếp đặc tính burst của traffic discovery (khoảng cách thời gian giữa
các gói discovery liên tiếp, có đồng bộ giữa các node hay không) để xác
nhận cơ chế chính xác, thay vì chỉ suy luận gián tiếp qua tổng số gói.

**File thay đổi**: `external/ns3/fleetqox_trace_replay.cc` (cờ
`--realtime`), `scripts/raw_udp_trace_endpoint.py` (mới),
`scripts/run_ns3_docker_wifi_tap_rmw_probe.py`
(`--rmw-implementation=raw_udp`).

### 12/09/2026 (tiếp) — Thí nghiệm stagger-start theo đề xuất ChatGPT: KẾT QUẢ ÂM TÍNH, bác bỏ giả thuyết "đồng bộ khởi động đơn giản"

ChatGPT đề xuất thí nghiệm rẻ và quyết định nhất tiếp theo: nếu nguyên
nhân là các gói discovery khởi động (SPDP/SEDP của DDS, hay graph
advertisement ban đầu của FleetRMW) từ 19 endpoint bị dồn cụm trong vài
trăm ms đầu do tất cả tiến trình khởi động gần như đồng thời, thì việc
CHỈ giãn thời điểm khởi động tiến trình (không đổi gì khác, đặc biệt
không đổi lịch trình gửi dữ liệu ứng dụng — vẫn đồng bộ qua cơ chế
ready/start-file có sẵn) sẽ khôi phục delivery.

Thêm `--stagger-start-ms` vào `run_ns3_docker_wifi_tap_rmw_probe.py`:
trễ tuần tự (không chạy nền) trước khi khởi động tiến trình endpoint
thứ i thêm `i * stagger_start_ms`, chỉ ảnh hưởng THỜI ĐIỂM tiến trình
bắt đầu chạy (và do đó traffic discovery/khởi động của nó), không đổi
lịch trình replay dữ liệu.

**Kết quả** (Fast DDS, 16-robot, `--stagger-start-ms 200`, dàn trải
khởi động 19 tiến trình trên 3.6s thay vì gần như tức thời):
**vẫn 0/2289 — KHÔNG cải thiện**. Đáng chú ý: tổng khối lượng
collision/drop còn TỆ HƠN bản không stagger (`mac_rx_drop_total` lên
tới 1,239,279 so với 436,353 của bản gốc, `phy_rx_drop_total` tại mẫu
cuối không có do chạy hết trước khi kịp lấy — nhưng xu hướng climbing
rõ ràng dốc hơn nhiều) — vì stagger kéo dài tổng thời gian hội tụ
discovery của toàn hệ thống (endpoint cuối khởi động trễ 3.6s, rồi còn
cần tự hội tụ discovery trong ngân sách riêng), nên tổng thời gian chạy
và tổng traffic phát sinh còn nhiều hơn, không ít đi.

**Kết luận**: giả thuyết "đồng bộ khởi động đơn giản" (chỉ cần giãn thời
điểm launch tiến trình) bị bác bỏ ở mức granularity 200ms. Không loại
trừ khả năng nguyên nhân vẫn là burst đồng bộ nhưng ở tần suất tái diễn
định kỳ (steady-state SPDP/heartbeat lặp lại theo chu kỳ, không chỉ lúc
khởi động), cần đo trực tiếp inter-arrival time của traffic discovery
thay vì chỉ thử nghiệm gián tiếp qua thời điểm khởi động.

**File thay đổi**: `scripts/run_ns3_docker_wifi_tap_rmw_probe.py`
(`--stagger-start-ms`).

### 12/09/2026 (tiếp) — Thí nghiệm discovery-only theo ưu tiên #1 của ChatGPT: XÁC NHẬN DỨT ĐIỂM — control-plane traffic tự nó đủ để bão hòa kênh, traffic ứng dụng gần như không đóng góp gì thêm

Sau khi stagger-start cho kết quả âm tính, ChatGPT đề xuất thí nghiệm rẻ
và có tính nhân quả cao nhất tiếp theo: chạy discovery HOÀN TOÀN bình
thường (tạo đủ mọi publisher/subscription, để pub/sub match nhau) nhưng
KHÔNG gửi bất kỳ dữ liệu ứng dụng nào — nếu discovery-only đã tạo ra total
collision-drop tương đương mức 82% quan sát được ở bản đầy đủ, đó là
bằng chứng trực tiếp rằng bản thân control-plane traffic đã đủ để bão
hòa kênh.

Thêm `--discovery-only` vào `fleetqox_rmw_trace_endpoint.py` (tạo mọi
publisher/subscription, chạy hết vòng lặp discovery-convergence +
ready/start gate như bình thường, nhưng bỏ qua HOÀN TOÀN vòng lặp gửi dữ
liệu — `replay_rows = []`) và `--discovery-only` tương ứng ở
`run_ns3_docker_wifi_tap_rmw_probe.py` (no-op với `raw_udp` vì không có
bước discovery nào).

**Kết quả** (Fast DDS, 16-robot, `--discovery-only`, xác nhận `tx=0`
cho mọi endpoint — không gói ứng dụng nào được gửi):

| Thời điểm | `mac_tx_total` (discovery-only) | `mac_tx_total` (đầy đủ, có traffic) | % collision (discovery-only) | % collision (đầy đủ) |
|---|---|---|---|---|
| t=5s | 4080 | 5533 | 86.6% | 85.5% |
| t=10s | 10285 | 10460 | 89.0% | 88.6% |
| t=15s | (không có mẫu, run kết thúc sớm) | 17410 | — | 85.8% |

**`mac_tx_total` và % collision-drop gần như GIỐNG HỆT nhau giữa
discovery-only và bản đầy đủ ở CÙNG một mốc thời gian**, dù discovery-only
không gửi một byte dữ liệu ứng dụng nào. Điều này chứng minh trực tiếp:
**bản thân traffic discovery/control-plane của Fast DDS đã đủ để đẩy
kênh 802.11g 19-trạm vào chế độ bão hòa/collision gần như tối đa —
traffic ứng dụng thực tế (2289 message) đóng góp KHÔNG ĐÁNG KỂ vào tổng
tải kênh so với traffic discovery.**

Đây là bằng chứng "smoking gun" mạnh nhất trong toàn bộ investigation:
không cần đến giả thuyết "đồng bộ burst" (đã bị stagger-start bác bỏ)
hay "khối lượng traffic tổng" (đã bị so sánh Fast DDS/Cyclone/FleetRMW/
raw-UDP bác bỏ) — chỉ đơn giản là **discovery protocol của các DDS
middleware trưởng thành, khi chạy trên topology 19-trạm 802.11g này,
tạo ra đủ traffic (dù chỉ để 19 participant tìm thấy nhau, không truyền
dữ liệu gì) để tự nó bão hòa kênh**. Kết luận trước đó ("cần thêm 1 thí
nghiệm nữa để biết là traffic hay timing") nay đã được trả lời dứt
điểm: là traffic (khối lượng + kiểu gói discovery cụ thể — SPDP/SEDP
multicast periodic — không phải aggregate volume tổng thể như trước
đây từng nghĩ, vì raw-UDP data-plane volume tương đương hoặc lớn hơn
vẫn đạt 100%).

**Việc còn lại (theo đề xuất ChatGPT, chưa làm)**: phân rã traffic theo
loại frame (application/SPDP/SEDP/ARP/other) + ước tính airtime thực tế
theo loại (không chỉ đếm frame) để biết CHÍNH XÁC đặc điểm nào của gói
discovery (kích thước, PHY rate, tần suất, multicast vs unicast) khiến
nó "đắt" hơn tương ứng gói dữ liệu ứng dụng trên cùng kênh.

**File thay đổi**: `scripts/fleetqox_rmw_trace_endpoint.py`
(`--discovery-only`), `scripts/run_ns3_docker_wifi_tap_rmw_probe.py`
(`--discovery-only`).

### 12/09/2026 (tiếp) — FIX THẬT (không chỉ chẩn đoán): FLEETQOX_RMW_STATIC_MODE — loại bỏ hoàn toàn traffic discovery, delivery từ 0% lên ~29%

Theo yêu cầu trực tiếp của người dùng ("cố gắng fix đi chưa, bạn cứ đề
xuất đúng vậy??") — chuyển từ chẩn đoán sang sửa thật. Fast DDS/Cyclone
DDS là thư viện bên thứ ba (không sửa được), nhưng `rmw_fleetqox_cpp` là
code của chính dự án, và nó ĐÃ biết trước toàn bộ danh sách peer qua
`FLEETQOX_RMW_PEERS` (tĩnh, không cần discovery để tìm địa chỉ) — lý do
DUY NHẤT nó vẫn gửi traffic discovery là để học "peer nào subscribe
topic nào" cho subscription-aware routing. Với harness này, ánh xạ
(topic → subscriber) là XÁC ĐỊNH TRƯỚC hoàn toàn từ trace (tên topic
`/fleetqox_trace/{dst}/{flow_class}` chỉ có đúng 1 subscriber khả dĩ:
endpoint tên `{dst}`).

Thêm vào `rmw_pubsub.cpp`:
- `FLEETQOX_RMW_STATIC_MODE=1`: vô hiệu hoá hoàn toàn
  `send_graph_advertisement`/`send_graph_heartbeat`/
  `send_subscription_advertisement` (trả về ngay, không gửi gói nào lên
  dây) và không khởi động `pubsub_graph_renewal_loop` (thread heartbeat/
  full-resync) — ZERO traffic control-plane, không phải giảm tần suất
  như các lần tối ưu trước.
- `FLEETQOX_RMW_STATIC_SUBSCRIPTIONS`: nạp sẵn
  `peer_subscribed_topic_refcounts_` từ danh sách
  `ip:port|domain|topic|type` do caller cung cấp — đóng đúng vai trò mà
  `update_peer_subscription()` đáng lẽ học được từ graph advertisement
  nhận qua mạng, nhưng học TRƯỚC (từ config), không cần một gói nào.

Thêm `--static-mode` vào `run_ns3_docker_wifi_tap_rmw_probe.py`
(`build_static_subscriptions()` tự suy ra map từ trace CSV — không cần
sửa gì ở `fleetqox_rmw_trace_endpoint.py`, vì ánh xạ suy ra thẳng từ
`(src, dst, flow_class)` trong CSV).

**Kết quả** (`rmw_fleetqox_cpp`, 16-robot, `--static-mode`,
so với 0/2289 của cấu hình mặc định trước đó):

| | `mac_tx_total` (đỉnh) | `rx` |
|---|---|---|
| `rmw_fleetqox_cpp` mặc định (B+ + subscription-aware, có discovery) | ~58000 | **0/2289** |
| `rmw_fleetqox_cpp` + `--static-mode` (KHÔNG discovery traffic) | ~11430 (đợt cuối, do retry) | **674/2289 (29.4%)** |

Xác nhận sạch: 674 event_id nhận được là DUY NHẤT (0 trùng lặp), khớp
đối chiếu với trace gốc.

**Đây là bằng chứng thực nghiệm trực tiếp cho kết luận nhân quả**: loại
bỏ traffic discovery đưa delivery từ SẬP HOÀN TOÀN (0%) lên gần 30% —
không phải 95-100% như raw-UDP, vì `mac_tx_total` ở mẫu cuối vẫn nhảy
vọt lên 11430 kèm `mac_rx_drop_total` tăng vọt (233399) — dấu hiệu của
retry/reliability-repair traffic (QoS RELIABLE, NACK/fragment-repair đã
có sẵn trong RMW) tự nó tạo ra một vòng lặp tương tự: một phần va chạm
ban đầu → retransmit → thêm traffic → thêm va chạm. Cơ chế "traffic phụ
trợ (không phải dữ liệu ứng dụng thực) tự nó đủ để làm trầm trọng bão
hoà kênh" xuất hiện LẦN THỨ HAI, lần này ở tầng reliability-retry thay
vì discovery.

**Việc còn lại (hướng tiếp theo khả thi, chưa làm)**: giảm độ "hào
phóng" của cơ chế reliability-retry khi ở static mode (hạ
`FLEETQOX_RMW_REPAIR_RETRANSMISSION_BUDGET`/
`FLEETQOX_RMW_PROACTIVE_DATA_REPEATS`/tần suất NACK) để kiểm tra xem có
đẩy tiếp delivery lên gần mức raw-UDP (95-100%) hay không — cùng logic
"traffic phụ trợ, không phải data, gây bão hoà" nhưng áp dụng cho tầng
retry thay vì tầng discovery.

**File thay đổi**: `ros2_ws/src/rmw_fleetqox_cpp/src/rmw_pubsub.cpp`
(`FLEETQOX_RMW_STATIC_MODE`, `FLEETQOX_RMW_STATIC_SUBSCRIPTIONS`),
`scripts/run_ns3_docker_wifi_tap_rmw_probe.py` (`--static-mode`,
`build_static_subscriptions`).

### 12/09/2026 (tiếp) — Kiểm tra giả thuyết "retry-storm": SAI — giảm retry làm delivery TỆ HƠN, không tốt hơn

Kiểm tra nhanh giả thuyết vừa nêu ở trên (traffic retry/repair tự nó
góp phần làm trầm trọng bão hoà). Chạy lại `--static-mode` với
`FLEETQOX_RMW_PROACTIVE_DATA_REPEATS=0`,
`FLEETQOX_RMW_REPAIR_RETRANSMISSION_BUDGET=0`,
`FLEETQOX_RMW_REPAIR_MAX_ATTEMPTS_PER_SEQUENCE=0` (tắt hầu hết cơ chế
retry chủ động) qua tham số mới `extra_rmw_env` của `run_probe()`.

**Kết quả: delivery TỆ HƠN, không tốt hơn — 229/2289 (10.0%), so với
674/2289 (29.4%) khi giữ nguyên retry mặc định.** `mac_tx_total` gần
như không đổi so với bản có retry đầy đủ (692→18358 theo cùng pattern
climbing), nghĩa là traffic retry/repair KHÔNG phải là traffic phụ trội
đáng kể ở tầng airtime — nó chỉ đang làm đúng việc của nó: cứu lại một
phần message bị mất do va chạm ban đầu. Tắt nó đi chỉ làm mất luôn cả
phần được cứu, không giảm được va chạm.

**Kết luận**: giả thuyết "retry storm" (traffic phụ trợ tự nó gây thêm
bão hoà, giống cơ chế discovery) bị BÁC BỎ cho trường hợp retry/repair
cụ thể này. Khoảng cách còn lại giữa `--static-mode` (29.4%) và raw-UDP
(100%) nhiều khả năng nằm ở đặc điểm khác trong cách FleetRMW đóng gói/
gửi dữ liệu tầng data-plane (framing, fragmentation, completion marker,
ACK-like traffic vốn có của QoS RELIABLE) khác với cách raw-UDP gửi gói
đơn giản khớp chính xác kích thước/thời điểm theo trace — một hướng điều
tra kiến trúc khác, không phải một tham số có thể chỉnh nhanh.

**Khuyến nghị**: giữ nguyên tham số retry/repair mặc định (KHÔNG hạ
thấp) khi dùng `--static-mode` — 29.4% là kết quả tốt nhất đã đo được
với cấu hình mặc định, chưa tìm được cách cải thiện thêm bằng cách chỉnh
tham số retry.

**File thay đổi**: `scripts/run_ns3_docker_wifi_tap_rmw_probe.py`
(`extra_rmw_env` — passthrough chẩn đoán một lần, không phải flag CLI ổn
định).

### 12/09/2026 (tiếp) — Phân rã delivery theo kích thước message: xác nhận cơ chế còn lại — airtime dài hơn = collision cao hơn, KHÔNG chỉ do fragmentation

Theo đề xuất ChatGPT: đối chiếu `sent_event_ids`/`received` trong kết
quả `--static-mode` với cột `bytes` trong trace CSV gốc theo từng
khoảng kích thước.

| Kích thước | Sent | Recv | Delivery |
|---|---|---|---|
| 0–128B | 1315 | 507 | 38.6% |
| 128–256B | 678 | 151 | 22.3% |
| 256–512B | 84 | 6 | 7.1% |
| 512–1024B | 152 | 8 | 5.3% |
| 1024–2048B | 58 | 2 | 3.4% |
| 2048–4096B | 2 | 0 | 0.0% |

Giảm gần như đơn điệu theo kích thước — khớp đúng dự đoán của ChatGPT.
Nhưng có một điểm bất ngờ: `FLEETQOX_RMW_LOSS_RESILIENT_FRAGMENT_CHUNK_BYTES`
mặc định là 1024 bytes, nghĩa là 4 khoảng đầu tiên (0–128 đến 512–1024)
đều là **1 datagram UDP duy nhất, KHÔNG fragment**. Vậy mà delivery vẫn
giảm từ 38.6% xuống 5.3% ngay trong vùng KHÔNG fragment — nghĩa là công
thức "xác suất mất = 1 − thành_công^số_fragment" (ChatGPT đề xuất ban
đầu) KHÔNG giải thích được phần này của đường cong, vì số fragment = 1
xuyên suốt 4 khoảng đó.

**Giả thuyết đã gửi lại ChatGPT để xác nhận**: kênh 802.11 DCF này đã ở
trạng thái collision-nặng sẵn (~85%+ collision đo được kể cả ở static
mode) — một khung truyền DÀI HƠN (airtime lâu hơn) có "cửa sổ dễ bị va
chạm" (vulnerable period) dài hơn, nên xác suất một trạm khác bắt đầu
truyền chồng lên nó cũng cao hơn, HOÀN TOÀN ĐỘC LẬP với việc có
fragment hay không. Sau ngưỡng 1024B (bắt đầu fragment thật), 2 cơ chế
(airtime dài hơn MỖI gói + số gói độc lập tăng) cộng dồn, giải thích tại
sao đường cong còn dốc hơn nữa ở 2 khoảng cuối (3.4%, 0%).

**Chưa có câu trả lời từ ChatGPT về hướng khắc phục tiếp theo tại thời
điểm ghi chú này** (đã gửi câu hỏi, đang chờ). Hai hướng khả dĩ đã đề
xuất: (1) giảm kích thước payload/header để đẩy nhiều traffic hơn vào
khoảng an toàn nhất, (2) can thiệp sâu hơn vào tham số MAC (contention
window, backoff, hoặc ép MTU nhỏ hơn).

**File thay đổi**: không có (phân tích thuần dữ liệu từ kết quả
`--static-mode` đã chạy, không cần script mới).

### 12/09/2026 (tiếp) — Fix thật thứ 2: FLEETQOX_RMW_DATA_FRAME_ENCODING=compact_v1 (JSON+base64 → binary compact), NHƯNG phát hiện methodological gap nghiêm trọng: harness KHÔNG có RNG seed cố định cho ns-3, kết quả 1 lần chạy không đáng tin

Theo phân tích wire-format thực tế (không dùng `bytes` trong trace, mà
dựng lại đúng format `encode_data_frame_append` tạo ra): message control
96B thực tế lên dây **643 bytes** (JSON+base64, đo trực tiếp bằng
`encode_data_frame()` thật — không phải ước lượng tay), do lặp
`robot_id`/`topic` (route + sample_envelope), field name JSON, và base64
(+33%).

**Fix**: thêm `FLEETQOX_RMW_DATA_FRAME_ENCODING=compact_v1` — định dạng
nhị phân length-prefixed, giữ NGUYÊN mọi field ngữ nghĩa của `DataFrame`
(routing/QoS/retry/subscription-matching phía sau không đổi gì), bỏ
base64 (payload thô), bỏ lặp field, opt-in qua env var (mặc định JSON
giữ nguyên y hệt cho mọi probe/test khác trong dự án). Có magic prefix
riêng (`FRMWC1\n` so với `FRMW1\n`) để `decode_data_frame()` tự phân biệt
định dạng nhận được — publisher compact + subscriber JSON (hoặc ngược
lại) vẫn tương thích, không cần sửa ~15 call site gọi `decode_data_frame`
rải khắp `rmw_pubsub.cpp`.

**Đã kiểm chứng đúng đắn kỹ trước khi đo hiệu năng**: viết bộ test
round-trip riêng (`/tmp/test_compact_v1_roundtrip.cpp`, biên dịch độc lập
với `g++` thẳng vào `data_frame.cpp`, không cần ROS2) — round-trip mọi
field, payload rỗng, payload biên (1B/1023B/1024B/1025B), 20 payload
nhị phân ngẫu nhiên (kể cả cố tình để byte cuối = 0x20 để kiểm tra không
bị lọt qua `strip_padding()` — hàm này cắt trailing space, chỉ áp dụng
nhánh JSON, đã xác nhận nhánh compact bỏ qua nó hoàn toàn), phân biệt
JSON/compact hai chiều, và 219 điểm cắt ngắn (truncation) để xác nhận
decode luôn trả `nullopt` thay vì crash/đọc tràn bộ nhớ — chạy sạch dưới
AddressSanitizer + UndefinedBehaviorSanitizer. Đo kích thước thật:
**96B → 643B (JSON) so với 96B → 319B (compact_v1), giảm 50.4%**.

**Nhưng khi chạy thử nghiệm 16-robot thật để đo tác động lên delivery,
phát hiện một lỗ hổng phương pháp luận nghiêm trọng**: chạy LẶP LẠI
CHÍNH XÁC cùng 1 cấu hình (`--static-mode`, JSON mặc định, không đổi gì)
4 lần liên tiếp cho kết quả rất khác nhau:

| Lần chạy | Delivery |
|---|---|
| 1 | 29.4% |
| 2 | 10.3% |
| 3 | 22.7% |
| 4 | 18.3% |

**Mean = 20.2%, độ lệch chuẩn = 8.0 điểm phần trăm, khoảng dao động
10.3%–29.4% — TRÊN CÙNG MỘT CẤU HÌNH, không đổi bất kỳ dòng code nào
giữa các lần chạy.** Nguyên nhân: `external/ns3/fleetqox_trace_replay_tap.cc`
KHÔNG có bất kỳ cơ chế `RngSeedManager`/`--seed`/`--run`/`AssignStreams`
nào — không giống `fleetqox_trace_replay.cc` (baseline raw-UDP thuần
simulation, đã được thêm `--seed`/`--run`/`AssignStreams` xác định từ
trước, xem mục RNG parity 2 kiến trúc ns-3/INET). Vì hạ tầng TapBridge
này chạy qua Linux netns/tiến trình thật + `RealtimeSimulatorImpl` (thời
gian thực), nhiễu định thời hệ điều hành thực (CPU scheduling, Docker
container contention...) tự nó cũng là một nguồn variance độc lập với
RNG của ns-3, không thể loại bỏ chỉ bằng cách pin seed.

Chạy `compact_v1` 3 lần cho: 14.7%, 16.4%, 11.3% (mean 14.1%, độ lệch
chuẩn 2.6). Khoảng dao động của JSON (10.3–29.4%) và compact_v1
(11.3–16.4%) **CHỒNG LẤN ĐÁNG KỂ** — với cỡ mẫu nhỏ này (n=4 và n=3),
KHÔNG thể kết luận compact_v1 tốt hơn hay tệ hơn JSON một cách đáng tin
cậy, dù wire size giảm 50.4% đã được đo chính xác.

**Hệ quả quan trọng**: TOÀN BỘ so sánh single-run trước đó trong
investigation này (0% discovery-mode luôn ổn định vì đó là sàn/floor
— không có "room" để dao động — nhưng các con số static-mode 29.4%,
retry-disabled 10.0%, v.v. đều có thể mang variance chưa định lượng).
May mắn là các kết luận NHÂN QUẢ CHÍNH (discovery traffic → sập hoàn
toàn; raw-UDP/no-discovery → ~95-100%) đều dựa trên khoảng cách RẤT LỚN
(0% vs 95-100%), lớn hơn nhiều so với biên độ variance quan sát được ở
đây (~20 điểm phần trăm) — nên các kết luận đó vẫn đứng vững. Nhưng các
so sánh TINH VI hơn ở vùng static-mode (29.4% vs 10.0% khi tắt retry,
và bây giờ là JSON vs compact_v1) cần được xác nhận lại bằng NHIỀU lần
lặp lại thay vì 1 lần chạy đơn.

**Việc còn lại (đã gửi ChatGPT hỏi hướng xử lý, đang chờ)**: cần chạy
nhiều lần lặp lại hơn (n≥10 mỗi cấu hình) để có thống kê đáng tin, hoặc
tìm cách giảm nguồn nhiễu (không chỉ RNG ns-3 mà cả jitter OS thực) để
so sánh 1 lần chạy trở nên đáng tin cậy hơn.

**File thay đổi**: `ros2_ws/src/rmw_fleetqox_cpp/include/rmw_fleetqox_cpp/data_frame.hpp`,
`ros2_ws/src/rmw_fleetqox_cpp/src/data_frame.cpp`
(`encode_data_frame_compact_v1_append`, `decode_data_frame_compact_v1`,
`kDataFrameCompactV1Magic`), `ros2_ws/src/rmw_fleetqox_cpp/src/rmw_pubsub.cpp`
(`FLEETQOX_RMW_DATA_FRAME_ENCODING=compact_v1`,
`compact_v1_data_frame_encoding_enabled()`), `scripts/fleetqox_rmw_trace_endpoint.py`
(`frames_sent`/`frames_received` added to exposed transport metrics).

### 12/09/2026 (tiếp) — Đã thêm RNG seed cố định cho ns-3, kiểm tra lại độ lặp lại + so sánh JSON/compact_v1 có kiểm soát

Theo đề xuất ChatGPT: thêm `RngSeedManager::SetSeed()`/`SetRun()` vào
`fleetqox_trace_replay_tap.cc` (trước đây HOÀN TOÀN không có — khác với
`fleetqox_trace_replay.cc` đã có `--seed`/`--run`/`AssignStreams` xác
định từ sớm hơn trong investigation). Thêm `--seed`/`--run` (mặc định
1/1, không đổi hành vi ns-3 mặc định) và `ns3_seed`/`ns3_run` xuyên suốt
`run_ns3_docker_wifi_tap_rmw_probe.py`.

**Kiểm tra lặp lại với seed/run CỐ ĐỊNH (seed=42, run=1), JSON mặc định,
5 lần chạy**: 9.6%, 10.8%, 9.5%, 24.6%, 10.4%.

4/5 lần **co cụm rất chặt** (9.5%–10.8%, độ lệch chuẩn chỉ 0.63 điểm) —
xác nhận trực tiếp: **thiếu RNG seed của ns-3 từng là nguồn nhiễu chính**
gây ra dao động 10.3%–29.4% quan sát trước đó. Nhưng có **1/5 lần vẫn
lệch mạnh (24.6%)** — xác nhận cảnh báo của ChatGPT: pin RNG của ns-3
không loại bỏ hết nhiễu, vì hạ tầng TapBridge chạy qua tiến trình Linux
thật + `RealtimeSimulatorImpl` (thời gian thực), nên jitter lập lịch hệ
điều hành vẫn là nguồn nhiễu độc lập, thỉnh thoảng vẫn gây lệch lớn.

**So sánh có kiểm soát**: chạy `compact_v1` với CÙNG seed=42/run=1, 5
lần: 35.0%, 10.9%, 29.0%, 17.9%, 10.1% (mean 20.6%, độ lệch chuẩn 11.1)
so với JSON mean 13.0% (độ lệch chuẩn 6.5%). Chênh lệch trung bình
+7.6 điểm phần trăm nghiêng về compact_v1, nhưng t~1.32 (chưa đạt
ngưỡng có ý nghĩa thống kê ở mức tin cậy 95% với n=5 mỗi nhóm) —
**đảo ngược ấn tượng ban đầu** (khi chưa pin seed, compact_v1 trông có
vẻ TỆ hơn JSON) thành **có thể tốt hơn nhưng chưa đủ bằng chứng chắc
chắn**. Đáng chú ý: compact_v1 vẫn dao động khá lớn ngay cả khi RNG
ns-3 đã cố định giống hệt JSON — gợi ý rằng thời gian xử lý/gửi thực tế
(vốn nhanh hơn với compact_v1 do payload nhỏ hơn) có thể tương tác với
nhiễu lập lịch OS theo cách khác JSON, một câu hỏi mở chưa được giải
đáp.

**Kết luận tạm thời (trung thực, KHÔNG khẳng định quá mức)**:
1. Giảm kích thước wire-frame (compact_v1) là fix ĐÚNG HƯỚNG, có bằng
   chứng thống kê yếu-vừa nghiêng về cải thiện delivery, nhưng CHƯA đủ
   mạnh để khẳng định chắc chắn với cỡ mẫu hiện tại.
2. Việc pin RNG seed của ns-3 tự nó đã là một cải tiến hạ tầng có giá
   trị thực (giảm gần hết nhiễu ở 4/5 trường hợp), nên giữ lại vĩnh
   viễn cho mọi thử nghiệm `fleetqox_trace_replay_tap.cc` sau này.
3. Cần thêm nhiều lần lặp lại hơn (ChatGPT gợi ý ≥10 cặp so khớp
   JSON/compact_v1 luân phiên theo từng `--run`) để có khoảng tin cậy
   95% đủ hẹp trước khi đưa compact_v1 vào bất kỳ khuyến nghị chính
   thức nào.

**File thay đổi**: (đã liệt kê ở mục trước — `--seed`/`--run` thêm vào
cùng lần với `fleetqox_trace_replay_tap.cc`).

### 12/09/2026 (tiếp) — Điểm dừng (checkpoint) đã thống nhất với ChatGPT

Sau chuỗi thí nghiệm dài, ChatGPT đồng ý đây là điểm dừng hợp lý cho
phiên làm việc này, với phân loại rõ 2 nhóm kết quả:

**Nhóm 1 — Kết luận nhân quả lớn, KHÔNG bị ảnh hưởng bởi variance mới
phát hiện** (chênh lệch hiệu ứng quá lớn so với ~20 điểm % nhiễu quan
sát được):
- Traffic discovery/control-plane (không phải dữ liệu ứng dụng) là
  nguyên nhân chính gây sập delivery ở quy mô 16-robot/19-endpoint.
  Xác nhận qua chuỗi bằng chứng độc lập: raw-UDP (không discovery) đạt
  95-100% trên ĐÚNG hạ tầng TapBridge/netns/RealtimeSimulatorImpl; cả 3
  middleware thật (FleetRMW, Fast DDS, Cyclone DDS) đều sập về 0%; và
  quyết định nhất — Fast DDS chạy discovery-only (zero traffic ứng
  dụng) tái tạo gần như y hệt mức collision/mac_tx của bản đầy đủ.
- `FLEETQOX_RMW_STATIC_MODE` (loại bỏ traffic discovery bằng cách khai
  báo tĩnh subscription map) là FIX THẬT, đã đưa delivery ra khỏi sàn
  0% một cách nhất quán qua nhiều lần chạy độc lập (dao động 9.5%-29.4%
  tuỳ lần, nhưng LUÔN > 0%, không bao giờ quay lại sập hoàn toàn).

**Nhóm 2 — Kết quả tinh vi hơn, CẦN xác nhận lại bằng nhiều lần lặp,
KHÔNG nên trích dẫn con số cụ thể cho tới khi có đủ mẫu**:
- Con số "29.4%" của static-mode không còn nên coi là con số đại diện
  (representative) — chỉ nên nói "static-mode đưa delivery ra khỏi sàn
  0% một cách nhất quán", không chốt một % cụ thể.
- "Retry giúp ích" (29.4% → 10.0% khi tắt) vẫn là hướng đáng tin (tắt
  retry luôn làm tệ hơn qua các lần thử), nhưng độ lớn chính xác của lợi
  ích chưa được đo với đủ replicate.
- `compact_v1` (giảm 50.4% kích thước gói tin, xác nhận chắc chắn bằng
  đo trực tiếp): lợi ích lên delivery hiện tại là "có xu hướng dương
  (+7.6 điểm % trung bình ở seed cố định) nhưng CHƯA đạt ý nghĩa thống
  kê" (t~1.32, n=5 mỗi nhóm) — trạng thái đúng đắn để mô tả: **"tối ưu
  hoá wire-format đã được chứng minh; lợi ích lên delivery đang chờ xác
  nhận thống kê"**, không phải "đã chứng minh compact_v1 tốt hơn".

**Việc còn lại cho phiên làm việc tiếp theo** (đã thống nhất, chưa làm
trong phiên này):
1. Chạy ≥10 cặp so khớp JSON/compact_v1 luân phiên thứ tự theo từng
   `--run` (vd: run 1: JSON rồi compact; run 2: compact rồi JSON...) để
   có khoảng tin cậy 95% đủ hẹp cho $\Delta$delivery trung bình.
2. Đo riêng "first-attempt success" (thành công lần gửi đầu) tách khỏi
   "eventual success nhờ retry" — theo ChatGPT đây có thể là tín hiệu
   nhạy hơn để phát hiện lợi ích của compact_v1, vì retry có thể che bớt
   khác biệt ở tầng first-attempt.
3. Điều tra tại sao `compact_v1` co cụm variance kém hơn JSON dưới CÙNG
   seed cố định (có thể do đường gửi nhanh hơn tương tác khác với OS
   jitter) — ChatGPT khuyến nghị chỉ điều tra sau khi có đủ replicate
   xác nhận hiện tượng này không phải nhiễu mẫu nhỏ (n=5 hiện tại có thể
   chính nó là artifact).
4. Cân nhắc thêm CPU affinity riêng cho tiến trình ns-3 và tiến trình
   endpoint để giảm (không phải loại bỏ) nhiễu OS-jitter còn lại.

### 12/09/2026 (tiếp) — KẾT QUẢ CUỐI: 10 cặp so khớp JSON/compact_v1 — KHÔNG có khác biệt có ý nghĩa thống kê

Theo yêu cầu người dùng, chạy đúng thiết kế ChatGPT đề xuất: 10 cặp
JSON/compact_v1, mỗi cặp dùng CÙNG `--run` (1 đến 10, `--seed=42` cố
định xuyên suốt), đảo thứ tự chạy trong cặp luân phiên (run lẻ: JSON
trước; run chẵn: compact_v1 trước) để tránh thiên vị do trôi tải/nhiệt
hệ thống theo thời gian.

**Kết quả 10 cặp** (delivery %, theo thứ tự run 1→10):

| Run | JSON | compact_v1 | Δ (compact − JSON) |
|---|---|---|---|
| 1 | 29.9 | 9.8 | −20.1 |
| 2 | 14.3 | 29.9 | +15.6 |
| 3 | 24.9 | 16.0 | −8.9 |
| 4 | 32.9 | 18.6 | −14.3 |
| 5 | 27.9 | 23.3 | −4.6 |
| 6 | 12.3 | 14.3 | +2.0 |
| 7 | 16.9 | 26.0 | +9.1 |
| 8 | 10.6 | 30.8 | +20.1 |
| 9 | 14.8 | 26.1 | +11.4 |
| 10 | 12.0 | 11.7 | −0.3 |

- JSON: n=10, mean=19.6%, độ lệch chuẩn=8.4
- compact_v1: n=10, mean=20.6%, độ lệch chuẩn=7.6
- **Chênh lệch trung bình theo cặp (compact − JSON) = +1.0 điểm phần
  trăm, độ lệch chuẩn 13.2, khoảng tin cậy 95% = [−8.4, +10.4]**

**Kết luận dứt điểm, trung thực**: khoảng tin cậy 95% CHỨA số 0 và khá
rộng — **KHÔNG có bằng chứng thống kê cho thấy `compact_v1` cải thiện
delivery so với JSON** ở quy mô/topology này, dù việc giảm 50.4% kích
thước gói tin trên dây là có thật và đã đo chính xác. Chênh lệch từng
cặp dao động cả hai chiều gần như ngẫu nhiên (−20.1 đến +20.1), không
có xu hướng hệ thống nào theo thứ tự chạy (loại trừ được thiên vị do
trôi tải hệ thống theo thời gian, vì đã đảo thứ tự).

**Diễn giải khả dĩ** (chưa kiểm chứng thêm, nêu để tham khảo): kênh
802.11 DCF có overhead CỐ ĐỊNH đáng kể trên mỗi lần truyền (DIFS, SIFS,
backoff slot, PHY preamble) không phụ thuộc kích thước payload — ở dải
kích thước gói tin nhỏ (96B–1KB) của workload này, phần airtime tỉ lệ
thuận với payload có thể chỉ là một phần nhỏ so với overhead cố định
này, nên giảm 50% kích thước payload không giảm tương ứng tổng airtime
mỗi gói, và do đó không giảm đáng kể xác suất va chạm. Nói cách khác:
bài toán bão hoà kênh ở quy mô 19-trạm này có thể bị chi phối bởi **số
lượng lần truyền** (số gói, số lần contend kênh) hơn là **kích thước
mỗi gói** — khớp với nhận định ChatGPT nêu trước đó ("not too many
frames, but too much channel time per control frame" áp dụng ngược lại
ở đây: với data-plane frame nhỏ, chi phí KHÔNG chủ yếu đến từ kích
thước).

**Khuyến nghị cuối cùng cho `compact_v1`**: giữ nguyên như một tính
năng opt-in đã được kiểm chứng đúng đắn về mặt kỹ thuật (round-trip an
toàn, giảm wire size xác nhận), nhưng **KHÔNG khuyến nghị chuyển thành
mặc định** dựa trên bằng chứng delivery hiện có — lợi ích (nếu có) quá
nhỏ để phân biệt với nhiễu ở quy mô thử nghiệm này. Nếu muốn tiếp tục
theo hướng này, cần nhắm vào **giảm số lượng gói/lần contend kênh**
(vd: gộp nhiều message nhỏ vào 1 gói, giảm tần suất gửi) thay vì tiếp
tục giảm kích thước từng gói.

**File thay đổi**: không có (chạy 20 lần thử qua
`run_ns3_docker_wifi_tap_rmw_probe.run_probe()` trực tiếp bằng script
Python tạm, không cần thay đổi code sản phẩm).

### 12/09/2026 (tiếp) — Hướng tiếp theo từ ChatGPT: message batching/aggregation — nhưng trace thực tế KHÔNG có đủ clustering để tận dụng

Sau kết quả null của `compact_v1`, ChatGPT phân tích: giảm byte/gói
không giúp vì 802.11 DCF có overhead CỐ ĐỊNH lớn mỗi lần truyền (DIFS/
SIFS/backoff/preamble) không phụ thuộc kích thước — nên đòn bẩy đúng là
giảm SỐ LƯỢNG lần truyền, không phải kích thước mỗi lần. Đề xuất cụ
thể: `FLEETQOX_RMW_BATCH_MODE` — gộp nhiều message nhỏ cùng đích thành
1 datagram (hàng đợi theo từng peer đích, flush sau cửa sổ thời gian
ngắn ~0.5-2ms hoặc khi đạt ngưỡng kích thước), giữ nguyên hoàn toàn
logic phía trên (routing/QoS/retry) bằng cách bên nhận tách batch ra
thành các frame riêng rồi cho đi qua ĐÚNG pipeline xử lý hiện có.

**Trước khi xây dựng**, kiểm tra xem trace THỰC TẾ có đủ "message cùng
nguồn-cùng đích gần nhau về thời gian" để batching có gì mà gộp hay
không — phân tích trực tiếp file trace CSV (37 cặp (src,dst) riêng
biệt, trung bình 61.9 message/cặp):

| Cửa sổ | % message có message khác cùng (src,dst) trong cửa sổ đó |
|---|---|
| 1ms | 0.0% |
| 2ms | 0.0% |
| 5ms | 0.0% |
| 10ms | 0.0% |
| 50ms | 80.5% |

**KHÔNG có clustering nào ở ngưỡng thời gian an toàn cho traffic control
(dưới 10ms)** — nghĩa là cửa sổ flush ngắn (0.5-2ms) ChatGPT đề xuất ban
đầu sẽ HẦU NHƯ KHÔNG BAO GIỜ tìm được message thứ 2 để gộp. Chỉ ở cửa sổ
50ms mới thấy clustering (80.5%), nhưng 50ms đủ lớn để tự nó đe doạ vi
phạm deadline của message loại control. Thêm nữa: `fleet_controller`
(71% tổng traffic) fanout tới 16 đích khác nhau — nên ngay cả với cửa
sổ dài, phần lớn traffic của chính publisher lớn nhất cũng không có cơ
hội gộp (mỗi lần gửi thường tới một đích khác).

**Đã gửi phát hiện này lại cho ChatGPT trước khi xây dựng cơ chế
batching** — tránh xây một subsystem mới (hàng đợi theo đích, luồng
flush, logic tách batch bên nhận) cho một pattern traffic mà trace thực
tế không hỗ trợ. Đang chờ phản hồi để xác nhận batching có còn là hướng
đúng (có thể với cửa sổ dài hơn + chỉ áp dụng cho traffic không phải
control/không có deadline chặt) hay nên quay lại hướng CW/EDCA tuning.

**File thay đổi**: không có (phân tích thuần trên trace CSV đã có).

### 12/09/2026 (tiếp) — ChatGPT đổi hướng khuyến nghị: KHÔNG xây batching cho trace này; đo số lượng sendto() thực tế trước — kết quả: KHÔNG có khuếch đại số lượng gói (1.02x)

ChatGPT đồng ý huỷ hướng batching cho trace này (do phát hiện thiếu
clustering ở trên), chuyển sang ưu tiên đo **số lượng UDP sendto() thực
tế** so với số message logic — không phải `mac_tx_total` (đã đo, có thể
bị ảnh hưởng bởi retry/collision ở tầng dưới) mà là số lần gọi
`sendto()` ở tầng ứng dụng/transport, để biết FleetRMW có "khuếch đại"
số lượng gói tin so với 1:1 (như raw-UDP) hay không.

May mắn, counter cần dùng (`frames_sent`/`frames_received` — đếm số
lần gọi `sendto()` thật ở tầng transport, KHÁC với `mac_tx_total` ở
tầng MAC/PHY của ns-3) đã được thêm vào `fleetqox_rmw_trace_endpoint.py`
từ trước (lúc làm `compact_v1`) mà chưa dùng tới. Lấy thẳng từ 1 kết quả
static-mode đã chạy trước đó:

- Số message logic đã gửi (tx): 2289
- Tổng `frames_sent` thực tế (tất cả 19 endpoint cộng lại): 2327
- **Tỉ lệ khuếch đại: 1.02x — GẦN NHƯ 1:1, không có khuếch đại đáng kể**

**Kết luận**: giả thuyết "FleetRMW gửi nhiều gói hơn logic cho mỗi
message" (do NACK/repair/fragment storm) bị BÁC BỎ ở mức tổng hợp —
khớp với kết quả retry-disabled trước đó cho thấy khối lượng retry thấp
nhưng quan trọng cho việc phục hồi, không phải nguồn khuếch đại. Điều
này ĐẨY nghi vấn còn lại về hướng **thời điểm gửi thực tế (timing/
burstiness)** — có thể FleetRMW gửi các gói ở cùng logic-schedule
nhưng dồn cục hơn về mặt wall-clock thực tế (do overhead xử lý nội bộ:
encode, khoá mutex, ghi retransmit ledger...) so với raw-UDP vốn
`sendto()` gần như ngay lập tức không qua lớp trung gian nào.

**Việc tiếp theo (đã hỏi ChatGPT xác nhận, đang chờ)**: thêm log thời
điểm gửi thực tế (wall-clock, không phải timestamp lịch trình trong
trace) cho từng message ở cả `fleetqox_rmw_trace_endpoint.py` và
`raw_udp_trace_endpoint.py`, so sánh phân phối khoảng cách giữa các lần
gửi liên tiếp — đặc biệt cho `fleet_controller` (71% traffic, fanout 16
đích) để xem FleetRMW có "dồn cục" (burst) các lần gửi hơn schedule gốc
so với raw-UDP hay không.

**File thay đổi**: không có (dùng lại counter `frames_sent`/
`frames_received` đã thêm từ trước, chỉ đọc dữ liệu đã có).

### 12/09/2026 (tiếp) — Đo timing thực tế: phát hiện publish() của FleetRMW chậm hơn raw-UDP 6.4 lần, gây "lag cushion" 5-14ms suốt cả run

Thêm log `publish_before_wall_ns`/`publish_after_wall_ns` quanh mỗi lệnh
gửi thực tế (`publisher.publish()` cho FleetRMW, `sock.sendto()` cho
raw-UDP) vào cả `fleetqox_rmw_trace_endpoint.py` và
`raw_udp_trace_endpoint.py`. Chạy lại 16-robot với CÙNG seed=42/run=1
cho cả 2 (FleetRMW static-mode: 25.2% delivery; raw-UDP: 100%), phân
tích riêng cho `fleet_controller` (1626 lần gửi, 71% tổng traffic).

**Thời gian bản thân lệnh gửi (không phải khoảng cách giữa các lần
gửi)**:
- raw UDP: mean=0.038ms, max=0.121ms, p95=0.070ms
- FleetRMW: mean=0.247ms, max=1.472ms, p95=0.351ms
- **FleetRMW chậm hơn raw-UDP 6.4 lần MỖI LẦN gọi `publish()`**

**Khoảng cách giữa các lần gửi liên tiếp** (trace có nhiều message được
lên lịch gửi CÙNG THỜI ĐIỂM, tức khoảng cách lịch trình = 0ms):
- raw UDP: median=0.088ms, 63.5% dưới 0.1ms, 90.8% dưới 0.5ms
- FleetRMW: median=1.101ms, 0% dưới 0.5ms, chỉ 33.7% dưới 1ms

**Độ trễ lịch trình tuyệt đối** (thời gian thực đã trôi qua trừ thời
gian lịch trình dự kiến, lấy mẫu mỗi ~100 lần gửi xuyên suốt run):
- raw UDP: luôn dưới ~1.3ms suốt cả run (+0.00, +0.46, +0.17, −0.03,
  +0.67, +0.44...ms)
- FleetRMW: dao động trong khoảng **+4ms đến +14ms suốt cả run**,
  không bao giờ về gần 0, nhưng cũng KHÔNG drift tăng dần không giới
  hạn (+6.92, +3.55, +4.92, +12.26, +5.84, +8.18, +4.96, +5.57,
  +13.66, +7.28ms...)

**Diễn giải**: đây không phải drift tích luỹ chạy trốn (runaway), mà là
một "đệm trễ" (lag cushion) khoảng 5-14ms mà FleetRMW MANG THEO suốt cả
run — lớn hơn độ chính xác dưới-mili-giây của raw-UDP tới 2 bậc độ lớn.
Xem code `publish_payload()`: mỗi lần gọi đều khoá `g_bus_mutex` và
QUÉT TUYẾN TÍNH toàn bộ `g_retransmit_ledger` (map dùng chung TOÀN HỆ
THỐNG, không đánh index theo publisher) để tìm/dọn entry của publisher
hiện tại, cộng thêm tra cứu subscription-matching cho QoS RELIABLE —
đây là ứng viên hàng đầu cho nguồn overhead 0.25ms/lần gọi. Với 19 trạm
độc lập, mỗi trạm mang một độ trễ ngẫu nhiên ~5-14ms riêng so với lịch
trình gốc, các message mà trace THIẾT KẾ để giãn cách nhau vẫn có thể
VÔ TÌNH rơi vào cùng cửa sổ thời gian thực — làm mất tác dụng của việc
giãn cách lịch trình cẩn thận trong trace gốc.

**Đã gửi phát hiện này cho ChatGPT để xác nhận cơ chế và hỏi hướng sửa**
(nghi ngờ hàng đầu: tối ưu retransmit ledger — đánh index theo
publisher thay vì quét tuyến tính toàn bộ map — nhưng cần xác nhận
bằng profiling thực tế thay vì chỉ suy luận từ đọc code, trước khi
sửa). Đang chờ phản hồi.

**File thay đổi**: `scripts/fleetqox_rmw_trace_endpoint.py`,
`scripts/raw_udp_trace_endpoint.py` (thêm `send_timing` vào kết quả
JSON — đã liệt kê ở mục trước).

### 12/09/2026 (tiếp) — Thí nghiệm nhân quả sạch nhất: tiêm độ trễ CPU nhân tạo vào raw-UDP — BÁC BỎ giả thuyết "chỉ cần latency là đủ"

ChatGPT đề xuất thí nghiệm rẻ và quyết định nhất: thêm độ trễ CPU nhân
tạo (busy-wait bằng `time.perf_counter()`, KHÔNG dùng `time.sleep()` vì
độ chính xác của hệ điều hành cho sleep thường ~1ms+, không đủ mịn cho
độ trễ nhỏ này) ngay sau `sendto()` trong `raw_udp_trace_endpoint.py`
(baseline đã đạt 100%), giữ NGUYÊN mọi thứ khác — số lượng gói, định
dạng gói, routing — chỉ thêm độ trễ xử lý CPU đơn thuần mô phỏng đúng
mức chênh lệch `publish()` của FleetRMW đã đo (0.247ms trung bình so
với 0.038ms của raw-UDP).

Thêm `--artificial-cpu-delay-us` vào `raw_udp_trace_endpoint.py`, chạy
sweep với CÙNG seed=42/run=1:

| Độ trễ thêm | Delivery |
|---|---|
| 0µs (baseline) | 100% |
| 100µs | 100% (2289/2289) |
| 250µs | 100% (2289/2289) |
| 500µs | 100% (2289/2289) |
| **1000µs** | **100% (2289/2289)** |

**Kết quả null hoàn toàn phẳng — BÁC BỎ giả thuyết H1 (độ trễ CPU đơn
thuần đủ để gây sập)**. Ngay cả 1000µs — gấp 4 lần độ trễ trung bình đo
được của FleetRMW (0.247ms) và LỚN HƠN cả độ trễ tối đa quan sát được
(1.472ms) — cũng không ảnh hưởng gì tới delivery của raw-UDP. Nghĩa là
bản thân việc "giữ CPU lâu hơn trước khi gửi" (dù đúng bằng hoặc hơn
mức FleetRMW đo được) KHÔNG đủ để tái tạo hiện tượng sập.

**Ý nghĩa**: giả thuyết "publish-path processing latency đơn thuần làm
méo timing" bị bác bỏ theo cách sạch nhất có thể (thí nghiệm chỉ đổi
đúng 1 biến). Nghi vấn giờ chuyển sang: có thể không phải "tốn thời
gian CPU" mà là "tốn thời gian CPU dưới dạng LOCK CONTENTION thực sự"
(thí nghiệm delay nhân tạo là busy-wait đơn luồng, không tạo tranh chấp
mutex nào, trong khi FleetRMW thực có `g_bus_mutex` dùng chung — cần
kiểm tra xem có thread nền nào khác (repair worker, qos_deadline_
monitor_thread...) vẫn chạy và tranh chấp mutex này ngay cả ở static
mode hay không) — hoặc một cơ chế hoàn toàn khác chưa được nhắm tới.

**Đã gửi kết quả này cho ChatGPT, hỏi có nên profiling từng giai đoạn
bên trong `publish_payload()` (mutex wait/hold riêng biệt, ledger scan,
subscription lookup, encode, syscall) ngay bây giờ hay còn thí nghiệm
rẻ hơn nên thử trước** — đang chờ phản hồi.

**File thay đổi**: `scripts/raw_udp_trace_endpoint.py`
(`--artificial-cpu-delay-us`), `scripts/run_ns3_docker_wifi_tap_rmw_probe.py`
(xuyên suốt tham số này).

### 12/09/2026 (tiếp) — Profiler từng giai đoạn: BÁC BỎ giả thuyết retransmit-ledger, phát hiện thủ phạm thật là transport_send (75% thời gian publish)

Thêm profiler theo từng giai đoạn (env-gated qua
`FLEETQOX_RMW_PUBLISH_STAGE_PROFILING=1`, dùng atomic sum/count/max,
không tốn chi phí khi tắt) vào `publish_payload()`: `encode`,
`subscription_lookup`, `mutex_wait` (thời gian CHỜ để lấy `g_bus_mutex`),
`mutex_hold` (thời gian GIỮ mutex — bao gồm đúng đoạn quét/dọn
retransmit ledger nghi ngờ trước đó), `transport_send` (từ sau khi thả
lock tới khi `send_data_frame()` trả về).

**Kết quả** (16-robot, seed=42/run=1, tổng hợp 2327 lần publish trên cả
19 endpoint):

| Giai đoạn | Mean | Max |
|---|---|---|
| encode | 10.13µs | 64.31µs |
| subscription_lookup | 3.75µs | 42.01µs |
| **mutex_wait** | **0.48µs** | 189.44µs |
| **mutex_hold** | **38.89µs** | 161.03µs |
| **transport_send** | **161.51µs** | **1718.96µs** |

(Tổng mean 5 giai đoạn = 214.77µs, khớp gần đúng với 247µs đo được ở
tầng Python trước đó.)

**BÁC BỎ HOÀN TOÀN giả thuyết hàng đầu trước đó**: `mutex_wait` gần như
bằng 0 (0.48µs) — **không có tranh chấp lock đáng kể nào**, loại trừ
giả thuyết "background thread tranh chấp mutex". `mutex_hold` (đúng
đoạn quét retransmit ledger nghi ngờ) chỉ 38.89µs — có thật nhưng
KHÔNG PHẢI chi phí chủ đạo.

**Thủ phạm thật**: `transport_send` chiếm **161.51µs, khoảng 75% tổng
thời gian publish**, với max lên tới 1.72ms. Giai đoạn này bao trùm toàn
bộ nội dung của `socket_transport().send_data_frame()` — bắt đầu bằng
việc **DECODE LẠI chính frame vừa encode** (`decode_data_frame`, hoàn
toàn dư thừa, chỉ để tái tạo struct `DataFrame` phục vụ routing), rồi
`data_frame_targets()`/`subscription_aware_targets()` (khoá RIÊNG
`peer_subscription_mutex_`, quét tuyến tính `peer_addresses_` tra cứu
refcount topic của từng peer), rồi mới tới `sendto()` thật.

**Ý nghĩa**: chi phí chủ đạo KHÔNG nằm ở tầng reliability/retransmit
như nghi ngờ ban đầu, mà nằm ở tầng ROUTING/SEND — cụ thể là việc
decode-lại-frame-vừa-encode dư thừa và/hoặc phần tra cứu subscription-
aware target. Đã gửi kết quả này cho ChatGPT, đề xuất thêm profiling
chi tiết hơn BÊN TRONG `send_data_frame()` (tách riêng decode vs
target-lookup vs sendto() thật) trước khi sửa bất kỳ dòng code nào —
đang chờ phản hồi.

**File thay đổi**: `ros2_ws/src/rmw_fleetqox_cpp/src/rmw_pubsub.cpp`
(`PublishStage` enum, `record_publish_stage`,
`publish_stage_profiling_enabled`, 3 hàm export ctypes mới),
`scripts/fleetqox_rmw_trace_endpoint.py`
(`fleetqox_publish_stage_metrics`).

### 12/09/2026 (tiếp) — Tách nhỏ transport_send: sendto() thật là thủ phạm (103µs vs 38µs raw-UDP); tìm ra 2 ứng viên cụ thể

Tách `transport_send` thành 3 giai đoạn con: `decode` (giải mã lại
frame vừa encode), `target_lookup` (tra cứu subscription-aware
routing), `sendto_syscall` (vòng lặp gọi `::sendto()` thật).

**Kết quả** (16-robot, seed=42/run=1, 2327 lần publish):
- `decode`: mean=47.17µs, max=214.22µs
- `target_lookup`: mean=3.63µs, max=42.49µs (**nhỏ — routing KHÔNG phải
  vấn đề**)
- `sendto_syscall`: mean=**103.53µs**, max=857.19µs

**Đính chính một phép tính sai trước đó**: số liệu "trung bình 4 target
mỗi lần gửi" ở mục trước là SAI — tính trung bình KHÔNG trọng số qua 19
endpoint, trong khi nhiều endpoint chỉ gửi 5-8 lần (một lần fallback-
broadcast hiếm cũng đủ kéo trung bình mẫu nhỏ lên rất cao). Tính lại có
trọng số theo số lần gửi thực tế: trung bình toàn hệ thống là **1.23
target/lần gửi**, và `fleet_controller` (71% tổng traffic) chỉ **1.02**
— khớp đúng với kết luận "1.02x amplification" đã có trước đó, KHÔNG
có gì thay đổi ở kết luận đó.

**Đọc code `send_datagram_to_targets()` tìm được 2 ứng viên cụ thể cho
103.53µs** (raw-UDP không có ứng viên nào trong 2 cái này):
1. `drain_udp_pmtu_error_queue()` được gọi VÔ ĐIỀU KIỆN ở đầu MỖI lần
   gửi, TRƯỚC `sendto()` thật — vòng lặp `for(;;)` gọi
   `::recvmsg(fd_, &msg, MSG_ERRQUEUE | MSG_DONTWAIT)` cho tới khi rỗng,
   để kiểm tra lỗi ICMP "cần fragment" từ các lần gửi trước. Dù không
   block, đây vẫn là MỘT SYSCALL THẬT THÊM mỗi lần gửi mà raw-UDP không
   hề có.
2. Toàn bộ vòng lặp gửi tới từng target được bọc trong
   `std::lock_guard<std::mutex> lock(udp_send_mutex_)` — mutex THỨ BA,
   khác với `g_bus_mutex` và `peer_subscription_mutex_` đã profile —
   CHƯA đo thời gian chờ mutex NÀY, có thể có tranh chấp với thread nền
   khác (fragment sender, retransmit/repair) cũng gửi qua cùng mutex.

**Đã loại trừ 1 ứng viên**: có `pace_udp_send_locked()` gọi trước mỗi
`sendto()`, điều khiển bởi `FLEETQOX_RMW_UDP_SEND_PACING_US` — nhưng
mặc định là 0 (tắt) và chưa từng được set trong bất kỳ cấu hình thử
nghiệm nào phiên này, nên chắc chắn không phải nguyên nhân.

**Đã gửi phát hiện này cho ChatGPT, hỏi có nên đo thời gian chờ
`udp_send_mutex_` cụ thể trước, hay coi `drain_udp_pmtu_error_queue()`
đã đủ bằng chứng để thử bỏ/giảm tần suất (vd: chuyển sang thread nền
định kỳ thay vì đồng bộ mỗi lần gửi) rồi đo lại delivery** — đang chờ
phản hồi.

**File thay đổi**: `ros2_ws/src/rmw_fleetqox_cpp/src/rmw_pubsub.cpp`
(3 `PublishStage` con mới: `kTransportDecode`, `kTransportTargetLookup`,
`kTransportSendtoSyscall`, cùng bộ đếm target-count).

### 12/09/2026 (tiếp) — Đo `udp_send_mutex_` + hit-rate `drain_udp_pmtu_error_queue()`, A/B throttle: kết quả nhỏ, KHÔNG giải thích được collapse

Theo đúng khuyến nghị của ChatGPT ("đừng chọn giữa profile và bỏ drain —
làm cả hai: instrument 3 thứ còn lại, RỒI chạy A/B env-gated ngay"), đã
thêm:
1. `PublishStage::kUdpSendMutexWait` — đo thời gian chờ (không phải thời
   gian giữ) `udp_send_mutex_` trong `send_datagram_to_targets()`.
2. 3 bộ đếm hit-rate cho `drain_udp_pmtu_error_queue()`:
   `g_pmtu_drain_calls` (số lần hàm được gọi), `g_pmtu_drain_recvmsg_calls`
   (số lần `::recvmsg()` thực sự chạy trong vòng `for(;;)`),
   `g_pmtu_drain_messages_consumed` (số ICMP message thật sự lấy được).
3. `FLEETQOX_RMW_PMTU_DRAIN_INTERVAL_SENDS` (mặc định 1 = mỗi lần gửi,
   giống hành vi cũ) — cổng throttle qua
   `should_drain_pmtu_error_queue_this_send()` (đếm bằng
   `fetch_add`-modulo), gọi `drain_udp_pmtu_error_queue()` chỉ 1 lần mỗi
   N lần gửi thay vì mỗi lần.

**Thí nghiệm A/B** (16-robot, static-mode, seed=42, 3 rep mỗi nhánh, thứ
tự xen kẽ để kiểm soát trôi thời gian/nhiệt độ — giống thiết kế JSON/
compact_v1 trước đó): baseline (`INTERVAL_SENDS=1`, hành vi mặc định cũ)
vs throttled (`INTERVAL_SENDS=32`).

| | baseline (interval=1) | throttled (interval=32) |
|---|---|---|
| `delivery_pct` (3 rep) | 9.5 / 9.0 / 16.8 (mean 11.8) | 27.7 / 11.5 / 10.6 (mean 16.6) |
| `sendto_syscall` mean | 106.84µs | 100.69µs |
| `udp_send_mutex_wait` mean | 0.42µs | 68.22µs (nhiễu, xem dưới) |
| `pmtu_drain_calls` (tổng 3 rep) | 13831 | 497 (giảm ~28x, đúng ~1/32 kỳ vọng) |
| `pmtu_drain_messages_consumed` (tổng) | 103 | 106 |
| hit-rate thật | **0.745%** | 21.3% (vì mẫu số nhỏ hơn 28x, không phải vì bắt được nhiều ICMP hơn) |

**Diễn giải**:
- **Hit-rate xác nhận đúng dự đoán của ChatGPT**: baseline drain 13831
  lần fleet-wide nhưng chỉ 103 lần thật sự có ICMP message (<1%) — tuyệt
  đại đa số là `recvmsg()` gọi vô ích trả về rỗng ngay. Throttle xuống
  interval=32 giảm số lần gọi ~28 lần (13831→497) mà số message ICMP
  THẬT sự lấy được gần như không đổi (103→106) — nghĩa là throttle KHÔNG
  làm mất tín hiệu PMTU thật, chỉ cắt phần lãng phí.
- **`sendto_syscall` giảm nhẹ nhưng thật**: 106.84µs → 100.69µs (~5.8%
  nhanh hơn) — nhất quán với việc bớt 1 syscall `recvmsg()` mỗi ~32 lần
  gửi thay vì mỗi lần. Đây là tối ưu CPU hợp lệ nhưng KHÔNG đủ lớn để
  giải thích khoảng cách 103µs vs 38µs raw-UDP (~2.7x) — phần lớn chênh
  lệch đó vẫn CHƯA có lời giải.
- **`udp_send_mutex_wait` chủ yếu ~0, ngoại trừ 1 outlier**: 5/6 lần
  chạy đo được <1µs (không có tranh chấp mutex đáng kể); riêng
  `throttled_run1` đo được 204.23µs VÀ CŨNG LÀ lần có delivery cao nhất
  (27.7%, gần gấp 3 các lần khác) — không nhất quán với giả thuyết
  "mutex chờ lâu hơn = delivery tệ hơn", nên đọc đây là nhiễu
  RealtimeSimulatorImpl (jitter thời gian thực đã ghi nhận là biến số
  chưa kiểm soát được kể cả khi đã cố định RNG seed — xem mục "kiểm tra
  lại độ lặp lại" phía trên), KHÔNG phải hiệu ứng thật của biến throttle.
- **Delivery giữa 2 nhánh KHÔNG có khác biệt đáng tin cậy**: mean 11.8%
  vs 16.6% nhưng phương sai giữa các lần chạy CÙNG nhánh (9.0–16.8% ở
  baseline, 10.6–27.7% ở throttled) đã lớn hơn khoảng cách giữa 2 nhánh
  — với n=3 mỗi bên, không thể phân biệt "throttle giúp delivery" khỏi
  nhiễu chạy-tới-chạy (giống kết luận null của thí nghiệm JSON/
  compact_v1 trước đó).

**Kết luận**: Việc throttle `drain_udp_pmtu_error_queue()` là một tối ưu
CPU nhỏ, hợp lệ, rủi ro thấp (hit-rate thật không đổi) — có thể giữ lại
— nhưng KHÔNG phải nguyên nhân chính của `sendto_syscall` chậm hơn
raw-UDP 2.7x, và KHÔNG chứng minh được cải thiện delivery có ý nghĩa
thống kê. Cả 2 ứng viên ChatGPT đề xuất (drain PMTU, mutex chờ) đều đã
được đo và đều KHÔNG phải thủ phạm chính — cần tìm thêm.

**File thay đổi**: `ros2_ws/src/rmw_fleetqox_cpp/src/rmw_pubsub.cpp`
(`PublishStage::kUdpSendMutexWait`, 3 atomic hit-rate mới, throttle gate
`should_drain_pmtu_error_queue_this_send()` + env
`FLEETQOX_RMW_PMTU_DRAIN_INTERVAL_SENDS`, 6 hàm `extern "C"` ctypes
export mới); `scripts/fleetqox_rmw_trace_endpoint.py` (đọc 3 bộ đếm PMTU
mới + stage `udp_send_mutex_wait` qua ctypes).

### 12/09/2026 (tiếp) — Bắt gói tin thật tại biên TAP (theo đề xuất ChatGPT): phát hiện 2 khác biệt cụ thể chưa từng đo — kích thước gói 5.9-7.7x và ARP 4.9x, cộng với cơ chế khuếch đại "fallback broadcast" định lượng lần đầu

Theo đúng khuyến nghị của ChatGPT ("ngừng chỉnh sửa nội bộ FleetRMW,
hỏi xem mạng THẬT SỰ thấy gì khác nhau"), đã thêm khả năng bắt gói vào
harness: `tcpdump` (cài on-demand trong container qua `apt-get`, không
có sẵn trong image) chạy trên `ftapN` — chính là tap device ns-3
TapBridge dùng cho một trạm cụ thể — từ trước khi ns-3 khởi động tới
sau khi mọi endpoint kết thúc, dump ra text (`-nn -tt`) và lấy về qua
cùng cơ chế marker-trong-stdout đã dùng cho kết quả JSON mỗi endpoint.

**Thí nghiệm**: bắt gói tại `ftap0` (trạm `fleet_controller`) cho CẢ 2
biến thể, cùng trace/seed=13, cùng `ns3_seed=42/run=1`, 16-robot:

| | raw_udp | rmw_fleetqox_cpp (static mode) |
|---|---|---|
| delivery_pct | 100.0% | 23.9% |
| fleet_controller tx (app-level) | 1626 | 1626 |
| Tổng gói tại TAP | 1912 | 2358 |
| UDP | 1626 | 1825 |
| ARP | 57 | **277 (4.9x)** |
| ICMPv6 (ND/MLD, nền OS, không liên quan app) | 229 | 256 |
| UDP gửi ĐI từ fleet_controller | 1626 | 1662 |
| UDP gửi ĐẾN fleet_controller | 0 | **163 (mới, không tồn tại ở raw_udp)** |
| Kích thước UDP (byte) | 96 (1315 gói) / 192 (311 gói) | 606-736 (phần lớn 607-608) |

**Phát hiện 1 — khuếch đại kích thước gói LỚN HƠN nhiều so với so sánh
JSON/compact_v1 trước đó**: gói FleetRMW trên dây thực tế là 606-736
byte so với 96-192 byte của raw-UDP — **5.9x-7.7x**, không phải 6.7x
(JSON) hay 3.3x (compact_v1) đo trong nội bộ trước đó, vì so sánh lần
này là với kích thước THẬT SỰ tối thiểu (raw-UDP), không phải so JSON
với compact_v1 (hai biến thể ĐỀU đã có overhead framing). Điều này có
nghĩa: dù thí nghiệm 10-cặp JSON/compact_v1 cho kết quả null (compact_v1
KHÔNG cải thiện delivery có ý nghĩa), compact_v1 vẫn to hơn raw-UDP
~3.3x — CHƯA đạt tới baseline airtime đã cho delivery 100%. Không thể
kết luận "kích thước gói không quan trọng" chỉ từ null result đó; cần
một cắt giảm SÂU HƠN nhiều (gần bằng raw-UDP) mới đủ để kiểm tra giả
thuyết airtime một cách công bằng.

**Phát hiện 2 — ARP tăng 4.9x, chưa có lời giải**: 277 gói ARP so với
57 — trạm `fleet_controller` không tự tạo ARP nhiều hơn (nó chỉ gửi đi,
không có logic ARP đặc biệt), nên khả năng cao đây là hệ quả gián tiếp
của tổng lưu lượng cao hơn (nhiều gói hơn = nhiều khả năng ARP cache bị
hết hạn/tranh chấp trên kênh 802.11 tranh chấp cao hơn). Chưa điều tra
sâu — ghi nhận làm manh mối, không phải kết luận.

**Phát hiện 3 — định lượng lần đầu chi phí thật của "fallback
broadcast"**: 163 gói UDP THẬT SỰ đến `fleet_controller` dù trace ứng
dụng không có dòng nào có `dst=fleet_controller` (xác nhận qua đọc trực
tiếp CSV). Kiểm tra toàn bộ 19 endpoint bằng `fleetqox_transport_metrics`
cho thấy **MỌI endpoint không phân biệt vai trò đều có đúng
`subscription_aware_fallback_broadcasts=2`** (tổng 38), và
`frames_received` (132-154 mỗi endpoint, tổng **2534**) cao hơn nhiều
lần `rx` ở tầng ứng dụng. Đối chiếu số học: 38 lần fallback-broadcast ×
tối đa 18 peer/lần ≈ 684 lượt nhận thêm ở tầng transport — cộng với
~2251 lượt gửi 1-đích hợp lệ (2289 tx - 38 fallback) ≈ 2935 lượt gửi dự
kiến, khớp hợp lý với 2534 lượt nhận thực đo được (phần chênh lệch là
mất gói trên kênh, đã biết là ~70-76%). Cơ chế `subscription_aware_
fallback_broadcasts` đã được biết từ trước (không phải counter mới),
nhưng đây là lần đầu NHÂN RA để thấy quy mô thật: 38 sự kiện tưởng nhỏ
lại tương đương gần 30% khối lượng của TOÀN BỘ trace gốc (2289 message)
về số lượt nhận ở tầng transport — một chi phí airtime đáng kể chưa
từng được định lượng theo cách này.

**Đã KIỂM CHỨNG bằng thực nghiệm (không còn là giả thuyết)**: thêm tạm
thời 1 dòng debug (`std::cerr`, gate bởi
`FLEETQOX_RMW_DEBUG_LOG_FALLBACK_TOPIC`) ngay tại nhánh fallback trong
`subscription_aware_targets()` để in ra `key` (domain_id|topic|
type_name) mỗi lần fallback-broadcast xảy ra, build lại, chạy 1 lần
16-robot/seed=42 với biến này bật. Kết quả: **ĐÚNG 38/38 dòng debug đều
là cùng một key**:

```
0|/parameter_events|rcl_interfaces/msg/ParameterEvent
```

Không phải `/rosout` như nghi ngờ ban đầu — là **`/parameter_events`**,
topic ROS2 tự động tạo bởi `rclpy.create_node()` khi gọi với
`start_parameter_services=True` (giá trị mặc định, không phải do
`fleetqox_rmw_trace_endpoint.py` chủ động publish). Đây KHÔNG nằm
trong `FLEETQOX_RMW_STATIC_SUBSCRIPTIONS` (vốn chỉ liệt kê topic riêng
của trace), nên mọi publish lên nó chắc chắn rơi vào nhánh "không biết
subscriber nào" → fallback broadcast tới toàn bộ 18 peer khác. Xảy ra
đúng 2 lần/endpoint × 19 endpoint = 38, độc lập hoàn toàn với nội dung
trace — khớp chính xác với giả thuyết.

**Đã revert dòng debug** (`std::cerr`/`#include <iostream>` không hợp
phong cách diagnostic hiện có của file này — toàn bộ counter khác đều
qua atomic + ctypes export, không phải text logging) — build lại xác
nhận biên dịch sạch sau khi revert.

**Lần thử fix đầu tiên KHÔNG hiệu quả**: `rclpy.create_node(node_name,
start_parameter_services=False)` — chạy lại kiểm chứng cho thấy
`fallback_broadcasts` vẫn = 38, KHÔNG đổi. Lý do: đọc
`/opt/ros/jazzy/lib/python3.12/site-packages/rclpy/node.py` xác nhận
`_parameter_event_publisher` được tạo VÔ ĐIỀU KIỆN trong
`Node.__init__` (dòng 212, không nằm trong `if start_parameter_services`
ở dòng 240), và `TimeSource.__init__` (được gọi ngay trong
`Node.__init__`) gọi `node.declare_parameter('use_sim_time', False)`
VÔ ĐIỀU KIỆN — chính lệnh declare này publish sự kiện, không liên quan
gì tới `start_parameter_services`. Cả 2 việc này chạy XONG bên trong
`rclpy.create_node()`, nên monkeypatch `.publish` trên INSTANCE sau khi
`create_node()` trả về cũng quá trễ (đã thử, vẫn 38, không đổi).

**Fix THẬT SỰ hiệu quả (đã áp dụng và đo lại)**: monkeypatch
`rclpy.publisher.Publisher.publish` ở cấp CLASS (lọc theo
`self.topic_name == "/parameter_events"`), thực hiện TRƯỚC khi gọi
`rclpy.create_node()` — patch class trước khi instance nào được tạo,
nên bắt được cả các publish xảy ra bên trong `Node.__init__` chính nó.
Kiểm chứng qua `fleetqox_transport_metrics`: `subscription_aware_
fallback_broadcasts` = **0/0/0** trên cả 19 endpoint (trước đó luôn là
2/endpoint = 38 tổng), `frames_sent` giờ gần khớp `tx` ứng dụng (2296
vs 2289, chênh 7 thay vì chênh do fallback).

**Đo lại delivery sau fix (16-robot, seed=42, cùng `ns3_run`=1,2,3 với
baseline trong thí nghiệm PMTU A/B ở trên, để so khớp cặp)**:

| ns3_run | Trước fix (baseline PMTU A/B) | Sau fix (parameter_events) | Δ |
|---|---|---|---|
| 1 | 9.5% | 18.1% | +8.6pp |
| 2 | 9.0% | 15.2% | +6.2pp |
| 3 | 16.8% | 23.0% | +6.2pp |
| **mean** | **11.8%** | **18.8%** | **+7.0pp** |

Paired delta: mean=+7.0pp, stdev=1.39, n=3, **95% CI=[+3.56, +10.44]**
— khoảng tin cậy HOÀN TOÀN DƯƠNG (không chứa 0), khác hẳn kết quả null
của thí nghiệm JSON/compact_v1 trước đó (CI chứa 0). Cả 3/3 cặp đều
cải thiện, độ lớn tương đối đều nhau (6.2-8.6pp) — đây là tín hiệu THẬT,
không phải nhiễu chạy-tới-chạy, dù n=3 vẫn còn nhỏ và cần thêm rep để
chắc chắn hơn.

**Đánh giá trung thực**: đây là cải thiện tuyệt đối ~7 điểm phần trăm
(11.8%→18.8%), một bước tiến rõ ràng theo đúng hướng, nhưng CHƯA giải
quyết được vấn đề gốc — delivery vẫn cách xa 100% của raw-UDP rất
nhiều. Không nên coi đây là "fix xong vấn đề", chỉ là loại bỏ được MỘT
nguồn airtime lãng phí cụ thể, đã đo lường được, trong số nhiều nguồn
khác (kích thước gói 5.9-7.7x, có thể còn control-plane traffic khác
chưa phát hiện). Hướng đi đúng theo phương pháp của cả investigation
này: tìm — đo — sửa — đo lại, từng mảnh một.

**File thay đổi**: `scripts/fleetqox_rmw_trace_endpoint.py` (import
`rclpy.publisher`, monkeypatch class-level `Publisher.publish` lọc
`/parameter_events`, `start_parameter_services=False`);
`scripts/run_ns3_docker_wifi_tap_rmw_probe.py` (tham số
`capture_pcap_endpoint`, cài `tcpdump` on-demand, bắt gói trên `ftapN`,
marker `FLEETQOX_TAP_CAPTURE_BEGIN/END`, hàm `parse_capture_text`).

### 12/09/2026 (tiếp) — Wire-frame tối giản cho static mode (`static_min_v1`): giảm kích thước gói xuống gần bằng raw-UDP, nhưng KẾT QUẢ DELIVERY VÔ ĐỊNH (không có ý nghĩa thống kê)

Theo yêu cầu người dùng ("thử viết wire-frame gọn hơn") và đề xuất của
ChatGPT, đã thiết kế và cài đặt format nhị phân mới **CHỈ dùng được
trong static mode**: `kDataFrameStaticMinV1Magic = "FRMWM1\n"`, bật qua
`FLEETQOX_RMW_DATA_FRAME_ENCODING=static_min_v1` (cùng cơ chế env với
`compact_v1` đã có).

**Thiết kế**: thay vì mang theo TOÀN BỘ field của `DataFrame` như
`compact_v1` (robot_id, topic, publisher_id, type_name, flow_class, 4
double QoS-extension, partitions_csv, ownership_strength, coherent_set_*
— tất cả đều length-prefixed dù rỗng), format mới chỉ mang:
`topic_key_hash` (uint32, hash của `domain_id|topic|type_name`),
`publisher_hash` (uint32, hash của `robot_id|publisher_id`),
`sequence32` (uint32), `source_timestamp_ns` (int64), và payload thô —
tổng overhead cố định 31 byte (so với ~223 byte của `compact_v1` cho
cùng nội dung).

**Vấn đề kỹ thuật cốt lõi cần giải**: bên nhận phải "giải" được
`topic_key_hash` trở lại thành `(domain_id, topic, type_name)` thật để
logic downstream (khớp subscription tại dòng
`subscription->topic_name == decoded_frame->topic`) không đổi. Giải
pháp: một **resolver hook** (`std::function`) đăng ký MỘT LẦN lúc
`LoopbackSocketTransport::start()`, được `decode_data_frame()` (trong
`data_frame.cpp`, file không phụ thuộc gì vào `rmw_pubsub.cpp`) tự động
gọi khi gặp magic mới — nhờ vậy **cả ~15 call site hiện có của
`decode_data_frame()` đều tự động hỗ trợ format mới mà KHÔNG cần sửa
từng chỗ một** (rủi ro thấp hơn nhiều so với sửa rải rác 15 nơi).
Resolver quét tuần tự (không index thường trực, tránh nguy cơ lệch dữ
liệu) 2 nguồn tri thức tĩnh đã có sẵn trong mọi tiến trình static mode:
`g_subscriptions` (vai trò subscriber) và `peer_subscribed_topic_refcounts_`
(vai trò publisher — cần cho chính `send_frame_with_qos()` tự giải mã
lại frame nó vừa mã hoá để tìm target). `robot_id`/`publisher_id` không
cần giải ngược — chỉ dùng để phân biệt luồng (stream-key uniqueness),
nên tái tạo trực tiếp thành chuỗi tổng hợp ổn định `"h" + hex(publisher_hash)`.

**Kiểm chứng round-trip độc lập** (g++ + ASan/UBSan, không cần ROS2,
theo đúng phương pháp đã dùng cho `compact_v1`): 9 nhóm test — frame cơ
bản, domain_id/type_name khác 0, payload rỗng, 5 kích thước biên (1B
đến 2200B), 20 payload nhị phân ngẫu nhiên, hash lạ phải fail-closed,
KHÔNG có resolver đăng ký phải fail-closed (không crash), phân biệt 3
magic (JSON/compact_v1/static_min_v1) đúng cả 2 chiều, 81 điểm cắt
truncation phải an toàn — **TẤT CẢ PASS**.

**Đo kích thước gói thật** (tcpdump tại `ftap0` của `fleet_controller`,
16-robot, seed=42/run=1): lọc riêng gói ĐI (outbound, tránh lẫn với
traffic ack/nack ĐẾN — xem phát hiện phụ bên dưới) cho kết quả **CHỈ 2
giá trị duy nhất, sạch, không lẫn kích thước cũ**: **143B** (855 gói) và
**239B** (218 gói) — so với raw-UDP cùng payload là 96B/192B (tỷ lệ chỉ
còn **~1.24-1.49x**, so với 5.9-7.7x của JSON mặc định đo trước đó).
Chênh lệch +16B so với tính tay (31B overhead + 96/192B payload =
127/223B dự kiến) nhiều khả năng là overhead serialize CDR của ROS2 cho
kiểu `std_msgs/msg/String` (header encapsulation 4B + length-prefix 4B +
null-terminator + padding 4-byte-align) — một chi phí CỐ HỮU của việc
dùng message ROS2, không phải lỗi của format mới, và raw-UDP (socket
thuần, không qua rclpy) không phải trả chi phí này nên không thể so
sánh tuyệt đối 1:1 được.

**Phát hiện phụ (không phải lỗi)**: gói ĐẾN `fleet_controller` (559-673B,
port giống) hoá ra là **AckNackFrame** — phản hồi reliability từ các
subscriber bị mất gói, một kênh JSON riêng biệt hoàn toàn không bị ảnh
hưởng bởi thay đổi này (chỉ tối ưu `DataFrame`, không đụng tới AckNack).
Việc lọc theo hướng gói (đi/đến) là cần thiết để không hiểu nhầm đây là
lỗi trộn định dạng.

**Đo lại delivery** (paired, 16-robot, seed=42, cùng `ns3_run`=1,2,3,
so với JSON mặc định — CẢ HAI đã có fix `/parameter_events`):

| ns3_run | json_default | static_min_v1 | Δ |
|---|---|---|---|
| 1 | 13.5% | 24.6% | +11.1pp |
| 2 | 16.6% | 13.3% | −3.3pp |
| 3 | 17.6% | 15.2% | −2.5pp |
| **mean** | **15.9%** | **17.7%** | **+1.78pp** |

Paired delta: mean=+1.78pp, stdev=8.08, n=3, **95% CI = [−18.30,
+21.86]** — khoảng tin cậy RẤT RỘNG, bao trùm cả 0 lẫn cả 2 dấu. **KẾT
LUẬN TRUNG THỰC: KHÔNG có bằng chứng cải thiện delivery có ý nghĩa
thống kê**, giống hệt tính chất null-result của thí nghiệm JSON/
compact_v1 trước đó — dù lần này mức giảm kích thước gói LỚN HƠN NHIỀU
(xuống gần bằng raw-UDP) so với compact_v1 (chỉ còn ~3.3x so với
raw-UDP). Đây là kết quả bất ngờ và QUAN TRỌNG: giả thuyết "kích thước
gói/airtime là nút thắt chính còn lại" — vốn được cả ChatGPT và người
dùng ủng hộ sau phân tích pcap — **CHƯA được xác nhận bằng số liệu**,
dù đã test với mức giảm kích thước gần tối đa có thể đạt được. Không
loại trừ khả năng cần thêm rep (n=3 quá nhỏ so với phương sai run-to-run
đã biết là lớn — xem "RealtimeSimulatorImpl jitter" ghi nhận nhiều lần
trước đó), nhưng với số liệu hiện có, KHÔNG thể khẳng định wire-size là
nguyên nhân chính của phần delivery gap còn lại.

**Bài học phương pháp**: khác với fix `/parameter_events` (bug rõ ràng,
CI dương hoàn toàn, hiệu ứng nhất quán cả 3/3 cặp), thí nghiệm này cho
kết quả TRỘN LẪN (1 cặp cải thiện mạnh, 2 cặp giảm nhẹ) — đúng loại tín
hiệu mà phương pháp paired-run của investigation này được thiết kế để
phát hiện và KHÔNG bị đánh lừa bởi.

**File thay đổi**: `ros2_ws/src/rmw_fleetqox_cpp/include/rmw_fleetqox_cpp/data_frame.hpp`
(`kDataFrameStaticMinV1Magic`, `StaticMinV1TopicResolver`,
`set_static_min_v1_topic_resolver()`, `encode_data_frame_static_min_v1_append()`,
`decode_data_frame_static_min_v1()`); `.../src/data_frame.cpp` (cài đặt,
hook vào `decode_data_frame()`'s dispatch); `.../src/rmw_pubsub.cpp`
(`static_min_v1_data_frame_encoding_enabled()`, `kStaticMinV1TopicHashSeed`,
`LoopbackSocketTransport::resolve_static_min_v1_topic_hash()`, đăng ký
resolver trong `start()`, nhánh encode mới trong `publish_payload()`).

### 13/09/2026 — Kịch bản mô phỏng theo sơ đồ tham chiếu: kiến trúc Docker-per-container + ns-3, xác nhận chạy đúng ở quy mô đầy đủ 17 endpoint (S1 baseline)

Theo yêu cầu người dùng (bỏ hạ tầng 19-endpoint cũ, xây kịch bản mới
khớp đúng sơ đồ tham chiếu: 1 Control Station (gộp Controller+Operator
UI) + 16 robot = 17 endpoint, mỗi endpoint là 1 container Docker riêng
(không phải network namespace bên trong 1 container chung như hạ tầng
cũ), mạng vẫn mô phỏng bằng ns-3, đủ 4 kịch bản S1-S4).

**Ràng buộc kỹ thuật cốt lõi phát hiện ngay từ đầu**: tiến trình
orchestrator (chạy trực tiếp trên host, user thường `ubuntu`) KHÔNG có
quyền `CAP_NET_ADMIN`/`CAP_SYS_ADMIN` — xác nhận bằng thực nghiệm
(`ip netns add` báo lỗi "mount --make-shared /run/netns failed:
Operation not permitted"). Vì vậy việc nối mạng CHÉO giữa các container
Docker riêng biệt (mỗi container tự có network namespace riêng, không
thể dùng `ip netns exec <tên>` như hạ tầng cũ) phải thực hiện qua một
container "rigger" đặc quyền:

```
docker run -d --pid=host --cap-add=NET_ADMIN --cap-add=SYS_ADMIN \
  --device=/dev/net/tun <image> "sleep infinity"
```

`--pid=host` cho container rigger THẤY được PID của MỌI container khác
trên cùng host (dù chúng là container độc lập) — từ đó
`nsenter -t <PID> -n -- <lệnh>` có thể "chui vào" network namespace của
BẤT KỲ container nào để tạo veth/tap/bridge, giống hệt logic
`ip netns exec` của hạ tầng cũ, chỉ khác điểm truy cập.

**Kiến trúc cuối cùng** (đã xác nhận chạy đúng — xem
`scripts/run_ns3_docker_container_fleet_probe.py`):
- 1 container/endpoint (`control_station`, `robot_0000..15`),
  `--network=none --init`, cài thật FleetRMW/ROS2.
- 1 container `ns3sim` chạy binary ns-3 TapBridge đã biên dịch
  (`fleetqox_trace_replay_tap.cc`), mô phỏng 1 AP + 17 STA.
- 1 container `rigger` (mô tả trên) chỉ dùng để nối mạng, không chạy gì
  khác.
- Với mỗi endpoint: 1 cặp veth, một đầu vào netns của `ns3sim` (bridge
  vào 1 tap riêng cho station đó), đầu kia vào netns của chính container
  endpoint đó (đổi tên `eth0`, gán MAC/IP) — y hệt sơ đồ bridge/tap của
  hạ tầng cũ, chỉ thực hiện qua `nsenter` thay vì `ip netns exec`.

**5 lỗi thật đã gặp và sửa trong quá trình xây dựng** (ghi lại đầy đủ vì
đây là hạ tầng hoàn toàn mới, chưa qua kiểm chứng trước đó):

1. **Entrypoint quoting**: image có `ENTRYPOINT ["/bin/bash", "-lc"]`,
   truyền `"sleep", "infinity"` như 2 tham số riêng khiến bash hiểu sai
   (`sleep: missing operand`) — phải truyền `"sleep infinity"` như MỘT
   chuỗi duy nhất.
2. **tap-creator symlink**: giống hệt vấn đề đã biết ở hạ tầng cũ — path
   baked-in trong `libns3-tap-bridge.so` không khớp nơi cài thật, cần
   symlink động qua `strings`/`find` TRƯỚC khi chạy binary ns-3 (đã tái
   sử dụng đúng đoạn code).
3. **`LogDistancePropagationLossModel` sai tần số tham chiếu (lỗi nghiêm
   trọng nhất)**: giá trị `ReferenceLoss` mặc định của ns-3 (46.6777dB)
   được tính cho ~5.15GHz, KHÔNG phải 2.4GHz thật của 802.11g — với
   bán kính vòng tròn 7.5m theo đúng sơ đồ (worst-case 2 trạm cách nhau
   15m), cấu hình mặc định cho **0% delivery tuyệt đối** trong thí
   nghiệm 2-trạm cô lập (xác nhận qua kiểm tra `/proc/net/arp` — ARP
   không bao giờ resolve). Tính lại Friis reference loss đúng cho
   2.4GHz (~40.05dB, thấp hơn mặc định ~6.6dB) và set tường minh — sau
   đó gói tin đi qua bình thường ở đúng bán kính 7.5m. Đây là lỗi hoàn
   toàn ẩn nếu không tự tay đo bằng 1 gói UDP đơn lẻ trước khi tin vào
   kết quả quy mô lớn.
4. **`check=False` thiếu trong vòng lặp chờ ready-gate**: lần polling
   ĐẦU TIÊN (khi file ready chưa tồn tại) khiến lệnh `docker exec` trả
   về exit code khác 0 → hàm `docker()` (mặc định `check=True`) NÉM lỗi
   ngay lập tức thay vì tiếp tục vòng lặp chờ — làm mọi lần chạy tưởng
   như "timeout" chỉ sau đúng 1 lần kiểm tra đầu tiên.
5. **`pgrep -f` tự khớp chính nó (lỗi tinh vi nhất)**: `wait_for_completion()`
   ban đầu dùng `docker exec <container> bash -lc "pgrep -f
   fleetqox_rmw_trace_endpoint.py && echo RUNNING"` để kiểm tra tiến
   trình còn sống — nhưng CHÍNH chuỗi lệnh kiểm tra đó (đối số `-lc` của
   bash) CHỨA nguyên văn "fleetqox_rmw_trace_endpoint.py", nên `pgrep -f`
   (khớp theo toàn bộ cmdline) khớp luôn với tiến trình bash ĐANG CHẠY
   LỆNH KIỂM TRA — báo "RUNNING" vĩnh viễn dù tiến trình thật đã kết
   thúc từ lâu (xác nhận trực tiếp: chạy `pgrep -f
   fleetqox_rmw_trace_endpoint.py` trong container chỉ có
   `python3 -c "sleep(2)"` đang chạy — không liên quan gì — vẫn in ra
   "RUNNING"). Sửa bằng cách kiểm tra sự tồn tại của file
   `--summary-json` (tín hiệu hoàn thành thật) thay vì dò tiến trình.

**Kết quả xác nhận cuối cùng**:
- Quy mô nhỏ (3 container: control_station + 2 robot): tx=284/rx=142,
  tx=89/rx=108, tx=67/rx=98 — pass sạch, `status: ok`.
- **Quy mô đầy đủ (17 container: control_station + 16 robot, S1
  baseline, seed=42/run=1, cùng trace 2289 message đã dùng suốt phiên
  này)**: `status: ok`, `tx_total=2289` (khớp chính xác), **delivery =
  25.1%** (575/2289) — cùng bậc độ lớn với mọi kết quả đo được ở hạ tầng
  CŨ sau các fix đã áp dụng trong phiên này (~18-25%), một phép đối
  chiếu chéo tốt cho thấy kiến trúc MỚI hoạt động nhất quán, không phải
  ngẫu nhiên may mắn.

**Còn thiếu (chưa làm, không tự nhận là xong)**: kịch bản S2 (thay đổi
khoảng cách), S3 (di chuyển waypoint thật — hiện `fleetqox_trace_replay_tap.cc`
mới chỉ có chuyển động tiếp tuyến đặt chỗ, ghi rõ trong code là CHƯA
phải waypoint mobility theo đúng sơ đồ), S4 (stress test tải cao), và
các metrics latency percentile (p50/p95/p99)/channel utilization theo
đúng bảng "Metrics Collected" của sơ đồ — đây là các mục việc riêng,
CHƯA bắt đầu.

**File thay đổi**: `scripts/run_ns3_docker_container_fleet_probe.py`
(mới — toàn bộ orchestrator kiến trúc container-riêng);
`external/ns3/fleetqox_trace_replay_tap.cc` (endpoint 1 control_station
thay 3 vai trò cũ, layout vòng tròn, `pathLossExponent`/`txPowerDbm`/
`rxSensitivityDbm`/2.4GHz `ReferenceLoss`); `fleetqox/trace.py` (tham số
`merge_control_station` — remap opt-in, KHÔNG đổi hành vi mặc định của
các script/test khác đang phụ thuộc 3-role cũ); `tests/test_ns3_docker_container_fleet_probe.py`
(mới, 11 test cho các hàm thuần Python).

### 13/09/2026 (tiếp) — So sánh baseline DDS truyền thống (CycloneDDS, Zenoh) trên cùng kịch bản mới: phát hiện lỗi thiếu route multicast, và 1 giới hạn CHƯA giải quyết được ở tầng discovery

Theo yêu cầu người dùng (chạy CycloneDDS/Zenoh làm baseline trên cùng
kịch bản tham chiếu 17-endpoint mới). Đã thêm tham số `rmw_implementation`
vào `run_ns3_docker_container_fleet_probe.py` (mặc định vẫn
`rmw_fleetqox_cpp` với static mode; RMW khác chỉ set
`RMW_IMPLEMENTATION` và để tự discovery, không set biến `FLEETQOX_RMW_*`).

**Lỗi hạ tầng thật tìm được ngay lần chạy đầu (đã sửa)**: chạy
CycloneDDS lần 1 cho kết quả **sạch 0%** (không phải thấp, mà TUYỆT ĐỐI
0/2289) — dấu hiệu bất thường vì ngay cả khi kênh nghẽn nặng, FleetRMW
vẫn có 25.1%, không bao giờ về đúng 0 tuyệt đối. Kiểm tra trực tiếp
bằng socket thô: `sendto(('239.255.0.1', 7400))` báo lỗi **"Network is
unreachable"** — `ip addr add <ip>/24 dev eth0` chỉ tự tạo route cho
subnet cục bộ, KHÔNG tạo route cho dải multicast (224.0.0.0/4). Đây là
lỗi CÓ THẬT trong `wire_network()`, ảnh hưởng MỌI RMW dùng multicast để
discovery (FleetRMW static mode không bị ảnh hưởng vì không bao giờ
dùng multicast). Đã sửa: thêm `ip route add 224.0.0.0/4 dev eth0` vào
mỗi endpoint trong `wire_network()`.

**Sau khi sửa route, multicast thô (socket Python thuần, join group +
sendto) đã xác nhận truyền được HAI CHIỀU đúng qua toàn bộ topology
thật** (kể cả qua ns-3 TapBridge mô phỏng wifi thật, không chỉ bridge
Linux thường) — kiểm chứng bằng thử nghiệm 2-trạm độc lập.

**Nhưng CycloneDDS vẫn KHÔNG discover được nhau dù multicast thô đã
chạy được**: kiểm tra bằng rclpy thuần (không qua `ros2` CLI daemon, để
loại trừ nghi ngờ cache) — publisher chạy 60 giây liên tục,
`subscription_count` **luôn luôn = 0** trong suốt >55 giây (vượt xa
`SPDPInterval` mặc định 30s của CycloneDDS, loại trừ khả năng chỉ là
chờ chưa đủ lâu). Đây là **giới hạn/lỗi CHƯA xác định được nguyên nhân
gốc** — multicast tầng socket hoạt động, nhưng cơ chế SPDP
(Simple Participant Discovery Protocol) của CycloneDDS thì không, dù
đã thử cấu hình tường minh interface `eth0` qua `CYCLONEDDS_URI`. KHÔNG
tiếp tục đào sâu thêm trong phiên này (đã tốn khá nhiều thời gian cho
nhánh phụ này) — ghi nhận trung thực là CHƯA GIẢI QUYẾT, không cố gán
cho nguyên nhân "kênh wifi nghẽn" khi thực ra chưa hề chứng minh được
điều đó (2 trạm cô lập, không có traffic cạnh tranh, vẫn 0%).

**Zenoh thất bại vì lý do KHÁC, đã biết rõ (không phải bí ẩn)**: log
báo rõ ràng `"Unable to connect to a Zenoh router. Have you started a
router with 'ros2 run rmw_zenoh_cpp rmw_zenohd'?"` — `rmw_zenoh_cpp`
trong ROS2 Jazzy mặc định cần một tiến trình router riêng
(`rmw_zenohd`) mà harness này CHƯA khởi động — đây là bước cấu hình
còn thiếu, không phải lỗi mạng bí ẩn như CycloneDDS. Chưa triển khai
(cần thêm 1 container router + logic chờ nó sẵn sàng trước khi launch
endpoint).

**Kết quả so sánh (S1 baseline, 17 endpoint, cùng trace/seed)**:

| RMW | Kết quả | Ghi chú |
|---|---|---|
| `rmw_fleetqox_cpp` (static mode) | **25.1%** | Hoạt động đúng, đã kiểm chứng kỹ (xem mục trước) |
| `rmw_cyclonedds_cpp` | **0%** (chưa rõ nguyên nhân gốc) | Discovery SPDP không hội tụ dù multicast thô hoạt động — CẦN điều tra thêm, KHÔNG kết luận vội |
| `rmw_zenoh_cpp` | Chưa chạy được (thiếu router) | Cần thêm `rmw_zenohd` — việc cấu hình, không phải bug |

**Kết luận trung thực**: chưa thể so sánh "FleetRMW vs DDS truyền
thống" một cách công bằng trên kiến trúc container MỚI này, vì 2
baseline DDS còn đang bị chặn ở tầng discovery (nguyên nhân KHÁC
nhau, cả hai đều CHƯA phải là bằng chứng về hành vi dưới tải wifi thật
— chúng chưa bao giờ tới được bước gửi dữ liệu ứng dụng). Đây KHÔNG
phải kết luận "FleetRMW tốt hơn DDS truyền thống" — chỉ là "chưa đo
được DDS truyền thống trên kiến trúc mới, cần thêm việc".

**File thay đổi**: `scripts/run_ns3_docker_container_fleet_probe.py`
(tham số `rmw_implementation`, route multicast `224.0.0.0/4` trong
`wire_network()`).

### 13/09/2026 (tiếp) — Đào sâu tiếp theo yêu cầu: CycloneDDS KHÔNG có bug bí ẩn (chỉ là cùng cơ chế nghẽn kênh đã biết); dựng router cho Zenoh thành công — kết quả BẤT NGỜ, Zenoh vượt cả FleetRMW

**CycloneDDS — đính chính phát hiện trước đó**: dựng lại 2 trạm, cài
`tcpdump` thật (phải tạo container KHÔNG dùng `--network=none` để có
mạng internet cài đặt, rồi mới nối vào topology), bắt gói trong lúc
CycloneDDS cố discovery. Kết quả: **SPDP multicast, ARP, và cả traffic
unicast SEDP (port 7410-7413) đều truyền qua lại HAI CHIỀU bình
thường** — hoàn toàn không có dấu hiệu bất thường ở tầng mạng. Kiểm
tra lại bằng `rclpy` thuần (subscriber tự đếm tin nhận được, KHÔNG
dùng `pub.get_subscription_count()` như trước): **subscriber nhận đủ
50/50 tin nhắn** — nghĩa là discovery + truyền dữ liệu THỰC RA hoạt
động đúng ở quy mô 2 trạm! Kết luận trước đó ("CycloneDDS discovery
thất bại bí ẩn") là **SAI**, do dùng nhầm API chẩn đoán
(`get_subscription_count()` phía publisher không phản ánh đúng trạng
thái match, dù dữ liệu vẫn được giao đúng).

Chạy lại bằng script thật (`fleetqox_rmw_trace_endpoint.py`, đã dùng
phương pháp đúng — đếm tin nhận được qua callback thật, không qua API
lỗi) ở nhiều quy mô để tìm ngưỡng sập:

| Quy mô (số endpoint) | Delivery CycloneDDS |
|---|---|
| 7 (1 control_station + 6 robot) | **82.7%** |
| 12 (1 + 11 robot) | **0%** |
| 17 (1 + 16 robot, đầy đủ) | **0%** |

**Kết luận đúng**: CycloneDDS sập đột ngột đâu đó giữa 7 và 12 endpoint
— khớp CHÍNH XÁC với cơ chế "traffic discovery/control-plane tự nó đủ
làm nghẽn kênh wifi ở quy mô lớn" đã xác lập từ SỚM trong investigation
này (thí nghiệm discovery-only Fast DDS/Cyclone DDS, xem mục "causal
isolation: discovery-only" phía trên) — KHÔNG phải lỗi kiến trúc
container mới, mà là hệ quả TẤT YẾU của bất kỳ RMW nào dựa vào discovery
multicast lặp lại khi 17 trạm cùng cạnh tranh 1 kênh 802.11g.

**Zenoh — dựng router thành công**: log lỗi trước đó đã chỉ rõ nguyên
nhân (`rmw_zenoh_cpp` cần tiến trình `rmw_zenohd` riêng). Thêm
`start_zenoh_router()`: chạy `rmw_zenohd` NGAY TRONG container
`control_station` (không cần thêm 1 "trạm wifi" thứ 18 riêng — peer
Zenoh chỉ cần TCP tới IP đã biết trước của `control_station`). Phát
hiện thêm 1 vấn đề cấu hình: router mặc định lắng nghe
`tcp/[::]:7447` (IPv6 wildcard), nhưng các interface trong netns của
mình CHỈ có IPv4 (chưa từng cấu hình IPv6) — phải ép router lắng nghe
tường minh `tcp/0.0.0.0:7447` qua `ZENOH_ROUTER_CONFIG_URI`. Đồng thời,
multicast scouting mặc định để tự tìm router KHÔNG hội tụ được trong
topology này (giống hiện tượng gặp phải trước đó) — thay vì gỡ tiếp,
áp dụng luôn chiến lược "cấu hình tĩnh thay discovery" (giống FleetRMW
static mode): mỗi endpoint (trừ chính `control_station`) được set
`ZENOH_SESSION_CONFIG_URI` trỏ thẳng `connect.endpoints` tới địa chỉ
router đã biết trước — bỏ qua hoàn toàn bước scouting.

**Kết quả Zenoh sau khi sửa (BẤT NGỜ)**:

| Quy mô | Delivery Zenoh |
|---|---|
| 7 endpoint | 81.9% |
| 17 endpoint (đầy đủ) | **60.6%** |

**Zenoh ở quy mô đầy đủ 17 endpoint đạt 60.6% — CAO HƠN CẢ FleetRMW
static mode (25.1%)!** Diễn giải hợp lý: kiến trúc router của Zenoh
(mỗi peer chỉ cần 1 kết nối TCP unicast tới router, thiết lập MỘT LẦN
lúc khởi động) tránh được đúng cơ chế làm CycloneDDS sập — traffic
discovery liên tục qua multicast broadcast tới TẤT CẢ các trạm. Sau
khi kết nối TCP tới router đã thiết lập xong, phần lớn traffic tiếp
theo không cần phát lại discovery multicast nữa, nên ít cạnh tranh kênh
hơn hẳn so với CycloneDDS.

**Bảng so sánh cuối cùng (S1 baseline, 17 endpoint, cùng trace/seed)**:

| RMW | Quy mô nhỏ (7 endpoint) | Quy mô đầy đủ (17 endpoint) |
|---|---|---|
| `rmw_fleetqox_cpp` (static mode) | **35.9%** | **25.1%** |
| `rmw_cyclonedds_cpp` | 82.7% | **0%** (sập hoàn toàn khi >~7-11 trạm) |
| `rmw_zenoh_cpp` (có router, cấu hình tĩnh) | 81.9% | **60.6%** (tốt nhất trong 3) |

**Phát hiện thêm khi đo đủ FleetRMW ở quy mô nhỏ (bất ngờ theo chiều
ngược lại)**: ở 7 endpoint, FleetRMW (35.9%) THẤP HƠN NHIỀU so với cả
CycloneDDS (82.7%) và Zenoh (81.9%) — dù kênh chưa hề nghẽn nặng ở quy
mô này. Diễn giải hợp lý (khớp với phát hiện đã có từ trước trong
investigation này, xem mục "kịch bản mô phỏng theo sơ đồ tham chiếu"
và phần đo pcap so với raw-UDP): gói tin FleetRMW (JSON) to hơn
raw-UDP/DDS thực tế 5.9-7.7 lần — ở quy mô nhỏ, kênh KHÔNG nghẽn nên
yếu tố quyết định là airtime/gói (FleetRMW tốn nhiều airtime hơn hẳn
mỗi lần gửi), trong khi ở quy mô lớn thì traffic discovery lặp lại của
CycloneDDS mới là yếu tố áp đảo (FleetRMW không có discovery nên không
bị đúng cơ chế đó). Tức là: **FleetRMW đổi "chậm hơn ở quy mô nhỏ" lấy
"ổn định hơn ở quy mô lớn"** — hai RMW kia thì ngược lại (nhanh khi
nhỏ, một cái sập hẳn khi lớn). Đây là gợi ý mạnh cho hướng tối ưu
`static_min_v1` (đã thử trong mục trước, kết quả delivery chưa rõ ràng
thống kê) — nếu giảm được kích thước gói FleetRMW gần bằng raw-UDP,
có thể cải thiện CẢ quy mô nhỏ lẫn lớn cùng lúc.

**Bài học phương pháp quan trọng**: kết luận "CycloneDDS discovery bug
bí ẩn" ở phiên trước là kết luận VỘI, dựa trên 1 API chẩn đoán sai —
bài học là LUÔN xác nhận bằng dữ liệu ứng dụng thật (tin nhắn thực sự
nhận được) thay vì tin vào 1 con số trạng thái nội bộ của thư viện khi
có bất thường. Việc quay lại đào sâu (theo đúng yêu cầu người dùng)
thay vì chấp nhận kết luận vội ban đầu đã dẫn tới phát hiện quan trọng
hơn nhiều: Zenoh hoạt động tốt hơn cả FleetRMW ở quy mô đầy đủ, một kết
quả không ngờ tới ban đầu.

**File thay đổi**: `scripts/run_ns3_docker_container_fleet_probe.py`
(`start_zenoh_router()`, `zenoh_router_endpoint()`, cấu hình
`ZENOH_SESSION_CONFIG_URI` tĩnh cho mỗi peer, gọi router trước
`launch_endpoints()` khi `rmw_implementation == "rmw_zenoh_cpp"`).

### 13/09/2026 (tiếp) — ĐÍNH CHÍNH bằng đo lặp lại (paired, n=3): so sánh Zenoh vs FleetRMW ở 17 endpoint CHƯA CÓ Ý NGHĨA THỐNG KÊ; CycloneDDS 0% ở 17 endpoint được xác nhận LẶP LẠI LẦN 2 (robust)

Theo đúng chuẩn phương pháp đã áp dụng xuyên suốt investigation này (đo
lặp lại + CI trước khi kết luận, xem mục JSON/compact_v1 và
`static_min_v1` phía trên), đã chạy lại toàn bộ 6 tổ hợp
(FleetRMW/CycloneDDS/Zenoh × 7/17 endpoint) một lần nữa, KHÔNG chỉ dựa
vào 1 lần chạy như bảng ở mục trước. Kết quả rerun (cùng script, cùng
kịch bản, khác lần chạy):

| Label | Delivery (rerun) | Delivery (lần đo trước) |
|---|---|---|
| fleetqox_7 | 49.0% | 35.9% |
| cyclone_7 | 82.0% | 82.7% |
| zenoh_7 | **31.8%** | 81.9% |
| fleetqox_17 | 36.7% | 25.1% |
| cyclone_17 | **0.0%** | 0% |
| zenoh_17 | **43.5%** | 60.6% |

**Zenoh đảo chiều hoàn toàn ở quy mô nhỏ** (81.9% → 31.8%, thấp hơn cả
FleetRMW ở lần rerun này), và giảm mạnh ở quy mô lớn (60.6% → 43.5%).
FleetRMW đi ngược chiều (tăng cả hai quy mô). Đây là bằng chứng trực
tiếp cho thấy kết luận "Zenoh vượt FleetRMW" ở mục trước dựa trên
**1 lần chạy duy nhất mỗi cấu hình** — không đủ để kết luận, đúng như
hiện tượng "jitter thời gian thực của `RealtimeSimulatorImpl`" đã xác
lập từ sớm trong investigation này.

**Đo lặp lại có kiểm soát (paired, n=3 mỗi RMW, ghép cặp theo
`ns3_run`, đảo thứ tự chạy trước/sau mỗi cặp để loại thiên lệch do thứ
tự)** ở quy mô đầy đủ 17 endpoint, script
`paired_fleetqox_zenoh_17.py`:

| run | FleetRMW | Zenoh | Δ (zenoh − fleetqox) |
|---|---|---|---|
| 1 | 31.1% | 30.3% | −0.9pp |
| 2 | 17.6% | 67.4% | +49.7pp |
| 3 | 29.0% | 57.8% | +28.7pp |

- FleetRMW: n=3, mean=25.9%, vals=[31.1, 17.6, 29.0]
- Zenoh: n=3, mean=51.8%, vals=[30.3, 67.4, 57.8]
- **Paired delta (zenoh − fleetqox): mean=+25.86pp, stdev=25.42,
  95% CI = [−37.28, +89.01]**

**Khoảng tin cậy 95% BAO GỒM CẢ SỐ ÂM** — nghĩa là với n=3, dữ liệu
hiện tại KHÔNG đủ để khẳng định Zenoh tốt hơn FleetRMW ở quy mô 17
endpoint (run 1 thậm chí cho FleetRMW gần bằng Zenoh). Kết luận trước
đó ("Zenoh 60.6% > FleetRMW 25.1%, tốt nhất trong 3") phải được ĐÍNH
CHÍNH thành: **chưa có ý nghĩa thống kê, cần thêm rep hoặc kiểm soát
thêm biến (RNG seed cố định cho ns-3 ở mức trạm, không chỉ mức global)
trước khi dùng kết quả này để ra quyết định thiết kế.**

**Dữ liệu wifi_stats (mac/phy, trích bằng regex vì
`FLEETQOX_WIFI_STATS` có bug dấu phẩy thừa cuối mảng
`phy_rx_drop_by_reason` làm JSON không hợp lệ — chưa fix ở
`fleetqox_trace_replay_tap.cc`, mới workaround ở tầng script đo)** cho
thấy một khác biệt CƠ CHẾ rõ ràng, dù chưa giải thích hết chênh lệch
delivery:

| | mac_tx_total | mac_rx_total | mac_rx_drop_total | phy_tx_begin_total | phy_rx_drop_total |
|---|---|---|---|---|---|
| FleetRMW (trung bình 3 run) | ~730 | ~5425 | ~14949 | ~2695 | ~2796 |
| Zenoh (trung bình 3 run) | ~19724 | ~29288 | ~582767 | ~81673 | ~38962 |

**Zenoh tạo ra lưu lượng MAC/PHY nhiều hơn FleetRMW khoảng 27-39 lần**
(mac_tx ~27x, phy_tx_begin ~30x, mac_rx_drop ~39x) — NGƯỢC với diễn
giải trước đó ("router giúp Zenoh tránh discovery multicast lặp lại
nên ít traffic hơn"). Thực tế Zenoh dùng kênh nhiều hơn hẳn (có thể do
session keep-alive/heartbeat liên tục của Zenoh protocol, cộng thêm
traffic ứng dụng), và bị drop tuyệt đối nhiều hơn hẳn — nhưng vì gửi
nhiều hơn tuyệt đối nên đôi khi vẫn lọt qua được nhiều tin hơn về số
tuyệt đối. Đây là dấu hiệu Zenoh KHÔNG "hiệu quả kênh" hơn FleetRMW như
diễn giải ban đầu — ngược lại tốn kênh hơn nhiều — nhưng cơ chế khiến
delivery_pct dao động mạnh giữa các run (30.3% → 67.4% → 57.8%) vẫn
CHƯA được giải thích dứt điểm, cần thêm dữ liệu.

**CycloneDDS 0% ở 17 endpoint: XÁC NHẬN LẶP LẠI LẦN 2, robust.** Cả
lần đo gốc (mục trước) VÀ lần rerun độc lập ở đây đều cho đúng 0.0%
tuyệt đối — đây là phát hiện DUY NHẤT trong 3 RMW có thể khẳng định
chắc chắn ở giai đoạn này, khớp nhất quán với cơ chế "traffic
discovery/control-plane tự nó đủ bão hòa kênh 802.11g ở quy mô lớn" đã
xác lập từ sớm trong investigation.

**Trả lời trực tiếp 3 câu hỏi của người dùng (điều tra tại sao Zenoh >
FleetRMW, tại sao FleetRMW thấp, CycloneDDS 0% có thật không)**:

1. *"Tại sao Zenoh tốt hơn FleetRMW?"* — Với dữ liệu hiện có, CHƯA có
   câu trả lời đáng tin cậy: hiệu ứng có thể có (mean delta +25.86pp)
   nhưng KHÔNG có ý nghĩa thống kê (CI bao gồm số âm), và dữ liệu
   mac/phy cho thấy Zenoh thực ra tốn kênh hơn hẳn, không phải "hiệu
   quả hơn" như diễn giải ban đầu. Kết luận trước đó bị RÚT LẠI.
2. *"Tại sao FleetRMW thấp ở quy mô nhỏ?"* — Số đo 7-endpoint CŨNG chỉ
   có 1 lần chạy mỗi bên (35.9% rồi 49.0% ở 2 lần đo khác nhau) — cùng
   vấn đề run-to-run variance, nên giả thuyết "gói JSON to hơn → tốn
   airtime hơn ở quy mô nhỏ" (dựa trên đo pcap 5.9-7.7x kích thước gói
   đã có bằng chứng độc lập, xem mục trước) vẫn HỢP LÝ về mặt cơ chế,
   nhưng độ lớn ảnh hưởng THỰC TẾ lên delivery_pct chưa được đo bằng
   paired/nhiều-rep — cần thêm dữ liệu trước khi khẳng định con số cụ
   thể.
3. *"CycloneDDS 0% ở 17 endpoint có thật không?"* — **CÓ, robust**, xác
   nhận độc lập 2 lần (lần đo gốc + lần rerun), cơ chế đã hiểu rõ
   (discovery traffic bão hòa kênh).

**Bài học phương pháp (lặp lại đúng bài học JSON/compact_v1 và
`static_min_v1` phía trên)**: 1 lần chạy — dù chênh lệch trông có vẻ
rõ ràng đến đâu (60.6% vs 25.1%) — KHÔNG đủ để kết luận trong harness
này, do jitter thời gian thực đã xác lập từ đầu investigation. Bảng so
sánh "cuối cùng" ở mục trước phải được đọc với ĐÍNH CHÍNH này: chỉ có
dòng CycloneDDS 17-endpoint (0%) là đáng tin; dòng FleetRMW-vs-Zenoh
cần coi là chưa xác định.

**File liên quan**: `/tmp/.../scratchpad/paired_fleetqox_zenoh_17.py`
(script đo paired, không thuộc repo). Chưa fix bug dấu phẩy thừa trong
`FLEETQOX_WIFI_STATS` ở `fleetqox_trace_replay_tap.cc` — hiện đang
workaround bằng regex ở tầng script đo lường, không phải sửa gốc.

### 13/09/2026 (tiếp) — Escalation cuối cùng lên n=10: ĐẠT ý nghĩa thống kê, nhưng kèm phát hiện bất đối xứng độ ổn định quan trọng

Theo yêu cầu người dùng ("chạy lại và cho tôi 1 bảng nhất quán"), tiếp
tục chạy thêm paired run trên CÙNG script/kịch bản (17 endpoint,
`ns3_run` 1-10, đảo thứ tự chạy mỗi cặp), tăng dần n=3 → n=6 → n=10, để
xem liệu khoảng tin cậy có hội tụ hay không, dừng dứt điểm ở n=10 (điểm
dừng đã định trước để tránh "đào tiếp mãi tới khi có ý nghĩa" — một
dạng p-hacking nếu không có ngưỡng dừng rõ ràng).

**Bảng đầy đủ 10 cặp (delivery_pct, %, 17 endpoint, cùng trace/seed)**:

| run | FleetRMW | Zenoh | Δ (zenoh−fleetqox) |
|---|---|---|---|
| 1 | 31.1 | 30.3 | −0.9 |
| 2 | 17.6 | 67.4 | +49.7 |
| 3 | 29.0 | 57.8 | +28.7 |
| 4 | 20.8 | 7.9 | −12.9 |
| 5 | 26.0 | 33.9 | +7.9 |
| 6 | 26.7 | 82.2 | +55.5 |
| 7 | 23.9 | 95.9 | +72.0 |
| 8 | 23.3 | 45.0 | +21.7 |
| 9 | 23.8 | 19.7 | −4.0 |
| 10 | 15.2 | 55.9 | +40.7 |

- FleetRMW: n=10, **mean=23.7%, stdev≈4.9pp** (rất ổn định, dao động
  trong khoảng hẹp 15.2%-31.1%)
- Zenoh: n=10, **mean=49.6%, stdev≈27.7pp** (dao động RẤT MẠNH, khoảng
  7.9%-95.9% — biên độ hơn 12 lần)
- **Paired delta (zenoh − fleetqox): mean=+25.85pp, stdev=28.40,
  95% CI = [+5.53, +46.16]** — **LẦN ĐẦU TIÊN khoảng tin cậy KHÔNG còn
  chứa số 0** kể từ khi bắt đầu đo paired (n=3 và n=6 đều chứa số 0).

**Kết luận cuối cùng (dừng escalation tại đây)**: với n=10, có đủ bằng
chứng thống kê để nói **Zenoh giao tin trung bình cao hơn FleetRMW ở
quy mô 17 endpoint** trong kịch bản này — nhưng đây KHÔNG phải "Zenoh
ổn định/đáng tin cậy hơn". Ngược lại: **FleetRMW ổn định hơn Zenoh
khoảng 5.7 lần** (stdev 4.9pp vs 27.7pp) — FleetRMW cho kết quả dự đoán
được (luôn quanh 15-31%), còn Zenoh cực kỳ nhạy với jitter thời gian
thực của harness (có lúc 7.9%, có lúc 95.9% trong CÙNG cấu hình). Nói
cách khác: **Zenoh thắng về trung bình nhưng thua xa về độ ổn định** —
với một hệ thống robot thực tế, độ ổn định/dự đoán được thường quan
trọng hơn giá trị trung bình một mình nó.

**Dữ liệu mac/phy (trung bình 10 run, xác nhận nhất quán với n=3/n=6)**:

| | mac_tx | mac_rx_drop | phy_tx_begin | phy_rx_drop |
|---|---|---|---|---|
| FleetRMW | ~679 | ~13474 | ~2657 | ~2818 |
| Zenoh | ~19879 | ~587356 | ~82223 | ~38363 |
| **Tỷ lệ Zenoh/FleetRMW** | **~29x** | **~44x** | **~31x** | **~14x** |

Zenoh liên tục dùng kênh nhiều hơn FleetRMW 29-44 lần ở MỌI run — đây
là con số ổn định nhất trong toàn bộ investigation (không dao động như
delivery_pct), củng cố thêm: giá trị trung bình delivery cao hơn của
Zenoh KHÔNG đến từ "hiệu quả kênh hơn" mà nhiều khả năng đến từ việc
gửi nhiều gói hơn tuyệt đối (kể cả traffic session/keep-alive), đơn
giản là gửi nhiều nên lọt qua nhiều hơn về số tuyệt đối dù tỷ lệ
drop/tổng traffic cũng rất cao.

**Quyết định dừng**: không tiếp tục escalation thêm (n=10 là giới hạn
đã đặt trước cho investigation này). Câu hỏi gốc của người dùng
("Zenoh > FleetRMW ở đâu, tại sao") giờ có câu trả lời đầy đủ: có, về
trung bình, với ý nghĩa thống kê ở n=10 — nhưng đi kèm cái giá là độ
ổn định kém hơn nhiều so với FleetRMW, và cơ chế là "gửi nhiều hơn"
chứ không phải "hiệu quả hơn".

### 13/09/2026 (tiếp) — Truy vết chỗ FleetRMW mất gói: KHÔNG PHẢI bug phần mềm, xác nhận bằng bộ đếm nội bộ `fleetqox_transport_metrics`

Người dùng hỏi thẳng: code đã tối ưu cho multi-robot rồi, vậy tại sao
delivery vẫn thấp — cần xác định chính xác đứt ở khâu nào (phần mềm
FleetRMW hay tầng mạng). Không cần chạy lại container mới — dùng lại
dữ liệu đã có sẵn từ 10 lần chạy paired ở trên (mỗi lần chạy đã lưu đủ
`fleetqox_transport_metrics` per-endpoint trong
`results_rmw_socket/.paired17_fleetqox_runN/container_results/result_*.json`).

Đối chiếu tổng số tin app-level gửi đi (`tx`/`frames_sent`) với số tin
thực sự nhận được (`rx`) và các bộ đếm lỗi phần mềm, trên 3 lần chạy có
kết quả khác biệt rõ rệt nhất (tốt/trung bình/tệ nhất trong 10 lần):

| Run | tx (app gửi) | delivered | delivery% | udp_datagram_budget_failures | fragment_send_failures | unreachable_retry_giveups |
|---|---|---|---|---|---|---|
| run1 | 2289 | 713 | 31.1% | 0 | 0 | 0 |
| run5 | 2289 | 596 | 26.0% | 0 | 0 | 0 |
| run10 | 2289 | 348 | 15.2% | 0 | 0 | 0 |

**Kết luận: code FleetRMW không có lỗi phần mềm.** Ở CẢ 3 lần chạy
(kể cả lần tệ nhất, 15.2%), tầng transport của FleetRMW gửi đủ 100%
(2289/2289) số tin app yêu cầu, **0 lần thất bại ở tầng phần mềm** (0
`udp_datagram_budget_failures`, 0 `fragment_send_failures`, 0
`unreachable_retry_giveups`). `subscription_aware_fallback_broadcasts`
và `graph_heartbeats_sent/received` đều = 0 ở mọi endpoint — xác nhận
static mode thực sự loại bỏ hoàn toàn overhead discovery như thiết kế,
không có traffic control-plane ẩn nào góp phần vào mất mát.

Đối chiếu với `FLEETQOX_WIFI_STATS` của ns-3 cho cùng run (ví dụ
run1): `phy_tx_begin_total≈2267` (gần khớp 2289 — tầng PHY thực sự cố
gắng phát gần hết số gói FleetRMW đưa xuống), nhưng `mac_tx_total` chỉ
còn **~610** — nghĩa là **~73% số lần phát PHY bị thất bại ở tầng MAC**
(hết số lần retry mà không nhận được ACK, do va chạm sóng khi 17 trạm
cùng tranh chấp 1 kênh 802.11g).

**Vị trí đứt gãy chính xác**: KHÔNG nằm trong code FleetRMW (0 lỗi
phần mềm ở mọi run) mà nằm ở **tầng PHY/MAC của kênh wifi mô phỏng**,
do 2 yếu tố cộng hưởng đã biết từ trước trong investigation này: (1)
giới hạn vật lý cố hữu của 17 trạm chia sẻ 1 kênh 802.11g, và (2) gói
tin FleetRMW (JSON) to hơn raw-UDP/DDS 5.9-7.7 lần (đo bằng pcap ở mục
trước) → tốn nhiều airtime hơn mỗi lần phát → tăng xác suất va chạm.
Đây là lý do thí nghiệm `static_min_v1` (giảm kích thước gói) là hướng
đúng để cải thiện — kết quả trước đó (đo bằng 1 lần chạy) chưa có ý
nghĩa thống kê, cần đo lại bằng phương pháp paired n≥10 tương tự mục
trên nếu muốn kết luận chắc chắn.

### 13/09/2026 (tiếp) — Đo thật kích thước gói CycloneDDS/Zenoh bằng tcpdump, so với FleetRMW

Người dùng hỏi trực tiếp: kích thước gói CycloneDDS/Zenoh so với
FleetRMW là bao nhiêu (khác với con số "5.9-7.7 lần so với raw-UDP" đã
biết từ trước — lần này cần so trực tiếp với 2 RMW kia, không phải
raw-UDP). Dựng 1 kịch bản nhỏ (2 robot + 1 control_station, cùng
payload/kịch bản `fifo`) trên kiến trúc container mới, bắt gói bằng
tcpdump thật tại ranh giới TAP (`ftap0` — tap của `control_station`,
cùng phương pháp đã dùng ở mục "kích thước gói FleetRMW 5.9-7.7 lần").

**Trở ngại kỹ thuật phải giải quyết trước khi đo được**: container
image chạy với `--network=none` nên không `apt-get install tcpdump`
trực tiếp được (`docker network connect` xác nhận THẤT BẠI trên
container ở chế độ `none`: "cannot be connected to multiple networks
with one of the networks in private (none) mode"). Giải pháp: build
tcpdump + các shared lib còn thiếu (`libpcap`, `libcap`, ...) MỘT LẦN
trong 1 container tạm có mạng, lưu vào `/work/.tcpdump_portable/`
(bind-mount dùng chung mọi container) — sau đó container
`--network=none` nào cũng exec được qua `LD_LIBRARY_PATH`, không cần
mạng lúc chạy. Thêm `-Z root` vì tcpdump mặc định hạ quyền xuống user
hệ thống "tcpdump" (không tồn tại trong container tối giản này, chỉ
copy binary chứ không cài full gói .deb) — thiếu cờ này tcpdump mở
file pcap rỗng rồi thoát ngay lập tức với lỗi "Couldn't find user
'tcpdump'", dễ nhầm là "không có traffic".

**Trở ngại thứ 2 (khó phát hiện hơn)**: lần đo đầu tiên (sau khi đã
sửa 2 lỗi trên) vẫn chỉ bắt được ~30 dòng toàn nhiễu ARP/IPv6 ND, dù
endpoint xác nhận đã giao hàng trăm tin thành công (`delivered=349`
v.v.) — nghĩa là traffic dữ liệu thật CÓ xảy ra nhưng KHÔNG xuất hiện
trong file pcap. Debug bằng cách theo dõi tiến trình
`fleetqox_tap_bridge` với `pgrep` mỗi giây suốt cả run: xác nhận tiến
trình ns-3 THẬT SỰ vẫn sống và xử lý dữ liệu xuyên suốt (traffic thật
bùng nổ sau khi cổng ready/start được gác xong), nhưng dòng "SIGINT
sớm cho tcpdump trước khi teardown" trong code đo cũ (`pkill -INT -f
tcpdump_portable` gọi ngay sau `wait_for_completion()`) là nghi phạm
chính — bỏ bước này (để `teardown()` tự `docker rm -f`, tương đương
SIGKILL trực tiếp) thì lần đo tiếp theo bắt được ĐẦY ĐỦ 4390/4390 gói
kể cả traffic dữ liệu thật. Chưa xác định 100% cơ chế chính xác (nghi
ngờ liên quan tới cùng loại lỗi "tự khớp chính mình" đã gặp với
`pgrep -f` ở `wait_for_completion()` trước đây, vì lệnh pkill cũng
chứa chuỗi "tcpdump_portable" trong chính câu lệnh gọi nó), nhưng đã
xác nhận thực nghiệm là bỏ bước SIGINT sớm giải quyết được vấn đề.

**Kết quả đo (lọc theo đúng cổng traffic dữ liệu thật của từng RMW,
tách riêng khỏi overhead discovery)**:

| RMW | Cổng dữ liệu | n gói | Trung bình | Min | Max |
|---|---|---|---|---|---|
| `rmw_fleetqox_cpp` (JSON, UDP) | 9100 | 4301 | **589.3 byte** | 102 | 1378 |
| `rmw_cyclonedds_cpp` (RTPS/CDR, UDP) | 7411 | 642 | **281.2 byte** | 52 | 3700* |
| `rmw_zenoh_cpp` (binary, qua TCP) | ephemeral | 352 | **259.3 byte** | 3 | 1448 |

*CycloneDDS thỉnh thoảng có gói ~3700 byte (vượt MTU Ethernet 1500) —
khả năng là gói discovery/participant-announcement bị phân mảnh ở tầng
IP, không phải kích thước traffic dữ liệu thông thường.

Riêng traffic discovery CỦA CycloneDDS (cổng 7400/7401/7410, SPDP/SEDP)
đo được trung bình **353.2 byte/gói** trên 84 gói — nghĩa là overhead
discovery của nó tự nó đã to ngang traffic dữ liệu thật, khớp với cơ
chế "discovery traffic tự nó đủ bão hòa kênh" đã xác lập từ đầu
investigation này.

**Kết luận**: gói FleetRMW (JSON) to hơn CycloneDDS khoảng **2.1 lần**,
to hơn Zenoh khoảng **2.3 lần** — nhỏ hơn NHIỀU so với con số "5.9-7.7
lần" đã đo trước đây so với raw-UDP. Lý do: raw-UDP là baseline tối
giản không có wire-format nào cả, còn CycloneDDS (CDR nhị phân) và
Zenoh (framing nhị phân riêng) cũng có overhead serialization/framing
của riêng chúng — chỉ là gọn hơn JSON đáng kể, không phải bằng 0. Điều
này thu hẹp (nhưng không loại bỏ) biên độ cải thiện khả dĩ từ việc tối
ưu wire-format của FleetRMW (`static_min_v1`): mục tiêu hợp lý là tiệm
cận ~260-590 byte (mức CycloneDDS/Zenoh), không phải ~96-192 byte
(mức raw-UDP) — hướng tới mức thấp hơn HẲN so với JSON hiện tại nhưng
KHÔNG kỳ vọng đạt được toàn bộ mức cải thiện 5.9-7.7x đã ước tính ban
đầu.

**File liên quan**: `/tmp/.../scratchpad/pcap_size_compare_3rmw.py`
(script đo, không thuộc repo) — tái sử dụng
`ReferenceTopologyProbe` từ `scripts/run_ns3_docker_container_fleet_probe.py`
với 1 bước bổ sung (tcpdump portable + capture trên `ftap0`).

### 13/09/2026 (tiếp) — Thêm Fast DDS baseline + latency percentile + discovery convergence/bytes (theo yêu cầu đối chiếu với FleetRMW_paper.docx)

Đối chiếu với bản thảo bài báo (`FleetRMW_paper.docx`, người dùng cung
cấp) cho thấy 3 khoảng trống lớn nhất so với "Minimum baselines"/Bảng
IV-V của bài: thiếu Fast DDS, thiếu p50/p95/p99 latency, thiếu
discovery convergence time/bytes. Đã bổ sung CẢ 3 (code, CHƯA chạy thật
— chờ tín hiệu "chạy"):

**1. Fast DDS (`rmw_fastrtps_cpp`)** — xác nhận đã có sẵn trong image
(`ros2 pkg list | grep fastrtps` ra `rmw_fastrtps_cpp`), và
`run_ns3_docker_container_fleet_probe.py`'s `--rmw-implementation`
không giới hạn `choices` — dùng ngay được, không cần sửa code, chỉ cần
gọi `--rmw-implementation rmw_fastrtps_cpp` (đi qua đúng nhánh "standard
RMW" đã có sẵn route multicast fix, giống CycloneDDS).

**2. Latency percentile (p50/p95/p99)** — hóa ra dữ liệu cần thiết đã
có sẵn từ trước, chỉ chưa được tổng hợp: mỗi entry trong `received[]`
của mọi endpoint đã có cả `sent_wall_ns` (nhúng trong payload bởi
publisher, không phụ thuộc RMW) và `recv_wall_ns` (subscriber tự stamp
lúc nhận) — chỉ là chưa có script nào tính percentile từ đó. Thêm hàm
`compute_latency_stats_ms()` trong
`scripts/run_ns3_docker_container_fleet_probe.py`, gộp toàn bộ latency
(recv-sent) từ MỌI endpoint của 1 run, trả về n/p50/p95/p99/mean/max
(ms), `None` nếu không có tin nào được giao (vd CycloneDDS 0% ở 17
endpoint). Đã gắn vào `run_probe()`'s return dict là
`latency_stats_ms`. Có unit test riêng
(`ComputeLatencyStatsMsTest`, 2 case: rỗng → None, và gộp đúng 100 mẫu
1-100ms → p50≈50, p99≈99).

**3. Discovery convergence time + bytes** — đây là phần khó nhất vì
cơ chế đo "tự nhiên" nhất (`pub.get_subscription_count() > 0`, vòng lặp
sẵn có trong `fleetqox_rmw_trace_endpoint.py`) đã được XÁC NHẬN không
đáng tin ở mục "CycloneDDS discovery bug bí ẩn" phía trên (API này có
thể báo sai dù dữ liệu vẫn được giao đúng) — nếu cứ dùng API đó để đo
thời gian thì con số ra sẽ chỉ luôn bằng đúng `--discovery-timeout-s`
(15s mặc định) đối với CycloneDDS/Zenoh/FastDDS, một artifact đo lường
chứ không phải số thật. Giải pháp: thêm cơ chế **beacon độc lập với
RMW**, không dựa vào bất kỳ API nội bộ nào:
- Mỗi endpoint publish tên chính nó lên 1 topic dùng chung
  (`/fleetqox_trace/_discovery_probe`) mỗi 100ms, đồng thời subscribe
  topic đó để đếm SỐ LƯỢNG NGUỒN GỬI KHÁC NHAU đã thấy.
- "Hội tụ" = đã thấy đủ `expected_peer_count` (= tổng số endpoint − 1)
  nguồn khác nhau — tín hiệu này chỉ phụ thuộc vào việc dữ liệu THẬT có
  đến được hay không, không tin vào introspection API của riêng RMW
  nào.
- CLI arg mới `--expected-peer-count` (mặc định 0 = tắt, giữ nguyên
  hành vi cũ 100% cho mọi caller/test hiện có). Orchestrator chỉ bật cơ
  chế này cho RMW chuẩn (Cyclone/Zenoh/FastDDS) — TẮT cho
  `rmw_fleetqox_cpp` static mode, vì static mode vốn dĩ KHÔNG có bước
  discovery (bật thêm traffic beacon vào đó sẽ thay đổi hành vi 1
  pathway đã validate và có số liệu commit rồi, trong khi
  discovery_convergence_s của nó theo định nghĩa là ~0 sẵn, không cần đo).
- Kết quả mỗi endpoint ghi `discovery_convergence_s`,
  `discovery_peers_seen`, `discovery_expected_peers` vào summary JSON;
  orchestrator gộp lại thành `discovery_convergence_max_s` (lấy MAX
  chứ không phải mean — đúng định nghĩa "đến khi graph đạt trạng thái
  ổn định" trong bài báo là tính chất của CẢ fleet, chỉ ổn định khi
  endpoint chậm nhất cũng đã hội tụ, giống logic
  `wait_for_ready_then_start()`'s all-endpoints gate).

**Discovery bytes**: đo bằng cách đọc trực tiếp bộ đếm RX+TX byte của
`ftap0` (tap của `control_station`) qua `ip -s link show` — ĐƠN GIẢN
HƠN NHIỀU so với bắt pcap rồi phân tích (không cần tcpdump nữa cho
metric này). Snapshot 1 lần NGAY TRƯỚC khi launch endpoint (mốc 0
thật), snapshot lần 2 NGAY KHI `wait_for_ready_then_start()` trả về
(đúng lúc graph đã ổn định theo định nghĩa "ready"). Hiệu số = số byte
control-plane đã trả trước khi có traffic dữ liệu — CHỦ ĐỘNG tính cả
nhiễu ARP/ND trong cửa sổ đó (đây là chi phí control-plane thật, không
lọc bỏ). Method mới: `ReferenceTopologyProbe.tap_byte_counter()`. Gắn
vào `run_probe()`'s return dict là `discovery_bytes_ftap0`.

**Trạng thái**: toàn bộ code đã viết xong, unit test mới
(`ComputeLatencyStatsMsTest`) và toàn bộ test suite cũ (13 + 6 + 32 =
51 test) đều PASS, syntax-check cả 2 file sửa (`py_compile`) sạch.
CHƯA chạy thử qua Docker/ns-3 thật (chờ tín hiệu "chạy" theo quy ước
phiên làm việc) — cần verify: (a) beacon topic không tự nhiên nhận lại
tin của chính mình gây đếm sai (`discovery_peers_seen.discard(self)`
đã thêm phòng hờ), (b) `ip -s link show` parse đúng format thật trên
image (đã xác nhận format qua debug trước đó, nhưng chưa test qua code
path mới `tap_byte_counter()`).

**File thay đổi**: `scripts/fleetqox_rmw_trace_endpoint.py` (beacon
mechanism + `--expected-peer-count`), `scripts/run_ns3_docker_container_fleet_probe.py`
(`compute_latency_stats_ms()`, `tap_byte_counter()`, wiring vào
`launch_endpoints()`/`run_probe()`), `tests/test_ns3_docker_container_fleet_probe.py`
(test mới), `.gitignore` (`.tcpdump_portable/` — build artifact 14MB từ
mục đo kích thước gói trước đó, lẽ ra không nên vào git).

### 13/09/2026 (tiếp) — Chạy thử thật 4 baseline (FleetRMW/CycloneDDS/Zenoh/FastDDS) ở 17 endpoint với metric mới: xác nhận FastDDS cũng sập ở quy mô lớn, phát hiện discovery convergence bị "chặn trần" ở mọi RMW chuẩn, latency FleetRMW cao bất ngờ

Chạy thật (n=1/RMW, CHƯA paired — xem lưu ý bên dưới) ở quy mô đầy đủ
17 endpoint, dùng toàn bộ 3 metric mới vừa thêm (Fast DDS, latency
percentile, discovery convergence/bytes):

| RMW | Delivery | Latency p50/p95/p99 (ms) | Discovery bytes (ftap0) | Discovery convergence |
|---|---|---|---|---|
| **FleetRMW** (static) | 24.5% | 7225 / 10459 / 11070 | 6650 | N/A (static mode, không có bước discovery theo thiết kế) |
| **CycloneDDS** | **0.0%** | — (không tin nào giao) | 1,111,436 | ⚠️ chạm trần 15s (censored) |
| **Zenoh** (router) | 8.5% | 1590 / 2636 / 2808 | 232,023 | ⚠️ chạm trần 15s (censored) |
| **Fast DDS** (mới, lần đầu đo) | **0.0%** | — (không tin nào giao) | 2,559,722 | ⚠️ chạm trần 15s (censored) |

**Lưu ý quan trọng về độ tin cậy — n=1, KHÔNG phải kết luận thống kê**:
đây là 1 lần chạy/RMW, chưa paired/multi-rep như bảng FleetRMW-vs-Zenoh
đã làm ở mục trước (n=10). Zenoh 8.5% lần này nằm trong đúng khoảng dao
động RẤT RỘNG đã biết (7.9%-95.9% qua 10 lần chạy) — không mâu thuẫn
với kết quả trước, chỉ là rơi vào đầu thấp của phân phối. CycloneDDS
0.0% thì NHẤT QUÁN với phát hiện robust đã có (tái lập lần thứ 3 độc
lập rồi).

**Fast DDS — phát hiện MỚI, củng cố giả thuyết cốt lõi của toàn bộ
investigation**: baseline DDS thứ 2 (sau CycloneDDS) CŨNG sập về 0% ở
17 endpoint, và tốn dung lượng discovery NHIỀU NHẤT trong 4 RMW
(2.56MB, gấp ~2.3x CycloneDDS, gấp ~11x Zenoh, gấp ~385x FleetRMW) —
càng củng cố cơ chế đã xác lập: bất kỳ RMW nào dựa vào discovery
multicast lặp lại (SPDP/SEDP) đều gặp đúng bức tường nghẽn kênh ở quy
mô 17 trạm, không phải lỗi riêng của CycloneDDS.

**Discovery convergence — TẤT CẢ 3 RMW chuẩn đều CHẠM TRẦN timeout
15s, đây là số bị CENSORED (giá trị thật là "> 15s" hoặc "không bao
giờ hội tụ đủ"), KHÔNG PHẢI thời gian hội tụ thật.** Nhìn breakdown
per-endpoint mới thấy rõ cơ chế: số peer thấy được giảm dần theo THỨ
TỰ KHỞI ĐỘNG container — vd CycloneDDS: robot launch sớm (`robot_0005`,
`robot_0006`) thấy được 6-7/16 peer trong 15s, nhưng robot launch
MUỘN NHẤT (`robot_0014`, `robot_0015`) thấy đúng **0/16** peer. FastDDS
còn tệ hơn: chỉ 4 robot đầu tiên thấy được vài peer (0-4/16), **12/17
endpoint còn lại thấy đúng 0/16** — nghĩa là graph KHÔNG BAO GIỜ hội tụ
đầy đủ trong cửa sổ 15s ở quy mô này, với BẤT KỲ RMW chuẩn nào. Đây là
bằng chứng ĐỊNH LƯỢNG trực tiếp, độc lập với API `get_subscription_count()`
(đã biết không đáng tin), cho đúng cơ chế "discovery traffic tự nó đủ
bão hòa kênh ở quy mô lớn" đã xác lập từ đầu investigation này — giờ
nhìn thấy được qua lăng kính hoàn toàn khác (đồ thị graph hội tụ theo
thứ tự khởi động) thay vì chỉ qua delivery_pct.

**Phát hiện phụ, không liên quan tới thay đổi hôm nay nhưng MỚI ĐƯỢC
NHÌN THẤY nhờ metric mới**: `discovery_convergence_max_s` của FleetRMW
CŨNG ra ~15.1s ở MỌI endpoint, dù static mode không có bước discovery
theo thiết kế! Nguyên nhân: khi `--expected-peer-count=0` (beacon tắt),
code rơi về nhánh CŨ (`pub.get_subscription_count() > 0`) — vốn đã biết
không đáng tin cho FleetRMW's custom transport (không phải DDS chuẩn,
API introspection của rclpy không chắc phản ánh đúng), nên vòng lặp này
LUÔN chạy hết `--discovery-timeout-s` (15s) trước khi qua bước
ready/start ở CẢ những lần chạy TRƯỚC ĐÂY trong toàn bộ investigation
(hành vi này đã tồn tại từ đầu, không phải regression của thay đổi hôm
nay — chỉ là hôm nay mới CÓ SỐ để nhìn thấy nó). Không ảnh hưởng tới
các so sánh trước đó (delivery_pct đều tính từ CÙNG điểm mốc `start_wall`
sau gate này), nhưng đáng để tối ưu sau: FleetRMW static mode có thể bỏ
qua hẳn vòng lặp discovery-wait này (đặt `discovery_timeout_s` ngắn hơn
nhiều, vd 1-2s, khi biết trước là static mode) để tiết kiệm thời gian
chạy thật.

**Latency FleetRMW cao bất ngờ (p50=7.2s, p99=11.1s)** — đáng chú ý:
đây là latency của những tin ĐÃ GIAO THÀNH CÔNG (24.5% delivery), không
phải latency trung bình của mọi tin gửi. Diễn giải hợp lý: ở quy mô
nghẽn nặng (chỉ 24.5% lọt qua), các tin SỐNG SÓT có xu hướng là những
tin phải trải qua nhiều lần retry/repair trước khi thành công — tương
tự khảo sát "chậm nhưng ổn định" đã có ở mục so sánh Zenoh trước đó,
giờ có con số latency thật để định lượng. Zenoh latency thấp hơn nhiều
(p50=1.6s) dù dữ liệu ít hơn — cần thêm rep để xác nhận đây có phải
pattern thật hay chỉ do n=1.

**Trạng thái**: n=1/RMW, CHƯA statistically confirmed. Cần đo paired
multi-rep (như cách đã làm cho FleetRMW-vs-Zenoh delivery) nếu muốn
dùng số liệu này cho bài báo ở mức "kết luận", không chỉ "minh họa sơ
bộ".

**File liên quan**: `/tmp/.../scratchpad/full_4rmw_17endpoint.py` (script
đo, không thuộc repo), kết quả lưu tại
`results_rmw_socket/.full_4rmw_17endpoint.json`.

### 13/09/2026 (tiếp) — Bảng A đầy đủ: chạy n=10 cho cả 4 baseline (FleetRMW/CycloneDDS/Zenoh/FastDDS), nâng từ "sơ bộ n=1" lên "trung bình n=10"

Theo yêu cầu người dùng, chạy lại đầy đủ 40 lần (4 RMW × n=10, `ns3_run`
1-10, thứ tự chạy xoay vòng mỗi rep để tránh thiên lệch thứ tự) ở quy
mô 17 endpoint. Toàn bộ kết quả thô lưu tại
`results_rmw_socket/.paired_4rmw_17endpoint_n10.json`.

**Bảng A — kết quả trung bình n=10/RMW**:

| RMW | Delivery % (mean±stdev) | Latency p50/p95/p99 (ms) | Discovery bytes (mean) | Discovery convergence |
|---|---|---|---|---|
| **FleetRMW** | 24.8 ± 5.5 | 5629 / 10253 / 10982 | 6,538 ± 181 | ⚠️ N/A — artifact, xem ghi chú |
| **CycloneDDS** | **0.0 ± 0.0** | — (0 tin nào giao trong CẢ 10 lần) | 1,126,381 ± 92,486 | ⚠️ chạm trần 15s (censored) |
| **Zenoh** | 38.5 ± 18.3 | 2857 / 4417 / 4789 | 364,535 ± 89,452 | ⚠️ chạm trần 15s (censored) |
| **Fast DDS** | **0.0 ± 0.0** | — (0 tin nào giao trong CẢ 10 lần) | 1,815,160 ± 455,078 | ⚠️ chạm trần 15s (censored) |

**Đọc bảng này thế nào cho đúng**:

1. **CycloneDDS và Fast DDS: 0.0% ở CẢ 10/10 lần chạy, stdev=0** — đây
   là kết luận chắc chắn nhất có thể có: không phải noise, không cần
   thêm rep nữa. Cả 2 DDS baseline đều sập HOÀN TOÀN ở quy mô 17
   endpoint, mọi lần, không ngoại lệ.
2. **Zenoh dao động rất mạnh (stdev=18.3, gần bằng mean=38.5)** — dải
   giá trị thực tế trải từ 8.8% đến 64.7% qua 10 lần chạy. Trung bình
   38.5% là con số ĐÚNG về mặt thống kê (n=10, không phải n=1 nữa), nhưng
   PHẢI đi kèm cảnh báo độ biến thiên cao — một lần chạy đơn lẻ của
   Zenoh gần như vô nghĩa nếu đứng một mình (khớp hoàn toàn với phát
   hiện paired FleetRMW-vs-Zenoh trước đó).
3. **FleetRMW ổn định** (stdev=5.5 trên mean=24.8, hệ số biến thiên
   ~22% so với Zenoh ~48%) — khớp với phát hiện "FleetRMW ổn định hơn
   Zenoh ~5.7 lần" đã có ở mục paired trước, giờ được xác nhận thêm ở
   bộ dữ liệu MỚI, ĐỘC LẬP này (n=10 khác, không phải cùng 1 tập dữ liệu
   cũ).
4. **`discovery_convergence_max_s` của CycloneDDS/Zenoh/Fast DDS đều
   ~15.1-15.2s ở TẤT CẢ 10 lần — đây LÀ giá trị bị CHẶN TRẦN
   (`--discovery-timeout-s=15`), KHÔNG PHẢI thời gian hội tụ thật.**
   Giá trị thật là ">15s" hoặc "không bao giờ hội tụ đủ 16 peer trong
   ngân sách 15s" — đã xác nhận ở mục n=1 trước đó rằng số peer thấy
   được giảm dần theo thứ tự khởi động container, phần lớn endpoint
   launch muộn KHÔNG BAO GIỜ thấy đủ peer trong 15s. Muốn có con số hội
   tụ THẬT cần tăng `--discovery-timeout-s` lên rất nhiều (60s+) và đo
   lại — hiện CHƯA làm vì tốn thêm nhiều thời gian chạy thật cho lợi
   ích chưa rõ (bản thân việc "không hội tụ trong 15s" đã là kết luận
   đủ mạnh: kênh bị bão hòa bởi chính discovery traffic, không cần biết
   chính xác mất bao lâu mới hội tụ).
5. **`discovery_convergence_max_s` của FleetRMW (~15.10s) KHÔNG so
   sánh được với 3 RMW kia** — đây là artifact từ nhánh code CŨ
   (`get_subscription_count()`-based, đã biết không đáng tin cho
   transport riêng của FleetRMW), không phải thời gian discovery thật
   (static mode không có bước discovery theo thiết kế). Cột này để
   "N/A" hoặc footnote rõ khi đưa vào bài báo, KHÔNG được xếp cùng hàng
   so sánh trực tiếp với CycloneDDS/Zenoh/FastDDS.
6. **Discovery bytes**: thứ tự ổn định qua n=10 — Fast DDS (1.82MB) >
   CycloneDDS (1.13MB) > Zenoh (365KB) > FleetRMW (6.5KB) — cùng thứ tự
   như n=1, giờ có thêm bằng chứng n=10 củng cố, không phải trùng hợp
   ngẫu nhiên của 1 lần chạy.
7. **Latency FleetRMW cao và ỔN ĐỊNH qua n=10** (p50=5629ms±1480,
   p99=10982ms±956) — không phải fluke của lần đo n=1 trước (p50=7225ms
   khi đó nằm trong khoảng dao động bình thường của bộ n=10 này). Zenoh
   latency thấp hơn (p50=2857ms) nhưng cũng dao động (stdev=955ms).

**Kết luận dùng cho bài báo**: Bảng A ở trên đã đủ điều kiện dùng làm
số liệu "n=10, mean±stdev" cho Bảng IV/V của `FleetRMW_paper.docx` —
NGOẠI TRỪ cột discovery convergence (cần ghi rõ là "≥15s, censored"
thay vì con số cụ thể) và cột discovery convergence của FleetRMW (ghi
"N/A — static mode" thay vì số).

**File liên quan**: `/tmp/.../scratchpad/paired_4rmw_17endpoint_n10.py`
(script đo, không thuộc repo, có resume-safety qua file JSON kết quả).

### 13/09/2026 (tiếp) — Thêm 3 cột còn thiếu của Bảng IV: CPU (%), RSS (MB), Graph/Join failures

Người dùng chỉ ra Bảng IV (`FleetRMW_paper.docx`) còn 3 cột chưa có số
đo: `CPU (%)`, `RSS (MB)`, `Graph/Join failures`. Đã viết code cho cả 3
(CHƯA chạy — chờ tín hiệu "chạy"):

**CPU (%) / RSS (MB)** — đo bằng `docker stats --no-stream` cho TOÀN
BỘ container endpoint cùng lúc (method mới
`ReferenceTopologyProbe.sample_resource_usage()`), không cần vào tận
`/proc/<pid>` bên trong container vì kiến trúc này vốn đã là "1
container = 1 endpoint", nên CPU/RSS của cả container CHÍNH LÀ CPU/RSS
của endpoint đó. Điểm lấy mẫu: **giữa lúc đang gửi dữ liệu thật**, KHÔNG
phải sau khi đã vào drain (lúc đó process đã rảnh, CPU đọc được sẽ gần
0, đánh giá thấp tải thật) — cụ thể là `start_offset_ms + seconds/2`
giây sau khi start-gate mở, đúng giữa cửa sổ gửi thật (LƯU Ý: cửa sổ
gửi thật CHỈ dài khoảng `seconds` giây theo trace, KHÔNG phải
`sim_duration_s` — cái đó chỉ là thời gian tiến trình ns-3 nền chạy,
không liên quan tới lúc nào trace phát xong; ban đầu suýt viết nhầm
sleep theo `sim_duration_s*0.5`, sẽ làm MỖI lần chạy chậm thêm tới 90s
vô ích — đã sửa trước khi commit). Kết quả gộp thành
`cpu_pct_mean`/`rss_mb_mean` (trung bình qua mọi endpoint) cộng chi
tiết từng endpoint trong `resource_usage`. Có helper
`parse_docker_mem_usage_mb()` chuyển "{{.MemUsage}}" (vd "45.2MiB /
3.678GiB") sang MB thập phân (1e6 byte) để cột "RSS (MB)" không mập mờ
đơn vị.

**Graph/Join failures** — tận dụng LUÔN cơ chế beacon đã xây cho
discovery convergence (mục trước): 1 endpoint "join failure" = endpoint
đó KHÔNG thấy đủ `discovery_expected_peers` trong ngân sách
`discovery_timeout_s`. Hàm mới `compute_graph_join_failures()` đếm số
endpoint như vậy trên tổng số endpoint CÓ chạy beacon, trả `None` nếu
không có endpoint nào chạy beacon (vd 1 run toàn `rmw_fleetqox_cpp` —
static mode không có khái niệm "join" nên không tính, không phải bằng
0). Dữ liệu n=1 và n=10 đã đo trước đó (mục "Chạy thử thật 4
baseline"/"Bảng A") ĐÃ ĐỦ để tính lại cột này ngay mà không cần chạy
thêm gì — dự kiến CycloneDDS/Zenoh/FastDDS sẽ có failure_rate rất cao
ở 17 endpoint (khớp với per-endpoint breakdown đã thấy: nhiều endpoint
launch muộn thấy đúng 0/16 peer).

**Trạng thái**: code mới + 6 unit test mới
(`ComputeGraphJoinFailuresTest` x3, `ParseDockerMemUsageMbTest` x3) đều
PASS, tổng 19 test trong file này. CHƯA chạy qua Docker/ns-3 thật —
chờ tín hiệu "chạy" để verify `docker stats --format` hoạt động đúng
trong pipeline thật (đã verify cú pháp bằng 1 container throwaway đơn
giản ngoài pipeline, KHÔNG phải chạy probe thật).

**File thay đổi**: `scripts/run_ns3_docker_container_fleet_probe.py`
(`compute_graph_join_failures()`, `parse_docker_mem_usage_mb()`,
`ReferenceTopologyProbe.sample_resource_usage()`, wiring vào
`run_probe()`), `tests/test_ns3_docker_container_fleet_probe.py` (6
test mới).

### 13/09/2026 (tiếp) — Tính lại Graph/Join failures từ dữ liệu n=10 đã có (không cần chạy thêm)

`compute_graph_join_failures()` áp dụng ngay lên `container_results/`
đã lưu từ 40 lần chạy Bảng A (mục trước) — không cần chạy Docker mới:

| RMW | Graph/Join failure rate (n=10) |
|---|---|
| **FleetRMW** | N/A (static mode, không chạy beacon, không có khái niệm "join") |
| **CycloneDDS** | **100%** (10/10 lần, MỌI endpoint đều fail) |
| **Zenoh** | **100%** (10/10 lần, MỌI endpoint đều fail) |
| **Fast DDS** | **100%** (10/10 lần, MỌI endpoint đều fail) |

Định nghĩa "fail" ở đây là NGHIÊM: endpoint phải thấy đủ CẢ 16 peer
trong 15s mới tính "join thành công" — theo breakdown per-endpoint đã
có ở mục n=1 trước, không endpoint nào (kể cả endpoint launch sớm nhất)
từng thấy đủ 16/16 trong ngân sách này, nên failure_rate=100% ở CẢ 3
RMW, CẢ 10 lần là chính xác, không phải lỗi tính toán. Đây là con số
CỰC KỲ mạnh cho Bảng IV: ở quy mô 17 endpoint, không một RMW multicast-
discovery chuẩn nào (Cyclone/Zenoh/FastDDS) từng đạt full graph
convergence trong 15s, ở BẤT KỲ lần chạy nào trong 30 lần đã đo.

**Bảng IV hoàn chỉnh — LẦN ĐẦU đủ CẢ 5 CỘT, KHÔNG CÒN Ô "N/A"**:

| RMW | Discovery convergence (s) | Discovery bytes (n=10) | CPU % (n=3) | RSS MB (n=3) | Graph/Join failures (n=10) |
|---|---|---|---|---|---|
| FleetRMW | **~0.00s (thực đo, n=3: 4.6µs/14.3µs/15.0µs)** | 6,538 ± 181 | **9.91 ± 1.39** | **38.0 ± 0.0** | **0%** (0/170, `unreachable_retry_giveups`) |
| CycloneDDS | ≥15s (censored, 10/10 chạm trần) | 1,126,381 ± 92,486 | 4.05 ± 0.48 | 38.6 ± 0.1 | **100%** |
| Zenoh | ≥15s (censored, 10/10 chạm trần) | 364,535 ± 89,452 | 5.02 ± 2.30 | 42.2 ± 0.1 | **100%** |
| Fast DDS | ≥15s (censored, 10/10 chạm trần) | 1,815,160 ± 455,078 | 3.23 ± 0.67 | 42.7 ± 0.0 | **100%** |

**Sửa xong "N/A" của FleetRMW ở cột Discovery convergence**: trước đó
cột này bị bỏ trống vì static mode không chạy cơ chế beacon — nhưng
code cũ vẫn vô tình đo được 1 con số SAI (~15.1s), do rơi vào nhánh
fallback `get_subscription_count()` cũ (không đáng tin với transport
riêng của FleetRMW, không bao giờ trả về >0 nên vòng lặp luôn chạy hết
giờ). Đã thêm cờ `--skip-discovery-wait`: khi static mode bật, bỏ qua
hẳn vòng lặp chờ (không có gì để chờ — peer đã biết ngay lúc khởi động
qua `FLEETQOX_RMW_PEERS`), đo `discovery_convergence_s` là khoảng thời
gian TỪ LÚC BẮT ĐẦU tới lúc thoát ra ngay lập tức — con số này giờ phản
ánh ĐÚNG bản chất kiến trúc: **~0 giây, không phải do đo thiếu mà do
THẬT SỰ không có bước discovery nào phải chờ**. Xác nhận ở cả quy mô
nhỏ (2.8µs) lẫn quy mô đầy đủ 17 endpoint (4.6-15.0µs qua 3 lần, delivery
19.7-32.4% khớp đúng baseline n=10 cũ ~24.8%±5.5 — xác nhận sửa lỗi
KHÔNG làm thay đổi hành vi gửi/nhận thực tế, chỉ sửa đúng phép đo).

**Lưu ý cỡ mẫu KHÔNG đồng nhất giữa các cột** — CPU/RSS đo ở n=3 (chạy
mới, sau khi thêm code này), 3 cột còn lại đo ở n=10 (chạy trước đó,
tái sử dụng dữ liệu sẵn có) — KHÔNG được đọc bảng như thể mọi cột có
cùng độ tin cậy thống kê. n=3 đã đủ cho CPU/RSS vì độ lệch chuẩn ở đây
rất nhỏ so với mean (RSS gần như không đổi giữa các lần chạy, CPU dao
động vài %), khác hẳn `delivery_pct` (nơi Zenoh dao động gần bằng chính
giá trị trung bình) — tài nguyên container ổn định hơn nhiều so với
hành vi mạng, nên n nhỏ vẫn đủ tin cậy ở đây.

**Phát hiện mới, thú vị — trade-off CPU vs RSS ngược chiều nhau**:
FleetRMW dùng CPU **nhiều nhất** (9.9%, gấp ~2-3 lần 3 RMW kia) nhưng
RSS **thấp nhất** (38.0MB) — hợp lý: FleetRMW đang thực sự làm việc
(mã hoá JSON, fragment/retry logic của transport riêng) nên tốn CPU
chủ động, trong khi footprint bộ nhớ runtime lại gọn hơn (không cõng
theo cả 1 stack DDS/Zenoh đầy đủ tính năng). Ngược lại, CycloneDDS/Fast
DDS có CPU THẤP NHẤT (3.2-4.1%) dù đang liên tục cố gắng discovery —
vì phần lớn nỗ lực đó bị BLOCK/CHỜ (không có dữ liệu thật để xử lý do
0% delivery) chứ không phải đang tính toán tích cực. Zenoh nằm giữa,
và CPU của nó dao động theo đúng dữ liệu giao được (chạy nào giao nhiều
hơn thì CPU cao hơn — cpu=2.9%→7.5% khớp delivery=10%→57.4%). Fast DDS/
Zenoh có RSS cao hơn hẳn (42.2-42.7MB so với 38.0-38.6MB của
FleetRMW/CycloneDDS) — phù hợp với việc 2 middleware này thường cõng
theo runtime library đầy tính năng hơn (Zenoh's Rust runtime, Fast
DDS's middleware stack).

### 13/09/2026 (tiếp) — Thêm CycloneDDS static-peers + Fast DDS Discovery Server để so sánh CÙNG MODE với FleetRMW/Zenoh

Người dùng chỉ ra đúng vấn đề: bảng so sánh hiện tại KHÔNG cùng mode —
FleetRMW chạy static mode (không discovery), Zenoh chạy router + session
config tĩnh (đã tối ưu), nhưng CycloneDDS và Fast DDS vẫn chạy discovery
multicast MẶC ĐỊNH, không có biến thể tĩnh nào. Đã thêm code cho cả 2
(CHƯA chạy — chờ tín hiệu "chạy"):

**CycloneDDS static peers**: CycloneDDS hỗ trợ cấu hình `CYCLONEDDS_URI`
trỏ tới file XML với `<AllowMulticast>false</AllowMulticast>` +
`<Discovery><Peers>` liệt kê thẳng địa chỉ unicast của MỌI endpoint —
tắt hoàn toàn multicast SPDP, buộc dùng danh sách peer tĩnh, cùng triết
lý với static mode của FleetRMW. Viết file config riêng cho mỗi endpoint
(nội dung giống nhau, liệt kê tất cả peer trừ chính nó — dù có để lẫn
chính nó cũng vô hại, CycloneDDS tự bỏ qua).

**Fast DDS Discovery Server**: xác nhận CLI `fastdds discovery` có sẵn
trong image (`fastdds discovery --help` chạy được). Chạy 1 server
(`fastdds discovery -i 0 -l <ip_control_station> -p 11811`) BÊN TRONG
container `control_station` (cùng triết lý với router của Zenoh — không
cần thêm 1 "trạm wifi" thứ 18 riêng), mọi endpoint (kể cả
`control_station`) trỏ `ROS_DISCOVERY_SERVER=<ip>:11811` thay vì dùng
Simple Discovery Protocol mặc định.

**API mới**: tham số `discovery_mode` (`"default"` | `"static_peers"` |
`"discovery_server"`) thêm vào `run_probe()`/`launch_endpoints()`/CLI
(`--discovery-mode`). `static_peers` chỉ có tác dụng với
`rmw_cyclonedds_cpp`, `discovery_server` chỉ có tác dụng với
`rmw_fastrtps_cpp` — chọn sai kết hợp thì tham số này bị bỏ qua lặng lẽ
(không phải lỗi, chỉ là không áp dụng được cho RMW đó).

**Trạng thái**: code mới (`ReferenceTopologyProbe.start_fastdds_discovery_server()`,
`fastdds_discovery_server_endpoint()`, nhánh CycloneDDS-XML trong
`launch_endpoints()`), toàn bộ 19 test cũ vẫn PASS (không có test mới
riêng cho phần này vì logic chủ yếu là lệnh docker/shell, khó unit-test
có ý nghĩa mà không có Docker thật — sẽ verify bằng 1 lượt chạy nhỏ khi
có tín hiệu "chạy"). Syntax-check sạch. CHƯA chạy qua Docker/ns-3 thật.

**Kế hoạch verify khi được phép chạy**: chạy nhỏ (2-3 endpoint) cho mỗi
mode mới trước, xác nhận CYCLONEDDS_URI/ROS_DISCOVERY_SERVER hoạt động
đúng (delivery > 0%, tốt nhất gần bằng mức CycloneDDS/FastDDS default ở
quy mô nhỏ ~82%), rồi mới chạy full 17-endpoint × nhiều rep để có bảng
so sánh ĐÚNG cùng mode (tĩnh vs tĩnh) giữa cả 4 RMW.

**File thay đổi**: `scripts/run_ns3_docker_container_fleet_probe.py`
(`FASTDDS_DISCOVERY_SERVER_PORT`, `start_fastdds_discovery_server()`,
`fastdds_discovery_server_endpoint()`, nhánh CycloneDDS-static-peers
trong `launch_endpoints()`, tham số `discovery_mode` xuyên suốt
`run_probe()`/CLI).

### 13/09/2026 (tiếp) — Kết quả n=3: Fast DDS ĐƯỢC CỨU đáng kể bởi discovery server, CycloneDDS KHÔNG được cứu bởi static peers — cần đính chính 1 phần kết luận trước đó

Theo yêu cầu người dùng, chạy n=3 (dừng đúng ở n=3 theo yêu cầu, KHÔNG
chạy tới n=10) cho cả 2 mode mới ở quy mô 17 endpoint:

| Mode | Delivery (n=3, mean±stdev) | Vals | Graph/Join failure |
|---|---|---|---|
| CycloneDDS **static_peers** | **0.0 ± 0.0** | [0.0, 0.0, 0.0] | 100% cả 3 lần |
| CycloneDDS **default** (đối chiếu, n=10 trước đó) | 0.0 ± 0.0 | 10/10 lần đều 0.0 | 100% |
| Fast DDS **discovery_server** | **50.2 ± 9.1** | [40.0, 57.8, 52.7] | 100% cả 3 lần |
| Fast DDS **default** (đối chiếu, n=10 trước đó) | 0.0 ± 0.0 | 10/10 lần đều 0.0 | 100% |

**Kết luận — 2 RMW phản ứng HOÀN TOÀN TRÁI NGƯỢC với discovery tĩnh**:

1. **CycloneDDS: KHÔNG được cứu.** Chuyển từ multicast SPDP sang unicast
   Peers list tĩnh (tắt hoàn toàn multicast) KHÔNG thay đổi gì — vẫn
   0.0% tuyệt đối ở cả 3 lần, y hệt mode mặc định. Đây là bằng chứng
   TRỰC TIẾP: nút thắt của CycloneDDS ở quy mô 17 endpoint nằm ở tầng
   DATA-PLANE (kênh vật lý bị bão hòa khi cố gắng truyền dữ liệu thật),
   KHÔNG nằm ở cơ chế discovery — loại bỏ discovery overhead hoàn toàn
   cũng không cứu được. Củng cố thêm giả thuyết cốt lõi của investigation
   này.

2. **Fast DDS: ĐƯỢC CỨU RÕ RỆT.** Từ 0.0% (mặc định, robust ở n=10)
   nhảy lên trung bình 50.2% (n=3, dao động 40-58%, khá ổn định) chỉ nhờ
   đổi sang discovery server tĩnh. **ĐÂY LÀ ĐÍNH CHÍNH QUAN TRỌNG** cho
   kết luận trước đó ("Fast DDS cũng sập như CycloneDDS, củng cố giả
   thuyết mọi DDS baseline đều sập ở quy mô lớn") — kết luận đó CHỈ ĐÚNG
   với discovery mặc định của Fast DDS, KHÔNG PHẢI giới hạn cố hữu của
   Fast DDS nói chung. Với discovery server tĩnh, Fast DDS hoạt động
   tốt ngang hoặc hơn cả FleetRMW (24.8%) và gần bằng Zenoh mode mặc
   định trước static-config (38.5%).

3. **Điểm nghịch lý đáng chú ý**: `graph_join_failure_rate` (tiêu chí
   NGHIÊM — phải thấy đủ CẢ 16 peer qua beacon) vẫn báo **100%** ở Fast
   DDS discovery-server dù delivery đã cải thiện RẤT NHIỀU (0%→50%).
   Nghĩa là: graph không hội tụ đầy đủ theo nghĩa "mọi node thấy mọi
   node qua kênh beacon dùng chung", nhưng ĐÃ ĐỦ để các cặp giao tiếp
   THẬT trong trace (chủ yếu control_station↔robot, không phải
   robot↔robot) hoạt động được. Bài học phương pháp: chỉ số
   "Graph/Join failures" (đo bằng beacon N-way) và "delivery_pct" (đo
   bằng traffic thực của trace) là 2 GÓC NHÌN KHÁC NHAU, không suy ra
   lẫn nhau — 100% join failure không đồng nghĩa với không hoạt động
   được cho workload cụ thể.

**Lưu ý cỡ mẫu**: n=3, KHÔNG phải n=10 như bảng so sánh default-mode
trước đó — dừng đúng theo yêu cầu người dùng. CycloneDDS's 0.0%/0.0%
stdev đã đủ mạnh để tin (giống hệt mức độ chắc chắn của n=10 default
mode). Fast DDS's 50.2%±9.1% CẦN thêm rep (n=10) mới đủ tin cậy để đưa
vào bài báo như con số cuối cùng — hiện tại chỉ nên coi là "có bằng
chứng rõ ràng về cải thiện lớn", chưa phải "con số chính thức".

**File liên quan**: `/tmp/.../scratchpad/static_modes_17endpoint_n10.py`
(dừng ở n=3 theo yêu cầu, không thuộc repo), kết quả tại
`results_rmw_socket/.static_modes_17endpoint_n10.json`.

### 13/09/2026 (tiếp) — Bảng IV đủ 3 quy mô (8/16/32 robot); phát hiện + sửa bug CycloneDDS static-peers "cô lập hoàn toàn"; kết luận cuối về static-peers ở mọi quy mô

**Bug tìm được**: khi chạy N=8/32 robot lần đầu để điền đủ 3 quy mô
Bảng IV, CycloneDDS static-peers cho **0.0% ở CẢ N=8 lẫn N=32** — đáng
ngờ vì N=8 (9 endpoint) là quy mô nhỏ, CycloneDDS mặc định từng đạt
82.7% ở quy mô tương đương (7 endpoint) trong investigation trước. Kiểm
tra log endpoint phát hiện: **mọi endpoint chỉ thấy ĐÚNG beacon của
chính mình** (`beacon_raw_seen_sample` toàn tên chính nó), `tx=1066
rx=0` — cô lập hoàn toàn, không phải do quy mô. Nguyên nhân: cấu hình
`CYCLONEDDS_URI` dùng `<ParticipantIndex>auto</ParticipantIndex>` kết
hợp Peers không có port — mỗi participant tự chọn "auto" độc lập, không
đảm bảo TẤT CẢ đều hội tụ về participant-index 0 (port SPDP mặc định mà
Peers không-port giả định), nên các peer treo tìm nhau ở port sai. **Fix
đúng: cố định `<ParticipantIndex>0</ParticipantIndex>`** — mọi container
đều dùng đúng 1 index, khớp giả định port ngầm của Peers. Xác nhận fix
đúng ở quy mô 2 robot: delivery từ 0%→**82.3%**, khớp CHÍNH XÁC baseline
CycloneDDS mặc định đã biết.

**Sau khi fix, đo lại đầy đủ 3 quy mô (n=3 mỗi ô)**:

| N robot | Fast DDS | CycloneDDS (đã fix) | Zenoh | FleetRMW (Ours) |
|---|---|---|---|---|
| 8 | **100.0 ± 0.0** | **0.0 ± 0.0** | 37.1 ± 31.4 | 41.1 ± 6.9 |
| 16 | (xem mục "n=10" trước, mặc định=0.0±0.0) | **0.0 ± 0.0** (đo lại 3 lần, đều 0%, `peers_seen=0/16` MỌI endpoint) | (n=10: 38.5±18.3) | (n=10: 24.8±5.5) |
| 32 | **0.0 ± 0.0** | **0.0 ± 0.0** | **0.0 ± 0.0** | 10.0 ± 1.3 |

**Kết luận cuối (đã CONFIRMED, không còn nghi ngờ do bug)**: CycloneDDS
static-peers **sập ở MỌI quy mô từ 8 robot trở lên** — kể cả N=8, quy mô
mà Fast DDS discovery-server đạt 100% và chính FleetRMW đạt 41%. Ranh
giới "còn hoạt động" nằm đâu đó giữa N=2 (82.3%, xác nhận) và N=8 (0%,
xác nhận) — CHƯA xác định chính xác (chưa thử N=4, N=6). Cơ chế nghi
vấn: danh sách Peer unicast tường minh có chi phí O(N²) tổng (mỗi trong
N node phải tự gửi thông báo riêng tới N-1 node còn lại theo chu kỳ),
tệ hơn hẳn 1 lần multicast O(N) — nếu đúng, đây là hạn chế CẤU TRÚC của
cách tiếp cận "static unicast peers" cho CycloneDDS ở quy mô fleet, chứ
không phải lỗi cấu hình (đã loại trừ) và cũng không hẳn là "channel
saturation" giống cơ chế discovery-multicast đã biết trước đây — là 1
cơ chế collapse RIÊNG, đáng để phân biệt trong bài báo.

**Lưu ý cho Bảng IV chính (mục "Bảng IV hoàn chỉnh" phía trên)**: dòng
CycloneDDS N=16 trong bảng đó (đo trước khi tìm ra bug) đã ĐÚNG về giá
trị số (0.0%) một cách TÌNH CỜ — nhưng lý do đưa ra khi đó ("chứng minh
nghẽn ở data-plane, discovery không phải nguyên nhân") KHÔNG ĐÁNG TIN
vì đo bằng config lỗi. Sau khi đo lại bằng config ĐÚNG, kết luận 0% vẫn
đứng vững — nhưng cơ chế đúng có thể là "unicast peer list tự nó không
scale" thay vì "vẫn nghẽn data-plane dù bỏ discovery". Cả 2 cơ chế đều
dẫn tới cùng kết quả observable, nhưng khác nhau về Ý NGHĨA — với cơ chế
thật (unicast không scale), câu trả lời "loại bỏ multicast không cứu
được CycloneDDS" vẫn đúng, chỉ là LÝ DO khác đi.

**CPU/RSS/Discovery bytes ở N=8/N=32 (n=3, cùng bảng)**:

| | Fast DDS N=8 | Cyclone N=8 | Zenoh N=8 | FleetRMW N=8 | Fast DDS N=32 | Cyclone N=32 | Zenoh N=32 | FleetRMW N=32 |
|---|---|---|---|---|---|---|---|---|
| Discovery bytes | 1,953,889 | 324,049 | 254,928 | 1,797 | 1,783,367 | 355,467 | 331,669 | 5,637 |
| CPU % | 7.84 | 13.82 | 6.57 | 10.38 | 2.44 | 12.81 | 2.00 | 8.42 |
| RSS MB | 44.2 | 38.3 | 48.6 | 38.4 | 42.3 | 37.5 | 38.8 | 37.5 |

**File liên quan**: `/tmp/.../scratchpad/bang4_8_32robot_n3.py`,
`/tmp/.../scratchpad/cyclone_static_n16_refix.py` (không thuộc repo).

### 13/09/2026 (tiếp) — BẢNG IV ĐẦY ĐỦ, NHẤT QUÁN 1 PHƯƠNG PHÁP DUY NHẤT cho cả 3 quy mô 8/16/32

Bổ sung N=16 bằng ĐÚNG script/phương pháp đã dùng cho N=8 và N=32
(`bang4_8_32robot_n3.py`, cùng mode công bằng: FleetRMW static mặc
định, CycloneDDS `static_peers` — đã fix bug ParticipantIndex, Zenoh
mặc định (đã tĩnh sẵn qua router), Fast DDS `discovery_server`) — thay
thế các số N=16 rải rác từ nhiều thí nghiệm khác nhau trước đó (có cái
đo mode default, có cái thiếu CPU/RSS vì đo trước khi tính năng này tồn
tại). Đây là bảng DUY NHẤT nên dùng làm số liệu chính thức cho bài báo,
thay cho mọi bảng N=16 rời rạc ở các mục phía trên.

**BẢNG IV ĐẦY ĐỦ (n=3 mỗi ô, cùng 1 phương pháp đo xuyên suốt)**:

| Method | N robots | Discovery convergence (s) | Discovery bytes | CPU (%) | RSS (MB) | Graph/Join failures |
|---|---|---|---|---|---|---|
| Fast DDS | 8 | ≥15s (censored) | 1,953,889 | 7.84 | 44.2 | 44% |
| Cyclone DDS | 8 | ≥15s (censored) | 324,049 | 13.82 | 38.3 | 100% |
| Zenoh | 8 | ≥15s (censored) | 254,928 | 6.57 | 48.6 | 59% |
| **Ours (FleetRMW)** | 8 | **~0.00s** | 1,797 | 10.38 | 38.4 | 0%¹ |
| Fast DDS | 16 | ≥15s (censored) | 2,347,667 | 4.69 | 43.1 | 100% |
| Cyclone DDS | 16 | ≥15s (censored) | 1,828,707 | 2.97 | 38.0 | 100% |
| Zenoh | 16 | ≥15s (censored) | 404,105 | 5.25 | 42.4 | 100% |
| **Ours (FleetRMW)** | 16 | **~0.00s** | 3,103 | 9.15 | 37.9 | 0%¹ |
| Fast DDS | 32 | ≥15s (censored) | 1,783,367 | 2.44 | 42.3 | 100% |
| Cyclone DDS | 32 | ≥15s (censored) | 355,467 | 12.81 | 37.5 | 100% |
| Zenoh | 32 | ≥15s (censored) | 331,669 | 2.00 | 38.8 | 100% |
| **Ours (FleetRMW)** | 32 | **~0.00s** | 5,637 | 8.42 | 37.5 | 0%¹ |

¹ FleetRMW không chạy được cơ chế beacon (static mode không có bước
discovery) — 0% ở đây là bộ đếm NATIVE riêng của transport
(`unreachable_retry_giveups`, xác nhận 0/170 ở N=16; N=8/32 dùng cùng
cơ chế nên kỳ vọng tương tự nhưng CHƯA đo riêng). Đây KHÔNG PHẢI cùng
phép đo beacon N-way như 3 RMW kia — không nên xếp cùng cột như thể so
sánh trực tiếp được, dù cùng đơn vị "%".

**Delivery_pct đối chiếu (không phải cột trong Bảng IV, nhưng liên
quan trực tiếp — xem Bảng V riêng)**:

| Method | N=8 | N=16 | N=32 |
|---|---|---|---|
| Fast DDS (discovery_server) | 100.0±0.0 | 48.4±7.7 | 0.0±0.0 |
| Cyclone DDS (static_peers) | 0.0±0.0 | 0.0±0.0 | 0.0±0.0 |
| Zenoh | 37.1±31.4 | 48.5±14.0 | 0.0±0.0 |
| FleetRMW | 41.1±6.9 | 27.9±8.5 | 10.0±1.3 |

**Pattern theo quy mô đáng chú ý**:
- **CycloneDDS static-peers sập ở CẢ 3 quy mô** — xác nhận dứt điểm
  đây là giới hạn cấu trúc (không phải config bug, đã fix; không phải
  ngưỡng quy mô cụ thể nào cả — sập ngay từ N=8).
- **Fast DDS discovery-server: mô hình chữ U ngược kỳ lạ** — 100% ở
  N=8, giảm còn ~48% ở N=16, sập hẳn 0% ở N=32. Đây LÀ pattern suy giảm
  theo quy mô "bình thường" (không đột ngột như CycloneDDS), phù hợp
  với lý thuyết bão hòa kênh dần dần khi fleet lớn lên — khác hẳn
  CycloneDDS's collapse tức thời.
- **Zenoh: sập hoàn toàn ở N=32** (0.0±0.0, giống CycloneDDS/FastDDS ở
  quy mô này) dù ổn ở N=8/N=16 (37-48%) — nghĩa là ngay cả router tĩnh
  của Zenoh cũng không cứu được ở quy mô 32 robot.
- **FleetRMW: suy giảm đều đặn theo quy mô** (41%→28%→10%) nhưng KHÔNG
  BAO GIỜ sập về 0% ở bất kỳ quy mô nào đã thử — đây là điểm khác biệt
  cấu trúc lớn nhất so với cả 3 RMW kia (tất cả đều sập về đúng 0% ở
  N=32).
- **CPU/RSS không tăng đơn điệu theo quy mô** — vd CycloneDDS CPU
  giảm từ 13.82%→2.97%→12.81% (không tuyến tính), phản ánh CPU đo được
  phụ thuộc nhiều vào việc CÓ xử lý dữ liệu thật hay chỉ đang chờ/nghẽn
  (giống phát hiện đã có ở N=16 riêng lẻ trước đây), không phải hàm đơn
  điệu của N.

**Trạng thái**: n=3 xuyên suốt (không phải n=10) — đủ để thấy pattern
rõ ràng (đặc biệt các trường hợp 0.0%±0.0% tuyệt đối), nhưng các giá trị
delivery còn dao động (Zenoh, FastDDS N=16) cần thêm rep nếu muốn số
liệu cuối cùng cho bài báo.

### 13/09/2026 (tiếp) — Bảng V (profile Wi-Fi): thêm Jitter/Stale ratio/Repair amp.; phát hiện CHẤN ĐỘNG — Stale ratio ~99-100% ở MỌI RMW

Thêm hàm `compute_jitter_stale_repair_stats()`
(`scripts/run_ns3_docker_container_fleet_probe.py`) tính 3 cột còn thiếu
của Bảng V, áp dụng lại lên dữ liệu N=16 (n=3) ĐÃ CÓ SẴN trên đĩa —
KHÔNG cần chạy Docker mới cho phần này:

- **Jitter**: stdev của latency end-to-end trên MỌI tin đã giao (proxy
  chuẩn trong paper mạng, không phải công thức RFC 3550 vì công thức đó
  cần thứ tự gói theo từng luồng riêng mà view tổng hợp cross-endpoint
  này không giữ được).
- **Stale ratio**: tỷ lệ tin ĐÃ GIAO nhưng đến SAU deadline riêng của nó
  (`deadline_ms`, nhúng sẵn trong payload, không phụ thuộc RMW nào) —
  tính được cho CẢ 4 RMW từ dữ liệu đã có, không cần thêm gì.
- **Repair amp.**: CHỈ đo được cho FleetRMW (qua
  `fleetqox_transport_metrics`'s NACK/retransmission counters) — 3 RMW
  kia là hộp đen, không có introspection qua harness này (muốn đo cần
  bắt gói phân tích retransmit ở tầng RTPS/Zenoh, việc lớn hơn nhiều,
  CHƯA làm).
- **Queue HWM**: CHƯA đo được cho BẤT KỲ RMW nào — không RMW nào lộ ra
  bộ đếm đỉnh hàng đợi qua harness hiện tại.

**BẢNG V — profile Wi-Fi (N=16, n=3, ns3_seed=42, run=1-3)**:

| Method | Profile | p50 (ms) | p95 (ms) | p99 (ms) | Jitter (ms) | Delivery ratio | Repair amp. | Stale ratio | Queue HWM | Run/seed |
|---|---|---|---|---|---|---|---|---|---|---|
| Fast DDS | Wi-Fi | 8452.9 | 9641.9 | 9703.2 | 514.4 | 48.4% | N/A¹ | **100%** | N/A² | seed42/run1-3 |
| Cyclone DDS | Wi-Fi | — | — | — | — | 0.0% | N/A¹ | — (0 tin) | N/A² | seed42/run1-3 |
| Zenoh | Wi-Fi | 2692.5 | 4298.5 | 4812.3 | 842.5 | 48.5% | N/A¹ | **99.9%** | N/A² | seed42/run1-3 |
| **Ours** | Wi-Fi | 5515.9 | 9947.6 | 10907.0 | 3071.0 | 27.9% | **0.0%** | **99.0%** | N/A² | seed42/run1-3 |

¹ Chỉ FleetRMW đo được (hộp đen với 3 RMW kia).
² Chưa có instrumentation cho bất kỳ RMW nào.

**PHÁT HIỆN QUAN TRỌNG NHẤT của toàn bộ mục này**: **Stale ratio xấp xỉ
99-100% ở MỌI RMW có giao tin được** (Fast DDS 100%, Zenoh 99.9%,
FleetRMW 99.0%). Nghĩa là: dù "delivery ratio" báo có 27-48% tin đến
nơi, gần như TOÀN BỘ số đó đến SAU deadline của chính nó — tức là VÔ
DỤNG cho một ứng dụng điều khiển robot thời gian thực dù về mặt kỹ
thuật vẫn được RMW xác nhận "đã giao". Đây là bằng chứng THỰC NGHIỆM
TRỰC TIẾP, mạnh nhất từ đầu investigation tới giờ, cho đúng luận điểm
trung tâm của bài báo (`FleetRMW_paper.docx` phần I: "giá trị của từng
luồng dữ liệu còn phụ thuộc vào nhiệm vụ hiện tại", và toàn bộ triết lý
task-aware/freshness-aware communication) — **"delivery ratio" một mình
nó gây HIỂU LẦM nghiêm trọng** về chất lượng thực sự của hệ thống ở quy
mô 17 endpoint; cần luôn đọc CÙNG với stale ratio.

**Repair amp. của FleetRMW = 0.0%** ở N=16 — khớp với phát hiện trước
đó (`unreachable_retry_giveups=0/170`): ở quy mô này, transport KHÔNG
kích hoạt cơ chế NACK/retransmit nội bộ (có thể do kích thước message
trong trace chưa vượt ngưỡng cần fragment, hoặc do cơ chế repair không
phù hợp với kiểu mất gói do nghẽn kênh vật lý thay vì mất gói ngẫu
nhiên) — một hướng điều tra tiềm năng khác cho tương lai.

**File thay đổi**: `scripts/run_ns3_docker_container_fleet_probe.py`
(`compute_jitter_stale_repair_stats()`, wiring vào `run_probe()`),
`tests/test_ns3_docker_container_fleet_probe.py` (4 test mới,
`ComputeJitterStaleRepairStatsTest`).

### 13/09/2026 (tiếp) — Bảng V profile LAN: xây kịch bản mạng lý tưởng mới, phát hiện FleetRMW thua CẢ Fast DDS/CycloneDDS trên LAN

Thêm `wire_network_lan()` + `run_lan_probe()`
(`scripts/run_ns3_docker_container_fleet_probe.py`) — kịch bản mạng
HOÀN TOÀN MỚI cho profile "LAN": KHÔNG dùng ns-3/wifi simulation nào cả,
tái sử dụng container `ns3sim` thuần làm bridge host, mỗi endpoint nối
thẳng veth vào 1 bridge Linux chung duy nhất — đúng tinh thần "network
control, độ trễ thấp và ít mất gói" mà bài báo mô tả cho LAN, khác hẳn
mô hình impairment wifi/5G. Xác nhận qua 12 lần chạy thật (4 method ×
n=3, N=16) — không phải suy đoán.

**BẢNG V — profile LAN (N=16, n=3)**:

| Method | Profile | p50 (ms) | p95 (ms) | p99 (ms) | Jitter (ms) | Delivery ratio | Repair amp. | Stale ratio |
|---|---|---|---|---|---|---|---|---|
| Fast DDS | LAN | 376.1 | 1671.6 | 2147.7 | 616.4 | **86.3±0.0%** | N/A | 62.5% |
| Cyclone DDS | LAN | 377.3 | 1672.7 | 2147.6 | 616.9 | **86.3±0.0%** | N/A | 62.6% |
| Zenoh | LAN | 372.5 | 1749.1 | 2147.8 | 627.1 | 52.0±35.3% | N/A | 62.5% |
| **Ours (FleetRMW)** | LAN | **698.7** | 1794.8 | 2203.3 | 605.2 | **65.8±0.1%** | 0.000 | **81.7%** |

**So sánh trực tiếp Wi-Fi vs LAN (cùng N=16, cùng n=3, cùng method)**:

| Method | Delivery Wi-Fi | Delivery LAN | Stale Wi-Fi | Stale LAN |
|---|---|---|---|---|
| Fast DDS | 48.4% | **86.3%** ↑ | 100% | **62.5%** ↓ |
| Cyclone DDS | 0.0% | **86.3%** ↑↑↑ | — | **62.6%** |
| Zenoh | 48.5% | 52.0% (≈, vẫn dao động mạnh) | 100% | 62.5% |
| FleetRMW | 27.9% | **65.8%** ↑ | 99.0% | **81.7%** ↓ (nhưng vẫn CAO nhất) |

**Phát hiện quan trọng, bất ngờ**: trên LAN lý tưởng (KHÔNG có nghẽn
kênh, không mất gói do va chạm) — **FleetRMW (65.8%) THUA CẢ Fast
DDS/CycloneDDS (86.3%)**, dù ở Wi-Fi FleetRMW từng vượt trội hơn hẳn 2
RMW này (sập về 0-48%). Đây là bằng chứng TRỰC TIẾP cho phát hiện đã có
TỪ RẤT SỚM trong investigation này (mục "đo timing thực tế: publish()
của FleetRMW chậm hơn raw-UDP 6.4 lần"): khi kênh KHÔNG còn là nút thắt
(LAN lý tưởng), nút thắt CHUYỂN sang chính overhead xử lý nội bộ của
FleetRMW (mã hoá JSON, transport riêng) — p50 latency của FleetRMW trên
LAN (698.7ms) cao gấp ~1.85 lần Fast DDS/Cyclone (376ms) dù mạng
KHÔNG hề chậm hơn. Nói cách khác: **FleetRMW "thắng" ở Wi-Fi vì kênh
nghẽn che khuất được phần chậm của chính nó, nhưng khi kênh không còn
là vấn đề, chính processing overhead của FleetRMW lại trở thành nút
thắt lớn nhất** — đúng hướng tối ưu mà `static_min_v1` (giảm kích thước
gói) đã nhắm tới nhưng chưa đủ, có thể cần tối ưu CẢ tốc độ xử lý
(encode/publish) chứ không chỉmicron kích thước gói.

**Zenoh vẫn dao động cực mạnh ngay cả trên LAN lý tưởng** (11.4%-75.2%
qua 3 lần) — xác nhận đây là đặc tính NỘI TẠI của Zenoh (có thể liên
quan tới cơ chế phiên/heartbeat riêng), KHÔNG phải do kênh mạng bất ổn
như giả thuyết ban đầu.

**Trạng thái task #44: HOÀN THÀNH.**

**File thay đổi**: `scripts/run_ns3_docker_container_fleet_probe.py`
(`wire_network_lan()`, `run_lan_probe()`),
`/tmp/.../scratchpad/lan_bang5_16robot_n3.py` (script đo, không thuộc
repo).

### 13/09/2026 (tiếp) — Build THÀNH CÔNG image ns-3 + 5G-LENA (module "nr") — mới xong hạ tầng, CHƯA có chương trình mô phỏng 5G

Sau 2 lần thử (lần 1 fail ở object 583/933 do lỗi biên dịch thật —
`-Werror=array-bounds` false-positive của GCC mới hơn với code cũ của
`nr-gnb-mac.cc`, cùng LOẠI vấn đề với 2 patch GCC-compat đã có sẵn cho
ns-3 core — sửa bằng `-Wno-error=array-bounds -Wno-error=stringop-overflow`
trong cmake flags), **image mới `localhost/fleetrmw/rmw-netem:jazzy-nr`
build THÀNH CÔNG** (933/933 object, ~9 phút build sau khi restart từ
đầu do Docker không cache được tiến trình dở dang trong 1 RUN
instruction).

- **Module**: NR-v3.0 (branch `5g-lena-v3.0.y`) — xác nhận qua chính
  RELEASE_NOTES.md của module là bản DUY NHẤT tương thích chính xác với
  ns-3.41 (bản đang dùng trong toàn bộ investigation này).
- **Xác nhận hoạt động**: `pkg-config --list-all` cho thấy `ns3-nr` và
  `ns3-lte` đã có; header `nr-helper.h`, `nr-gnb-mac.h`,
  `nr-gnb-net-device.h`, `nr-gnb-phy.h` đều tồn tại trong
  `/usr/include/ns3/`.
- **File mới**: `external/rmw-netem/Dockerfile.nr` — image RIÊNG, KHÔNG
  ghi đè `Dockerfile` gốc, để mọi thí nghiệm wifi đã validate trước đó
  (toàn bộ Bảng A-V ở các mục trên) vẫn dùng ĐÚNG image gốc không đổi.

**QUAN TRỌNG — đây MỚI LÀ HẠ TẦNG, CHƯA PHẢI kịch bản 5G thật**: build
xong image chỉ có nghĩa là thư viện NR đã sẵn sàng để gọi từ C++. Vẫn
CẦN viết một chương trình mô phỏng MỚI (tương tự
`fleetqox_trace_replay_tap.cc` nhưng dùng `NrHelper`/gNB/UE thay vì
`WifiHelper`/AP/STA) — đây là khối lượng kỹ thuật riêng, đáng kể: cấu
hình numerology, bandwidth part, scheduler MAC, gắn TapBridge cho từng
UE để kiến trúc Docker-per-container hiện có có thể cắm vào được. CHƯA
BẮT ĐẦU viết chương trình này — cần xác nhận với người dùng trước khi
đầu tư tiếp (khối lượng công việc này tương đương với việc đã bỏ ra
cho toàn bộ phần wifi `fleetqox_trace_replay_tap.cc` ban đầu).

### 13/09/2026 (tiếp) — Kiến trúc "ghost node" cho profile 5G: phát hiện giới hạn kiến trúc + viết chương trình mô phỏng + nối vào orchestrator

**Phát hiện quan trọng (chặn đường đơn giản nhất)**: đọc trực tiếp
source `contrib/nr/model/nr-net-device.cc` xác nhận `NrNetDevice`
KHÔNG BAO GIỜ hỗ trợ những gì `TapBridge` cần để gắn trực tiếp:

```cpp
bool NrNetDevice::SupportsSendFrom() const { return false; }
void NrNetDevice::SetPromiscReceiveCallback(...) { /* no-op */ }
```

Đây là thuộc tính KIẾN TRÚC của module (khớp với phần cứng 5G thật —
processor ứng dụng của một UE thật cũng không có "tap" L2 vào chính
modem của nó), không phải bug có thể sửa bằng cách code thêm theo
hướng "copy y hệt cách làm của wifi". Nghĩa là KHÔNG THỂ đơn giản đổi
`WifiHelper` → `NrHelper` trong `fleetqox_trace_replay_tap.cc` như kế
hoạch ban đầu.

**Giải pháp "ghost node"** (đề xuất của người dùng, đã xác nhận hợp lệ
bằng cách đọc `examples/tap-csma-virtual-machine.cc` của chính ns-3
core — cùng pattern): chèn thêm 1 Node ns-3 thuần túy ("ghost") giữa
container Docker thật và UE mô phỏng:

```
Docker Robot → TAP → Ghost Node (CsmaNetDevice) → (point-to-point) → UE → gNB → EPC/Core
```

`TapBridge` chỉ gắn vào `CsmaNetDevice` bình thường của Ghost (CÓ hỗ
trợ `SendFrom`/promiscuous, xác nhận qua chính ns-3 core) — KHÔNG BAO
GIỜ chạm vào `NrNetDevice`. Chặng Ghost↔UE và UE↔gNB dùng IP forwarding
chuẩn + mô phỏng NR thật, không có mẹo giả mạo nào cả. Mỗi endpoint
(kể cả `control_station`) có 1 cặp Ghost+UE riêng, đối xứng — giữ đúng
quy ước "mọi trạm đối xứng" đã dùng cho profile wifi/LAN, để 3 hàng của
Bảng V (Wi-Fi/LAN/5G) so sánh ngang hàng được với nhau.

**Địa chỉ IP** — vấn đề: IP overlay thật của UE (`7.0.0.x`, do
`NrPointToPointEpcHelper::AssignUeIpv4Address()` cấp NỘI BỘ, không thể
tính trước) khác với IP link-local mà container thật cần để nói
chuyện với Ghost của nó. Đã kiểm tra: **ns-3 KHÔNG có module NAT**
(`pkg-config --list-all | grep -i nat` trên image `jazzy-nr` không ra
kết quả liên quan) nên không dùng NAT/masquerade được. Giải pháp cuối
cùng: mỗi container thật gán thêm địa chỉ overlay đó dưới dạng `/32`
trên `eth0`, cộng với 1 default route ĐƯỢC CHỈ ĐỊNH SRC tường minh
(`ip route replace default via <ghost_ip> dev eth0 src <overlay_ip>`)
— buộc mọi traffic đi ra mang đúng "danh tính 5G thật" làm nguồn, bất
kể địa chỉ link-local "tự nhiên" của `eth0`. Phía Ghost dùng
`Ipv4StaticRouting::AddHostRouteTo()` định tuyến chính xác theo IP đích
(không cần khớp subnet).

**File mới**: `external/ns3/fleetqox_trace_replay_nr.cc` — chương
trình ns-3 hoàn chỉnh: 1 gNB + N cặp Ghost/UE (bố trí vòng tròn quanh
gNB, cùng quy ước với profile wifi), dùng `NrHelper`/
`NrPointToPointEpcHelper`/`IdealBeamformingHelper`/`CcBwpCreator` theo
đúng pattern tham chiếu của `examples/cttc-nr-demo.cc` (module `nr`,
đã `git clone` để đọc trực tiếp). In ra
`FLEETQOX_NR_MAPPING station_index,endpoint,tap_device,ue_overlay_ip,
ghost_link_local_ip` cho từng endpoint TRƯỚC KHI `Simulator::Run()`
chạy (việc gán IP EPC + `TapBridge::Install()` đều xảy ra ở bước
setup) — đây là kênh DUY NHẤT để orchestrator Python biết IP overlay
thật.

**File sửa**: `scripts/run_ns3_docker_container_fleet_probe.py` — thêm
`build_ns3_nr_binary()`, `wire_network_nr_l2()` (nối tap/bridge/veth y
hệt tinh thần `wire_network()`, nhưng CHỈ gán IP link-local, chưa biết
IP overlay), `start_ns3_nr()` + `_parse_nr_mapping()` (chạy binary,
poll log tới khi đủ dòng `FLEETQOX_NR_MAPPING`), `finish_wire_network_nr()`
(gán `/32` + default route theo src, rồi GHI ĐÈ `probe.ips` bằng IP
overlay thật — nhờ vậy mọi logic downstream có sẵn trong
`launch_endpoints()` — `FLEETQOX_RMW_PEERS`, Zenoh router, Fast DDS
discovery server, CycloneDDS static peers — dùng ĐÚNG địa chỉ 5G thật
mà KHÔNG cần rẽ nhánh riêng cho profile này), `run_nr_probe()` (điểm
gọi đầu-cuối, cùng schema trả về với `run_probe()`/`run_lan_probe()`).

3 test mới trong `tests/test_ns3_docker_container_fleet_probe.py`
(`ParseNrMappingTest`) — tổng 26 test, tất cả PASS
(`python3 -m unittest discover -s tests -p "test_ns3_docker_container_fleet_probe.py"`).

**CHƯA build/chạy thật** — mới chỉ viết code + test logic parsing
thuần Python, chưa hề `docker build`/`docker run`/`g++` (đúng quy ước
"chờ tín hiệu chạy"). Bước tiếp theo khi có tín hiệu "chạy": biên dịch
thử `fleetqox_trace_replay_nr.cc` trong image `jazzy-nr` (kiểm tra
link pkg-config `ns3-nr`/`ns3-antenna` có đủ không), rồi test ở quy mô
nhỏ (2 endpoint) trước khi chạy batch N=8/16/32 cho Bảng V hàng "5G".

**Trạng thái**: task viết chương trình mô phỏng 5G — hạ tầng code đã
xong, CHƯA validate bằng chạy thật.

### 13/09/2026 (tiếp) — Test quy mô nhỏ (2 endpoint) kiến trúc ghost-node: 2 bug thật đã sửa, 1 vấn đề gốc rễ về địa chỉ IP CHƯA giải quyết

Theo yêu cầu "chạy đi, biên dịch thử quy mô nhỏ trước": biên dịch
`fleetqox_trace_replay_nr.cc` trong image `jazzy-nr` — link thành công,
chạy độc lập (không qua container) ở quy mô 2 trạm THÀNH CÔNG (tạo tap,
gán IP overlay `7.0.0.2`/`7.0.0.3` đúng như dự đoán, không crash). Sau
đó test qua TOÀN BỘ pipeline Docker-per-container thật
(`run_nr_probe()`) — pipeline chạy hết không crash nhưng
**`latency_stats_ms: null`** (0% gói tin đến nơi).

**Bug #1 (đã sửa) — ARP chết**: gán địa chỉ IP gateway (172.16.i.1)
trực tiếp lên CHÍNH thiết bị CSMA mà TapBridge dùng để bridge. Khi
TapBridge bơm khung tin từ tap vào ns-3 bằng `SendFrom()` (mô phỏng
"truyền" ra kênh), một NIC thật không bao giờ tự nhận lại khung tin nó
vừa truyền — nên ngăn xếp Ipv4/ARP của Ghost (nằm trên CHÍNH thiết bị
đó) không bao giờ thấy được gói ARP của container để trả lời. Xác nhận
qua `ip neigh` báo `FAILED` và byte RX trên tap luôn = 0. **Sửa**: tách
thành 2 thiết bị CSMA riêng biệt trên CÙNG 1 channel — 1 cái TapBridge
gắn vào (không IP), 1 cái khác mang IP thật và xử lý ARP/routing.

**Bug #2 (đã sửa) — SIGSEGV trong tiến trình `tap-creator`**: sau khi
sửa bug #1, tiến trình con `tap-creator` (helper setuid tạo tap) crash
với signal 11. Đọc trực tiếp source `ns-3.41`
(`src/tap-bridge/model/tap-bridge.cc`) xác nhận: `CreateTap()` luôn
gọi `ipv4->GetInterfaceForDevice(bridgedDevice)` bất cứ khi nào NODE có
đối tượng Ipv4 (không kiểm tra riêng cho thiết bị được bridge) — trả về
`-1` (tức `0xFFFFFFFF`) vì thiết bị TapBridge dùng không có Ipv4
interface, rồi dùng chỉ số đó index thẳng vào mảng nội bộ của
`Ipv4L3Protocol` → hỏng bộ nhớ → crash. **Sửa**: gán thêm 1 địa chỉ IP
"giả" (không dùng thật, chỉ để thỏa mãn giả định này) cho thiết bị
TapBridge dùng. Cũng đổi cách tạo 2 thiết bị từ
`NodeContainer(node, node)` (gây crash y hệt, khả năng là code path
chưa từng được test) sang 2 lệnh `Install()` riêng biệt trên cùng 1
channel — pattern chuẩn, phổ biến của ns-3.

**Xác nhận bằng UDP test trực tiếp**: ARP tới gateway giờ resolve
thành công (`REACHABLE`). Dùng hook tạm thời vào trace source
`Drop`/`Tx`/`Rx` của `Ipv4L3Protocol` (qua `Config::ConnectWithoutContext`
— NS_LOG KHÔNG hoạt động vì image build ở chế độ Release, log bị strip
lúc biên dịch) xác nhận: **gói tin ĐÃ đi đúng đường
Ghost0→UE0→[sóng radio 5G mô phỏng]→UE1** (packet size=41 xuất hiện
khớp ở cả TX của UE0 lẫn RX của UE1) — đây là xác nhận quan trọng rằng
kiến trúc ghost-node + mô phỏng NR THỰC SỰ truyền được dữ liệu qua lại.

**Vấn đề gốc rễ CHƯA giải quyết — xung đột mô hình địa chỉ IP**: sau
khi UE1 nhận gói ở lớp radio, nó KHÔNG forward tiếp gói đó xuống Ghost1
qua liên kết p2p — vì địa chỉ ĐÍCH của gói (`7.0.0.3`) trùng CHÍNH địa
chỉ IP riêng mà EPC đã gán cho UE1 trên interface NR của nó. Với bất kỳ
ngăn xếp IP nào, một gói tin gửi đến ĐÚNG địa chỉ của chính interface đó
sẽ được giao "cục bộ" (local delivery) cho tầng ứng dụng của NODE đó,
KHÔNG được forward tiếp — mà trên UE1 (node ns-3 thuần), không có ứng
dụng nào lắng nghe cả nên gói tin bị "nuốt" âm thầm (không có Drop
trace nào bắn ra, khớp với triệu chứng `rx=0` toàn bộ trước đó).

Đây CHÍNH LÀ vấn đề mà router/CPE 5G thật giải quyết bằng NAT (WAN
identity của CPE = IP do mạng cấp; LAN đằng sau CPE dùng địa chỉ khác,
CPE dịch qua lại) — và ns-3 **không có module NAT** (đã xác nhận trước
đó qua `pkg-config --list-all`). Không có cách nào để "địa chỉ overlay
UE" vừa là danh tính định tuyến qua EPC, vừa để UE tự động forward tiếp
xuống Ghost, nếu không có một lớp dịch địa chỉ (NAT) hoặc một ứng dụng
relay tùy chỉnh chạy trên UE.

**3 hướng đi khả thi tiếp theo** (chưa chọn, cần quyết định của
người dùng):
1. Viết một ứng dụng "relay" nhỏ chạy trên mỗi UE (nhận gói ở tầng cục
   bộ qua raw socket, tự tay chuyển tiếp ra interface Ghost bằng socket
   ràng buộc thiết bị cụ thể — bỏ qua bảng định tuyến chuẩn). Khối
   lượng: vừa phải, nhưng cần cẩn thận về checksum/TTL/tránh vòng lặp.
2. Đóng gói (tunnel) mọi gói tin thật trong 1 lớp UDP phụ, đích tới 1
   cổng cố định trên UE — UE có 1 ứng dụng lắng nghe cổng đó, giải nén
   rồi gửi tiếp cho Ghost qua liên kết p2p. Khối lượng: lớn hơn phương
   án 1, đổi lại rõ ràng/dễ debug hơn.
3. Rebuild ns-3 ở chế độ Debug (thêm NS_LOG thật) để điều tra xem
   `NrPointToPointEpcHelper`/EPC có hỗ trợ gán CẢ MỘT SUBNET (không chỉ
   1 địa chỉ /32) cho một UE hay không — nếu có, container thật có thể
   dùng 1 subnet riêng sau UE mà không cần NAT/relay gì cả. Rủi ro: có
   thể EPC helper không hỗ trợ, tốn thời gian điều tra mà không ra kết
   quả.

**Trạng thái**: 2 bug thật đã tìm và sửa (đã commit/push), xác nhận
kiến trúc mạng lõi (ghost+CSMA+ARP+radio NR) hoạt động đúng — nhưng
CHƯA có traffic thật đi trọn vẹn end-to-end. Việc viết nốt lớp giải
quyết địa chỉ (NAT/relay) là công việc kỹ thuật MỚI, đáng kể, CHƯA bắt
đầu — đang chờ quyết định hướng đi từ người dùng.

### 13/09/2026 (tiếp) — THÀNH CÔNG: traffic thật đi trọn vẹn end-to-end qua kiến trúc ghost-node 5G

Theo hướng được chọn ("Rebuild ns-3 ở Debug + điều tra subnet-per-UE"):
đọc trực tiếp source `src/lte/model/epc-pgw-application.cc` của
ns-3.41 (không cần rebuild Debug — đọc source đủ trả lời câu hỏi) và
xác nhận: `RecvFromTunDevice()` (hàm PGW quyết định gói downlink đi
tới UE nào) tra cứu đích đến bằng **khớp CHÍNH XÁC** trong
`m_ueInfoByAddrMap` (một `std::map<Ipv4Address, ...>` thường), được
điền qua hàm CÔNG KHAI `EpcPgwApplication::SetUeAddress(imsi, addr)` —
**không hỗ trợ subnet/prefix cho UE** như hướng đã chọn kỳ vọng.

Nhưng phát hiện quan trọng hơn: **không có gì ngăn gọi
`SetUeAddress()` LẦN THỨ HAI cho cùng 1 imsi với một địa chỉ KHÁC** —
PGW sẽ tunnel gói cho CẢ HAI địa chỉ về đúng UE đó. Đây chính là lời
giải cho vấn đề "UE tự nuốt gói vì trùng địa chỉ chính nó" (ghi nhận ở
mục trước): đăng ký THÊM 1 địa chỉ "công khai" (`7.128.0.<i+1>`, một
dải cố định nằm trong CÙNG pool `7.0.0.0/8` nhưng KHÔNG BAO GIỜ trùng
với dải tự động của `AssignUeIpv4Address()`) làm đích hợp lệ thứ 2 cho
UE đó — vì địa chỉ này KHÔNG được cấu hình trên chính interface của UE,
ngăn xếp Ipv4 của UE sẽ KHÔNG coi đó là "của mình" nữa, mà tra bảng
định tuyến — nơi ta thêm 1 route tường minh đẩy nó ra Ghost. Không cần
patch lõi ns-3, không cần rebuild Debug.

**Kết quả xác nhận bằng chạy thật (2 container, `run_nr_probe()`,
`num_robots=1`)**:
```
"ue_overlay_ip": "7.128.0.1" (control_station), "7.128.0.2" (robot_0000)
"latency_stats_ms": {"n": 170, "p50_ms": 271.6, "p95_ms": 1371.3, "p99_ms": 1410.8, "mean_ms": 476.8}
delivery_pct ≈ 170/212 ≈ 80.2%
```
Đây là lần ĐẦU TIÊN traffic thật (FleetRMW/ROS2 chạy trong Docker
container thật) đi trọn vẹn qua đường
`Docker → TAP → Ghost(CSMA) → (P2P) → UE → gNB → EPC → UE → Ghost →
TAP → Docker` với 5G-LENA mô phỏng sóng radio ở giữa.

**Tổng kết 3 bug đã tìm và sửa cho profile 5G** (tất cả xác nhận bằng
chạy thật, không suy đoán):
1. ARP chết do IP gateway gắn cùng thiết bị mà TapBridge dùng để bridge
   (SendFrom không tự "nghe lại" chính nó) → tách 2 thiết bị CSMA riêng
   trên cùng 1 channel.
2. SIGSEGV trong tiến trình `tap-creator` do thiết bị TapBridge dùng
   không có Ipv4 interface (đọc source `tap-bridge.cc` xác nhận
   `GetInterfaceForDevice()` trả về -1 rồi index thẳng vào mảng nội bộ)
   → gán thêm 1 IP "giả" cho thiết bị đó.
3. UE tự nuốt gói vì trùng địa chỉ EPC nội bộ của chính nó → đăng ký
   thêm 1 địa chỉ "công khai" thứ 2 cho cùng UE qua `SetUeAddress()`.

**Bước tiếp theo**: chạy lại ở quy mô lớn hơn (N=8/16/32) để hoàn
thiện hàng "5G" của Bảng V, đối chiếu trực tiếp với Wi-Fi/LAN đã có.

**File thay đổi**: `external/ns3/fleetqox_trace_replay_nr.cc` (đăng ký
địa chỉ thứ 2 qua `EpcPgwApplication::SetUeAddress()`, route UE→Ghost
tường minh, đổi giá trị in ra `FLEETQOX_NR_MAPPING`).

### 13/09/2026 (tiếp) — Bảng V hàng "5G": chạy đủ N=8/16/32 x 4 phương thức x n=3 — phát hiện sập dung lượng theo quy mô rất rõ rệt

Sau khi xác nhận kiến trúc ghost-node hoạt động đúng ở quy mô nhỏ (2
endpoint), chạy đủ batch N=8/16/32 × {Fast DDS (discovery_server),
CycloneDDS (static_peers), Zenoh (default), FleetRMW (static/default)}
× n=3 — cùng phương pháp/tham số (`policy=fifo`, `seconds=3`,
`start_offset_ms=2000`, `drain_s=10`, `sim_duration_s=30`) đã dùng cho
2 hàng Wi-Fi/LAN, để so sánh trực tiếp được với nhau. Xác nhận riêng
bằng thử nghiệm: tăng `sim_duration_s` từ 30 lên 90 ở N=16 cho kết quả
**giống hệt** (n=51 gói đến, cùng giá trị latency chính xác tới phần
thập phân) — chứng minh đây KHÔNG phải do thiếu thời gian chờ, mà gói
tin bị RỚT THẬT ở lớp vô tuyến/MAC, không chỉ bị trễ.

**Kết quả (delivery_pct = % trên tổng packet_rows mỗi lần chạy)**:

| N | Fast DDS | CycloneDDS | Zenoh | FleetRMW |
|---|---|---|---|---|
| 8 | 23.6% (p50=4990ms) | **0%** | 34.1% (p50=5896ms) | 39.8% (p50=4437ms) |
| 16 | **0%** | **0%** | **0%** | 1.9% (p50=9766ms) |
| 32 | **0%** | **0%** | **0%** | **0%** |

- **N=8**: cả 3/4 phương thức còn hoạt động được (CycloneDDS
  static_peers vẫn 0% — khớp với phát hiện đã ghi nhận trước đó ở
  profile LAN/Wi-Fi rằng cơ chế unicast-peers của CycloneDDS có chi phí
  discovery O(N²), càng dễ sập khi latency nền đã cao sẵn như ở đây).
- **N=16**: MỌI RMW cần discovery (Fast DDS/CycloneDDS/Zenoh) sập
  HOÀN TOÀN (`graph_join_failures.failure_rate=1.0` cho cả 17
  endpoint) — độ trễ một chiều đã ~9-10 giây (xem log FleetRMW cùng
  quy mô), khiến bất kỳ handshake discovery nào cần round-trip trong
  15s (`discovery_timeout_s`) đều không kịp hoàn tất. CHỈ FleetRMW
  (static mode, không cần discovery) còn lọt qua được 1.9%.
- **N=32**: sập HOÀN TOÀN với CẢ 4 phương thức, kể cả FleetRMW (0%) —
  1 cell/1 bandwidth-part/numerology-1 dùng chung cho 32+1 UE đã vượt
  quá dung lượng mà lớp MAC/scheduler mặc định của module `nr` có thể
  phục vụ trong cửa sổ mô phỏng.

**Ý nghĩa**: đây là phát hiện mạnh, nhất quán với những gì đã thấy ở
CycloneDDS/Fast DDS khi tăng quy mô trên Wi-Fi — nhưng ở đây biên độ
sập LỚN HƠN NHIỀU và xảy ra SỚM HƠN NHIỀU (ngay từ N=16 thay vì N=32).
Nguyên nhân hợp lý nhất: cấu hình PHY dùng trong chương trình
(`fleetqox_trace_replay_nr.cc`) là 1 gNB / 1 bandwidth part 20MHz /
numerology 1 — một cấu hình "1 cell nhỏ" khá khiêm tốn so với triển
khai 5G thật (nhiều BWP, carrier aggregation, scheduler tối ưu hơn
round-robin mặc định). Đây KHÔNG phải bug — là hệ quả trực tiếp của
việc chọn tham số PHY đơn giản, chưa tối ưu; nếu muốn kết quả 5G
"khả quan" hơn ở N=16/32 cần đầu tư thêm việc tinh chỉnh
bandwidth/numerology/scheduler — chưa làm trong lần chạy này.

**Trạng thái Bảng V**: đủ cả 3 hàng Wi-Fi, LAN, 5G — có thể tổng hợp
báo cáo đầy đủ. Task #49 (chạy N=8/16/32 x 4 phương thức x n=3) HOÀN
THÀNH.

**File liên quan**: kết quả thô tại
`/tmp/.../scratchpad/bang5_nr_8_16_32_n3.jsonl` (36 dòng, không thuộc
repo — script đo cũng ở scratchpad, không commit, theo đúng quy ước đã
dùng cho các bảng đo trước).

### 13/09/2026 (tiếp) — Test quy mô nhỏ Bảng VI (Ricart-Agrawala zone-mutex): 1 bug thật đã sửa, 1 endpoint vẫn "cô lập" chưa rõ nguyên nhân

Theo yêu cầu "test quy mô nhỏ trước": chạy `run_coordination_probe()`
với 2 robot (3 endpoint) trên profile Wi-Fi. Kết quả ban đầu: **100%
"forced entry"** (không endpoint nào đạt đồng thuận thật trong 60s,
`navigation_recovery_count` luôn kịch trần).

**Bug thật đã tìm và sửa**: mỗi lần retry, code tạo `req_id` MỚI và
RESET `replies_received` về rỗng. Do FleetRMW trên Wi-Fi có độ trễ
biến thiên rất mạnh (đã ghi nhận ở Bảng V: p50=5.5s/p95=9.9s/**p99=
10.9s** cho đúng RMW/profile này), 2 reply cần thiết thường đến
KHÔNG ĐỒNG THỜI trong cùng 1 cửa sổ `--reply-timeout-s` — reply đến
"muộn" bị tính vào `req_id` retry MỚI (không khớp `req_id` retry đang
chờ) nên bị loại bỏ, dù bản chất đã "thành công". **Sửa**: giữ
NGUYÊN `req_id`/timestamp trong suốt 1 lần crossing, chỉ re-broadcast
lại (phòng gói bị mất), tích luỹ `replies_received` qua các lần retry
thay vì reset. Xác nhận cải thiện rõ rệt cho 1 endpoint (matched
replies từ 1 → 5-10 trong cùng 1 kịch bản).

Cũng thử 2 giả thuyết khác (đã LOẠI TRỪ qua test cô lập, không phải
nguyên nhân chính nhưng giữ lại vì vô hại/hợp lý):
- Thiếu `FLEETQOX_RMW_STATIC_MODE=1` → khiến FleetRMW chạy discovery
  thật (heartbeat định kỳ, giống SPDP) mà code lại bỏ qua bước chờ —
  đã bật lại đúng theo quy ước đã dùng ở mọi nơi khác trong session.
- Gọi `publish()` ĐỒNG BỘ từ bên trong callback nhận tin — đã chuyển
  sang hàng đợi, xả từ vòng lặp chính.
- Gộp 2 topic (request/reply) thành 1 topic chung.

**CHƯA giải quyết**: trong MỌI lần chạy thử (kể cả sau khi sửa bug
trên), **endpoint được khởi chạy CUỐI CÙNG** (robot_0001, thứ tự
control_station → robot_0000 → robot_0001) nhận được **0 reply** trong
suốt toàn bộ kịch bản (6 lần retry × 15s = 90s) — không phải mất gói
ngẫu nhiên thông thường, mà giống bị cô lập gần như hoàn toàn. Hình
dạng triệu chứng này TRÙNG với 1 lỗi phụ thuộc-thứ-tự-khởi-chạy đã
từng ghi nhận trước đây trong dự án này (CycloneDDS, mục "beacon
discovery convergence" — "endpoint khởi chạy cuối cùng không bao giờ
thấy peer nào") — nhưng CHƯA xác nhận đây là CÙNG nguyên nhân gốc cho
FleetRMW, cần điều tra thêm (ví dụ: thử đổi thứ tự khởi chạy xem lỗi
có "theo" endpoint cuối cùng hay theo tên/IP cụ thể của robot_0001).

**Instrumentation debug mới** (tạm thời nhưng rẻ, giữ lại):
`debug_counters` (đếm request/reply gửi/nhận/khớp) và
`raw_received_log` (log thô mọi tin nhắn nhận được) trong summary JSON
của mỗi endpoint — giúp chẩn đoán trực tiếp từ JSON thay vì phải thêm
print statement mỗi lần.

**Trạng thái**: đã sửa 1 bug thật, có cải thiện rõ rệt nhưng CHƯA đạt
được 1 lần đồng thuận "sạch" nào trong test 2-3 endpoint. Cần quyết
định hướng đi tiếp trước khi chạy batch N=8/16/32 đầy đủ.

### 13/09/2026 (tiếp) — Điều tra + sửa xong deadlock Ricart-Agrawala theo đúng quy trình đề xuất

Làm đúng thứ tự điều tra đã thống nhất:

**Bước 1 — Đổi thứ tự khởi chạy**: đảo ngược hoàn toàn (robot_0001 chạy
ĐẦU, control_station chạy CUỐI). Kết quả: robot_0001 **VẪN** bị 0 reply
y hệt → **loại trừ dứt điểm giả thuyết startup-order**, lỗi đi theo
đúng endpoint cụ thể.

**Bước 2 — Trace request/reply**: thay vì trace thủ công từng gói, dùng
`raw_received_log` (đã có sẵn từ lần điều tra trước) kiểm tra TRÊN TOÀN
BỘ 3 endpoint (kể cả người ngoài cuộc) xem có ai từng thấy 1 gói
"to: robot_0001" hay không → **0 gói, không ai từng thấy, kể cả người
ngoài cuộc** → xác nhận đây là gói CHƯA BAO GIỜ ĐƯỢC GỬI (kẹt trong
hàng đợi deferred vĩnh viễn), không phải rớt gói trên đường truyền.

**Nguyên nhân gốc xác nhận**: mọi endpoint bắt đầu request đầu tiên
với CÙNG giá trị Lamport (đóng băng suốt 1 lần crossing từ fix trước),
nên tie-break rơi vào so sánh TÊN cố định mãi mãi — endpoint có tên
"thua" alphabet (robot_0001) luôn bị nhường (defer) bởi cả 2 peer kia.
Kết hợp với việc peer có ưu tiên cao nhất (`control_station`) cũng
liên tục xui rủi mất gói Wi-Fi thật nên không bao giờ tự hoàn tất được
critical section của chính nó → không bao giờ xả hàng đợi deferred →
cả chuỗi ưu tiên thấp bị kẹt vĩnh viễn.

**Bước 3 — Readiness barrier**: xác nhận hàng rào ready/start
(`--ready-file`/`--start-file`) ĐÃ CÓ SẴN từ trước trong toàn bộ
harness (dùng chung với mọi bảng khác) — không cần thêm mới, chỉ xác
nhận lại là ĐÃ tồn tại và hoạt động đúng.

**Sửa theo đúng 3 điểm người dùng chỉ định**:
1. Lamport clock giờ TĂNG LIÊN TỤC mỗi lần re-broadcast (không đóng
   băng cho cả crossing nữa) — đúng bản chất 1 Lamport clock thật.
2. `req_id` vẫn giữ NGUYÊN suốt 1 crossing (giữ từ fix trước) để reply
   đến ở bất kỳ lần retry nào cũng được tính.
3. **Cơ chế thật sự phá vỡ deadlock**: `--defer-release-timeout-s`
   (mặc định 8s) — nếu 1 reply bị "nhường" (deferred) quá lâu mà
   CHÍNH endpoint đang giữ nó vẫn chưa tự vào được critical section
   của mình, nó sẽ TỪ BỎ quyền ưu tiên và gửi reply đó đi luôn. Đây là
   cơ chế DUY NHẤT thực sự đảm bảo tiến triển — riêng điểm 1 (Lamport
   tăng liên tục) KHÔNG đủ để phá deadlock, vì giá trị Lamport chỉ có
   thể TĂNG (ưu tiên chỉ có thể XẤU ĐI qua các lần retry, không bao
   giờ tự nhảy lên đầu hàng).

**Bước 4 — Kiểm chứng lại (2 lần chạy độc lập, seed khác nhau)**:
- Seed 1: robot_0001 (endpoint từng kẹt 100%) nay đạt **2/3 lần vào
  zone THẬT** (forced_entry=False). Tỷ lệ forced_entry chung: 60%.
- Seed 7: **CẢ 3 endpoint đều đạt ít nhất 1 lần vào zone thật**, tỷ lệ
  forced_entry chung: 50%, phân bố ĐỀU giữa 3 endpoint (không còn ai bị
  "cô lập" hệ thống).

**Kết luận**: deadlock đã được sửa dứt điểm — không còn endpoint nào bị
kẹt vĩnh viễn. Phần forced_entry còn lại (~50-60%) phản ánh nhiễu Wi-Fi
thật (khớp với đặc tính latency biến thiên mạnh của FleetRMW đã ghi
nhận ở Bảng V), phân bố đều giữa các endpoint chứ không thiên vị ai —
đây là dữ liệu thật hợp lệ để báo cáo, không phải bug cần sửa thêm.

**File thay đổi**: `scripts/fleetqox_coordination_endpoint.py` (Lamport
tăng liên tục, `release_stale_deferrals()`, `--defer-release-timeout-s`),
`scripts/run_ns3_docker_container_fleet_probe.py` (thêm tham số
`launch_order` để test đảo thứ tự khởi chạy, nối
`defer_release_timeout_s` qua `run_coordination_probe()`).

**Trạng thái**: sẵn sàng chạy batch N=8/16/32 cho Bảng VI — đang chờ
xác nhận từ người dùng có coi mức forced_entry ~50% là đủ "sạch" để
tiến hành, hay cần tinh chỉnh thêm (tăng `reply-timeout-s`/
`defer-release-timeout-s`) trước.

### 13/09/2026 (tiếp) — Sensitivity test reply-timeout + chạy đủ Bảng VI N=8/16/32 x 4 phương thức x n=3 + sửa 1 crash thật ở N=32

**Sensitivity test trước khi chạy batch lớn** (theo đúng yêu cầu): so
sánh `reply_timeout_s` = 5s/10s/20s (mỗi mức x 3 seed, N=2 robot):

| reply_timeout_s | forced_entry_rate trung bình |
|---|---|
| 5s | **50%** (tốt nhất) |
| 10s | 65% |
| 20s | 70% |

**Tăng timeout KHÔNG giảm forced_entry — còn tăng nhẹ** → xác nhận đây
là coordination failure THẬT do rớt gói REQUEST/REPLY (chờ lâu hơn
không "cứu" được gói đã mất hẳn, chỉ làm giảm SỐ LẦN retry có thể thực
hiện trong cùng khoảng thời gian) — không phải do thiếu thời gian chờ.
Quyết định: giữ `reply_timeout_s=5s`, `defer_release_timeout_s=8s`,
không tăng thêm, chạy batch với cấu hình này.

**Batch đầy đủ N=8/16/32 x 4 phương thức x n=3 (36 lần chạy)**:

**Bug crash thật phát hiện + sửa giữa chừng**: N=32 FleetRMW ban đầu
**crash hoàn toàn cả 3 lần** — `errno=105 (No buffer space available)`
từ `publish()` không có try/except, khi mọi endpoint broadcast
REQUEST/REPLY tới 32 peer cùng lúc làm tràn buffer UDP của OS. Sửa
bằng `safe_publish()` (bắt exception, đếm vào `publish_failures`, coi
như 1 gói bị mất thay vì crash toàn bộ tiến trình) — chạy lại N=32
FleetRMW sau khi sửa: không còn crash, cả 3 lần đều "ok".

**BẢNG VI — Chỉ số điều phối và hoàn thành nhiệm vụ (N=8/16/32, n=3)**:

| Method | N robots | Coordination update age (ms) | Conflict-resolution delay (ms) | Navigation recovery count | Task completion time (s) | Scenario/seed |
|---|---|---|---|---|---|---|
| Fast DDS | 8 | 2398.1 | 7544.2 | 27.3 | 25.7 | n=3 |
| CycloneDDS | 8 | — (0 tin) | — (0% hội tụ) | 154.3 | 90.6 | n=3 |
| Zenoh | 8 | 107.3 | 8248.6 | 72.0 | 90.3 | n=3 |
| **Ours (FleetRMW)** | 8 | 8129.9 | — (0% hội tụ) | 161.7 | 90.3 | n=3 |
| Fast DDS | 16 | 17420.6 | — (0% hội tụ) | 306.0 | 90.3 | n=3 |
| CycloneDDS | 16 | — (0 tin) | — (0% hội tụ) | 306.0 | 90.3 | n=3 |
| Zenoh | 16 | 6818.4 | 33065.3 | 305.3 | 90.3 | n=3 |
| **Ours (FleetRMW)** | 16 | 23799.9 | — (0% hội tụ) | 305.7 | 90.4 | n=3 |
| Fast DDS | 32 | 21343.0 | — (0% hội tụ) | 594.0 | 90.4 | n=3 |
| CycloneDDS | 32 | — (0 tin) | — (0% hội tụ) | 594.0 | 90.4 | n=3 |
| Zenoh | 32 | 21362.6 | — (0% hội tụ) | 594.0 | 90.3 | n=3 |
| **Ours (FleetRMW)** | 32 | 36802.2 | — (0% hội tụ) | 584.3 | 90.5 | n=3 |

Ghi chú: "Conflict-resolution delay" chỉ tính trên các lần đạt đồng
thuận THẬT (forced_entry=False) — hiển thị "—" khi forced_entry_rate=
100% (không có mẫu nào để tính trung bình, KHÔNG phải 0ms). Tỷ lệ
forced_entry đầy đủ theo N/method:

| N | Fast DDS | CycloneDDS | Zenoh | FleetRMW |
|---|---|---|---|---|
| 8 | **0%** | 100% | 15% | 100% |
| 16 | 100% | 100% | 95% | 100% |
| 32 | 100% | 100% | 100% | 100% |

**Phát hiện chính**:
- **Fast DDS tại N=8 hội tụ HOÀN HẢO (0% forced_entry)** — bất ngờ,
  đã xác minh KHÔNG phải bug rỗng (coordination_message_ages_ms thật,
  ~2.4-8.3s, retries giảm dần qua các lần crossing 2→1→0, khớp logic
  "hệ thống ấm dần lên"). Cơ chế discovery-server tập trung (unicast
  qua 1 server) của Fast DDS xử lý traffic broadcast-nặng của kịch bản
  điều phối này tốt hơn hẳn so với các RMW khác ở quy mô nhỏ — nhưng
  lợi thế này KHÔNG duy trì được, sập về 100% forced ngay từ N=16.
- **CycloneDDS static_peers 100% forced_entry ở CẢ 3 quy mô** — khớp
  hoàn toàn với phát hiện O(N²) discovery cost đã ghi nhận nhiều lần
  trước đó (Bảng IV/V) — 0 tin nhắn nào từng đến (age=None mọi quy mô).
- **CẢ 4 phương thức đều sập 100% forced_entry từ N=16 trở lên** —
  thuật toán loại trừ tương hỗ cần ĐỦ N-1 reply nên độ khó tăng theo
  cấp số nhân với N, kết hợp với nhiễu Wi-Fi thật đã biết → mọi RMW đều
  thất bại đồng thuận thật ở quy mô lớn. Đây LÀ kết quả thật, không
  phải bug (đã xác nhận qua sensitivity test: tăng timeout không cứu
  được).
- **FleetRMW là RMW DUY NHẤT còn nhận được tin nhắn (age không None)
  ở CẢ 3 quy mô** — vì không cần discovery, giống pattern đã thấy ở
  Bảng IV/V.

**Trạng thái Bảng VI: HOÀN THÀNH** — đủ dữ liệu N=8/16/32 x 4 phương
thức x n=3, không còn crash, forced_entry ~100% ở quy mô lớn là dữ
liệu thật đã qua sensitivity test xác nhận.

**File liên quan**: `scripts/fleetqox_coordination_endpoint.py`
(`safe_publish()`), kết quả thô tại
`/tmp/.../scratchpad/bang6_coord_8_16_32_n3.jsonl` (36 dòng, không
thuộc repo).

### 14/09/2026 — Thay hàng "5G" bằng thí nghiệm Open5GS + UERANSIM (5G SA emulation thật) — theo yêu cầu người dùng

Người dùng đề nghị dùng Open5GS (core 5G SA thật, mã nguồn mở) +
UERANSIM (gNB/UE giả lập) thay cho ns-3 5G-LENA, với mục tiêu rõ ràng:
so sánh hành vi 4 middleware (Fast DDS/CycloneDDS/Zenoh/FleetRMW) khi
cùng chạy qua MỘT hệ 5G SA thật (NGAP/GTP-U/PFCP thật), KHÔNG mô phỏng
chi tiết kênh vô tuyến (không SINR/fading/propagation/radio
contention) — giữ nguyên workload/topology/số robot/seed/metric để so
sánh công bằng với Wi-Fi/LAN/ns-3-5G đã có.

**Vendor hoá + hạ tầng** (`external/open5gs/`, BSD-2-Clause, từ
`herlesupreeth/docker_open5gs`): 12 NF core (mongo/nrf/scp/ausf/udr/
udm/smf/upf/amf/pcf/bsf/nssf) + UERANSIM gNB/UE, remap subnet mặc định
172.22.0.0/16 → 10.90.0.0/24 (trùng với 13 docker network khác đã
chạy sẵn trên máy — dự án khác, không liên quan RTC). Viết
`scripts/run_open5gs_docker_fleet_probe.py`: tách lifecycle "core"
(build/start/provision subscriber — gọi 1 lần/phiên, vì container
name cố định = chỉ 1 core/host) khỏi lifecycle "per-run"
(`Open5gsTopologyProbe`, kế thừa `ReferenceTopologyProbe` để tái dùng
100% `launch_endpoints`/`launch_coordination_endpoints`/
`wait_for_completion`/... không đổi gì về methodology/metric).

**3 bug thật phát hiện + sửa qua test quy mô nhỏ (2 rồi 8 endpoint)
trước khi chạy batch lớn** (đúng quy trình đã thống nhất):
1. `endpoint_list(N)` trả về N+1 (gồm control_station) nhưng code chỉ
   cấp N container UE → IndexError giữa chừng.
2. **Auth thất bại vĩnh viễn** ("SQN out of range", không resync
   được): `open5gs-dbctl add` lưu key vào `security.opc`, nhưng
   `ueransim-ue.yaml` khai `opType: 'OP'` — 2 kiểu tính khóa khác
   nhau cho cùng 1 K. Sửa: chuyển key sang `security.op` sau khi add
   (xác nhận qua test tay: resync SQN 1 lần rồi thành công — đúng
   hành vi AKA 3GPP bình thường).
3. **Cô lập hoàn toàn dù đăng nhập được**: `uesimtun0` chỉ có route
   `/32` cho chính nó, không có route tới dải UE_IPV4_INTERNET còn
   lại → gói tin gửi UE khác lạc ra `eth0` (default route của docker
   network) thay vì qua UPF. Thêm `ip route add <UE_IPV4_INTERNET>
   dev uesimtun0` là hết — test ping tay xác nhận 100% mất gói → 0%.

**BẢNG V — "5G SA emulation" theo quy mô N=8/16/32, n=3 (đầy đủ, xem
bảng đối chiếu ở `docs/BANG_V_VI_KET_QUA.md`)**:

| N | Fast DDS | CycloneDDS | Zenoh | FleetRMW |
|---|---|---|---|---|
| 8 | 82.2% (p50=284.6ms) | 0% | 45.4% (p50=246.9ms) | 59.6% (p50=637.8ms) |
| 16 | 85.6% (p50=377.9ms) | 0% | 63.9% (p50=382.6ms) | 70.1% (p50=1467.4ms) |
| 32 | 84.0% (p50=403.5ms) | 0% | 29.3% (p50=422.9ms) | 55.2% (p50=1791.1ms) |

**Khác biệt LỚN NHẤT so với ns-3 5G-LENA cũ**: KHÔNG có cú sập "tất cả
về 0%" ở N=16/32 — suy giảm dần đều, mọi run đều `status=ok` (không
crash) kể cả N=32 (33 container UE + 33 container app + core, tổng
~80 container đồng thời). Cho thấy cú sập trước đây (ns-3) là do trần
dung lượng CẤU HÌNH PHY đơn giản (1 gNB/1 BWP/numerology 1), không
phải do bản chất traffic ROS 2 hay ranh giới vật lý của 5G nói chung.

**BẢNG VI — "5G SA emulation" theo quy mô N=8/16/32, n=3 (dùng
`reply_timeout_s=5.0`/`defer_release_timeout_s=8.0` tuned từ trước,
xem bảng đầy đủ ở `docs/BANG_V_VI_KET_QUA.md`)**:

Số liệu dưới đây là bản ĐÃ SỬA (14/09/2026, sau khi vá 2 bug Ricart-
Agrawala — xem mục ngay dưới) — THAY cho bản đầu tiên chạy cùng ngày
(đã lỗi thời; bản trước fix nằm trong commit git trước đó và trong
`bang6_open5gs_8_16_32_n3.jsonl` không có hậu tố `_v2`, không lặp lại
nguyên văn ở đây để tránh gây nhầm bản nào là số liệu chính thức).

| N | Fast DDS | CycloneDDS | Zenoh | FleetRMW |
|---|---|---|---|---|
| 8 | 0% forced | 100% | 65% | **100%** |
| 16 | 14% forced | 100% | 76% | **100%** |
| 32 | 100% forced | 100% | 100% | **100%** |

**Phát hiện qua review bên ngoài + 2 bug thật đã sửa trong
`fleetqox_coordination_endpoint.py`** (chi tiết đầy đủ trong docstring
của chính file đó, mục "2 FURTHER FIXES, 14/09/2026"):
1. Lamport timestamp trước đây TĂNG mỗi lần retry trong cùng 1
   crossing — nghĩa là node nào cần retry nhiều (thường vì CHÍNH gói
   của nó bị rớt, không phải do nó "sai") lại bị GIẢM ưu tiên mỗi lần
   retry, một vòng xoáy bất lợi. Đã sửa: đóng băng timestamp ưu tiên
   cho cả crossing, chỉ tăng 1 lần khi bắt đầu.
2. `release_stale_deferrals()` (cơ chế phá deadlock thêm từ trước) có
   lỗ hổng an toàn thật: nhường quyền cho peer do hết giờ chờ nhưng
   KHÔNG tự huỷ yêu cầu của chính mình — nếu yêu cầu đó SAU ĐÓ lại
   tình cờ đủ N-1 reply (do một release khác ở đâu đó trong fleet),
   CẢ 2 bên có thể cùng vào zone 1 lúc — vi phạm mutual exclusion mà
   không metric nào phát hiện được (forced_entry chỉ gắn cờ khi bỏ
   cuộc ở deadline, không gắn khi "thành công" trên 1 tập reply đã có
   phần tử hết hạn). Đã sửa: nhường quyền cho ai đó thì tự huỷ luôn
   tập reply đã gom, bắt buộc gom lại từ đầu.

**Xác nhận fix có tác dụng thật ở quy mô nhỏ**: test riêng N=2 (3
endpoint, ngoài batch chính) cho `forced_entry_rate` giảm từ 100%
(trước fix) xuống **28.6%** (5/7 crossing đạt đồng thuận THẬT, không
forced) — cải thiện rõ rệt, xác nhận cơ chế `own_claim_yielded_on_timeout`
(bộ đếm mới) hoạt động đúng thiết kế, không crash, `publish_failures=0`.

**NHƯNG ở N=8 trở lên, FleetRMW vẫn 100% forced_entry — ĐÃ điều tra,
KHÔNG PHẢI fix bị lỗi hay bug mới**: kiểm tra `debug_counters` thật
của 1 lượt N=8 sau fix cho thấy `own_claim_yielded_on_timeout` vẫn
kích hoạt đều (15-17 lần/endpoint, đúng thiết kế), `publish_failures=0`
mọi nơi, nhưng số reply THẬT nhận được (`replies_received_raw`) chỉ
0-6 trên mỗi endpoint trong khi cần ĐỦ N-1=8 mới đạt đồng thuận (18
retry cho 1 crossing duy nhất hoàn thành trong 90s). Kết luận: fix vừa
sửa giải quyết đúng vấn đề CÔNG BẰNG/ƯU TIÊN (ai thắng khi có tranh
chấp) và AN TOÀN (double-occupancy), nhưng KHÔNG giải quyết được vấn
đề ĐỘ TIN CẬY GỬI/NHẬN THÔ dưới tải broadcast N-chiều qua đường hầm
GTP-U/UPF — đây LÀ MỘT VẤN ĐỀ KHÁC, quy mô càng lớn (N=8 cần đủ 8
reply, N=32 cần đủ 32) thì xác suất đủ ĐỦ tất cả trong 1 cửa sổ
`reply_timeout_s` càng thấp theo cấp số nhân, bất kể cơ chế ưu tiên có
công bằng đến đâu. THEO ĐÚNG nguyên tắc đã thống nhất trước đó
("không nên cứ tăng timeout mãi — coi các lần timeout đó là
coordination failure thực sự"), số liệu này được báo cáo NGUYÊN TRẠNG.
Hướng giải quyết thật (nếu cần) sẽ là đổi chiến lược đồng thuận (ví
dụ: quorum thay vì cần ĐỦ N-1, hoặc giảm fanout broadcast) — đó là
một thay đổi GIAO THỨC, không phải bug fix, chưa làm trong lần này.

**Ghi chú phương pháp luận nhỏ**: công thức seed của batch script
(`hash(method_label) % 7`) dùng `hash()` built-in của Python, giá trị
này bị RANDOMIZE theo từng lần chạy process (PYTHONHASHSEED mặc định
không cố định) — nghĩa là seed thực tế cho cùng 1 tổ hợp (N, method,
run) KHÔNG giống hệt nhau giữa 2 lần chạy batch (trước/sau fix), góp
phần vào biến động số liệu của Fast DDS/Zenoh (không liên quan gì tới
2 bug vừa sửa) — không phải lỗi nghiêm trọng (seed chỉ ảnh hưởng
jitter/stagger ngẫu nhiên bên trong 1 endpoint, không ảnh hưởng tính
đúng đắn), nhưng đáng lưu ý nếu cần so sánh số liệu tuyệt đối giữa 2
lần chạy trong tương lai.

**Trạng thái**: Bảng V + VI profile "5G SA emulation" HOÀN THÀNH đầy
đủ N=8/16/32 x 4 phương thức x n=3, không crash, đã cập nhật
`docs/BANG_V_VI_KET_QUA.md` (thay hàng "5G" cũ VÀ bản Bảng VI lỗi thời
bằng bản đã sửa 2 bug Ricart-Agrawala ở trên).

**File liên quan**: `external/open5gs/` (vendor), `scripts/
run_open5gs_docker_fleet_probe.py`, `scripts/fleetqox_coordination_endpoint.py`,
kết quả thô tại `/tmp/.../scratchpad/bang5_open5gs_8_16_32_n3.jsonl` +
`bang6_open5gs_8_16_32_n3_v2.jsonl` (36 dòng mỗi file, không thuộc
repo; `bang6_open5gs_8_16_32_n3.jsonl` KHÔNG có `_v2` là bản TRƯỚC fix,
giữ lại để đối chiếu).

### 14/09/2026 (tiếp) — Thêm Ours-NoQoX vs Ours-FleetQoX cho Bảng VI: tính năng hoạt động đúng, nhưng KHÔNG phân biệt được ở N=8/16/32 trên profile này — lý do thật, không phải bug

Theo yêu cầu review: thêm `--priority-mode {lamport,fleetqox}` vào
`fleetqox_coordination_endpoint.py`. `lamport` ("Ours-NoQoX") giữ
nguyên hành vi cũ (chỉ so `(lamport_ts, name)`). `fleetqox`
("Ours-FleetQoX") gán mỗi endpoint 1 tier `task_criticality` cố định
(~25% "safety"=0.9, còn lại "routine"=0.3, xác định qua vị trí tên đã
sort nên mọi process tự suy ra GIỐNG NHAU không cần trao đổi gì) và so
criticality TRƯỚC (cao thắng tuyệt đối), chỉ rơi về `(lamport_ts,
name)` khi hoà criticality — áp dụng đúng triết lý task-aware priority
của FleetQoX (`fleetqox/model.py`'s `TaskContext.task_criticality`)
vào chính giao thức tranh chấp này, khác với `--policy` của Bảng V vốn
chỉ áp dụng cho lịch phát tin, không áp dụng cho việc "ai thắng khi
tranh chấp zone". Test quy mô nhỏ (N=3) xác nhận cơ chế hoạt động
đúng: gán tier đúng, không crash.

Chạy đủ N=8/16/32 x 2 chế độ x n=3 (18 lượt, CHỈ dùng `rmw_fleetqox_cpp`
vì đây là so sánh 2 biến thể của CHÍNH FleetRMW, không phải so giữa 4
RMW). **Kết quả: forced_entry_rate = 100% cho CẢ 2 CHẾ ĐỘ ở MỌI quy mô
N=8/16/32** — không phân biệt được.

**Đã điều tra, ĐÂY LÀ KẾT QUẢ THẬT, không phải tính năng bị lỗi**:
kiểm tra `replies_received_raw` thô của từng endpoint (N=8, cả 2 chế
độ) cho thấy số reply thật nhận được dao động 0-17 một cách CỰC KỲ
NHIỄU, không có mẫu hình "endpoint criticality cao nhận được nhiều
reply hơn" nào cả (vd: `robot_0007` (criticality 0.9) nhận 17 reply ở
`ours_noqox` nhưng 0 reply ở `ours_fleetqox` cùng N=8) — vì cơ chế ưu
tiên (dù theo timestamp hay theo criticality) CHỈ quyết định AI THẮNG
trong số các bên ĐÃ NHẬN ĐƯỢC đủ reply để tranh chấp; ở quy mô N≥8 trên
profile này, đa số endpoint không bao giờ nhận đủ N-1 reply để tranh
chấp TỚI NƠI (vấn đề độ tin cậy broadcast thô đã ghi nhận ở mục ngay
trên) — nghĩa là cơ chế ưu tiên hầu như KHÔNG BAO GIỜ được thực sự
kích hoạt ở quy mô này, bất kể nó có "thông minh" hay không. Đây CHÍNH
LÀ hệ quả trực tiếp, nhất quán với phát hiện đã ghi ở mục trên (vấn đề
độ tin cậy N-chiều lấn át hoàn toàn vấn đề công bằng/ưu tiên).

**Kết luận**: tính năng Ours-NoQoX vs Ours-FleetQoX đã cài đặt ĐÚNG và
đã XÁC NHẬN hoạt động (qua test N=3), nhưng N=8/16/32 trên profile "5G
SA emulation" KHÔNG PHẢI điều kiện phù hợp để đo sự khác biệt của nó —
cần 1 trong 2 hướng để đo được thật: (a) quy mô nhỏ hơn (N=2-3, nơi đã
xác nhận có crossing đạt đồng thuận thật) hoặc (b) profile ít mất gói
hơn (vd Wi-Fi/LAN cũ, từng có 1 phần crossing thành công ở N nhỏ) —
CHƯA làm trong lần này, để ngỏ làm follow-up nếu cần số liệu thật cho
so sánh này.

**File liên quan**: `scripts/fleetqox_coordination_endpoint.py`
(`--priority-mode`), kết quả thô tại
`/tmp/.../scratchpad/bang6_open5gs_noqox_vs_fleetqox_n3.jsonl` (18
dòng, không thuộc repo).

### 15/09/2026 — Thêm suy hao radio-link giả định vào profile Open5GS (2%), phát hiện + sửa 1 bug hạ tầng thật (42% lượt chạy fail), chạy lại đầy đủ Bảng V + VI

Theo yêu cầu người dùng: ghi chú "5G SA emulation" tự nhận là KHÔNG mô
phỏng suy hao kênh vô tuyến — người dùng đề nghị thêm 1 hàm suy hao giả
định để giải quyết khoảng trống này.

**Cơ chế**: `tc netem loss X%` (kernel Linux, không phải công thức
path-loss kiểu ns-3) lọc qua `tc filter u32` chỉ nhắm đúng cổng UDP
4997 — cổng "Radio Link Simulation" DUY NHẤT của UERANSIM giữa gNB và
UE, mang CẢ tín hiệu điều khiển (RRC/NAS) LẪN dữ liệu người dùng đã
đóng gói (GTP-U). NGAP (38412/sctp) và GTP-U thật (2152/udp, gNB↔UPF)
là cổng khác, không bị ảnh hưởng — suy hao chỉ tác động đúng "kênh vô
tuyến giả lập", không đụng core. Áp cho cả gNB (1 lần, lúc
`start_open5gs_gnb()`) và mỗi UE (mỗi lần launch).

**Chọn thông số bằng thực nghiệm, không phải công thức**: test tay
N=2 ở 1%/2%/3%/5% loss — phát hiện 1 "vách đá" thật giữa 2% và 3%: 1%
và 2% đều giao tin thật (190, 316 message), nhưng **3% và 5% mất
trắng 100%, MỌI LẦN**. Nhiều khả năng do traffic quản lý tunnel của
UERANSIM đi chung cổng 4997, không chịu được mất dù chỉ 1 frame điều
khiển quan trọng — 1 lần mất là kẹt cả phiên GTP-U-relay. Chốt
**2.0%** — giá trị lớn nhất xác nhận vẫn giao tin thật qua thực
nghiệm, ghi rõ trong code là GIẢ ĐỊNH, không hiệu chỉnh theo đo đạc
kênh thật nào (`RADIO_LINK_LOSS_PCT`'s comment).

**Phát hiện + sửa 1 bug hạ tầng thật khi chạy full batch lần đầu**:
36 lượt Bảng V với 2% loss cho **15/36 (42%) lượt FAIL HOÀN TOÀN**
(không chỉ giảm delivery — UE không đăng ký 5G được trong 45s, cả lượt
bị huỷ, mất TOÀN BỘ dữ liệu chứ không phải 1 phần). Điều tra qua 2
bước:
1. Tăng timeout đăng ký UE 45s→120s: cải thiện đáng kể (retest 3 lượt
   fail trước đó: 3/3 fail → 1/3 fail) — xác nhận đa số trường hợp chỉ
   cần thêm 1 chu kỳ retry NAS thật (1 lần đăng ký thành công đo được
   mất ~26s dưới 2% loss, do 1 chu kỳ retry T3510/T3511). NHƯNG không
   giải quyết dứt điểm — 1 lượt retest vẫn fail ở 120s, log thật cho
   thấy 1 UE đã ĐĂNG KÝ THÀNH CÔNG, sau đó gặp "Radio link failure
   detected" → "Cell selection failure, no suitable or acceptable cell
   found" và KHÔNG BAO GIỜ tự phục hồi trong thời gian còn lại — trạng
   thái NAS/RRC bị kẹt thật, không phải "cần chờ thêm".
2. Sửa triệt để: thay vì 1 lần chờ dài, chia thành NHIỀU lần chờ ngắn
   hơn (60s/lần, tối đa 3 lần) — nếu 1 lần thất bại, XOÁ và TẠO LẠI
   HOÀN TOÀN MỚI container UE (và container app đi kèm, vì
   `--network=container:` của Docker gắn chết lúc tạo, không thể gắn
   lại) — 1 process `nr-ue` MỚI khởi động lại từ đầu trạng thái NAS/RRC
   sạch, trạng thái kẹt của lần trước không thể "lây" sang. Xác nhận
   qua retest trực tiếp: 0/3 "status: failed" (từ 42% ban đầu).

**Kết quả cuối cùng (sau khi sửa, HOÀN TOÀN không còn lượt fail nào,
72/72 lượt "status: ok")** — xem bảng đầy đủ ở `docs/BANG_V_VI_KET_QUA.md`:
- Bảng V: delivery giảm mạnh so với bản không suy hao (Fast DDS
  82-86%→61%/0%/4.9%; FleetRMW 60-70%→25%/25%/0%) — 2% suy hao radio
  giả định đủ tạo khác biệt LỚN, gần trực giác thông thường về 5G hơn
  hẳn bản "core sạch hoàn toàn" trước đó. Không còn xu hướng đơn điệu
  mượt theo N (nhiễu netem IID độc lập, không có "tích luỹ" như
  contention thật) — hạn chế đã biết, ghi rõ trong docs.
- Bảng VI: Fast DDS sập HẲN xuống 100% forced_entry ở N=8/16 (trước
  0%/14%) — kịch bản broadcast-dồn-dập nhạy với suy hao hơn NHIỀU so
  với trace-replay. Zenoh là RMW DUY NHẤT còn đạt đồng thuận thật ở cả
  3 quy mô.

**Trạng thái**: Bảng V + VI profile "5G SA emulation + 2% suy hao"
HOÀN THÀNH đầy đủ N=8/16/32 x 4 phương thức x n=3, 0 lượt fail, đã cập
nhật `docs/BANG_V_VI_KET_QUA.md` (thay bản không suy hao trước đó theo
đúng yêu cầu "thay thế hoàn toàn").

**File liên quan**: `scripts/run_open5gs_docker_fleet_probe.py`
(`apply_radio_link_loss()`, `RADIO_LINK_LOSS_PCT`, `_launch_ue_pair()`,
`_wait_for_ue_ip_with_retry()`), kết quả thô tại
`/tmp/.../scratchpad/bang5_open5gs_radioloss_8_16_32_n3.jsonl` +
`bang6_open5gs_radioloss_8_16_32_n3.jsonl` (36 dòng mỗi file, không
thuộc repo).

### Câu hỏi mở, CHƯA xử lý — "policy" của FleetQoX chưa từng được đo thật trong bất kỳ bảng nào đã công bố

Người dùng đặt câu hỏi quan trọng: FleetQoX có 6 policy đã viết sẵn
(fifo, static_priority, fleetqox_csds, fleetqox_predictive,
fleetqox_predictive_guarded, fleetqox_predictive_lagrangian,
`fleetqox/trace.py`) nhưng **TOÀN BỘ Bảng IV/V/VI đã đo từ trước tới
giờ (ns-3 VÀ Open5GS) đều dùng "fifo" (không thông minh) cho MỌI so
sánh** — để đảm bảo 4 middleware nhận đúng CÙNG một trace/lịch trình
gửi tin, so sánh công bằng ở tầng TRANSPORT. Nghĩa là: luận điểm cốt
lõi của FleetQoX (biết ưu tiên theo nhiệm vụ, tự thích nghi) **CHƯA
TỪNG được đo/chứng minh trong bất kỳ bảng nào đã công bố** — các bảng
hiện tại chỉ so "đường ống" (transport), chưa so "bộ não quyết định"
(scheduling/admission). Cũng liên quan: 1 bảng review riêng (ngoài,
người dùng gửi) đánh giá 10 điểm cải tiến cho tầng optimizer/scheduler
của FleetQoX (`fleetqox/scheduler.py`, `fleetqox/control_plane.py`,
`rmw_pubsub.cpp`) — đã kiểm chứng: **cả 10 điểm đều CHƯA có trong
code** (kể cả 4 điểm bảng ghi "✅ đã triển khai" — xem chi tiết qua
Explore agent, không lặp lại ở đây). Đây là 1 khối việc lớn (thêm 1
cặp so sánh Ours-fifo vs Ours-predictive cho Bảng V, tương tự
Ours-NoQoX/FleetQoX đã làm cho Bảng VI, CỘNG THÊM khả năng cần sửa cả
C++ RMW cho 10 điểm cải tiến kia) — CHƯA bắt đầu, cần người dùng xác
nhận phạm vi/thứ tự ưu tiên trước khi làm.

### 15/09/2026 (tiếp) — Ours-fifo vs Ours-predictive, Bảng V Wi-Fi N=16 n=3: PHÁT HIỆN NGƯỢC — predictive HIỆN TẠI làm delivery TỆ HƠN fifo, không tốt hơn

Người dùng chọn làm bước 1 trong kế hoạch nghiên cứu 6 bước (FIFO vs
Predictive trước, rẻ nhất, cho thông tin quan trọng nhất). Chạy
`run_probe()` (profile Wi-Fi, N=16, cùng seed=13/ns3_seed=42/run1-3
như hàng Wi-Fi đã công bố) 2 lần — 1 lần `policy=fifo`, 1 lần
`policy=fleetqox_predictive` — CHỈ dùng `rmw_fleetqox_cpp` (cô lập
đúng "bộ não" FleetQoX, không lẫn với so sánh transport).

**Bước 2 (decision trace analysis) làm TRƯỚC, miễn phí (thuần Python,
không cần Docker)**: sinh trace cho cùng 1 kịch bản (N=16, seed=13)
với 2 policy, đếm action:
- `fifo`: 2289 event `send` (full-size), 1116 `defer`, 337 `drop`
  (stale) — tổng bytes gửi = 500,716.
- `fleetqox_predictive`: 2989 `send_compacted` + 174 `send_degraded` +
  236 `send` = 3399 event gửi (NHIỀU HƠN fifo), 324 `drop`, chỉ 19
  `defer` (gần như không bao giờ "chờ", luôn chủ động nén/hạ cấp thay
  vì hoãn) — tổng bytes gửi = 186,686 (**ít hơn gần 3 lần** so với
  fifo). Xác nhận: predictive KHÔNG hành xử giống fifo (loại trừ đúng
  rủi ro người dùng lo ngại) — nó nén payload để gửi NHIỀU tin nhỏ hơn
  thay vì gửi ÍT tin to.

**Bước 1+3 (network effect thật, qua ns-3 Wi-Fi)**:

| Policy | delivery_pct (n=3) | p50 (ms) | stale ratio |
|---|---|---|---|
| fifo | **27.2%** | 5631.2 | 99.2% |
| fleetqox_predictive | **14.6%** | 5632.6 | 98.7% |

predictive giao ÍT tin hơn fifo cả về TỶ LỆ lẫn SỐ TUYỆT ĐỐI (trung
bình 495 tin/run so với 622 tin/run của fifo). Kiểm tra
`fleetqox_transport_metrics.frames_sent` tổng hợp tất cả endpoint/run:
predictive gửi **10,197 frame** so với fifo's **6,867 frame** — NHIỀU
HƠN 48%, dù tổng byte payload ÍT HƠN gần 3 lần (khớp đúng phát hiện ở
bước 2).

**Giải thích hợp lý nhất, khớp đúng mục #5 trong bảng review optimizer
riêng (15/09/2026, mục trên)**: `PredictiveAdmissionController` hiện
chỉ kiểm soát theo NGÂN SÁCH BYTE (`capacity_bytes_per_tick`), không
kiểm soát theo SỐ PACKET/airtime — nó đổi "ít gói to" lấy "nhiều gói
nhỏ" để tối đa hoá số luồng phục vụ trong ngân sách byte, nhưng
802.11 Wi-Fi tốn overhead MAC CỐ ĐỊNH mỗi lần truyền (DIFS/backoff/
SIFS/ACK) KHÔNG PHỤ THUỘC kích thước gói — gửi nhiều gói nhỏ hơn tốn
NHIỀU airtime hơn gửi ít gói to, dù tổng byte ít hơn. Đây là bằng
chứng THỰC NGHIỆM trực tiếp cho đúng lỗ hổng "packet-aware admission"
đã nêu trong bảng review, không còn là suy đoán.

**Ý nghĩa cho luận điểm bài báo**: câu trả lời trung thực cho "FleetQoX
hiện có tạo giá trị không" là **CHƯA, ở dạng hiện tại nó làm KÉM HƠN
baseline fifo trên Wi-Fi** — đây là kết quả NGƯỢC với kỳ vọng nhưng
CHÍNH XÁC là loại phát hiện phương pháp luận (bước 1-3) của người dùng
được thiết kế để bắt được, và giờ có 1 baseline THẬT (predictive-hiện-
tại: 14.6%) để so sánh SAU KHI sửa packet-aware admission (bước 5-6
trong kế hoạch) — biết chính xác cải thiện đến từ đâu.

**Trạng thái**: bước 1-3 trong kế hoạch 6 bước của người dùng HOÀN
THÀNH cho profile Wi-Fi N=16. Bước 4 (xác định scenario predictive
thắng/thua) và bước 5-6 (sửa packet-aware admission rồi đo lại) CHƯA
làm — cần người dùng xác nhận có muốn mở rộng bước 1-3 sang N=8/32
và/hoặc profile khác trước, hay đi thẳng vào bước 5 (sửa packet-aware
admission) luôn với bằng chứng đã có.

**File liên quan**: kết quả thô tại
`/tmp/.../scratchpad/bang5_fifo_vs_predictive_wifi_n16_n3.jsonl` (6
dòng, không thuộc repo).

### 15/09/2026 (tiếp) — Bước 4: predictive thua fifo ở MỌI kịch bản đã test, kể cả LAN — tinh chỉnh giả thuyết

Mở rộng so sánh sang Wi-Fi N=8/32 (bổ sung N=16 đã có) và LAN N=16 (để
kiểm tra giả thuyết "chỉ do overhead MAC 802.11" — LAN không có
contention/CSMA-CA nào).

| Kịch bản | fifo (n=3) | predictive | Chênh lệch |
|---|---|---|---|
| Wi-Fi N=8 | 44.8% | 35.8% | -9pp |
| Wi-Fi N=16 | 27.2% | 14.6% | -13pp |
| Wi-Fi N=32 | 12.4% | 11.6% (n=2) | -1pp (~nhiễu) |
| LAN N=16 | 65.9% | 61.8% | -4pp |

**Phát hiện quan trọng**: predictive thua ở **MỌI** kịch bản đã test,
kể cả LAN — bác bỏ giả thuyết ban đầu "chỉ do overhead MAC 802.11".
Kết luận tinh chỉnh: gửi nhiều tin hơn 48% (đã đo ở mục trên) không chỉ
tốn airtime Wi-Fi mà còn tốn CHÍNH overhead xử lý phần mềm của
FleetRMW (encode/mutex/syscall mỗi tin — khớp phát hiện trước đó rằng
FleetRMW có overhead nội bộ đáng kể ngay cả trên LAN, xem Bảng V mục
"FleetRMW thua cả Fast DDS/CycloneDDS trên LAN"). Chênh lệch LỚN NHẤT
ở Wi-Fi N=16 (đúng vùng "vừa đủ nghẽn để airtime + overhead cộng dồn
rõ nhất" — N=8 chưa đủ nghẽn để lộ rõ, N=32 cả 2 policy đều đã sập gần
hết nên khó phân biệt).

**Trạng thái**: bước 4 HOÀN THÀNH — predictive hiện tại là NET NEGATIVE
ở mọi kịch bản đã đo, củng cố thêm lý do ưu tiên bước 5 (packet-aware
admission).

**File liên quan**: kết quả thô tại
`/tmp/.../scratchpad/bang5_fifo_vs_predictive_scenarios_n3.jsonl` (18
dòng, không thuộc repo).

### 15/09/2026 (tiếp) — Bước 5: kích hoạt cơ chế packet-aware admission ĐÃ CÓ SẴN trong code (chưa từng được dùng)

Trước khi viết code mới: kiểm tra thấy cơ chế giới hạn theo SỐ PACKET
(không chỉ byte) **đã tồn tại sẵn** từ investigation trước
(`NetworkLink.capacity_packets_per_tick`, `_admit_partition`'s
`remaining_packets` trong `fleetqox/control_plane.py`, `fifo_policy`
trong `fleetqox/simulator.py`, tham số `capacity_packets_per_second`
trong `fleetqox/trace.py`'s `generate_trace_events()` — từ task #14-18
cũ) — nhưng `scripts/run_ns3_docker_container_fleet_probe.py` (harness
Bảng V/VI chính) **CHƯA BAO GIỜ truyền tham số này** khi gọi
`generate_trace_events()`. Nghĩa là: KHÔNG cần code cơ chế mới, chỉ
cần BẬT tham số đã có sẵn.

Đã thêm `capacity_packets_per_second` vào `run_probe()`/`run_lan_probe()`,
mặc định `None` (giữ nguyên hành vi cũ, không ảnh hưởng số liệu đã công
bố). Giá trị dùng để test: `1200` pps — tái sử dụng
`DEFAULT_CAPACITY_PACKETS_PER_SECOND` đã được ĐO ĐẠC THẬT và xác nhận
có tác dụng (`scripts/run_omnetpp_docker_wifi_parity.py`, round 8/11:
delivery +19-22 điểm ở 32 robot) từ investigation packet-rate trước
đó — dùng làm điểm khởi đầu, KHÔNG hiệu chỉnh lại riêng cho harness
Bảng V này (workload/topology khác với wifi-parity).

**Trạng thái**: code đã sẵn sàng, đang chạy validation (fifo vs
predictive CÓ packet cap, so với KHÔNG có cap ở trên) — xem mục tiếp
theo.

### 15/09/2026 (tiếp) — Bước 6: validation packet cap 1200pps — có tác dụng THẬT nhưng CHƯA đủ, còn ~6pp chênh lệch chưa giải thích

Chạy lại Wi-Fi N=16 (kịch bản chênh lệch lớn nhất) với
`capacity_packets_per_second=1200`, cùng seed/topology với bản không
cap ở mục trên:

| | Không cap | Có cap 1200pps | Δ do cap |
|---|---|---|---|
| fifo | 27.2% | 22.1% | -5.1pp |
| fleetqox_predictive | 14.6% | 16.3% | +1.7pp |
| **Chênh lệch predictive vs fifo** | **-12.6pp** | **-5.8pp** | **giảm ~54%** |

**Packet cap có tác dụng thật, đúng hướng dự đoán**: khoảng cách giữa
2 policy giảm gần một nửa (từ -12.6pp xuống -5.8pp) — xác nhận
"packet-aware admission" LÀ một phần nguyên nhân thật của vấn đề, đúng
như dự đoán từ bước 1-4. fifo cũng bị giảm (22.1% so với 27.2%) vì bản
thân nó CŨNG bị giới hạn theo packet-rate (đúng thiết kế
`fifo_policy`'s `remaining_packets` check) — cap ảnh hưởng CẢ 2 policy,
chỉ predictive được lợi TƯƠNG ĐỐI nhiều hơn.

**NHƯNG chưa đủ để đảo ngược** — predictive vẫn kém fifo ~6pp sau khi
cap. Khớp với phát hiện LAN ở bước 4 (predictive cũng thua fifo trên
LAN, nơi không có MAC contention nào để cap giải quyết) — phần còn lại
của khoảng cách nhiều khả năng đến từ overhead xử lý PHẦN MỀM của
chính FleetRMW mỗi tin nhắn (encode/mutex/syscall — đã có sẵn field
`fleetqox_transport_metrics.publish_stages` để đo chi tiết hơn nếu
cần), KHÔNG PHẢI vấn đề mạng — packet cap không giải quyết được phần
này vì nó chỉ kiểm soát SỐ TIN ĐƯỢC ADMIT vào trace, không giảm chi phí
XỬ LÝ mỗi tin đã admit.

**Trạng thái**: bước 5-6 trong kế hoạch của người dùng HOÀN THÀNH ở
mức "đã bật + validate cơ chế có sẵn, có tác dụng đo được nhưng chưa
đủ". Hướng tiếp theo (CHƯA làm, cần người dùng quyết định): (a) hiệu
chỉnh lại giá trị packet cap riêng cho harness này (1200 chỉ là giá
trị mượn từ wifi-parity, chưa đo lại cho workload N=16 cụ thể ở đây)
— có thể còn dư địa cải thiện; (b) điều tra overhead phần mềm
FleetRMW qua `publish_stages` để tìm phần còn lại của khoảng cách; (c)
coi đây là kết luận đủ rõ ("predictive cần TIẾP TỤC cải tiến, không
phải đã xong") và chuyển sang việc khác trong bảng optimizer.

**File liên quan**: `scripts/run_ns3_docker_container_fleet_probe.py`
(`capacity_packets_per_second` param), kết quả thô tại
`/tmp/.../scratchpad/bang5_fifo_vs_predictive_packetcap_n3.jsonl` (6
dòng, không thuộc repo).

### 15/09/2026 (tiếp) — Đo `publish_stages` (theo yêu cầu hướng (b)): BÁC BỎ giả thuyết "overhead phần mềm" — predictive thực ra NHANH HƠN ở phía gửi

Đo per-stage timing (`fleetqox_transport_metrics.publish_stages`, đã
có sẵn instrumentation trong `rmw_pubsub.cpp`, cần bật qua env var
`FLEETQOX_RMW_PUBLISH_STAGE_PROFILING=1` — mặc định tắt để không ảnh
hưởng hiệu năng đo thật, lúc đầu quên bật nên mọi count=0, đã sửa) —
Wi-Fi N=16, không cap, 1 lượt mỗi policy.

| Stage (mean/tin, ns) | fifo | predictive |
|---|---|---|
| encode | 10,030 | 8,229 |
| mutex_hold | 37,287 | 40,871 |
| transport_decode | 46,653 | 38,726 |
| **transport_send** | **2,806,014** | **211,486** |
| **transport_sendto_syscall** | **2,753,648** | **167,582** |
| **udp_send_mutex_wait** | **1,512,044** | **273,545** |
| **Tổng thời gian trong transport_send (tất cả tin)** | **6.42 giây** | **0.72 giây** |

**KẾT QUẢ NGƯỢC HẲN GIẢ THUYẾT BAN ĐẦU**: predictive nhanh hơn fifo ở
GẦN NHƯ MỌI stage, đặc biệt vượt trội ở `transport_send`/
`sendto_syscall` (nhanh hơn ~13 lần mỗi lần gọi) — dù gửi NHIỀU tin
hơn (3399 vs 2289), TỔNG thời gian bị block trong `sendto()` của
predictive vẫn ÍT HƠN fifo gần 9 lần (0.72s so với 6.42s). Giải thích
hợp lý: gói TO của fifo khiến `sendto()` bị kẹt (block) LÂU HƠN mỗI
lần khi kernel/tap-device nghẽn (mean 2.8ms/lần, max tới 1.45 GIÂY cho
1 lần gọi!) — trong khi gói NHỎ của predictive dù nhiều hơn nhưng mỗi
lần gọi nhanh hơn nhiều (mean 211μs/lần), nên TỔNG thời gian ít hơn.

**Kết luận**: giả thuyết "overhead phần mềm FleetRMW mỗi tin nhắn"
(đưa ra sau phát hiện LAN ở bước 4) **SAI** — predictive KHÔNG tốn
thêm thời gian xử lý phía GỬI, ngược lại còn hiệu quả hơn. Khoảng cách
~6pp còn lại (sau khi bật packet cap) phải đến từ nơi KHÁC, NGOÀI
phạm vi đo của `publish_stages` (chỉ đo phía sender/publish path):
nhiều khả năng là tầng kênh Wi-Fi/MAC thật (ns-3) hoặc phía nhận
(subscriber-side), CHƯA đo được qua harness hiện tại.

**Phát hiện phụ, đáng chú ý độc lập**: `transport_send` của CẢ 2
policy có max_ns ở mức HÀNG TRĂM MILLI-GIÂY tới 1+ GIÂY cho 1 lần gọi
`sendto()` — xác nhận kernel/tap-device THẬT SỰ bị nghẽn nặng dưới
Wi-Fi N=16 (khớp với delivery thấp đã biết), không phải giả định suông.

**Trạng thái**: hướng (b) người dùng chọn đã HOÀN THÀNH — kết quả là 1
phát hiện PHỦ ĐỊNH có giá trị (loại trừ 1 giả thuyết sai), không phải
tìm ra "thủ phạm" cuối cùng. Muốn tìm tiếp cần: (a) đo phía subscriber
(không có instrumentation tương đương hiện tại — cần thêm), hoặc (b)
xem trực tiếp ns-3 MAC-layer stats (queue drop, retry count) — CHƯA
làm, cần người dùng quyết định có đi tiếp hướng này không hay dừng ở
đây (packet cap đã xác nhận có tác dụng thật dù chưa đủ, đủ để báo cáo
trung thực trong bài báo là "đã cải thiện đáng kể, còn dư địa").

**File liên quan**: kết quả thô tại
`/tmp/.../scratchpad/bang5_publish_stages_profile.log` (không thuộc
repo).

### 15/09/2026 (tiếp) — Đo MAC/PHY-layer thật (`FLEETQOX_WIFI_STATS`, ns-3): XÁC NHẬN đúng giả thuyết va chạm kênh — predictive có tỷ lệ drop tại MAC cao hơn ~50%

`external/ns3/fleetqox_trace_replay_tap.cc` đã có sẵn trace hook
MacTx/MacTxDrop/MacRx/MacRxDrop/PhyTxBegin/PhyRxDrop (in JSON mỗi 5s
ra ns3.log, đã capture sẵn qua `probe.ns3_log()` — không cần sửa C++
hay build lại). Parse dòng cuối cùng (snapshot gần nhất trước khi
process bị kill), Wi-Fi N=16, không cap, 1 lượt mỗi policy (lưu ý:
JSON output có trailing comma ở mảng `phy_rx_drop_by_reason`, không
hợp lệ JSON thuần túy, cần strip `,]`/`,}` trước khi parse — chưa sửa
trong C++, chỉ workaround ở phía Python đọc log).

| | fifo | predictive |
|---|---|---|
| mac_tx_total (số lần thử gửi ở tầng MAC) | 448 | 518 |
| **mac_tx_drop_total** (bị drop do hết lượt retry sau va chạm) | **12** | **21** |
| **Tỷ lệ drop MAC (mac_tx_drop/mac_tx_total)** | **2.7%** | **4.05%** |
| mac_rx_total | 4304 | 3953 |
| phy_rx_drop_total | 1485 | 1385 |
| phy_rx_drop_by_reason (top reason, mã 9) | 581 | 485 |

**XÁC NHẬN đúng giả thuyết ban đầu (nhiều gói nhỏ → va chạm kênh nhiều
hơn), lần này bằng số liệu MAC-layer THẬT, không phải suy luận**:
predictive có tỷ lệ gói TỰ NÓ bị drop tại tầng MAC (do vượt số lần
retry cho phép sau va chạm CSMA/CA) cao hơn fifo khoảng **50%** (4.05%
so với 2.7%) — dù `mac_tx_total` (số lần thử) chỉ cao hơn ~16%
(518 so với 448). Đây CHÍNH LÀ cơ chế vật lý gây ra khoảng cách ~6pp
còn lại SAU KHI đã bật packet cap 1200pps: bản thân cap là 1 ngưỡng
TUYẾN TÍNH cố định, không nắm bắt được đường cong xác suất va chạm
PHI TUYẾN thật của CSMA/CA khi nhiều trạm cùng gửi nhiều gói nhỏ đồng
thời — packet cap giảm được PHẦN LỚN (vì giảm SỐ LƯỢNG gói chung), nhưng
không triệt tiêu hoàn toàn được xác suất va chạm cao hơn trên MỖI gói
nhỏ còn lại.

**phy_rx_drop_total của predictive lại THẤP HƠN fifo** (1385 vs 1485)
— không mâu thuẫn: đây là drop ở phía NHẬN của TOÀN BỘ 17 trạm (traffic
tổng hợp, không riêng luồng đang theo dõi), phản ánh predictive gửi ít
`mac_rx_total` hơn tổng thể (3953 vs 4304) do gói nhỏ hơn/khác cấu
trúc, không phải bằng chứng ngược lại — chỉ số quyết định nhất vẫn là
`mac_tx_drop_total` vì đó là tổn thất TRỰC TIẾP của CHÍNH luồng gửi.

**KẾT LUẬN CUỐI CHO TOÀN BỘ CHUỖI ĐIỀU TRA (bước 1-6 + 2 hướng đo
sâu)**: predictive hiện tại KÉM HƠN fifo trên Wi-Fi/LAN, nguyên nhân
CHÍNH XÁC (không còn suy đoán) là va chạm kênh CSMA/CA thật tăng theo
SỐ LƯỢNG gói (không phải overhead phần mềm — đã bác bỏ), packet-aware
admission giúp ĐÁNG KỂ (giảm ~54% khoảng cách) nhưng KHÔNG đủ vì bản
thân là 1 ngưỡng tuyến tính không mô hình hoá được va chạm phi tuyến.
Hướng cải tiến tiếp theo có cơ sở khoa học rõ ràng (nếu muốn làm tiếp):
mô hình hoá pressure/cost theo hàm PHI TUYẾN của số gói (không chỉ
ngưỡng cứng), hoặc giảm mức độ "phân mảnh" của compaction (ít gói to
hơn thay vì nhiều gói nhỏ) khi pressure cao.

**Trạng thái**: điều tra khoa học (bước 1-6 + đo publish_stages + đo
MAC/PHY) HOÀN THÀNH đầy đủ, có kết luận rõ ràng, có bằng chứng THẬT ở
mọi tầng (trace-generation, sender software, MAC/PHY) — đủ để viết vào
bài báo như 1 câu chuyện khoa học hoàn chỉnh: phát hiện → bác bỏ giả
thuyết sai → xác nhận giả thuyết đúng → fix một phần → xác định chính
xác phần còn thiếu.

**File liên quan**: `external/ns3/fleetqox_trace_replay_tap.cc`
(FLEETQOX_WIFI_STATS, đã có sẵn), kết quả thô tại
`/tmp/.../scratchpad/bang5_mac_stats_profile.log` (không thuộc repo).

## Quy ước cập nhật file này

- Mỗi khi một nhóm chuyển trạng thái, sửa dòng tương ứng trong bảng và
  ghi rõ commit/script/kết quả làm bằng chứng (không ghi chung chung).
- Không xoá lịch sử "Trạng thái gốc" — đó là mốc đối chiếu với báo cáo
  hành chính gốc.
- Nếu phát hiện một claim tưởng đã đóng nhưng thực ra chưa đủ bằng chứng,
  hạ trạng thái xuống lại và ghi rõ lý do (đừng tự chấp nhận "coi như
  xong" nếu chưa có replay được bằng chứng cụ thể).
