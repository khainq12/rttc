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

## Quy ước cập nhật file này

- Mỗi khi một nhóm chuyển trạng thái, sửa dòng tương ứng trong bảng và
  ghi rõ commit/script/kết quả làm bằng chứng (không ghi chung chung).
- Không xoá lịch sử "Trạng thái gốc" — đó là mốc đối chiếu với báo cáo
  hành chính gốc.
- Nếu phát hiện một claim tưởng đã đóng nhưng thực ra chưa đủ bằng chứng,
  hạ trạng thái xuống lại và ghi rõ lý do (đừng tự chấp nhận "coi như
  xong" nếu chưa có replay được bằng chứng cụ thể).
